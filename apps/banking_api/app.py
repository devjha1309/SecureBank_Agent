"""FastAPI authentication and mock core banking boundary."""

import csv
import io
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.auth.security import AuthContext, current_auth, digest, hasher, token_pair, verify_password
from core.authorization.policy import own_account, require
from core.errors import BankError
from core.memory.store import store
from core.observability.telemetry import (
    LATENCY,
    REQUESTS,
    SECURITY,
    audit,
    configure_tracing,
    trace_id,
)
from mock_bank.api import operations as op
from mock_bank.api.schemas import (
    ActionInput,
    OTPInput,
    PreferenceInput,
    PrepareInput,
    RefreshInput,
    SearchInput,
    StatementInput,
    SubmitInput,
)
from mock_bank.database.session import get_db
from mock_bank.models.entities import (
    AuthSession,
    Customer,
    ServiceRequest,
    Statement,
    Transaction,
    User,
    now,
)

DB = Annotated[Session, Depends(get_db)]
Auth = Annotated[AuthContext, Depends(current_auth)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_tracing()
    yield
    from agents.coordinator.graph import coordinator

    await coordinator.close()


async def request_audit(request: Request, db: DB):
    from core.auth.security import authenticate
    from core.observability.telemetry import customer_hash
    from mock_bank.models.entities import SecurityAuditLog

    authorization = request.headers.get("authorization", "")
    auth = None
    if authorization.startswith("Bearer "):
        try:
            auth = authenticate(authorization[7:], db)
        except BankError:
            pass
    started = time.monotonic()
    status = "success"
    try:
        yield
    except Exception:
        status = "failed"
        db.rollback()
        raise
    finally:
        if auth:
            route = getattr(request.scope.get("route"), "path", "unknown")
            audit(db, auth, request.method + " " + route, status, int((time.monotonic() - started) * 1000))
            if status == "failed":
                db.add(
                    SecurityAuditLog(
                        trace_id=trace_id.get(),
                        customer_hash=customer_hash(auth.customer_id),
                        event="request_denied_or_failed",
                    )
                )
            db.commit()


app = FastAPI(
    title="SecureBank Agent — synthetic development banking",
    version="0.2.0",
    lifespan=lifespan,
    dependencies=[Depends(request_audit)],
)


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    started = time.monotonic()
    token = trace_id.set(uuid4().hex)
    try:
        store.rate("edge:" + (request.client.host if request.client else "unknown"), 600)
        if request.url.path.startswith("/assistant"):
            response = await call_next(request)
        else:
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                chunks.append(chunk)
                if size > 32768:
                    raise BankError("REQUEST_TOO_LARGE", "The request is too large.", 413)
            request._body = b"".join(chunks)
            response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id.get()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        label = getattr(route, "path", "unmatched")
        REQUESTS.labels(label, str(response.status_code)).inc()
        LATENCY.labels(label).observe(time.monotonic() - started)
        return response
    except BankError as exc:
        return JSONResponse(exc.payload.model_dump(), status_code=exc.status)
    finally:
        trace_id.reset(token)


@app.exception_handler(BankError)
async def safe_error(request: Request, exc: BankError):
    exc.payload.trace_id = trace_id.get() or exc.payload.trace_id
    if exc.status in (401, 403, 404, 429):
        SECURITY.labels(exc.payload.error_code).inc()
    return JSONResponse(exc.payload.model_dump(), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc):
    error = BankError("INVALID_INPUT", "Check the supplied fields and try again.", 422)
    return await safe_error(request, error)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc):
    error = BankError("BANKING_API_UNAVAILABLE", "We cannot verify that information right now.", 503, True)
    if isinstance(exc, IntegrityError):
        error = BankError("ACTION_CONFLICT", "This action has already been processed or is in progress.", 409)
    return await safe_error(request, error)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc):
    return await safe_error(
        request, BankError("SERVICE_UNAVAILABLE", "We cannot complete this request right now.", 503, True)
    )


@app.get("/health")
def health():
    return {"status": "ok", "mode": "synthetic-development"}


@app.get("/ready")
def ready(db: DB):
    db.execute(select(User.id).limit(1))
    if store.redis:
        store.redis.ping()
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/auth/login")
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    store.rate("login:" + digest((request.client.host if request.client else "") + form.username), 10)
    user = db.scalar(select(User).where(User.username == form.username))
    # A dummy hash makes unknown-user password checking comparable to valid-user checking.

    encoded = user.password_hash if user else DUMMY_HASH
    if not verify_password(form.password, encoded) or not user or not user.active:
        raise BankError("INVALID_CREDENTIALS", "Username or password is incorrect.", 401)
    tokens = token_pair(db, user)
    db.commit()
    return tokens


