# API guide

Interactive OpenAPI: `/docs`. All banking and chat endpoints require `Authorization: Bearer <access token>`. The UI handles this through an encrypted server-side session. Only `/auth/login`, `/auth/refresh`, health, and metrics endpoints omit bearer authentication. Never put a token in a URL.

## Authentication

`POST /auth/login` accepts OAuth2 form fields `username` and `password`. Demo users are `demo01`–`demo12`, password `SyntheticDemo!42`. It returns a 15-minute access token and an opaque refresh token. `/auth/refresh` accepts JSON `refresh_token` and rotates it atomically. `/auth/logout` revokes the login session. `/auth/me` returns masked profile data and server-derived permissions.

## Banking reads

| Method | Endpoint | Input |
|---|---|---|
| GET | `/customers/me/accounts` | None |
| GET | `/accounts/{account_id}` | Owned account reference |
| GET | `/accounts/{account_id}/balance` | Owned account reference |
| GET | `/accounts/{account_id}/transactions` | `limit` 1–100, default 5 |
| GET | `/transactions/{transaction_id}` | Owned transaction reference |
| POST | `/transactions/search` | `account_id`, `query`, `limit` |
| POST | `/accounts/{account_id}/statements` | ISO `start_date`, `end_date`, at most 366 days |
| GET | `/statements/{reference}/download` | Owned, unexpired reference; returns CSV |
| GET | `/service-requests/{request_id}` | Customer's `SR-...` reference |
| GET | `/knowledge/search` | `query` |
| GET | `/knowledge/policies/{policy_id}` | Policy ID from retrieval |
| GET | `/knowledge/products` | None |

Money is returned as integer paise with `currency=INR`. The UI formats INR. Opaque account/transaction IDs are API references and are not displayed in the transaction table.

## Sensitive workflows

1. Prepare through `/service-requests/checkbook/prepare`, `/service-requests/credit-limit/prepare`, or `/transactions/{transaction_id}/report/prepare`. JSON includes `account_id`; credit requests also require integer `requested_limit` in INR.
2. Display the returned proposal `summary` and masked registered address.
3. `POST /actions/{action_id}/confirm` is an explicit customer action. It creates the step-up challenge.
4. `/auth/request-otp` accepts `action_id` and reports an already-issued challenge; it does not reset attempts. `/auth/verify-otp` accepts `action_id` and `otp` through a separate component.
5. Submit using the matching `/submit` endpoint with `action_id` and a unique `idempotency_key` (16–128 characters). Reporting includes the original transaction in the URL.
6. `/actions/{action_id}/cancel` cancels a proposal before submission.

The public development OTP is `654321`. It expires in three minutes and locks after three wrong attempts. No production OTP delivery is implemented; disabling demo delivery blocks writes safely.

## Chat

`POST /chat` and `/chat/stream` accept:

```json
{"message":"Show my balance and last five transactions","session_id":null,"account_id":"owned-account-reference"}
```

If multiple accounts exist and no account is selected, the response asks for selection. Responses include sanitized text, the opaque conversation reference, safe agent labels, optional transactions, a pending proposal and an optional statement reference. `/chat/stream` uses SSE `progress`, `result` and `error` events.

- `GET /chat/{session_id}/history`: owned sanitized history.
- `POST /chat/{session_id}/confirm`: confirm the server-owned pending proposal.
- `POST /chat/{session_id}/verify-otp`: JSON `otp`; verify outside the model, resume the graph and submit once.
- `POST /chat/{session_id}/cancel`: cancel the proposal and resume the graph without submission.
- `DELETE /chat/{session_id}`: cancel pending work and delete chat/checkpoint memory.

`PUT /customers/me/preferences` accepts enumerated `preferred_language`, `communication_preference`, and `approved: true`. Preferences are never inferred from arbitrary chat.

## Errors

```json
{"error_code":"BANKING_API_UNAVAILABLE","safe_message":"We cannot verify that information right now.","trace_id":"generated-reference","retryable":true}
```

Foreign/missing resources both use 404. Invalid auth uses 401; missing permissions use 403; workflow conflicts use 409; malformed input uses 422; limits use 429; dependency failure uses 503. Do not automatically retry writes without their original idempotency key.
