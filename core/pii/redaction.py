"""Local deterministic PII minimization. No mappings or raw text are persisted."""

import re

PATTERNS = [
    (r"(?i)\b(?:bearer\s+)[\w.\-]+", "[ACCESS_TOKEN]"),
    (r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", "[ACCESS_TOKEN]"),
    (r"\bsk-[\w-]+", "[API_KEY]"),
    (r"(?i)\b(?:password|cvv|otp|pin)\s*(?:is|:|=)?\s*\S+", "[SECRET]"),
    (r"(?i)\bCUST[-_]?\d+\b", "[CUSTOMER_ID]"),
    (r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL]"),
    (r"(?i)\b[A-Z]{5}\d{4}[A-Z]\b", "[GOVERNMENT_ID]"),
    (r"(?<!\w)(?:\+?\d[ -]?){10,19}(?!\w)", "[NUMBER]"),
    (r"\b\d{6}\b", "[SENSITIVE_NUMBER]"),
    (r"(?i)\b(?:TXN|UTR|REF)[-_]?[A-Z0-9]{6,}\b", "[TRANSACTION_REFERENCE]"),
]


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def mask(last_four: str) -> str:
    return "•••• " + last_four[-4:]