DUMMY_HASH = hasher.hash("not-a-demo-password")


@app.post("/auth/refresh")
def refresh(data: RefreshInput, db: DB):
    old = digest(data.refresh_token)
    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.refresh_hash == old, AuthSession.revoked.is_(False))
        .with_for_update()
    )
    if not session or session.expires_at <= now():
        raise BankError("AUTH_REQUIRED", "Please sign in again.", 401)
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise BankError("AUTH_REQUIRED", "Please sign in again.", 401)
    # Atomic claim prevents concurrent refresh-token reuse, including on SQLite.
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.id == session.id, AuthSession.refresh_hash == old)
        .values(refresh_hash=uuid4().hex)
    )
    if getattr(result, "rowcount", 0) != 1:
        raise BankError("AUTH_REQUIRED", "Please sign in again.", 401)
    tokens = token_pair(db, user, session)
    db.commit()
    return tokens


@app.post("/auth/logout")
def logout(auth: Auth, db: DB):
    session = db.get(AuthSession, auth.session_id)
    assert session is not None
    session.revoked = True
    db.commit()
    return {"status": "signed_out"}


@app.get("/auth/me")
def me(auth: Auth, db: DB):
    c = db.get(Customer, auth.customer_id) if auth.customer_id else None
    return {
        "first_name": c.first_name if c else "Employee",
        "masked_customer_id": "••••" + (auth.customer_id or "")[-3:] if c else "employee",
        "membership": c.membership if c else auth.roles[0],
        "permissions": sorted(auth.permissions),
        "roles": auth.roles,
    }


@app.get("/customers/me/accounts")
def accounts(auth: Auth, db: DB):
    result = op.list_accounts(db, auth)
    audit(db, auth, "list_customer_accounts", "success")
    db.commit()
    return {"accounts": result}


@app.get("/accounts/{account_id}")
def account(account_id: str, auth: Auth, db: DB):
    return op.account_view(own_account(db, auth, account_id, "accounts:read"))


@app.get("/accounts/{account_id}/balance")
def balance(account_id: str, auth: Auth, db: DB):
    a = own_account(db, auth, account_id, "balance:read")
    audit(db, auth, "get_account_balance", "success")
    db.commit()
    return {**op.account_view(a), "current_paise": a.current_paise, "available_paise": a.available_paise}


@app.get("/accounts/{account_id}/transactions")
def transactions(account_id: str, auth: Auth, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 5):
    own_account(db, auth, account_id, "transactions:read")
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.account_id == account_id)
        .order_by(Transaction.date.desc(), Transaction.id)
        .limit(limit)
    )
    result = {"transactions": [op.transaction_view(t) for t in rows]}
    audit(db, auth, "get_recent_transactions", "success")
    db.commit()
    return result


@app.get("/transactions/{transaction_id}")
def transaction(transaction_id: str, auth: Auth, db: DB):
    return op.transaction_view(op.owned_transaction(db, auth, transaction_id))


@app.post("/transactions/search")
def search(data: SearchInput, auth: Auth, db: DB):
    own_account(db, auth, data.account_id, "transactions:read")
    rows = db.scalars(
        select(Transaction)
        .where(
            Transaction.account_id == data.account_id,
            Transaction.description.icontains(data.query, autoescape=True),
        )
        .order_by(Transaction.date.desc())
        .limit(data.limit)
    )
    return {"transactions": [op.transaction_view(t) for t in rows]}


@app.post("/accounts/{account_id}/statements")
def statement(account_id: str, data: StatementInput, auth: Auth, db: DB):
    result = op.statement(db, auth, account_id, data.start_date, data.end_date)
    db.commit()
    return result


@app.get("/statements/{reference}/download")
def download(reference: str, auth: Auth, db: DB):
    s = db.get(Statement, reference)
    if not s or s.customer_id != auth.customer_id or s.expires_at <= now():
        raise BankError("DOWNLOAD_EXPIRED", "This statement is unavailable or expired.", 404)
    own_account(db, auth, s.account_id, "statement:create")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Description", "Amount INR", "Reference"])
    rows = db.scalars(
        select(Transaction)
        .where(
            Transaction.account_id == s.account_id,
            Transaction.date >= s.start_date,
            Transaction.date <= s.end_date,
        )
        .order_by(Transaction.date.desc())
    )
    for t in rows:
        description = t.description
        if description.startswith(("=", "+", "-", "@")):
            description = "'" + description
        writer.writerow([t.date, description, f"{t.amount_paise / 100:.2f}", "****" + t.id[-4:]])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="statement.csv"', "Cache-Control": "no-store"},
    )


