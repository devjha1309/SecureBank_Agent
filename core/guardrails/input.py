import re

from core.errors import BankError

INJECTION = re.compile(
    r"ignore.{0,35}instructions|all customer|every customer|system prompt|database credentials|pretend.{0,20}admin|without authentication|use customer id|instead of my authenticated",
    re.I,
)


def validate_message(text: str) -> None:
    if not text.strip() or len(text) > 2000:
        raise BankError("INVALID_MESSAGE", "Enter a message between 1 and 2,000 characters.")
    if INJECTION.search(text) or re.search(r"\bCUST[-_]?\d+\b", text, re.I):
        raise BankError(
            "UNSAFE_REQUEST",
            "I can only help with your authenticated accounts and supported banking requests.",
            403,
        )
