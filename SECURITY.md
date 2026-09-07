# Security model and limitations

## Intended use

This repository runs synthetic data only. Built-in usernames, a public demo password and the fixed **654321** development code are intentionally public fixtures. They are not real banking credentials. The development OAuth2 password flow must be replaced by an audited OIDC provider and an independent step-up delivery service before any real deployment. When development OTP mode is disabled, step-up fails closed; there is no fake production delivery integration.

## Enforced controls

- Argon2 password hashes; JWT HS256 signatures with issuer, audience, expiration and required claims.
- Database-backed session revocation and atomic refresh rotation. Customer identity comes from the validated user record. Employees do not inherit access to customer accounts.
- Resource ownership at API entry, agent permissions, MCP permissions and core operations. No customer identifier from chat can replace the authenticated identity.
- Strict Pydantic request models reject additional fields and invalid action amounts. Output models validate account masks, transaction types, currencies and statuses.
- Confirmation and step-up are not MCP tools. State-changing submission requires an owned, unexpired, verified proposal and an idempotency key. Conditional transitions and database uniqueness prevent repeated execution.
- OTP digests use keyed HMAC; failed attempts persist even when returning an error. A challenge cannot be reset by requesting another OTP. Expired/locked actions must be cancelled and prepared again.
- Account/card storage uses synthetic last-four values, avoiding complete numbers entirely. Regex-based local PII redaction runs before checkpoint/model input and on customer text output. No token mappings are created.
- API bodies are bounded, login and chat calls are rate-limited, and common prompt-injection/identity-override attempts are rejected. Error responses do not include internal exceptions or request-validation inputs.
- UI bearer tokens are encrypted in an expiring server-side store. Browser authentication uses an opaque HttpOnly, SameSite cookie. Gradio state and chat history never contain authentication tokens. OTP input is separate and cleared after submission.
- Statements require authenticated ownership on every download and expire after five minutes. CSV formula prefixes are escaped. Files are generated in memory.
- `.env`, local encryption keys, database files and virtual environments are excluded from Git and Docker build contexts. Containers run as a non-root user; MCP containers have read-only filesystems.

## Development and operational limits

Regex PII detection and injection heuristics are defense-in-depth, not complete protection against all languages, encodings or adversarial prompts. Safety also relies on deterministic permissions and ownership, which the model cannot override. Do not claim that the synthetic test corpus proves universal prompt-injection immunity.

The demo is not certified for financial use, has no real fraud adjudication, and does not transfer money. Credit-limit and dispute results acknowledge a submitted review request; they never promise approval or refund. Real production deployment needs mTLS/service identity, Cognito/OIDC, managed step-up, TLS ingress, a security review, dependency remediation, backup/restore testing, threat modeling, privacy review and load tests.

SQLite and the in-memory TTL fallback support one process only. Use PostgreSQL/Redis and a shared, managed encryption key for multiple replicas. The UI encryption key is stored in `.local/ui-key`; the Compose volume preserves it for the single backend. Use KMS envelope encryption and controlled key rotation before scaling across hosts.

The backend's local `.env` can contain a provider key, but mock mode does not use it. No database credentials or financial tool results are sent to a model. Do not enable third-party prompt tracing; use the provided metadata-only telemetry instead.

Refresh-token reuse fails closed but does not currently revoke every session in a user's token family. Customer-approved preferences are limited to enumerated language/channel choices. Session histories expire after one hour and should be purged hourly. Audit retention must be defined by deployment policy; audit records do not contain raw messages, passwords, OTPs or tokens.

## Security checks

Run `make check` and `python scripts/secret_scan.py`. CI runs authorization, confirmation, PII and idempotency regressions before container checks. The deterministic tests include foreign accounts, transactions, statements, service requests, invalid JWT claims, expired sessions, OTP lockout, injection attempts, model/tool failure and concurrent submission.

Please report security findings privately to the repository owner. Include a synthetic reproduction and affected code path, without credentials or customer data.
