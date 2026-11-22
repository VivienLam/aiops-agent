"""Is the demo at rest? Checked before each run, and by reset.py to know when to stop waiting.

Two parts: take_snapshot() reads Docker and Prometheus, judge() decides.
judge() only works on plain data, so it is tested without the demo.
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker

from bench.flags import flags_file_modified
from server import COMPOSE_PROJECT, container_counters, prom_by_service, prom_scalar

EXPECTED_CONTAINERS = 28   # demo + demo/compose.local.yaml, counted on 2026-11-21
# Prometheus gets span metrics about once a minute, and payment sees ~3 calls/min:
# a 2 min window sometimes showed 0 errors while paymentFailure 50% was on.
ERROR_WINDOW = "5m"
METRICS_DELAY_SECONDS = 90  # an error takes up to ~1-2 min to show up in Prometheus
# After a reset, the whole error window must be after the reset (plus the delay).
SETTLE_SECONDS = 5 * 60 + METRICS_DELAY_SECONDS
MAX_ERROR_PERCENT = 0.0
# Long-lived gRPC streams (services subscribed to flag changes) sometimes close with an
# error status, fault or not: ~3 per 30 min at rest. Not a fault, so not counted.
IGNORED_ERROR_SPANS = ["flagd.evaluation.v2.Service/EventStream"]
P95_WINDOW = "5m"          # checkout gets ~3 orders/min: a shorter window gives too few samples
MAX_CHECKOUT_P95_MS = 250  # ~90 ms at rest, 12 s when it lacked memory
SAMPLE_SECONDS = 10        # disk reads and memory-limit hits are counted over this window
MAX_DISK_READ_MIB_S = 5.0  # <1 MiB/s at rest, 121 MiB/s when checkout lacked memory
DISK_REF_FACTOR = 2        # services that read more at rest (grafana, ~5-6 MiB/s) get 2x their max

# Memory, compared with what each service uses at rest (scripts/measure_idle_baseline.py).
BASELINE_FILE = Path(__file__).resolve().parent / "idle_baseline.json"
MEMORY_MARGIN_FRACTION = 0.10  # of the limit
MEMORY_MARGIN_MIN_MB = 20
SWAP_MARGIN_MB = 20
LIMIT_HITS_FACTOR = 3          # services already at their limit at rest (kafka, opensearch...)
LIMIT_HITS_EXTRA_PER_MIN = 30


@dataclass
class Snapshot:
    # name, state, health, oom_killed, and when running: memory_used_mb, memory_limit_mb,
    # swap_mb, limit_hits_per_min (None when the cgroup files are not readable)
    containers: list[dict]
    error_percent: dict[str, float]             # service -> % of server spans in error
    checkout_p95_ms: float | None
    disk_read_mib_s: dict[str, float] = field(default_factory=dict)
    flags_file_modified: bool = False


def memory_checks(c: dict, ref: dict | None) -> tuple[list[str], list[str]]:
    """Memory of one running container against its reference at rest.

    Returns (reasons, warnings). Memory used and swap are only warnings: they drift on a
    long bench, and what a fault leaves behind is cleared by reset.py restarting the
    service. Hitting the limit much more often than at rest still blocks.
    """
    name = c["name"]
    if ref is None:
        return [], [f"container {name} has no memory reference at rest"]
    reasons, warnings = [], []
    limit = c.get("memory_limit_mb") or ref["memory_limit_mb"]
    margin = max(MEMORY_MARGIN_FRACTION * limit, MEMORY_MARGIN_MIN_MB)
    used = c.get("memory_used_mb")
    if ref["memory_max_mb"] + margin < limit:
        threshold = ref["memory_max_mb"] + margin
        if used is not None and used > threshold:
            warnings.append(f"container {name} uses {used:.0f} MB "
                            f"(max {threshold:.0f}, {ref['memory_max_mb']:.0f} at rest)")
    else:
        # Already near its limit at rest: memory can't grow, watch how often it hits the limit.
        hits = c.get("limit_hits_per_min")
        threshold = LIMIT_HITS_FACTOR * ref["limit_hits_per_min_max"] + LIMIT_HITS_EXTRA_PER_MIN
        if hits is not None and hits > threshold:
            reasons.append(f"container {name} hits its memory limit {hits:.0f} times/min "
                           f"(max {threshold:.0f})")
    swap = c.get("swap_mb")
    if swap is not None and swap > ref["swap_max_mb"] + SWAP_MARGIN_MB:
        warnings.append(f"container {name} has {swap:.0f} MB in swap "
                        f"(max {ref['swap_max_mb'] + SWAP_MARGIN_MB:.0f})")
    return reasons, warnings


def disk_threshold(ref: dict | None) -> float:
    """Fixed 5 MiB/s, or twice what the service reads at rest when that is higher (grafana)."""
    if ref is None or "disk_read_mib_s_max" not in ref:
        return MAX_DISK_READ_MIB_S
    return max(MAX_DISK_READ_MIB_S, DISK_REF_FACTOR * ref["disk_read_mib_s_max"])


def judge(snap: Snapshot, baseline: dict | None = None) -> tuple[list[str], list[str]]:
    """(reasons, warnings). reasons: why the demo is NOT at rest, empty = at rest.
    warnings: shown and kept with the run, they do not block (memory used, swap).

    baseline: the "services" part of idle_baseline.json, None to skip the memory checks.
    """
    reasons, warnings = [], []
    if snap.flags_file_modified:
        reasons.append("flag file modified (a fault may still be on): run scripts/reset.py")

    if len(snap.containers) != EXPECTED_CONTAINERS:
        reasons.append(f"{len(snap.containers)} containers instead of {EXPECTED_CONTAINERS}")
    for c in snap.containers:
        if c["state"] != "running":
            reasons.append(f"container {c['name']} is {c['state']}")
        elif c.get("health") not in (None, "healthy"):
            reasons.append(f"container {c['name']} health is {c['health']}")
        if c.get("oom_killed"):
            reasons.append(f"container {c['name']} was killed for lack of memory")
        if baseline is not None and c["state"] == "running":
            r, w = memory_checks(c, baseline.get(c["name"]))
            reasons += r
            warnings += w

    for service, pct in sorted(snap.error_percent.items()):
        if pct > MAX_ERROR_PERCENT:
            reasons.append(f"{service}: {pct:.1f}% errors over {ERROR_WINDOW}")

    p95 = snap.checkout_p95_ms
    if p95 is None:
        reasons.append(f"no checkout request over {P95_WINDOW}")
    elif p95 > MAX_CHECKOUT_P95_MS:
        reasons.append(f"checkout p95 is {p95:.0f} ms (max {MAX_CHECKOUT_P95_MS})")

    for name, rate in sorted(snap.disk_read_mib_s.items()):
        threshold = disk_threshold((baseline or {}).get(name))
        if rate > threshold:
            reasons.append(f"container {name} reads {rate:.1f} MiB/s from disk (max {threshold:.1f})")
    return reasons, warnings


def container_samples(seconds: float = SAMPLE_SECONDS) -> tuple[list[dict], dict[str, float]]:
    """State of every demo container, with rates measured over `seconds`.

    Returns (containers, disk read MiB/s per container).
    """
    client = docker.from_env()
    try:
        everything = client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={COMPOSE_PROJECT}"})
        running = [c for c in everything if c.status == "running"]
        before = {c.name: container_counters(c) for c in running}
        start = time.monotonic()
        time.sleep(seconds)
        after = {c.name: container_counters(c) for c in running}
        elapsed = time.monotonic() - start

        containers, disk = [], {}
        for c in everything:
            state = c.attrs["State"]
            info = {
                "name": c.name,
                "state": state["Status"],
                "health": state.get("Health", {}).get("Status"),
                "oom_killed": state.get("OOMKilled", False),
                "restarts": c.attrs.get("RestartCount", 0),
            }
            if c.name in before and c.name in after:
                b, a = before[c.name], after[c.name]
                disk[c.name] = (a["read_bytes"] - b["read_bytes"]) / elapsed / 2**20
                limit = c.attrs["HostConfig"].get("Memory") or 0
                info["memory_limit_mb"] = limit / 2**20 if limit else None
                if a["memory_used"] is not None:
                    info["memory_used_mb"] = a["memory_used"] / 2**20
                if a["cgroup"] and b["cgroup"]:
                    info["swap_mb"] = a["cgroup"]["swap_bytes"] / 2**20
                    info["limit_hits"] = a["cgroup"]["limit_hits"]
                    info["limit_hits_per_min"] = (
                        (a["cgroup"]["limit_hits"] - b["cgroup"]["limit_hits"]) / elapsed * 60)
            containers.append(info)
    finally:
        client.close()
    return containers, disk


def take_snapshot() -> Snapshot:
    containers, disk = container_samples()

    sel = 'span_kind="SPAN_KIND_SERVER"'
    ignored = "|".join(IGNORED_ERROR_SPANS)
    calls = prom_by_service(
        f"sum by (service_name) (rate(traces_span_metrics_calls_total{{{sel}}}[{ERROR_WINDOW}]))")
    errors = prom_by_service(
        f"sum by (service_name) (rate(traces_span_metrics_calls_total"
        f'{{{sel},status_code="STATUS_CODE_ERROR",span_name!~"{ignored}"}}[{ERROR_WINDOW}]))')
    error_percent = {s: 100 * e / calls[s] for s, e in errors.items() if calls.get(s)}

    p95 = prom_scalar(
        "histogram_quantile(0.95, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket"
        f'{{service_name="checkout",{sel}}}[{P95_WINDOW}])))')

    return Snapshot(containers, error_percent, p95, disk, flags_file_modified())


def load_baseline() -> dict | None:
    if not BASELINE_FILE.is_file():
        return None
    return json.loads(BASELINE_FILE.read_text())["services"]


_warned = False


def check_idle() -> tuple[list[str], list[str], Snapshot]:
    """(reasons, warnings, snapshot). The bench keeps the warnings in the run's log."""
    snap = take_snapshot()
    baseline = load_baseline()
    reasons, warnings = judge(snap, baseline)
    global _warned
    if baseline is None and not _warned:
        # Only before the first measurement; the file is then kept in the repo.
        _warned = True
        print(f"warning: no memory reference at rest ({BASELINE_FILE.name}), memory not checked. "
              "Run scripts/measure_idle_baseline.py", file=sys.stderr)
    return reasons, warnings, snap


def print_warnings(warnings: list[str]) -> None:
    if warnings:
        print("Warnings (not blocking):")
        for w in warnings:
            print(f"  - {w}")


def summary(snap: Snapshot) -> str:
    worst_disk = max(snap.disk_read_mib_s.values(), default=0.0)
    p95 = "none" if snap.checkout_p95_ms is None else f"{snap.checkout_p95_ms:.0f} ms"
    worst_err = max(snap.error_percent.values(), default=0.0)
    return (f"{len(snap.containers)} containers, max errors {worst_err:.1f}%, "
            f"checkout p95 {p95}, max disk read {worst_disk:.1f} MiB/s")
