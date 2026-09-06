"""Authoritative operations; all actions are bound to customer AND login session."""

import hashlib
import hmac
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.auth.security import AuthContext, secret
from core.authorization.policy import own_account, require
from core.config.settings import get_settings
from core.errors import BankError
from core.observability.telemetry import SECURITY, audit
from core.pii.redaction import mask
from mock_bank.api.schemas import PrepareInput
from mock_bank.models.entities import (
    Account,
    Customer,
    CustomerAccount,
    OTPChallenge,
    PendingAction,
    ServiceRequest,
    Statement,
    Transaction,
    now,
)

ACTION_PERMISSIONS = {
    "checkbook": "checkbook:create",
    "credit_limit": "credit_limit:request",
    "suspicious_transaction": "transaction:report",
}


def account_view(account: Account) -> dict:
    return {
        "account_id": account.id,
        "masked_account": mask(account.last_four),
        "kind": account.kind,
        "currency": account.currency,
    }


def list_accounts(db: Session, auth: AuthContext) -> list[dict]:
    require(auth, "accounts:read")
    rows = db.scalars(
        select(Account).join(CustomerAccount).where(CustomerAccount.customer_id == auth.customer_id)
    )
    return [account_view(a) for a in rows]


def transaction_view(t: Transaction) -> dict:
    return {
        "transaction_id": t.id,
        "date": t.date,
        "description": t.description,
        "amount_paise": t.amount_paise,
        "type": "credit" if t.amount_paise > 0 else "debit",
        "masked_reference": "••••" + t.id[-4:],
        "status": t.status,
        "currency": "INR",
    }


def owned_transaction(
    db: Session, auth: AuthContext, transaction_id: str, permission="transactions:read"
) -> Transaction:
    t = db.get(Transaction, transaction_id)
    if not t:
        raise BankError("RESOURCE_NOT_FOUND", "The transaction is not available.", 404)
    own_account(db, auth, t.account_id, permission)
    return t


def prepare(db: Session, auth: AuthContext, kind: str, data: PrepareInput) -> dict:
    a = own_account(db, auth, data.account_id, ACTION_PERMISSIONS[kind])
    customer = db.get(Customer, auth.customer_id)
    assert customer is not None
    summary = f"Request a {kind.replace('_', ' ')} for account {mask(a.last_four)}."
    if kind == "checkbook":
        summary += f" Deliver to your registered address: {customer.masked_address}. Synthetic fee: ₹0."
    elif kind == "credit_limit":
        if data.requested_limit is None:
            raise BankError("MISSING_LIMIT", "Specify a requested limit between ₹1,000 and ₹500,000.")
        summary += f" Requested limit: ₹{data.requested_limit:,}. Subject to human review; approval is not guaranteed."
    else:
        if not data.transaction_id:
            raise BankError("MISSING_TRANSACTION", "Select a transaction to report.")
        t = owned_transaction(db, auth, data.transaction_id, "transaction:report")
        if t.account_id != a.id:
            raise BankError("INVALID_TRANSACTION", "Choose a transaction from the selected account.")
        summary += f" Transaction reference ending {t.id[-4:]} will be reviewed by support."
    action = PendingAction(
        customer_id=auth.customer_id,
        auth_session_id=auth.session_id,
        account_id=a.id,
        kind=kind,
        payload=data.model_dump(),
        summary=summary,
        expires_at=now() + 600,
    )
    db.add(action)
    db.flush()
    audit(db, auth, "prepare_" + kind, "prepared")
    return action_view(action)


def action_view(action: PendingAction) -> dict:
    return {
        "action_id": action.id,
        "kind": action.kind,
        "summary": action.summary,
        "status": action.status,
        "expires_at": action.expires_at,
    }


def owned_action(db: Session, auth: AuthContext, action_id: str) -> PendingAction:
    action = db.scalar(select(PendingAction).where(PendingAction.id == action_id).with_for_update())
    if not action or action.customer_id != auth.customer_id or action.auth_session_id != auth.session_id:
        raise BankError("RESOURCE_NOT_FOUND", "The proposed action is not available.", 404)
    own_account(db, auth, action.account_id, ACTION_PERMISSIONS[action.kind])
    if action.expires_at <= now() and action.status != "submitted":
        raise BankError("ACTION_EXPIRED", "This proposal expired. Please prepare a new request.", 409)
    return action


def otp_hash(action_id: str, code: str) -> str:
    return hmac.new(secret().encode(), f"{action_id}:{code}".encode(), hashlib.sha256).hexdigest()


