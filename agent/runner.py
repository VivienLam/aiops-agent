"""One agent run, shared by the command line and the web UI.

Streams the graph and yields an event for each tool call as soon as it is done,
then runs the after-the-fact checks on the report.
"""

from collections.abc import AsyncIterator

from agent.graph import RunLog, build_graph, initial_state
from agent.mcp_bridge import mcp_tools
from agent.report import check_evidence, check_root_cause

# LangGraph counts each node visit; 15 tool rounds + report fit easily.
RECURSION_LIMIT = 100


async def run_agent(question: str, llm, log: RunLog) -> AsyncIterator[dict]:
    """Yield {"type": "tool_call", ...} per tool call, then {"type": "done"}.

    Everything is recorded in `log`; the caller saves it.
    """
    async with mcp_tools() as tools:
        graph = build_graph(llm, tools, log)
        seen, last_message, report = 0, None, None
        async for update in graph.astream(initial_state(question),
                                          {"recursion_limit": RECURSION_LIMIT},
                                          stream_mode="updates"):
            for node, delta in update.items():
                if node == "llm":
                    last_message = delta["messages"][-1]
                elif node == "report":
                    report = delta["report"]
            calls = log.calls()
            for call in calls[seen:]:
                yield {"type": "tool_call", **call}
            seen = len(calls)

        log.answer = last_message.text if last_message else ""
        if report is not None:
            # Checked after the fact, never corrected: we measure what the model wrote.
            log.report = report.model_dump()
            services = await next(t for t in tools if t.name == "list_services").ainvoke(
                {"minutes": 60})
            log.checks = {
                "root_cause_service": check_root_cause(report, services),
                "evidence": check_evidence(report, log.calls()),
            }
    yield {"type": "done"}
