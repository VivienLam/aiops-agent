"""Turn every fault off, restart the service the scenario touched, then wait until the demo is at rest.

Usage: uv run python scripts/reset.py [scenario-id or path] [--timeout SECONDS]
Without a scenario, only the flags are restored (nothing is restarted).
The restart clears what a fault leaves behind once its flag is off
(memory kept by a JVM, swap). Exit code 0 once at rest, 1 otherwise.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.demo import restart_service  # noqa: E402
from bench.flags import load_flags, restore_flags  # noqa: E402
from bench.idle import SETTLE_SECONDS, check_idle, summary  # noqa: E402
from bench.scenarios import find_scenario, load_scenario, service_to_restart  # noqa: E402

POLL_SECONDS = 20


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    service = None
    if args.scenario:
        service = service_to_restart(load_scenario(find_scenario(args.scenario), load_flags()))

    restore_flags()
    print("Flag file restored, all flags back to their original value.")
    if service:
        restart_service(service)
        print(f"Restarted {service}.")

    # Errors from before the reset can still arrive, and the error window still covers
    # the time the fault was on: "at rest" only counts once the window is all after the reset.
    print(f"At rest no sooner than {SETTLE_SECONDS} s from now.")
    start = time.monotonic()
    while True:
        reasons, snap = check_idle()
        waited = time.monotonic() - start
        print(f"[{waited:4.0f} s] {summary(snap)}")
        if not reasons and waited >= SETTLE_SECONDS:
            print(f"At rest after {waited:.0f} s.")
            return 0
        if not reasons:
            print(f"  looks at rest, waiting until {SETTLE_SECONDS} s")
        if waited > args.timeout:
            print(f"Still NOT at rest after {args.timeout} s:")
            for r in reasons:
                print(f"  - {r}")
            return 1
        if reasons:
            print("  waiting: " + "; ".join(reasons))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