def confirm(db: Session, auth: AuthContext, action_id: str) -> dict:
    action = owned_action(db, auth, action_id)
    if action.status != "prepared":
        raise BankError(
            "INVALID_ACTION_STATE", "This request is already confirmed, cancelled, or submitted.", 409
        )
    claimed = db.execute(
        update(PendingAction)
        .where(PendingAction.id == action.id, PendingAction.status == "prepared")
        .values(status="confirmed")
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise BankError("ACTION_CONFLICT", "The request has already changed.", 409)
    settings = get_settings()
    demo = settings.demo_otp and settings.environment == "development"
    if not demo:
        raise BankError("STEP_UP_UNAVAILABLE", "A verified step-up delivery provider is required.", 503)
    code = "654321"  # Public synthetic challenge, never a real OTP; development only.
    db.add(OTPChallenge(action_id=action.id, code_hash=otp_hash(action.id, code), expires_at=now() + 180))
    audit(db, auth, "confirm_" + action.kind, "confirmed")
    return {
        "status": "awaiting_otp",
        "message": "Development simulation only: use the public demo code 654321 in the separate OTP field.",
        "expires_in": 180,
        "attempts_remaining": 3,
    }


def verify_otp(db: Session, auth: AuthContext, action_id: str, code: str) -> dict:
    action = owned_action(db, auth, action_id)
    challenge = db.scalar(select(OTPChallenge).where(OTPChallenge.action_id == action.id).with_for_update())
    if action.status != "confirmed" or not challenge or challenge.verified:
        raise BankError("CONFIRMATION_REQUIRED", "Confirm this proposal before verifying the code.", 409)
    if challenge.expires_at <= now():
        raise BankError("OTP_EXPIRED", "The code expired. Cancel and prepare a new request.", 409)
    if challenge.attempts >= 3:
        raise BankError("OTP_LOCKED", "Too many attempts. Cancel and prepare a new request.", 429)
    claimed = db.execute(
        update(OTPChallenge)
        .where(OTPChallenge.id == challenge.id, OTPChallenge.attempts < 3, OTPChallenge.verified.is_(False))
        .values(attempts=OTPChallenge.attempts + 1)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise BankError("OTP_LOCKED", "Verification is locked.", 429)
    if not hmac.compare_digest(challenge.code_hash, otp_hash(action.id, code)):
        db.commit()  # Persist failed attempts even though the request returns an error.
        SECURITY.labels("otp").inc()
        raise BankError(
            "OTP_INVALID", f"Incorrect code. {max(0, 3 - challenge.attempts)} attempts remain.", 400
        )
    challenge.verified = True
    action.status = "verified"
    audit(db, auth, "verify_step_up", "verified")
    return {"status": "verified"}


def cancel(db: Session, auth: AuthContext, action_id: str) -> dict:
    action = owned_action(db, auth, action_id)
    if action.status == "submitted":
        raise BankError("ALREADY_SUBMITTED", "This request has already been submitted.", 409)
    action.status = "cancelled"
    audit(db, auth, "cancel_action", "cancelled")
    return {"status": "cancelled"}


def submit(db: Session, auth: AuthContext, action_id: str, key: str, kind: str) -> dict:
    action = owned_action(db, auth, action_id)
    if action.kind != kind:
        raise BankError("ACTION_MISMATCH", "The proposal does not match this operation.", 409)
    existing = db.scalar(select(ServiceRequest).where(ServiceRequest.action_id == action.id))
    if existing:
        if action.idempotency_key != key:
            raise BankError(
                "IDEMPOTENCY_CONFLICT", "This action was already submitted with another key.", 409
            )
        return {"reference": existing.reference, "status": existing.status, "kind": existing.kind}
    challenge = db.scalar(select(OTPChallenge).where(OTPChallenge.action_id == action.id))
    if (
        action.status != "verified"
        or not challenge
        or not challenge.verified
        or challenge.expires_at <= now()
    ):
        raise BankError("STEP_UP_REQUIRED", "Explicit confirmation and a valid OTP are required.", 409)
    if db.scalar(select(PendingAction.id).where(PendingAction.idempotency_key == key)):
        raise BankError("IDEMPOTENCY_CONFLICT", "Use a unique key for this proposal.", 409)
    claimed = db.execute(
        update(PendingAction)
        .where(PendingAction.id == action.id, PendingAction.status == "verified")
        .values(status="submitted", idempotency_key=key)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise BankError("ACTION_CONFLICT", "This action is already being processed.", 409)
    request = ServiceRequest(
        customer_id=auth.customer_id, account_id=action.account_id, kind=kind, action_id=action.id
    )
    db.add(request)
    db.flush()
    audit(db, auth, "submit_" + kind, "submitted")
    return {"reference": request.reference, "status": request.status, "kind": request.kind}


def statement(db: Session, auth: AuthContext, account_id: str, start: str, end: str) -> dict:
    own_account(db, auth, account_id, "statement:create")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or (last - first).days > 366:
            raise ValueError()
    except ValueError:
        raise BankError("INVALID_DATE_RANGE", "Use a valid date range of at most 366 days.") from None
    item = Statement(
        customer_id=auth.customer_id,
        account_id=account_id,
        start_date=start,
        end_date=end,
        expires_at=now() + 300,
    )
    db.add(item)
    db.flush()
    audit(db, auth, "generate_account_statement", "created")
    return {
        "download_reference": item.id,
        "download_url": f"/statements/{item.id}/download",
        "expires_in": 300,
    }
