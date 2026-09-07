# Evaluation

Run `python -m evaluations.evaluators.run` after installing development requirements. It writes `evaluations/reports/latest.json` and a JUnit runtime report. `make check` additionally executes the full pytest suite, Ruff and MyPy.

The versioned golden corpus has **104 synthetic scenarios**: 80 labeled routing/tool-selection cases, six injection cases, eight PII cases, and ten linked runtime regressions. It covers balance, recent transactions, combined requests, policies, ambiguous requests, sensitive proposals, reference follow-ups, failures, session expiry and duplicate submissions.

## Measured results

The generated JSON reports intent accuracy, tool-selection accuracy, PII leakage on the known test strings, runtime gate success, elapsed evaluation time and mock-model cost (zero). The JUnit report carries individual runtime assertions. These are reproducible regression measurements, not production accuracy claims.

Runtime tests exercise the LangGraph coordinator and MCP proxy against real FastAPI/SQLAlchemy behavior using an in-process HTTP test transport. `scripts/smoke.py` separately crosses the real HTTP/MCP boundaries of all five running services. It authenticates a synthetic customer, checks combined reads, submits a confirmed checkbook and retrieves cited policies.

## Required gates

- All cross-customer authorization tests pass.
- Submission without confirmation and valid OTP is rejected.
- Every tested account-specific answer comes from successful tool data.
- Known PII strings do not survive redaction; OTP is absent from chat history.
- Concurrent submissions create one service request.
- Model/tool failures do not invent balances or expose exceptions.

CI has no deployment step and makes the container job depend on these checks. Set branch protection to require the workflow before merging. Dependency audit and tracked-source secret scan are also blocking checks.

## Limitations and expansion

The corpus is deterministic and synthetic. It does not establish real-user task completion, semantic multilingual routing, production cost, or universal injection resistance. Provider adapters are available, but paid-provider and Ollama calls require separate credentials/model setup and were not part of mock-mode testing. The PostgreSQL/Redis Docker stack passed the HTTP/MCP smoke test. Chromium acceptance passed at desktop (1440px) and mobile (390px) widths, including login, combined reads, confirmed checkbook, cookie isolation, OTP clearing, and logout. Other browsers and load profiles still require validation.

For production evaluation, add independent human-labeled paraphrases, provider-specific routing/argument measurements, real load-test latency, human-escalation outcomes, and configured per-model prices. Keep all customer data out of evaluation datasets. Runtime metrics expose API/agent/MCP latency, failures, completions, tokens and configured cost estimates for deployment monitoring.

Use `python -m evaluations.evaluators.run --persist` after applying migrations to also store deterministic outcomes in the evaluation-results table.
