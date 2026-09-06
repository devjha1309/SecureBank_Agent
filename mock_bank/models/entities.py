"""Synthetic banking ledger and server-owned security state."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return uuid4().hex


def now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    first_name: Mapped[str]
    membership: Mapped[str]
    masked_address: Mapped[str]
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(primary_key=True)


class Permission(Base):
    __tablename__ = "permissions"
    name: Mapped[str] = mapped_column(primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(ForeignKey("roles.name"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role: Mapped[str] = mapped_column(ForeignKey("roles.name"), primary_key=True)
    permission: Mapped[str] = mapped_column(ForeignKey("permissions.name"), primary_key=True)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    last_four: Mapped[str]
    kind: Mapped[str]
    currency: Mapped[str] = mapped_column(default="INR")
    current_paise: Mapped[int] = mapped_column(Integer)
    available_paise: Mapped[int] = mapped_column(Integer)


class CustomerAccount(Base):
    __tablename__ = "customer_accounts"
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)


class Card(Base):
    __tablename__ = "cards"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    last_four: Mapped[str]
    limit_paise: Mapped[int]


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[str]
    description: Mapped[str]
    amount_paise: Mapped[int]
    status: Mapped[str] = mapped_column(default="posted")


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="submitted")
    reference: Mapped[str] = mapped_column(unique=True, default=lambda: "SR-" + uid()[:12].upper())
    action_id: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[int] = mapped_column(default=now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    refresh_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[int]
    revoked: Mapped[bool] = mapped_column(default=False)


class PendingAction(Base):
    __tablename__ = "pending_actions"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    customer_id: Mapped[str]
    auth_session_id: Mapped[str]
    account_id: Mapped[str]
    kind: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str]
    status: Mapped[str] = mapped_column(default="prepared")
    expires_at: Mapped[int]
    idempotency_key: Mapped[str | None] = mapped_column(nullable=True, unique=True)


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    action_id: Mapped[str] = mapped_column(ForeignKey("pending_actions.id"), unique=True)
    code_hash: Mapped[str]
    expires_at: Mapped[int]
    attempts: Mapped[int] = mapped_column(default=0)
    verified: Mapped[bool] = mapped_column(default=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    customer_id: Mapped[str]
    auth_session_id: Mapped[str]
    selected_account_id: Mapped[str | None]
    pending_action_id: Mapped[str | None]
    summary: Mapped[str] = mapped_column(default="")
    expires_at: Mapped[int]


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str]
    content: Mapped[str]
    created_at: Mapped[int] = mapped_column(default=now)


class Statement(Base):
    __tablename__ = "statements"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    customer_id: Mapped[str]
    account_id: Mapped[str]
    expires_at: Mapped[int]
    start_date: Mapped[str]
    end_date: Mapped[str]


class ToolAuditLog(Base):
    __tablename__ = "tool_audit_logs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    trace_id: Mapped[str]
    customer_hash: Mapped[str]
    tool: Mapped[str]
    status: Mapped[str]
    latency_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[int] = mapped_column(default=now)


class SecurityAuditLog(Base):
    __tablename__ = "security_audit_logs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    trace_id: Mapped[str]
    customer_hash: Mapped[str]
    event: Mapped[str]
    created_at: Mapped[int] = mapped_column(default=now)


class ModelUsage(Base):
    __tablename__ = "model_usage"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    provider: Mapped[str]
    tokens: Mapped[int]
    cost_microusd: Mapped[int]
    latency_ms: Mapped[int]


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    scenario: Mapped[str]
    passed: Mapped[bool]
    metrics: Mapped[dict] = mapped_column(JSON)


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (UniqueConstraint("session_id", "version"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    session_id: Mapped[str]
    version: Mapped[int]
    state: Mapped[dict] = mapped_column(JSON)
