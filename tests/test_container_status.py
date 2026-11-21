"""get_container_status formatting, on a fake container. No Docker needed."""

from server import container_summary, problem_score

MB = 2**20


class FakeContainer:
    def __init__(self, status="running", memory_limit=100 * MB, oom=False, health=None):
        self.name = "email"
        self.labels = {"com.docker.compose.service": "email"}
        state = {"Status": status, "OOMKilled": oom, "ExitCode": 137}
        if health:
            state["Health"] = {"Status": health}
        self.attrs = {"State": state, "RestartCount": 0, "HostConfig": {"Memory": memory_limit}}


def counters(read_mb=0.0, used_mb=50, swap_mb=0, hits=0, ooms=0, cgroup=True):
    return {
        "read_bytes": int(read_mb * MB),
        "memory_used": used_mb * MB,
        "cgroup": {"swap_bytes": swap_mb * MB, "limit_hits": hits, "oom_kills": ooms} if cgroup else None,
    }


def test_quiet_container_shows_no_extra_field():
    info = container_summary(FakeContainer(), counters(), counters(read_mb=0.5), 5.0)
    assert info == {"service": "email", "state": "running", "restarts": 0,
                    "memory_used_mb": 50, "memory_limit_mb": 100, "memory_used_percent": 50.0}


def test_memory_pressure_fields():
    before = counters(hits=290, swap_mb=20)
    after = counters(used_mb=91, hits=300, swap_mb=28, read_mb=40, ooms=1)
    info = container_summary(FakeContainer(), before, after, 5.0)
    assert info["swap_mb"] == 28
    assert info["memory_limit_hits_last_5s"] == 10   # a rate, not the 300 since start
    assert info["disk_read_mib_per_s"] == 8.0
    assert info["oom_kills_since_start"] == 1


def test_unreadable_cgroup_files_are_skipped():
    info = container_summary(FakeContainer(), counters(cgroup=False), counters(cgroup=False), 5.0)
    assert "swap_mb" not in info and "memory_limit_hits_last_5s" not in info


def test_stopped_container():
    info = container_summary(FakeContainer(status="exited", oom=True))
    assert info["exit_code"] == 137 and info["oom_killed"] is True
    assert "memory_used_mb" not in info


def test_problems_first():
    quiet = container_summary(FakeContainer(), counters(), counters(used_mb=90), 5.0)
    hitting = container_summary(FakeContainer(), counters(hits=0), counters(used_mb=40, hits=5), 5.0)
    killed = container_summary(FakeContainer(), counters(), counters(ooms=2), 5.0)
    ordered = sorted([quiet, hitting, killed], key=problem_score)
    assert ordered == [killed, hitting, quiet]
