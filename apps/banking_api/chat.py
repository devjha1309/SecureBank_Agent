"""Chat session ownership and confirmation controls outside the language model."""

import asyncio
import json
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import delete, select

from agents.client import bearer, permissions
from agents.coordinator.graph import coordinator
from apps.banking_api.app import DB, Auth
from core.authorization.policy import own_account, require
from core.errors import BankError
from core.guardrails.input import validate_message
from core.memory.store import store
from core.observability.telemetry import audit, customer_hash, trace_id
from core.pii.redaction import redact
from mock_bank.api import operations as op
from mock_bank.api.schemas import ChatInput, StrictModel
from mock_bank.models.entities import ChatMessage, ChatSession, SecurityAuditLog, now

router = APIRouter()
locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class ChatOTP(StrictModel):
    otp: str = Field(pattern=r"^\d{6}$")


@asynccontextmanager
async def session_lock(session_id):
    if store.redis:
        lock = store.redis.lock(
            "chat-lock:" + session_id, timeout=180, blocking_timeout=0, thread_local=False
        )
        if not await asyncio.to_thread(lock.acquire, blocking=False):
            raise BankError("SESSION_BUSY", "Another request is running. Please wait.", 409)
        try:
            yield
        finally:
            await asyncio.to_thread(lock.release)
    else:
        lock = locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise BankError("SESSION_BUSY", "Another request is running. Please wait.", 409)
        async with lock:
            yield


def owned_chat(db, auth, session_id):
    chat = db.get(ChatSession, session_id)
    if (
        not chat
        or chat.customer_id != (auth.customer_id or auth.user_id)
        or chat.auth_session_id != auth.session_id
        or chat.expires_at <= now()
    ):
        raise BankError("SESSION_EXPIRED", "This conversation is unavailable. Start a new conversation.", 404)
    return chat


def save_response(db, auth, chat, result):
    pending = result.get("pending_action")
    text = result.get("final_response")
    if pending:
        chat.pending_action_id = pending["action_id"]
        text = (
            pending["summary"]
            + " Review this proposal and use Confirm or Cancel. No request has been submitted."
        )
    else:
        chat.pending_action_id = None
    if result.get("selected_account_id"):
        chat.selected_account_id = result["selected_account_id"]
    text = redact(text or "Please clarify your request.")
    db.add(ChatMessage(session_id=chat.id, role="assistant", content=text))
    # Only sanitized compact summary is retained; no raw prompts or token mappings.
    chat.summary = text[-800:]
    audit(
        db, auth, "chat:" + ",".join(result.get("detected_intents", [])), "paused" if pending else "completed"
    )
    from sqlalchemy import func

    from mock_bank.models.entities import AgentCheckpoint, ModelUsage

    for usage in result.get("model_usage", []):
        db.add(ModelUsage(**usage))
    version = (
        db.scalar(select(func.max(AgentCheckpoint.version)).where(AgentCheckpoint.session_id == chat.id)) or 0
    ) + 1
    db.add(
        AgentCheckpoint(
            session_id=chat.id,
            version=version,
            state={
                "intents": result.get("detected_intents", []),
                "agents": result.get("selected_agents", []),
                "status": "paused" if pending else "completed",
                "response": text,
            },
        )
    )
    db.commit()
    transactions = []
    download_reference = None
    for intent, item in result.get("agent_results", {}).items():
        if item.get("ok"):
            transactions.extend(item["data"].get("transactions", []))
            download_reference = item["data"].get("download_reference", download_reference)
    return {
        "session_id": chat.id,
        "response": text,
        "pending_action": pending,
        "transactions": transactions,
        "download_reference": download_reference,
        "agents": result.get("selected_agents", []),
        "trace_id": trace_id.get(),
        "selected_account_id": chat.selected_account_id,
    }


async def invoke(request, auth, chat, state=None, resume=None):
    token = bearer.set(request.headers.get("authorization", ""))
    permission_token = permissions.set(auth.permissions)
    try:
        return await coordinator.run(state, chat.id, resume=resume)
    finally:
        bearer.reset(token)
        permissions.reset(permission_token)


