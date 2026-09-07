"""LangGraph coordinator: no database access, only specialized MCP agents."""

import asyncio
import re
from contextlib import AsyncExitStack
from datetime import date, timedelta
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents import client
from agents.accounts import agent as accounts_agent
from agents.knowledge import agent as knowledge_agent
from agents.services import agent as services_agent
from agents.transactions import agent as transactions_agent
from core.config.settings import get_settings
from core.errors import BankError
from core.guardrails.input import validate_message
from core.model_router.router import classify
from core.observability.telemetry import AGENTS, COMPLETED, model_usage
from core.pii.redaction import redact
from mcp_servers.contracts import TOOLS

SPECIALISTS = {
    "accounts": accounts_agent,
    "transactions": transactions_agent,
    "services": services_agent,
    "knowledge": knowledge_agent,
}


class BankingAgentState(TypedDict, total=False):
    messages: list[dict]
    session_id: str
    authenticated_customer_id: str
    permissions: list[str]
    selected_account_id: str | None
    detected_intents: list[str]
    execution_plan: list[dict]
    selected_agents: list[str]
    agent_results: dict
    pending_action: dict | None
    confirmation_status: str | None
    otp_verification_status: str | None
    final_response: str | None
    errors: list[dict]
    trace_id: str
    sensitive: bool
    summary: str


INTENT_TOOL = {
    "accounts": "list_customer_accounts",
    "balance": "get_account_balance",
    "transactions": "get_recent_transactions",
    "search": "search_transactions",
    "details": "get_transaction_details",
    "statement": "generate_account_statement",
    "checkbook": "prepare_checkbook_request",
    "credit_limit": "prepare_credit_limit_request",
    "report": "prepare_suspicious_transaction_report",
    "status": "get_service_request_status",
    "knowledge": "search_bank_policies",
}


async def validate_request(state):
    # Input was validated before checkpointing by the API. This validates sanitized input again.
    validate_message(state["messages"][-1]["content"])
    return {
        "errors": [],
        "pending_action": None,
        "agent_results": {},
        "final_response": None,
        "confirmation_status": None,
        "otp_verification_status": None,
    }


async def authenticated_context(state):
    if not client.bearer.get():
        raise BankError("AUTH_REQUIRED", "Please sign in again.", 401)
    return {"permissions": sorted(client.permissions.get())}


async def pii_node(state):
    return {
        "messages": [{"role": m["role"], "content": redact(m["content"])} for m in state["messages"][-8:]]
    }


async def intents_node(state):
    plan = await classify(state["messages"][-1]["content"], state.get("sensitive", False))
    if plan.confidence < 0.6 or "unsupported" in plan.intents:
        return {
            "detected_intents": ["unsupported"],
            "final_response": "I can help with balances, transactions, statements, checkbooks, credit-limit requests, and bank policies. Please clarify your request or contact a human support specialist.",
        }
    return {"detected_intents": plan.intents}


async def permission_node(state):
    for intent in state["detected_intents"]:
        tool = INTENT_TOOL.get(intent)
        if tool and TOOLS[tool].permission not in client.permissions.get():
            raise BankError("FORBIDDEN", "Your role cannot perform this request.", 403)
    return {}


