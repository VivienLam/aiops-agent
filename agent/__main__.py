"""Run the agent: uv run python -m agent "<question>"."""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.graph import DailyQuotaExhausted, RunLog, load_model
from agent.runner import run_agent

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def main(question: str) -> None:
    model_name, llm = load_model()
    log = RunLog(question, model_name)
    try:
        async for _ in run_agent(question, llm, log):
            pass  # RunLog already prints each step live
        print("\n=== Answer ===\n" + log.answer)
        if log.report is not None:
            print("\n=== Report ===\n" + json.dumps(log.report, indent=2, ensure_ascii=False))
            print("\n=== Checks ===\n" + json.dumps(log.checks, indent=2))
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
