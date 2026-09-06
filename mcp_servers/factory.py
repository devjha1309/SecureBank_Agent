"""Independent MCP services proxy only authenticated, allowlisted banking endpoints."""

import asyncio
from time import monotonic

import httpx
from mcp.server.mcpserver import Context, MCPServer
from starlette.responses import JSONResponse

from core.config.settings import get_settings
from core.errors import SafeError
from core.observability.telemetry import FAILURES, TOOLS
from mcp_servers.contracts import TOOLS as SPECS
from mcp_servers.contracts import ToolResult


async def execute_tool(
    name: str, arguments: dict, authorization: str, client: httpx.AsyncClient | None = None
) -> ToolResult:
    spec = SPECS[name]
    started = monotonic()

    async def execute(c):
        args = spec.schema.model_validate(arguments).model_dump()
        headers = {"Authorization": authorization}
        me = await c.get("/auth/me", headers=headers)
        if me.status_code != 200:
            return ToolResult(ok=False, error=SafeError.model_validate(me.json()))
        if spec.permission not in me.json()["permissions"]:
            return ToolResult(
                ok=False,
                error=SafeError(
                    error_code="FORBIDDEN", safe_message="This action is not permitted.", trace_id="mcp"
                ),
            )
        if "account_id" in args:
            owned = await c.get("/accounts/" + args["account_id"], headers=headers)
            if owned.status_code != 200:
                return ToolResult(ok=False, error=SafeError.model_validate(owned.json()))
        path = spec.path.format(**args)
        body = {k: v for k, v in args.items() if "{" + k + "}" not in spec.path}
        if name == "prepare_suspicious_transaction_report":
            body["account_id"] = args["account_id"]
        response = None
        for attempt in range(2 if spec.method == "GET" else 1):
            response = await c.request(
                spec.method,
                path,
                headers=headers,
                params=body if spec.method == "GET" else None,
                json=body if spec.method == "POST" else None,
            )
            if response.status_code < 500:
                break
            if attempt == 0 and spec.method == "GET":
                await asyncio.sleep(0.05)
        assert response is not None
        if response.status_code >= 400:
            return ToolResult(ok=False, error=SafeError.model_validate(response.json()))
        return ToolResult(ok=True, data=response.json())

    try:
        async with asyncio.timeout(12):
            if client:
                return await execute(client)
            async with httpx.AsyncClient(
                base_url=get_settings().banking_api_url, timeout=5, follow_redirects=False
            ) as c:
                return await execute(c)
    except Exception:
        FAILURES.labels(name).inc()
        return ToolResult(
            ok=False,
            error=SafeError(
                error_code="BANKING_API_UNAVAILABLE",
                safe_message="We cannot verify that information right now.",
                trace_id="mcp",
                retryable=True,
            ),
        )
    finally:
        TOOLS.labels(name).observe(monotonic() - started)


def create_server(group: str):
    server = MCPServer("SecureBank-" + group)
    for name, spec in SPECS.items():
        if spec.agent != group:
            continue

        def register(tool_name, tool_spec):
            async def handler(arguments, ctx: Context) -> ToolResult:
                request = ctx.request_context.request
                authorization = request.headers.get("authorization", "") if request else ""
                return await execute_tool(tool_name, arguments.model_dump(), authorization)

            handler.__name__ = tool_name
            handler.__annotations__["arguments"] = tool_spec.schema
            server.tool(name=tool_name, description=tool_spec.description)(handler)

        register(name, spec)

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok", "service": group})

    return server.streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0", max_request_body_size=16384
    )