async def plan_node(state):
    if state.get("final_response"):
        return {}
    intents = state["detected_intents"]
    writes = [i for i in intents if i in ("checkbook", "credit_limit", "report")]
    if len(writes) > 1:
        return {
            "final_response": "Please request one sensitive action at a time so you can review it separately."
        }
    account = state.get("selected_account_id")
    if any(i not in ("knowledge", "status", "accounts") for i in intents) and not account:
        result = await client.call_tool("accounts", "list_customer_accounts", {})
        if not result.ok:
            return {
                "errors": [(result.error.model_dump() if result.error else {"error_code": "INVALID_RESULT"})],
                "final_response": "I cannot retrieve your accounts right now.",
            }
        accounts = result.data["accounts"]
        if len(accounts) != 1:
            return {
                "agent_results": {"accounts": result.data},
                "final_response": "Please select one of your accounts using the account selector.",
            }
        account = accounts[0]["account_id"]
    text = state["messages"][-1]["content"]
    plan = []
    for intent in intents:
        tool = INTENT_TOOL[intent]
        args = {"account_id": account} if intent not in ("knowledge", "status", "accounts") else {}
        if intent == "knowledge":
            args = {"query": text}
        if intent == "transactions":
            args["limit"] = 5
        if intent == "search":
            match = re.search(r"(?:for|matching|containing)\s+(.+)", text, re.I)
            if not match:
                return {
                    "final_response": "What transaction description should I search for? Try: search transactions for Grocery."
                }
            args.update(query=match.group(1), limit=20)
        if intent in ("details", "report"):
            # Resolve a displayed transaction position against owned, freshly retrieved transactions.
            recent = await client.call_tool(
                "transactions", "get_recent_transactions", {"account_id": account, "limit": 5}
            )
            if not recent.ok:
                return {
                    "final_response": "I cannot verify your transactions right now.",
                    "errors": [
                        (recent.error.model_dump() if recent.error else {"error_code": "INVALID_RESULT"})
                    ],
                }
            match = re.search(r"transaction\s+(\d)\b", text, re.I)
            if not match and not any(
                word in text.lower() for word in ("latest", "last transaction", "most recent")
            ):
                return {
                    "final_response": "Which transaction do you mean? Show your recent transactions, then specify transaction 1 to 5."
                }
            index = int(match.group(1)) - 1 if match else 0
            rows = recent.data["transactions"]
            if index < 0 or index >= len(rows):
                return {"final_response": "Choose transaction 1 to 5 from the recent transaction list."}
            args["transaction_id"] = rows[index]["transaction_id"]
            if intent == "details":
                args.pop("account_id")
        if intent == "statement":
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
            args.update(
                start_date=dates[0] if len(dates) == 2 else (date.today() - timedelta(days=30)).isoformat(),
                end_date=dates[1] if len(dates) == 2 else date.today().isoformat(),
            )
        if intent == "credit_limit":
            match = re.search(r"(?:to|of|₹|inr)\s*([\d,]+)", text, re.I)
            if not match:
                return {
                    "final_response": "What credit limit would you like to request? For example: increase my credit limit to 50,000."
                }
            args["requested_limit"] = int(match.group(1).replace(",", ""))
        if intent == "status":
            match = re.search(r"SR-[A-Z0-9]{12}", text, re.I)
            if not match:
                match = re.search(r"SR-[A-Z0-9]{12}", state.get("summary", ""), re.I)
            if not match:
                return {"final_response": "Please provide the service-request reference, starting with SR-."}
            args = {"request_id": match.group(0).upper()}
        plan.append({"intent": intent, "tool": tool, "agent": TOOLS[tool].agent, "args": args})
    return {
        "execution_plan": plan,
        "selected_account_id": account,
        "selected_agents": sorted({p["agent"] for p in plan}),
    }


async def route_node(state):
    return {}


async def execute_node(state):
    if state.get("final_response"):
        return {}

    async def run(item):
        try:
            result = await SPECIALISTS[item["agent"]].execute(item["tool"], item["args"])
            return item["intent"], result.model_dump()
        except BankError as exc:
            return item["intent"], {"ok": False, "data": {}, "error": exc.payload.model_dump()}

    reads = [p for p in state["execution_plan"] if not p["tool"].startswith("prepare_")]
    return {"agent_results": dict(await asyncio.gather(*(run(p) for p in reads)))}


async def validate_results(state):
    errors = [
        r["error"]
        for r in state.get("agent_results", {}).values()
        if isinstance(r, dict) and r.get("ok") is False
    ]
    return {"errors": errors}


async def prepare_node(state):
    if state.get("final_response"):
        return {}
    for item in state["execution_plan"]:
        if item["tool"].startswith("prepare_"):
            result = await SPECIALISTS[item["agent"]].execute(item["tool"], item["args"])
            if not result.ok:
                return {
                    "errors": [
                        (result.error.model_dump() if result.error else {"error_code": "INVALID_RESULT"})
                    ]
                }
            return {"pending_action": result.data, "confirmation_status": "pending"}
    return {}


async def pause_node(state):
    decision = interrupt(
        {
            "action": state["pending_action"],
            "message": "Review the proposal. Confirm and verify the separate OTP to continue.",
        }
    )
    if decision.get("cancel"):
        return {
            "confirmation_status": "cancelled",
            "pending_action": None,
            "final_response": "The proposal was cancelled.",
        }
    # This flag is supplied only by the authenticated API after server-side OTP verification.
    return {
        "confirmation_status": "confirmed",
        "otp_verification_status": "verified" if decision.get("verified") else "pending",
    }


async def verify_node(state):
    if state.get("confirmation_status") == "cancelled":
        return {}
    if state.get("otp_verification_status") != "verified":
        raise BankError("STEP_UP_REQUIRED", "Verify the OTP in the separate panel.", 409)
    return {}


async def submit_node(state):
    if state.get("confirmation_status") == "cancelled":
        return {}
    action = state["pending_action"]
    kind = action["kind"]
    names = {
        "checkbook": "submit_checkbook_request",
        "credit_limit": "submit_credit_limit_request",
        "suspicious_transaction": "submit_suspicious_transaction_report",
    }
    name = names[kind]
    args = {"action_id": action["action_id"], "idempotency_key": action["action_id"]}
    if kind == "suspicious_transaction":
        args["transaction_id"] = next(
            p["args"]["transaction_id"] for p in state["execution_plan"] if p["intent"] == "report"
        )
    result = await client.call_tool(TOOLS[name].agent, name, args)
    if not result.ok:
        return {"errors": [(result.error.model_dump() if result.error else {"error_code": "INVALID_RESULT"})]}
    return {
        "agent_results": {**state["agent_results"], "submission": result.model_dump()},
        "pending_action": None,
    }


