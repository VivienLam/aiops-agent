"""The MCP bridge must expose every server tool as a LangChain tool."""
import asyncio

from agent.mcp_bridge import mcp_tools
from tests.test_tools_exposed import EXPECTED_TOOLS


async def bridge_check():
    async with mcp_tools() as tools:
        by_name = {t.name: t for t in tools}
        output = await by_name["list_services"].ainvoke({})
        return set(by_name), by_name["list_services"].args, output


def test_bridge_exposes_tools_and_returns_text():
    names, args, output = asyncio.run(bridge_check())
    assert names == EXPECTED_TOOLS
    assert "minutes" in args  # JSON schema passed through
    assert isinstance(output, str) and output.strip()
