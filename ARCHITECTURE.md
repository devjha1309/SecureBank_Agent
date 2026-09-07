# SecureBank architecture

SecureBank is a working **synthetic banking demonstration**, with production-style boundaries. Its development identity provider and public simulated OTP must not protect real accounts.

## Request path

1. Gradio collects input. Login uses a same-origin FastAPI endpoint that sets an opaque HttpOnly, SameSite cookie.
2. The UI backend keeps an encrypted access token in a short-lived server-side vault. Gradio state contains presentation state only.
3. FastAPI validates JWT signature, issuer, audience, expiration and the revocable login session. Roles and permissions are loaded from the database, never trusted from chat or token role claims.
4. Input guardrails reject identity overrides and common injection attempts. A local detector redacts secret/PII patterns before any message reaches checkpoints or models.
5. LangGraph classifies intents, checks permissions, selects an owned account, constructs typed tool arguments and dispatches to specialized agents. Independent reads use `asyncio.gather`.
6. Each specialist enforces its MCP allowlist. The MCP service validates the delegated authentication through the banking API, checks its permission, validates account ownership where present, and invokes the specific banking endpoint.
7. The banking endpoint checks permissions and ownership again. Integer paise avoid floating-point ledger storage. Response models check currency, amounts, masked identifiers and statuses.
8. Deterministic templates compose customer facts from successful tool results. Unavailable data produces a safe response with a trace reference.

## Agents and services

| Agent | Responsibility | MCP port |
|---|---|---|
| Accounts | Account list, details, balances | 8101 |
| Transactions | Recent/search/detail, statements, dispute proposals | 8102 |
| Services | Checkbook, credit-limit proposals, service status | 8103 |
| Knowledge | Synthetic policies and products with citations | 8104 |

The coordinator has no database queries. Authentication is carried in runtime context variables, not tool arguments, model prompts or graph state. MCP services have no database credentials in Compose.

## Write state machine

`prepared → confirmed → verified → submitted`

A proposal expires after ten minutes. Confirmation creates a three-minute, three-attempt challenge. Cancellation is allowed before submission. SQL conditional updates and a unique service-request action key enforce single execution, including concurrent requests. The idempotency key is bound to the proposal; reuse with another proposal or another key is rejected. The customer and login session own the proposal, so another session cannot approve it.

LangGraph interrupts **after preparation**. Confirmation and OTP verification occur through authenticated endpoints outside the LLM. Only after verified step-up does the API resume the graph. A failed submission pauses again so retrying uses the same action key. MCP never exposes a confirm or OTP tool.

## Persistence

PostgreSQL stores domain data and server-owned workflow state. Local development can use SQLite. Alembic owns domain schema migrations. The official LangGraph saver owns its checkpoint tables (SQLite locally; PostgreSQL in Compose). `agent_checkpoints` additionally stores minimal application audit snapshots.

Redis provides TTL session metadata, rate limits, encrypted UI token envelopes and distributed chat locks. The local in-memory fallback is deliberately single-process. Chat history is sanitized and bounded to six recent messages for routing context; a compact sanitized summary keeps the latest response. Approved language and channel preferences use a strict dedicated API. No OTP, password or provider key enters conversation memory.

Run `python scripts/retention.py` on an hourly scheduler. It removes expired chat records, both checkpoint stores, and statement references. Statement CSVs are generated in memory on authenticated download; no downloadable disk files survive expiry.

## Models and knowledge

`MODEL_PROVIDER=mock` is deterministic and costs nothing. Adapters are included for Ollama, OpenAI, Azure OpenAI, Anthropic and Bedrock. When external processing is disabled or a message contained detected PII, classification is forced to the local provider. External providers receive sanitized text and a versioned routing prompt only. They cannot return tool arguments, banking facts or permissions.

Provider calls have a timeout and zero configured retries where supported. Model token counts use returned usage metadata. Cost is estimated only when explicit input/output rates are configured; `-1` in a database usage row means unknown, not zero. Provider integrations require their own credentials and were not exercised against paid services.

Policy ingestion cleans paragraphs and creates normalized, deterministic 256-dimensional hashed token embeddings. A local JSON vector index uses cosine retrieval with a threshold and top-three citations. This lightweight lexical embedding implementation is intentional for offline demonstration; it is not a trained semantic embedding model. The index contains no banking records. Retrieved passages remain untrusted and are checked before display.

## Runtime dependencies

The API must be ready before MCPs start. API readiness checks PostgreSQL and Redis, not MCP availability, which avoids a startup dependency cycle. A failed MCP yields a safe error. Public ingress reaches the UI/API only. All databases, Redis and MCP services remain on the internal Docker network.

API details: [API.md](API.md). Security limits: [SECURITY.md](SECURITY.md). AWS design: [infrastructure/AWS.md](infrastructure/AWS.md).
