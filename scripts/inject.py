"""Turn on the fault of a scenario, after checking the demo is at rest.

Usage: uv run python scripts/inject.py <scenario-id or path> [--no-wait]
Then waits the scenario's wait_seconds, so the fault shows up in the metrics.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.flags import evaluate_flag, load_flags, set_flag  # noqa: E402
from bench.idle import check_idle, summary  # noqa: E402
from bench.scenarios import find_scenario, load_scenario  # noqa: E402

FLAGD_RELOAD_TIMEOUT = 30


def wait_for_flag(flag: str, variant: str) -> bool:
    """flagd reloads the file by itself; check it really serves the new variant."""
    deadline = time.monotonic() + FLAGD_RELOAD_TIMEOUT
    while time.monotonic() < deadline:
        if evaluate_flag(flag) == variant:
            return True
        time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--no-wait", action="store_true", help="skip the wait after injection")
    args = parser.parse_args()

    scenario = load_scenario(find_scenario(args.scenario), load_flags())
    print(f"Scenario {scenario.id} ({scenario.lot}): {scenario.description}")

    reasons, snap = check_idle()
    print(f"Start state: {summary(snap)}")
    if reasons:
        print("Refused, the demo is NOT at rest:")
        for r in reasons:
            print(f"  - {r}")
        return 1

    inj = scenario.injection
    if inj.kind == "flag":
        set_flag(inj.flag, inj.variant)
        if not wait_for_flag(inj.flag, inj.variant):
            print(f"flagd still does not serve {inj.flag}={inj.variant} after "
                  f"{FLAGD_RELOAD_TIMEOUT} s. Run scripts/reset.py")
            return 1
        print(f"Injected: {inj.flag} = {inj.variant} (confirmed by flagd)")
    elif inj.kind == "docker":
        print("Docker injections are not implemented yet.")
        return 2
    else:
        print("Nothing to inject.")

    if not args.no_wait and scenario.wait_seconds:
        print(f"Waiting {scenario.wait_seconds} s for the fault to show up...")
        time.sleep(scenario.wait_seconds)
    print("Ready for the agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
