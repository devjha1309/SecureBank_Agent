"""Accounts agent with an enforced MCP tool allowlist."""

from agents.client import call_tool
from mcp_servers.contracts import TOOLS

ALLOWED_TOOLS = frozenset(name for name, spec in TOOLS.items() if spec.agent == "accounts")


async def execute(name: str, arguments: dict):
    if name not in ALLOWED_TOOLS:
        from core.errors import BankError

        raise BankError("FORBIDDEN", "This tool is not allowed for this agent.", 403)
    return await call_tool("accounts", name, arguments)