def rupees(paise: int) -> str:
    if type(paise) is not int:
        raise BankError("INVALID_TOOL_RESULT", "We cannot verify that amount right now.", 503)
    return f"₹{paise / 100:,.2f}"


async def compose_node(state):
    if state.get("final_response"):
        return {}
    parts = []
    for intent, result in state.get("agent_results", {}).items():
        if not result.get("ok"):
            continue
        d = result["data"]
        if intent == "balance":
            if d.get("currency") != "INR":
                raise BankError("INVALID_TOOL_RESULT", "Unsupported currency.", 503)
            parts.append(
                f"Your available balance for account {d['masked_account']} is {rupees(d['available_paise'])}. Current balance: {rupees(d['current_paise'])}."
            )
        elif intent in ("transactions", "search"):
            parts.append("Recent transactions:" if intent == "transactions" else "Matching transactions:")
            parts.extend(
                f"{n}. {t['date']} · {t['description']} · {rupees(t['amount_paise'])} · {t['status']}"
                for n, t in enumerate(d["transactions"], 1)
            )
            if not d["transactions"]:
                parts.append("No transactions matched.")
        elif intent == "details":
            parts.append(
                f"{d['description']} on {d['date']}: {rupees(d['amount_paise'])}, {d['status']}. The ledger records this {d['type']}; it does not establish why the merchant initiated it. Report it if you do not recognize it."
            )
        elif intent == "accounts":
            parts.extend(f"{a['kind'].title()} account {a['masked_account']}" for a in d["accounts"])
        elif intent == "statement":
            parts.append(
                "Your statement is ready. Use the authenticated download button within five minutes."
            )
        elif intent in ("status", "submission"):
            parts.append(f"Service request {d['reference']}: {d['status']}.")
        elif intent == "knowledge":
            sources = d["sources"]
            if not sources:
                parts.append("I don't know based on the available bank policies. Please contact support.")
            for source in sources:
                # Retrieved text is data: validate before rendering, never execute it.
                validate_message(source["text"])
                parts.append(
                    f"{source['text']} [Source: {source['title']}, v{source['version']}, section {source['section']}]"
                )
    if state.get("errors"):
        parts.append(
            "I cannot verify part of this request right now. Please try again or contact support. Reference: "
            + state["trace_id"]
        )
    return {"final_response": redact("\n\n".join(parts)) or "Please clarify what you would like to do."}


async def audit_node(state):
    COMPLETED.labels("error" if state.get("errors") else "completed").inc()
    return {}


async def complete_node(state):
    return {}


def build_graph(checkpointer):
    g = StateGraph(BankingAgentState)
    nodes = {
        "validate_request": validate_request,
        "load_authenticated_context": authenticated_context,
        "redact_pii": pii_node,
        "detect_intents": intents_node,
        "check_permissions": permission_node,
        "create_plan": plan_node,
        "route_agents": route_node,
        "parallel_reads": execute_node,
        "validate_results": validate_results,
        "prepare_action": prepare_node,
        "await_confirmation": pause_node,
        "verify_step_up": verify_node,
        "execute_confirmed_action": submit_node,
        "compose_response": compose_node,
        "audit": audit_node,
        "complete": complete_node,
    }
    for name, fn in nodes.items():
        g.add_node(name, fn)
    chain = list(nodes)[:10]
    g.add_edge(START, chain[0])
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    g.add_conditional_edges(
        "prepare_action", lambda s: "await_confirmation" if s.get("pending_action") else "compose_response"
    )
    g.add_edge("await_confirmation", "verify_step_up")
    g.add_edge("verify_step_up", "execute_confirmed_action")
    g.add_conditional_edges(
        "execute_confirmed_action",
        lambda s: "await_confirmation" if s.get("pending_action") else "compose_response",
    )
    g.add_edge("compose_response", "audit")
    g.add_edge("audit", "complete")
    g.add_edge("complete", END)
    return g.compile(checkpointer=checkpointer)


class Coordinator:
    def __init__(self):
        self.stack = AsyncExitStack()
        self.graph = None
        self.init_lock = asyncio.Lock()

    async def start(self):
        async with self.init_lock:
            if self.graph is None:
                url = get_settings().checkpoint_url
                saver: Any
                if url.startswith("postgres"):
                    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                    saver = await self.stack.enter_async_context(AsyncPostgresSaver.from_conn_string(url))
                    await saver.setup()
                else:
                    saver = await self.stack.enter_async_context(AsyncSqliteSaver.from_conn_string(url))
                self.graph = build_graph(saver)

    async def run(self, state, session_id, resume=None):
        await self.start()
        config = {"configurable": {"thread_id": session_id}}
        assert self.graph is not None
        records: list = []
        usage_token = model_usage.set(records)
        try:
            with AGENTS.time():
                result = await self.graph.ainvoke(
                    Command(resume=resume) if resume is not None else state, config
                )
            result["model_usage"] = records
            return result
        finally:
            model_usage.reset(usage_token)

    async def close(self):
        await self.stack.aclose()
        self.graph = None


coordinator = Coordinator()