@router.post("/chat")
async def chat(data: ChatInput, request: Request, auth: Auth, db: DB):
    require(auth, "knowledge:read")
    store.rate("chat:" + auth.session_id, 30)
    try:
        validate_message(data.message)
    except BankError:
        db.add(
            SecurityAuditLog(
                trace_id=trace_id.get(), customer_hash=customer_hash(auth.customer_id), event="input_rejected"
            )
        )
        db.commit()
        raise
    if data.session_id:
        session = owned_chat(db, auth, data.session_id)
    else:
        session = ChatSession(
            customer_id=auth.customer_id or auth.user_id,
            auth_session_id=auth.session_id,
            expires_at=now() + 3600,
        )
        db.add(session)
        db.flush()
    async with session_lock(session.id):
        if session.pending_action_id:
            raise BankError(
                "CONFIRMATION_PENDING",
                "Confirm or cancel the pending proposal before sending another message.",
                409,
            )
        if data.account_id:
            own_account(db, auth, data.account_id, "accounts:read")
            session.selected_account_id = data.account_id
        sanitized = redact(data.message)
        recent = list(
            db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(6)
            )
        )
        messages = [{"role": m.role, "content": m.content} for m in reversed(recent)] + [
            {"role": "user", "content": sanitized}
        ]
        db.add(ChatMessage(session_id=session.id, role="user", content=sanitized))
        db.commit()
        store.put("chat-meta:" + session.id, {"selected_account_id": session.selected_account_id}, 3600)
        state = {
            "messages": messages,
            "session_id": session.id,
            "authenticated_customer_id": customer_hash(auth.customer_id),
            "selected_account_id": session.selected_account_id,
            "trace_id": trace_id.get(),
            "sensitive": sanitized != data.message,
            "summary": session.summary,
        }
        result = await invoke(request, auth, session, state)
        return save_response(db, auth, session, result)


@router.post("/chat/stream")
async def chat_stream(data: ChatInput, request: Request, auth: Auth, db: DB):
    # SSE progress is safe and independent of chain-of-thought. Completion is grounded.
    async def stream():
        yield "event: progress\ndata: " + json.dumps({"message": "Understanding your request"}) + "\n\n"
        try:
            result = await chat(data, request, auth, db)
            yield "event: result\ndata: " + json.dumps(result) + "\n\n"
        except BankError as exc:
            yield "event: error\ndata: " + exc.payload.model_dump_json() + "\n\n"
        except Exception:
            yield (
                "event: error\ndata: "
                + json.dumps(
                    {"safe_message": "This request could not be completed.", "trace_id": trace_id.get()}
                )
                + "\n\n"
            )

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.get("/chat/{session_id}/history")
def history(session_id: str, auth: Auth, db: DB):
    owned_chat(db, auth, session_id)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
        .limit(100)
    )
    return {"messages": [{"role": m.role, "content": m.content} for m in messages]}


@router.post("/chat/{session_id}/confirm")
async def confirm(session_id: str, auth: Auth, db: DB):
    async with session_lock(session_id):
        session = owned_chat(db, auth, session_id)
        if not session.pending_action_id:
            raise BankError("NO_PENDING_ACTION", "There is no proposal to confirm.", 409)
        result = op.confirm(db, auth, session.pending_action_id)
        db.commit()
        return result


@router.post("/chat/{session_id}/verify-otp")
async def verify(session_id: str, data: ChatOTP, request: Request, auth: Auth, db: DB):
    async with session_lock(session_id):
        session = owned_chat(db, auth, session_id)
        if not session.pending_action_id:
            raise BankError("NO_PENDING_ACTION", "There is no proposal to verify.", 409)
        action = op.owned_action(db, auth, session.pending_action_id)
        if action.status != "verified":
            op.verify_otp(db, auth, session.pending_action_id, data.otp)
        db.commit()
        result = await invoke(request, auth, session, resume={"verified": True})
        return save_response(db, auth, session, result)


@router.post("/chat/{session_id}/cancel")
async def cancel(session_id: str, request: Request, auth: Auth, db: DB):
    async with session_lock(session_id):
        session = owned_chat(db, auth, session_id)
        if not session.pending_action_id:
            raise BankError("NO_PENDING_ACTION", "There is no proposal to cancel.", 409)
        op.cancel(db, auth, session.pending_action_id)
        db.commit()
        result = await invoke(request, auth, session, resume={"cancel": True})
        return save_response(db, auth, session, result)


@router.delete("/chat/{session_id}")
async def clear(session_id: str, auth: Auth, db: DB):
    async with session_lock(session_id):
        session = owned_chat(db, auth, session_id)
        if session.pending_action_id:
            op.cancel(db, auth, session.pending_action_id)
        from mock_bank.models.entities import AgentCheckpoint

        db.execute(delete(AgentCheckpoint).where(AgentCheckpoint.session_id == session_id))
        db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
        db.delete(session)
        db.commit()
        await coordinator.start()
        assert coordinator.graph is not None
        await coordinator.graph.checkpointer.adelete_thread(session_id)
        store.delete("chat-meta:" + session_id)
        return {"status": "cleared"}
