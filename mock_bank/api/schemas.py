from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RefreshInput(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=256)


class PrepareInput(StrictModel):
    account_id: str = Field(min_length=1, max_length=64)
    requested_limit: int | None = Field(default=None, ge=1000, le=500000)
    transaction_id: str | None = Field(default=None, max_length=64)


class ActionInput(StrictModel):
    action_id: str = Field(min_length=1, max_length=64)


class SubmitInput(ActionInput):
    idempotency_key: str = Field(min_length=16, max_length=128)


class OTPInput(ActionInput):
    otp: str = Field(pattern=r"^\d{6}$")


class SearchInput(StrictModel):
    account_id: str
    query: str = Field(default="", max_length=100)
    limit: int = Field(default=20, ge=1, le=100)


class StatementInput(StrictModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ChatInput(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    account_id: str | None = Field(default=None, max_length=64)


class PreferenceInput(StrictModel):
    preferred_language: Literal["English", "Hindi"] = "English"
    communication_preference: Literal["email", "app"] = "app"
    approved: Literal[True]
