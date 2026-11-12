"""Run the agent: uv run python -m agent "<question>"."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.graph import DailyQuotaExhausted, RunLog, build_graph, initial_state, load_model
from agent.mcp_bridge import mcp_tools

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# LangGraph counts each node visit; 15 tool rounds + final answer fit easily.
RECURSION_LIMIT = 100


async def main(question: str) -> None:
    model_name, llm = load_model()
    log = RunLog(question, model_name)
    try:
        async with mcp_tools() as tools:
            graph = build_graph(llm, tools, log)
            final = await graph.ainvoke(initial_state(question),
                                        {"recursion_limit": RECURSION_LIMIT})
        log.answer = final["messages"][-1].text
        print("\n=== Answer ===\n" + log.answer)
    except* DailyQuotaExhausted as group:
        log.error = f"daily quota exhausted: {group.exceptions[0]}"
        print(f"\nStopped: daily quota of {model_name} is used up. "
              "Try again tomorrow or switch AGENT_MODEL.", file=sys.stderr)
    finally:
        path = log.save()
        t = log.totals()
        print(f"\n=== Totals === llm calls={t['llm_calls']} tool calls={t['tool_calls']} "
              f"in={t['input_tokens']} out={t['output_tokens']} "
              f"(reasoning={t['reasoning_tokens']}) "
              f"{t['duration_s']}s (waited {t['waited_s']}s, active {t['active_s']}s)")
        print(f"Run log: {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit('usage: python -m agent "<question>"')
    asyncio.run(main(sys.argv[1]))
