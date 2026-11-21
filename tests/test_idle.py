"""Idle-state check: the decision part only, on made-up snapshots. No demo needed."""

from bench.idle import ERROR_WINDOW, EXPECTED_CONTAINERS, Snapshot, judge


def at_rest(**changes) -> Snapshot:
    snap = Snapshot(
        containers=[{"name": f"c{i}", "state": "running", "health": "healthy", "oom_killed": False}
                    for i in range(EXPECTED_CONTAINERS)],
        error_percent={"frontend": 0.0, "payment": 0.0},
        checkout_p95_ms=92.0,
        disk_read_mib_s={"c0": 0.5},
        flags_file_modified=False,
    )
    for key, value in changes.items():
        setattr(snap, key, value)
    return snap


def test_at_rest():
    assert judge(at_rest()) == []


def test_container_without_healthcheck_is_fine():
    snap = at_rest()
    snap.containers[0]["health"] = None
    assert judge(snap) == []


def test_stopped_or_unhealthy_container():
    snap = at_rest()
    snap.containers[0]["state"] = "exited"
    snap.containers[1]["health"] = "unhealthy"
    snap.containers[2]["health"] = "starting"
    reasons = judge(snap)
    assert len(reasons) == 3
    assert "c0 is exited" in reasons[0]


def test_missing_container():
    snap = at_rest()
    snap.containers.pop()
    assert "containers instead of" in judge(snap)[0]


def test_oom_killed():
    snap = at_rest()
    snap.containers[3]["oom_killed"] = True
    assert "lack of memory" in judge(snap)[0]


def test_any_error_refuses():
    reasons = judge(at_rest(error_percent={"payment": 48.2, "frontend": 0.0}))
    assert reasons == [f"payment: 48.2% errors over {ERROR_WINDOW}"]


def test_slow_checkout():
    assert "checkout p95" in judge(at_rest(checkout_p95_ms=12000.0))[0]


def test_no_checkout_traffic():
    assert "no checkout request" in judge(at_rest(checkout_p95_ms=None))[0]


def test_continuous_disk_reads():
    assert "121.0 MiB/s" in judge(at_rest(disk_read_mib_s={"checkout": 121.0}))[0]


def test_flag_file_modified():
    assert "reset.py" in judge(at_rest(flags_file_modified=True))[0]


BASELINE = {
    # normal service: threshold = 240 + 30 (10% of 300) = 270 MB
    "ad": {"memory_limit_mb": 300, "memory_max_mb": 240, "swap_max_mb": 0, "limit_hits_per_min_max": 0},
    # small service: margin is at least 20 MB -> 78 MB
    "email": {"memory_limit_mb": 100, "memory_max_mb": 58, "swap_max_mb": 0, "limit_hits_per_min_max": 0},
    # already at its limit at rest: hits/min checked instead, max 3*25+30 = 105
    "kafka": {"memory_limit_mb": 620, "memory_max_mb": 610, "swap_max_mb": 8, "limit_hits_per_min_max": 25},
}


def with_memory(**per_name) -> Snapshot:
    names = list(BASELINE) + [f"c{i}" for i in range(EXPECTED_CONTAINERS - len(BASELINE))]
    snap = at_rest()
    snap.containers = [{"name": n, "state": "running", "health": None, "oom_killed": False}
                       for n in names]
    defaults = {"ad": dict(memory_used_mb=230, memory_limit_mb=300, swap_mb=0, limit_hits_per_min=0),
                "email": dict(memory_used_mb=55, memory_limit_mb=100, swap_mb=0, limit_hits_per_min=0),
                "kafka": dict(memory_used_mb=615, memory_limit_mb=620, swap_mb=8, limit_hits_per_min=20)}
    for c in snap.containers:
        c.update(defaults.get(c["name"], {}))
        c.update(per_name.get(c["name"], {}))
    return snap


def baseline_for_all() -> dict:
    b = dict(BASELINE)
    for i in range(EXPECTED_CONTAINERS - len(BASELINE)):
        b[f"c{i}"] = {"memory_limit_mb": 100, "memory_max_mb": 10, "swap_max_mb": 0,
                      "limit_hits_per_min_max": 0}
    return b


def test_memory_at_rest():
    assert judge(with_memory(), baseline_for_all()) == []


def test_memory_skipped_without_baseline():
    assert judge(with_memory(ad={"memory_used_mb": 299})) == []


def test_memory_kept_after_a_fault():
    """ad after adManualGc: 294 MB, its reference is 240."""
    reasons = judge(with_memory(ad={"memory_used_mb": 294}), baseline_for_all())
    assert reasons == ["container ad uses 294 MB (max 270, 240 at rest)"]


def test_small_service_uses_min_margin():
    assert judge(with_memory(email={"memory_used_mb": 77}), baseline_for_all()) == []
    assert "email uses 91 MB" in judge(with_memory(email={"memory_used_mb": 91}), baseline_for_all())[0]


def test_service_at_limit_uses_hit_rate():
    # kafka at 619/620 MB is normal; hitting the limit 120 times/min is not
    assert judge(with_memory(kafka={"memory_used_mb": 619}), baseline_for_all()) == []
    reasons = judge(with_memory(kafka={"limit_hits_per_min": 120}), baseline_for_all())
    assert reasons == ["container kafka hits its memory limit 120 times/min (max 105)"]


def test_swap_left_after_a_fault():
    reasons = judge(with_memory(email={"swap_mb": 28}), baseline_for_all())
    assert reasons == ["container email has 28 MB in swap (max 20)"]


def test_unknown_container_and_unreadable_cgroup():
    b = baseline_for_all()
    del b["c0"]
    assert judge(with_memory(), b) == ["container c0 has no memory reference at rest"]
    # cgroup files not readable: swap and hit rate are None, no crash, nothing flagged
    snap = with_memory(kafka={"swap_mb": None, "limit_hits_per_min": None})
    assert judge(snap, baseline_for_all()) == []


def test_disk_threshold_uses_reference():
    b = baseline_for_all()
    b["c0"] = dict(b["c0"], disk_read_mib_s_max=6.0)   # like grafana at rest
    snap = with_memory()
    snap.disk_read_mib_s = {"c0": 9.0, "c1": 6.0}
    # c0 may read up to 12 MiB/s, c1 keeps the fixed 5 MiB/s
    assert judge(snap, b) == ["container c1 reads 6.0 MiB/s from disk (max 5.0)"]


def test_all_reasons_are_listed():
    snap = at_rest(error_percent={"payment": 50.0}, checkout_p95_ms=500.0, flags_file_modified=True)
    assert len(judge(snap)) == 3
