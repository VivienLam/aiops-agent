"""Measure what each demo service uses at rest (memory, swap, memory-limit hits).

Writes bench/idle_baseline.json, used by the idle check as a safety net.
Run it on a demo that has been up for a while (ad's JVM keeps growing for ~1 h), e.g.:
    nohup uv run python scripts/measure_idle_baseline.py --wait 3600 > baseline.log 2>&1 &
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.flags import DEMO_DIR  # noqa: E402
from bench.idle import BASELINE_FILE, container_samples, judge, take_snapshot  # noqa: E402

RATE_WINDOW_SECONDS = 300  # limit-hit rate is taken over 5 min windows, the worst one is kept


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def rest_check() -> list[str]:
    """Idle check without the memory part (that's what we are measuring)."""
    return judge(take_snapshot(), baseline=None)


def summarize(samples: list[tuple[float, list[dict]]], interval: int) -> dict:
    per_service: dict[str, list[tuple[float, dict]]] = {}
    for t, containers in samples:
        for c in containers:
            if c["state"] == "running":
                per_service.setdefault(c["name"], []).append((t, c))

    services = {}
    step = max(1, RATE_WINDOW_SECONDS // interval)
    for name, rows in sorted(per_service.items()):
        used = [c["memory_used_mb"] for _, c in rows if c.get("memory_used_mb") is not None]
        swap = [c["swap_mb"] for _, c in rows if c.get("swap_mb") is not None]
        hits = [(t, c["limit_hits"]) for t, c in rows if c.get("limit_hits") is not None]
        rates = [(h2 - h1) / (t2 - t1) * 60
                 for (t1, h1), (t2, h2) in zip(hits, hits[step:]) if t2 > t1]
        if len(hits) >= 2 and not rates:  # run shorter than one window
            rates = [(hits[-1][1] - hits[0][1]) / (hits[-1][0] - hits[0][0]) * 60]
        services[name] = {
            "memory_limit_mb": rows[-1][1].get("memory_limit_mb"),
            "memory_max_mb": round(max(used), 1) if used else None,
            "memory_median_mb": round(statistics.median(used), 1) if used else None,
            "swap_max_mb": round(max(swap), 1) if swap else 0.0,
            "limit_hits_per_min_max": round(max(rates), 1) if rates else 0.0,
            "limit_hits_per_min_mean": round(statistics.mean(rates), 1) if rates else 0.0,
            "restarts_during_measure": rows[-1][1]["restarts"] - rows[0][1]["restarts"],
        }
    return services


def demo_version() -> str:
    out = subprocess.run(["git", "-C", str(DEMO_DIR), "describe", "--tags", "--always"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=0, help="seconds to wait before measuring")
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--output", type=Path, default=BASELINE_FILE)
    args = parser.parse_args()

    if args.wait:
        log(f"waiting {args.wait} s before measuring")
        time.sleep(args.wait)

    start_reasons = rest_check()
    log(f"rest check at start: {start_reasons or 'at rest'}")
    if start_reasons:
        log("NOT at rest, baseline not written")
        return 1

    samples = []
    start = time.monotonic()
    while time.monotonic() - start < args.duration:
        t0 = time.monotonic()
        containers, _ = container_samples(seconds=1)
        samples.append((time.monotonic(), containers))
        if len(samples) % 10 == 0:
            log(f"{len(samples)} samples")
        time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))

    end_reasons = rest_check()
    log(f"rest check at end: {end_reasons or 'at rest'}")
    services = summarize(samples, args.interval)
    restarted = [n for n, s in services.items() if s["restarts_during_measure"]]
    if end_reasons or restarted:
        log(f"NOT at rest at the end (restarted: {restarted}), baseline not written")
        return 1

    result = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "demo_version": demo_version(),
        "duration_seconds": args.duration,
        "interval_seconds": args.interval,
        "samples": len(samples),
        "services": services,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    log(f"written {args.output} ({len(services)} services)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
