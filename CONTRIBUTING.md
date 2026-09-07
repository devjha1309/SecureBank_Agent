# Contributing

Use Python 3.12 or newer, a virtual environment, and synthetic data only.

1. Install `requirements-dev.txt` and run `make setup`.
2. Make a focused change. Never add `.env`, tokens, database files or real banking information.
3. Add a regression test for security, workflow or banking behavior changes.
4. Run `make check` and `python scripts/secret_scan.py`.
5. Run `python agent.py` and `python scripts/smoke.py` for integration changes.
6. Submit a focused commit with the behavior, reason and validation described.

Use Alembic migrations for schema changes. Do not expose new tools without an input/output contract, permission, ownership policy and specialist allowlist. Sensitive tools need a server-owned preparation/confirmation/step-up state machine and idempotency.

Do not log raw prompts, credentials, OTPs, tokens or financial identifiers. Do not add direct database calls to agents or direct banking operations to UI callbacks. Keep factual responses grounded in validated MCP results.
