import httpx
import pytest
from pydantic import ValidationError

from mcp_servers.contracts import AccountInput
from mcp_servers.factory import execute_tool


async def test_mcp_auth_and_ownership(signed):
    c, _, a, _, aid, bid = signed
    from apps.banking_api.app import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bank") as http:
        good = await execute_tool("get_account_balance", {"account_id": aid}, a["Authorization"], http)
        assert good.ok and good.data["available_paise"] > 0
        bad = await execute_tool("get_account_balance", {"account_id": bid}, a["Authorization"], http)
        assert not bad.ok
        anonymous = await execute_tool("get_account_balance", {"account_id": aid}, "", http)
        assert not anonymous.ok


def test_strict_tool_input():
    with pytest.raises(ValidationError):
        AccountInput(account_id="abc", customer_id="CUST-002")
