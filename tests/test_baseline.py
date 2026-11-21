"""Summary of the at-rest measurement, on made-up samples. No demo needed."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "measure", Path(__file__).resolve().parent.parent / "scripts" / "measure_idle_baseline.py")
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def sample(t, used, swap, hits, restarts=0):
    return (t, [{"name": "kafka", "state": "running", "memory_limit_mb": 620.0,
                 "memory_used_mb": used, "swap_mb": swap, "limit_hits": hits, "restarts": restarts},
                {"name": "init", "state": "exited", "restarts": 0}])


def test_summary():
    # 30 s apart, 11 samples = 5 min. Hits: +10 per sample, then a burst of +100.
    hits = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 200]
    samples = [sample(30.0 * i, 600 + i, 8.0, h) for i, h in enumerate(hits)]
    s = measure.summarize(samples, interval=30)
    assert list(s) == ["kafka"]          # stopped containers are left out
    k = s["kafka"]
    assert k["memory_max_mb"] == 610 and k["memory_median_mb"] == 605
    assert k["swap_max_mb"] == 8.0
    assert k["limit_hits_per_min_max"] == 40.0   # 200 hits over the 5 min window
    assert k["restarts_during_measure"] == 0


def test_summary_short_run_and_restart():
    samples = [sample(0.0, 600, 0.0, 0), sample(60.0, 601, 0.0, 30, restarts=1)]
    k = measure.summarize(samples, interval=30)["kafka"]
    assert k["limit_hits_per_min_max"] == 30.0
    assert k["restarts_during_measure"] == 1
