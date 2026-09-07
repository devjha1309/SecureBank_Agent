"""Provider-independent routing; models never authorize or create banking facts."""

import asyncio
import re
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.config.settings import get_settings
from core.errors import BankError
from core.observability.telemetry import COST, MODEL_LATENCY, TOKENS, record_model_usage, tracer
from core.pii.redaction import redact

Intent = Literal[
    "accounts",
    "balance",
    "transactions",
    "search",
    "details",
    "statement",
    "checkbook",
    "credit_limit",
    "report",
    "status",
    "knowledge",
    "unsupported",
]


class IntentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intents: list[Intent] = Field(max_length=4)
    confidence: float = Field(ge=0, le=1)


def deterministic_plan(message: str) -> IntentPlan:
    text = message.lower()
    intents: list[Intent] = []
    if any(
        word in text for word in ("transfer", "send money", "block card", "change address", "system prompt")
    ):
        return IntentPlan(intents=["unsupported"], confidence=1)
    if any(
        word in text
        for word in (
            "policy",
            "policies",
            "fees",
            "charges",
            "products",
            "how long",
            "privacy",
            "what is a",
            "what are",
        )
    ):
        return IntentPlan(intents=["knowledge"], confidence=1)
    if "balance" in text:
        intents.append("balance")
    if "list" in text and "account" in text:
        intents.append("accounts")
    if any(word in text for word in ("checkbook", "chequebook", "cheque book")):
        intents.append("checkbook")
    if "limit" in text and any(word in text for word in ("credit", "increase", "raise")):
        intents.append("credit_limit")
    if any(word in text for word in ("suspicious", "dispute", "report transaction")):
        intents.append("report")
    if "statement" in text:
        intents.append("statement")
    if "status" in text or re.search(r"\bsr-[a-z0-9]{12}\b", text):
        intents.append("status")
    if (
        any(word in text for word in ("why", "explain transaction", "transaction details", "money debited"))
        and "knowledge" not in intents
    ):
        intents.append("details")
    elif any(word in text for word in ("search", "find transactions")):
        intents.append("search")
    elif (
        any(word in text for word in ("transactions", "last five", "last 5", "recent payments"))
        and "report" not in intents
    ):
        intents.append("transactions")
    return IntentPlan(intents=intents or ["unsupported"], confidence=1 if intents else 0)


def build_model(provider: str, name: str):
    if provider in ("openai", "azure"):
        from langchain_openai import AzureChatOpenAI, ChatOpenAI

        return (
            ChatOpenAI(model=name, temperature=0, timeout=10, max_retries=0)
            if provider == "openai"
            else AzureChatOpenAI(azure_deployment=name, temperature=0, timeout=10, max_retries=0)
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=name, base_url=get_settings().ollama_base_url, temperature=0)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model_name=name, temperature=0, timeout=10, max_retries=0, stop=None)
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(model=name, temperature=0)
    raise BankError("MODEL_CONFIGURATION", "This model provider is not configured.", 503)


async def classify(message: str, sensitive: bool = False) -> IntentPlan:
    settings = get_settings()
    if settings.model_provider == "mock":
        record_model_usage([{"provider": "mock", "tokens": 0, "cost_microusd": 0, "latency_ms": 0}])
        return deterministic_plan(message)
    provider = settings.model_provider
    name = settings.local_model_name
    # Sensitive context always stays local; external routing is opt-in only.
    if sensitive or not settings.allow_external_llm:
        provider = settings.local_model_provider
    elif provider not in ("mock", "ollama"):
        name = settings.external_model_name
    try:
        started = monotonic()
        with (
            MODEL_LATENCY.labels(provider).time(),
            tracer.start_as_current_span("model.classify", record_exception=False) as span,
        ):
            span.set_attribute("model.provider", provider)
            async with asyncio.timeout(12):
                from pathlib import Path

                prompt = (Path(__file__).with_name("routing_prompt.txt")).read_text()
                model = build_model(provider, name)
                result = await model.with_structured_output(IntentPlan, include_raw=True).ainvoke(
                    [("system", prompt), ("human", redact(message))]
                )
                usage = getattr(result["raw"], "usage_metadata", None) or {}
                tokens = int(usage.get("total_tokens", 0))
                TOKENS.labels(provider).inc(tokens)
                estimate = -1
                if (
                    settings.model_input_usd_per_million is not None
                    and settings.model_output_usd_per_million is not None
                ):
                    estimate = int(
                        usage.get("input_tokens", 0) * settings.model_input_usd_per_million
                        + usage.get("output_tokens", 0) * settings.model_output_usd_per_million
                    )
                    COST.labels(provider).inc(estimate / 1_000_000)
                record_model_usage(
                    [
                        {
                            "provider": provider,
                            "tokens": tokens,
                            "cost_microusd": estimate,
                            "latency_ms": int((monotonic() - started) * 1000),
                        }
                    ]
                )
                return IntentPlan.model_validate(result["parsed"])
    except Exception:
        raise BankError(
            "MODEL_UNAVAILABLE",
            "I cannot understand this request reliably right now. Please try again or contact support.",
            503,
            True,
        ) from None
