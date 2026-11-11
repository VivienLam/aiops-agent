"""The MCP server must expose every tool.

Catches a lost @mcp.tool() decorator: the function still exists and works when
called directly, but the agent never sees it.
"""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
EXPECTED_TOOLS = {
    "get_service_health",
    "list_services",
    "get_container_status",
    "search_logs",
    "find_traces",
    "get_trace",
}


async def exposed_tools() -> set[str]:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}


def test_all_tools_are_exposed():
    assert asyncio.run(exposed_tools()) == EXPECTED_TOOLS
