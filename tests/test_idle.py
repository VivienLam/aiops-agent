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


def test_all_reasons_are_listed():
    snap = at_rest(error_percent={"payment": 50.0}, checkout_p95_ms=500.0, flags_file_modified=True)
    assert len(judge(snap)) == 3
