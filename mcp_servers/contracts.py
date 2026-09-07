"""Shared public tool contracts. No credentials or customer identity in arguments."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from core.errors import SafeError
from mock_bank.api.schemas import StrictModel


class Empty(StrictModel):
    pass


class AccountInput(StrictModel):
    account_id: str = Field(min_length=1, max_length=64)


class RecentInput(AccountInput):
    limit: int = Field(default=5, ge=1, le=100)


class SearchInput(RecentInput):
    query: str = Field(max_length=100)


class TransactionInput(StrictModel):
    transaction_id: str = Field(min_length=1, max_length=64)


class StatementInput(AccountInput):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class CreditInput(AccountInput):
    requested_limit: int = Field(ge=1000, le=500000)


class ReportInput(AccountInput, TransactionInput):
    pass


class SubmitInput(StrictModel):
    action_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=16, max_length=128)


class ReportSubmit(SubmitInput, TransactionInput):
    pass


class ReferenceInput(StrictModel):
    request_id: str = Field(pattern=r"^SR-[A-Z0-9]{12}$")


class PolicySearch(StrictModel):
    query: str = Field(min_length=1, max_length=500)


class PolicyInput(StrictModel):
    policy_id: str = Field(pattern=r"^[a-z_]+$")


class ToolResult(StrictModel):
    ok: bool
    data: dict = Field(default_factory=dict)
    error: SafeError | None = None


@dataclass(frozen=True)
class ToolSpec:
    agent: str
    permission: str
    method: str
    path: str
    schema: type[StrictModel]
    description: str

    @property
    def write(self) -> bool:
        return self.method == "POST"


TOOLS = {
    "list_customer_accounts": ToolSpec(
        "accounts",
        "accounts:read",
        "GET",
        "/customers/me/accounts",
        Empty,
        "List accounts owned by the authenticated customer; returns only masked numbers.",
    ),
    "get_account_details": ToolSpec(
        "accounts",
        "accounts:read",
        "GET",
        "/accounts/{account_id}",
        AccountInput,
        "Read details of an owned account.",
    ),
    "get_account_balance": ToolSpec(
        "accounts",
        "balance:read",
        "GET",
        "/accounts/{account_id}/balance",
        AccountInput,
        "Read current and available balance in integer paise from an owned account.",
    ),
    "get_recent_transactions": ToolSpec(
        "transactions",
        "transactions:read",
        "GET",
        "/accounts/{account_id}/transactions",
        RecentInput,
        "Read up to 100 recent transactions from an owned account.",
    ),
    "search_transactions": ToolSpec(
        "transactions",
        "transactions:read",
        "POST",
        "/transactions/search",
        SearchInput,
        "Search transaction descriptions within an owned account.",
    ),
    "get_transaction_details": ToolSpec(
        "transactions",
        "transactions:read",
        "GET",
        "/transactions/{transaction_id}",
        TransactionInput,
        "Read an owned transaction; do not infer merchant intent or fraud.",
    ),
    "generate_account_statement": ToolSpec(
        "transactions",
        "statement:create",
        "POST",
        "/accounts/{account_id}/statements",
        StatementInput,
        "Create a five-minute statement download reference after ownership validation.",
    ),
    "prepare_suspicious_transaction_report": ToolSpec(
        "transactions",
        "transaction:report",
        "POST",
        "/transactions/{transaction_id}/report/prepare",
        ReportInput,
        "Prepare a suspicious transaction report for customer review; does not submit it.",
    ),
    "submit_suspicious_transaction_report": ToolSpec(
        "transactions",
        "transaction:report",
        "POST",
        "/transactions/{transaction_id}/report/submit",
        ReportSubmit,
        "Submit only a confirmed, OTP-verified proposal using its idempotency key.",
    ),
    "prepare_checkbook_request": ToolSpec(
        "services",
        "checkbook:create",
        "POST",
        "/service-requests/checkbook/prepare",
        AccountInput,
        "Prepare a checkbook proposal with the masked registered delivery address.",
    ),
    "submit_checkbook_request": ToolSpec(
        "services",
        "checkbook:create",
        "POST",
        "/service-requests/checkbook/submit",
        SubmitInput,
        "Submit a confirmed, OTP-verified checkbook proposal exactly once.",
    ),
    "prepare_credit_limit_request": ToolSpec(
        "services",
        "credit_limit:request",
        "POST",
        "/service-requests/credit-limit/prepare",
        CreditInput,
        "Prepare a credit-limit review proposal, not an approval.",
    ),
    "submit_credit_limit_request": ToolSpec(
        "services",
        "credit_limit:request",
        "POST",
        "/service-requests/credit-limit/submit",
        SubmitInput,
        "Submit a confirmed, OTP-verified credit-limit proposal exactly once.",
    ),
    "get_service_request_status": ToolSpec(
        "services",
        "service_request:read",
        "GET",
        "/service-requests/{request_id}",
        ReferenceInput,
        "Read status of a service reference owned by the authenticated customer.",
    ),
    "search_bank_policies": ToolSpec(
        "knowledge",
        "knowledge:read",
        "GET",
        "/knowledge/search",
        PolicySearch,
        "Search synthetic policies with citations; never answers live account questions.",
    ),
    "get_policy_details": ToolSpec(
        "knowledge",
        "knowledge:read",
        "GET",
        "/knowledge/policies/{policy_id}",
        PolicyInput,
        "Read a cited synthetic policy document.",
    ),
    "list_bank_products": ToolSpec(
        "knowledge", "knowledge:read", "GET", "/knowledge/products", Empty, "List synthetic banking products."
    ),
}


class AccountOutput(StrictModel):
    account_id: str
    masked_account: str = Field(pattern=r"^•••• \d{4}$")
    kind: Literal["savings", "current"]
    currency: Literal["INR"]


class BalanceOutput(AccountOutput):
    current_paise: int
    available_paise: int


class AccountsOutput(StrictModel):
    accounts: list[AccountOutput]


class TransactionOutput(StrictModel):
    transaction_id: str
    date: str
    description: str
    amount_paise: int
    type: Literal["credit", "debit"]
    masked_reference: str = Field(pattern=r"^••••[a-z0-9]{4}$")
    status: Literal["posted", "pending", "reversed"]
    currency: Literal["INR"]


class TransactionsOutput(StrictModel):
    transactions: list[TransactionOutput]


class ActionOutput(StrictModel):
    action_id: str
    kind: Literal["checkbook", "credit_limit", "suspicious_transaction"]
    summary: str
    status: Literal["prepared", "confirmed", "verified", "submitted", "cancelled"]
    expires_at: int


class StatusOutput(StrictModel):
    reference: str = Field(pattern=r"^SR-[A-Z0-9]{12}$")
    status: Literal["submitted", "in_review", "completed", "rejected"]
    kind: Literal["checkbook", "credit_limit", "suspicious_transaction"]


class StatementOutput(StrictModel):
    download_reference: str = Field(pattern=r"^[a-f0-9]{32}$")
    download_url: str = Field(pattern=r"^/statements/[a-f0-9]{32}/download$")
    expires_in: int


OUTPUTS: dict[str, type[StrictModel]] = {
    "list_customer_accounts": AccountsOutput,
    "get_account_details": AccountOutput,
    "get_account_balance": BalanceOutput,
    "get_recent_transactions": TransactionsOutput,
    "search_transactions": TransactionsOutput,
    "get_transaction_details": TransactionOutput,
    "generate_account_statement": StatementOutput,
    "get_service_request_status": StatusOutput,
}
for _name in TOOLS:
    if _name.startswith("prepare_"):
        OUTPUTS[_name] = ActionOutput
    if _name.startswith("submit_"):
        OUTPUTS[_name] = StatusOutput


def validate_output(name: str, data: dict) -> dict:
    schema = OUTPUTS.get(name)
    if schema:
        data = schema.model_validate(data).model_dump()
    return data
