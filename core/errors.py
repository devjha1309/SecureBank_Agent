from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class SafeError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    error_code: str
    safe_message: str
    trace_id: str
    retryable: bool = False


class BankError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        self.status = status
        self.payload = SafeError(
            error_code=code, safe_message=message, trace_id=uuid4().hex, retryable=retryable
        )
        super().__init__(code)
