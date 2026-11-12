"""Bridge between our MCP server and LangChain.

Starts server.py over stdio with the MCP SDK v2 client, lists its tools and
wraps each one as a LangChain tool. Names, descriptions and JSON schemas are
passed through unchanged. Tool output is returned as raw text: no filtering
(see DECISIONS.md, tools have no injection defense for now).
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from langchain_core.tools import StructuredTool
from mcp import Client, StdioServerParameters

SERVER = Path(__file__).resolve().parent.parent / "server.py"


def result_text(result) -> str:
    """Join the text parts of a CallToolResult."""
    parts = [c.text for c in result.content if getattr(c, "text", None) is not None]
    return "\n".join(parts) if parts else "(empty result)"


def to_langchain_tool(client: Client, tool) -> StructuredTool:
    async def call(**arguments) -> str:
        try:
            result = await client.call_tool(tool.name, arguments)
        except Exception as e:  # keep the agent running, let the LLM see the error
            return f"Tool error: {type(e).__name__}: {e}"
        text = result_text(result)
        return f"Tool error: {text}" if result.is_error else text

    return StructuredTool(
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.input_schema,
        coroutine=call,
    )


@asynccontextmanager
async def mcp_tools():
    """Start the MCP server and yield its tools as LangChain tools."""
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with Client(params) as client:
        listing = await client.list_tools()
        yield [to_langchain_tool(client, t) for t in listing.tools]
