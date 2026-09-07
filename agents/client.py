"""MCP transport. Authentication is runtime-only, never graph state or tool arguments."""

import asyncio
from contextvars import ContextVar

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from core.config.settings import get_settings
from core.errors import BankError
from mcp_servers.contracts import TOOLS, ToolResult, validate_output

bearer: ContextVar[str] = ContextVar("banking_bearer", default="")
permissions: ContextVar[frozenset[str]] = ContextVar("banking_permissions", default=frozenset())


async def call_tool(agent: str, name: str, args: dict) -> ToolResult:
    spec = TOOLS.get(name)
    if not spec or spec.agent != agent or spec.permission not in permissions.get():
        raise BankError("FORBIDDEN", "This agent cannot perform the requested action.", 403)
    validated = spec.schema.model_validate(args)
    url = getattr(get_settings(), agent + "_mcp_url")
    try:
        async with asyncio.timeout(20):
            async with httpx2.AsyncClient(headers={"Authorization": bearer.get()}, timeout=15) as http:
                async with streamable_http_client(url, http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(name, {"arguments": validated.model_dump()})
                        if result.is_error:
                            raise ValueError("tool error")
                        if not result.content or result.content[0].type != "text":
                            raise ValueError("invalid result content")
                        validated_result = ToolResult.model_validate_json(result.content[0].text)
                        if validated_result.ok:
                            validated_result.data = validate_output(name, validated_result.data)
                        return validated_result
    except Exception:
        raise BankError(
            "MCP_UNAVAILABLE", "We cannot verify that information right now.", 503, True
        ) from None
