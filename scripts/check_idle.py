"""Is the demo at rest? Exit code 0 if yes, 1 with the reasons if not.

Usage: uv run python scripts/check_idle.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.idle import check_idle, print_warnings, summary  # noqa: E402


def main() -> int:
    reasons, warnings, snap = check_idle()
    print(summary(snap))
    print_warnings(warnings)
    if reasons:
        print("NOT at rest:")
        for r in reasons:
            print(f"  - {r}")
        return 1
    print("At rest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