@app.post("/service-requests/checkbook/prepare")
def prepare_checkbook(data: PrepareInput, auth: Auth, db: DB):
    result = op.prepare(db, auth, "checkbook", data)
    db.commit()
    return result


@app.post("/service-requests/credit-limit/prepare")
def prepare_limit(data: PrepareInput, auth: Auth, db: DB):
    result = op.prepare(db, auth, "credit_limit", data)
    db.commit()
    return result


@app.post("/transactions/{transaction_id}/report/prepare")
def prepare_report(transaction_id: str, data: PrepareInput, auth: Auth, db: DB):
    data.transaction_id = transaction_id
    result = op.prepare(db, auth, "suspicious_transaction", data)
    db.commit()
    return result


@app.post("/actions/{action_id}/confirm")
def confirm(action_id: str, auth: Auth, db: DB):
    result = op.confirm(db, auth, action_id)
    db.commit()
    return result


@app.post("/actions/{action_id}/cancel")
def cancel(action_id: str, auth: Auth, db: DB):
    result = op.cancel(db, auth, action_id)
    db.commit()
    return result


@app.post("/auth/request-otp")
def request_otp(data: ActionInput, auth: Auth, db: DB):
    action = op.owned_action(db, auth, data.action_id)
    if action.status != "confirmed":
        raise BankError("CONFIRMATION_REQUIRED", "Confirm the proposal first.", 409)
    return {"status": "already_issued", "message": "Use the code issued at confirmation; it cannot be reset."}


@app.post("/auth/verify-otp")
def verify(data: OTPInput, auth: Auth, db: DB):
    result = op.verify_otp(db, auth, data.action_id, data.otp)
    db.commit()
    return result


@app.post("/service-requests/checkbook/submit")
def submit_checkbook(data: SubmitInput, auth: Auth, db: DB):
    result = op.submit(db, auth, data.action_id, data.idempotency_key, "checkbook")
    db.commit()
    return result


@app.post("/service-requests/credit-limit/submit")
def submit_limit(data: SubmitInput, auth: Auth, db: DB):
    result = op.submit(db, auth, data.action_id, data.idempotency_key, "credit_limit")
    db.commit()
    return result


@app.post("/transactions/{transaction_id}/report/submit")
def submit_report(transaction_id: str, data: SubmitInput, auth: Auth, db: DB):
    action = op.owned_action(db, auth, data.action_id)
    if action.payload.get("transaction_id") != transaction_id:
        raise BankError("ACTION_MISMATCH", "The transaction does not match the proposal.", 409)
    result = op.submit(db, auth, data.action_id, data.idempotency_key, "suspicious_transaction")
    db.commit()
    return result


@app.get("/service-requests/{request_id}")
def status(request_id: str, auth: Auth, db: DB):
    require(auth, "service_request:read")
    r = db.scalar(
        select(ServiceRequest).where(
            ServiceRequest.reference == request_id, ServiceRequest.customer_id == auth.customer_id
        )
    )
    if not r:
        raise BankError("RESOURCE_NOT_FOUND", "This service request is not available.", 404)
    own_account(db, auth, r.account_id, "service_request:read")
    return {"reference": r.reference, "status": r.status, "kind": r.kind}


@app.put("/customers/me/preferences")
def preferences(data: PreferenceInput, auth: Auth, db: DB):
    require(auth, "accounts:read")
    customer = db.get(Customer, auth.customer_id)
    assert customer is not None
    customer.preferences = data.model_dump(exclude={"approved"})
    db.commit()
    return customer.preferences


@app.get("/knowledge/search")
def knowledge_search(auth: Auth, q: str = "", query: str = ""):
    require(auth, "knowledge:read")
    from knowledge_base.ingestion.store import search

    return {"sources": search((query or q)[:500])}


@app.get("/knowledge/policies/{policy_id}")
def policy(policy_id: str, auth: Auth):
    require(auth, "knowledge:read")
    import json

    from knowledge_base.ingestion.store import ROOT

    if policy_id not in {"accounts", "checkbook", "credit", "disputes", "fees", "products", "faq", "privacy"}:
        raise BankError("RESOURCE_NOT_FOUND", "This policy is not available.", 404)
    return json.loads((ROOT / "documents" / f"{policy_id}.json").read_text())


@app.get("/knowledge/products")
def products(auth: Auth):
    require(auth, "knowledge:read")
    return {"products": ["Synthetic Savings", "Synthetic Current", "Synthetic Credit Card"]}
