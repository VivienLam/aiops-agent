"""Is the demo at rest? Checked before each run, and by reset.py to know when to stop waiting.

Two parts: take_snapshot() reads Docker and Prometheus, judge() decides.
judge() only works on plain data, so it is tested without the demo.
"""

import time
from dataclasses import dataclass, field

import docker

from bench.flags import flags_file_modified
from server import COMPOSE_PROJECT, prom_by_service, prom_scalar

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
DISK_SAMPLE_SECONDS = 10
MAX_DISK_READ_MIB_S = 5.0  # <1 MiB/s at rest, 121 MiB/s when checkout lacked memory


@dataclass
class Snapshot:
    containers: list[dict]                      # name, state, health, oom_killed
    error_percent: dict[str, float]             # service -> % of server spans in error
    checkout_p95_ms: float | None
    disk_read_mib_s: dict[str, float] = field(default_factory=dict)
    flags_file_modified: bool = False


def judge(snap: Snapshot) -> list[str]:
    """Reasons why the demo is NOT at rest. Empty list = at rest."""
    reasons = []
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

    for service, pct in sorted(snap.error_percent.items()):
        if pct > MAX_ERROR_PERCENT:
            reasons.append(f"{service}: {pct:.1f}% errors over {ERROR_WINDOW}")

    p95 = snap.checkout_p95_ms
    if p95 is None:
        reasons.append(f"no checkout request over {P95_WINDOW}")
    elif p95 > MAX_CHECKOUT_P95_MS:
        reasons.append(f"checkout p95 is {p95:.0f} ms (max {MAX_CHECKOUT_P95_MS})")

    for name, rate in sorted(snap.disk_read_mib_s.items()):
        if rate > MAX_DISK_READ_MIB_S:
            reasons.append(f"container {name} reads {rate:.1f} MiB/s from disk")
    return reasons


def disk_read_bytes(container) -> int:
    stats = container.stats(stream=False, one_shot=True)
    rows = stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []
    return sum(r["value"] for r in rows if r["op"].lower() == "read")


def take_snapshot() -> Snapshot:
    client = docker.from_env()
    try:
        everything = client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={COMPOSE_PROJECT}"})
        containers = []
        for c in everything:
            state = c.attrs["State"]
            containers.append({
                "name": c.name,
                "state": state["Status"],
                "health": state.get("Health", {}).get("Status"),
                "oom_killed": state.get("OOMKilled", False),
            })

        # Disk reads: two samples of the cumulative counter, DISK_SAMPLE_SECONDS apart.
        running = [c for c in everything if c.status == "running"]
        before = {c.name: disk_read_bytes(c) for c in running}
        start = time.monotonic()
        time.sleep(DISK_SAMPLE_SECONDS)
        after = {c.name: disk_read_bytes(c) for c in running}
        elapsed = time.monotonic() - start
    finally:
        client.close()
    disk = {n: (after[n] - before[n]) / elapsed / 2**20 for n in before if n in after}

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


def check_idle() -> tuple[list[str], Snapshot]:
    snap = take_snapshot()
    return judge(snap), snap


def summary(snap: Snapshot) -> str:
    worst_disk = max(snap.disk_read_mib_s.values(), default=0.0)
    p95 = "none" if snap.checkout_p95_ms is None else f"{snap.checkout_p95_ms:.0f} ms"
    worst_err = max(snap.error_percent.values(), default=0.0)
    return (f"{len(snap.containers)} containers, max errors {worst_err:.1f}%, "
            f"checkout p95 {p95}, max disk read {worst_disk:.1f} MiB/s")
