import httpx
import pytest
from sqlalchemy import select

from agents import client as mcp_client
from agents.accounts import agent as accounts
from agents.knowledge import agent as knowledge
from agents.services import agent as services
from agents.transactions import agent as transactions
from apps.banking_api.app import app
from apps.banking_api.chat import router
from core.config.settings import get_settings
from mcp_servers.factory import execute_tool
from mock_bank.models.entities import ChatMessage

if not any(getattr(r, "path", "") == "/chat" for r in app.routes):
    app.include_router(router)


@pytest.fixture
def chat_bank(signed, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "checkpoint_url", str(tmp_path / "checkpoints.db"))

    async def call(agent, name, args):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bank") as http:
            return await execute_tool(name, args, mcp_client.bearer.get(), http)

    for module in (mcp_client, accounts, transactions, services, knowledge):
        monkeypatch.setattr(module, "call_tool", call)
    return signed


def test_combined_and_checkbook(chat_bank):
    c, f, a, b, aid, _ = chat_bank
    r = c.post(
        "/chat", headers=a, json={"message": "Show my balance and last five transactions", "account_id": aid}
    )
    assert r.status_code == 200, r.text
    data = r.json()
    sid = data["session_id"]
    assert "₹24,355.50" in data["response"] and len(data["transactions"]) == 5
    assert c.get(f"/chat/{sid}/history", headers=b).status_code == 404
    prepared = c.post("/chat", headers=a, json={"session_id": sid, "message": "Request a checkbook"}).json()
    assert prepared["pending_action"]
    assert c.post(f"/chat/{sid}/verify-otp", headers=a, json={"otp": "654321"}).status_code == 409
    assert c.post(f"/chat/{sid}/confirm", headers=a).status_code == 200
    done = c.post(f"/chat/{sid}/verify-otp", headers=a, json={"otp": "654321"})
    assert done.status_code == 200, done.text
    assert "SR-" in done.json()["response"]
    with f() as db:
        for m in db.scalars(select(ChatMessage)):
            assert "654321" not in m.content
    assert c.delete(f"/chat/{sid}", headers=a).status_code == 200


def test_account_clarification(chat_bank):
    c, _, a, _, _, _ = chat_bank
    data = c.post("/chat", headers=a, json={"message": "What is my balance?"}).json()
    assert "select" in data["response"].lower() and "₹" not in data["response"]


def test_input_rejected_before_checkpoint(chat_bank):
    c, _, a, _, aid, _ = chat_bank
    assert (
        c.post(
            "/chat", headers=a, json={"message": "Show balance for CUST-002", "account_id": aid}
        ).status_code
        == 403
    )


def test_model_failure_is_safe(chat_bank, monkeypatch):
    from agents.coordinator import graph
    from core.errors import BankError

    async def failed(*args):
        raise BankError("MODEL_UNAVAILABLE", "Cannot understand this request now.", 503)

    monkeypatch.setattr(graph, "classify", failed)
    c, _, a, _, aid, _ = chat_bank
    r = c.post("/chat", headers=a, json={"message": "Show my balance", "account_id": aid})
    assert r.status_code == 503
    assert "Traceback" not in r.text and "₹" not in r.text


def test_tool_failure_never_invents_balance(chat_bank, monkeypatch):
    from core.errors import SafeError
    from mcp_servers.contracts import ToolResult

    async def failed(*args):
        return ToolResult(
            ok=False, error=SafeError(error_code="BANK_DOWN", safe_message="Unavailable", trace_id="test")
        )

    monkeypatch.setattr(accounts, "call_tool", failed)
    c, _, a, _, aid, _ = chat_bank
    r = c.post("/chat", headers=a, json={"message": "Show my balance", "account_id": aid})
    assert r.status_code == 200, r.text
    assert "₹" not in r.json()["response"] and "cannot verify" in r.json()["response"]


def test_history_restores_owned_proposal_and_order(chat_bank):
    c, _, a, b, aid, _ = chat_bank
    r = c.post("/chat", headers=a, json={"message": "Request a checkbook", "account_id": aid}).json()
    sid = r["session_id"]
    restored = c.get(f"/chat/{sid}/history", headers=a).json()
    assert restored["selected_account_id"] == aid
    assert restored["pending_action"]["action_id"] == r["pending_action"]["action_id"]
    assert [m["role"] for m in restored["messages"]] == ["user", "assistant"]
    assert c.get(f"/chat/{sid}/history", headers=b).status_code == 404


def test_expired_proposal_can_be_cleared(chat_bank):
    from mock_bank.models.entities import PendingAction

    c, f, a, _, aid, _ = chat_bank
    data = c.post("/chat", headers=a, json={"message": "Request a checkbook", "account_id": aid}).json()
    with f.begin() as db:
        db.get(PendingAction, data["pending_action"]["action_id"]).expires_at = 1
    assert c.post(f"/chat/{data['session_id']}/confirm", headers=a).status_code == 409
    assert c.delete(f"/chat/{data['session_id']}", headers=a).status_code == 200
