"""Scenario files: reading and validation. No demo needed."""

import pytest
import yaml

from bench.scenarios import SCENARIO_DIR, load_all, load_scenario

FLAGS = {
    "paymentFailure": {"variants": {"off": 0, "50%": 0.5, "100%": 1}},
    "adManualGc": {"variants": {"off": False, "on": True}},
}

PAYMENT = {
    "id": "payment-failure-50",
    "description": "Payment refuses half of the payments",
    "lot": "tuning",
    "injection": {"kind": "flag", "flag": "paymentFailure", "variant": "50%"},
    "wait_seconds": 120,
    "expected": {
        "status": "incident",
        "root_cause_service": "payment",
        "fault_type": "application_error",
        "repair": {"action": "escalate", "target": "payment"},
    },
    "leaks_own_name": False,
}


def write(tmp_path, data, name=None):
    path = tmp_path / f"{name or data['id']}.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def changed(**changes):
    """Copy of PAYMENT with some fields replaced (dotted keys for nested ones)."""
    data = yaml.safe_load(yaml.safe_dump(PAYMENT))
    for key, value in changes.items():
        *parents, last = key.split("__")
        target = data
        for p in parents:
            target = target[p]
        target[last] = value
    return data


def test_valid_scenario(tmp_path):
    s = load_scenario(write(tmp_path, PAYMENT), FLAGS)
    assert s.injection.flag == "paymentFailure"
    assert s.expected.repair.action == "escalate"


def test_idle_scenario(tmp_path):
    data = changed(id="idle", injection={"kind": "none"}, wait_seconds=0,
                   expected={"status": "no_incident", "root_cause_service": None,
                             "fault_type": None, "repair": {"action": "none"}})
    s = load_scenario(write(tmp_path, data), FLAGS)
    assert s.injection.kind == "none"


def test_docker_injection_is_accepted(tmp_path):
    data = changed(injection={"kind": "docker", "action": "memory_limit",
                              "service": "checkout", "params": {"memory": "15M"}})
    assert load_scenario(write(tmp_path, data), FLAGS).injection.service == "checkout"


@pytest.mark.parametrize("data", [
    changed(lot="training"),
    changed(expected__fault_type="disk_full"),
    changed(expected__repair={"action": "disable_flag", "target": "payment"}),
    changed(expected__repair={"action": "escalate"}),                 # target missing
    changed(expected__root_cause_service="payment; drop"),            # regex
    changed(injection__flag="payment Failure"),                       # regex
    changed(injection__variant="50%;rm"),                             # regex
    changed(injection={"kind": "docker", "action": "format_disk", "service": "cart"}),
    changed(injection={"kind": "script", "path": "x.sh"}),
    changed(wait_seconds=-1),
    changed(extra_field=1),                                           # typo in a field name
    changed(expected__status="no_incident"),                          # fault_type must be null
    changed(expected__fault_type_also_accepted=["disk_full"]),
    changed(expected__fault_type_also_accepted=["application_error"]),  # same as the main one
    changed(expected__fault_type_also_accepted=["latency", "latency"]),
    changed(expected__fault_type_also_accepted=[None]),
])
def test_invalid_scenarios_are_refused(tmp_path, data):
    with pytest.raises(ValueError):
        load_scenario(write(tmp_path, data, name=data["id"]), FLAGS)


def test_fault_type_also_accepted(tmp_path):
    assert load_scenario(write(tmp_path, PAYMENT), FLAGS).expected.fault_type_also_accepted == []
    data = changed(expected__fault_type="bad_config",
                   expected__fault_type_also_accepted=["application_error"])
    s = load_scenario(write(tmp_path, data), FLAGS)
    assert s.expected.fault_type_also_accepted == ["application_error"]
    idle = changed(injection={"kind": "none"},
                   expected={"status": "no_incident", "root_cause_service": None, "fault_type": None,
                             "fault_type_also_accepted": ["latency"], "repair": {"action": "none"}})
    with pytest.raises(ValueError):
        load_scenario(write(tmp_path, idle), FLAGS)


def test_id_must_match_file_name(tmp_path):
    with pytest.raises(ValueError, match="file name"):
        load_scenario(write(tmp_path, PAYMENT, name="other"), FLAGS)


def test_unknown_flag_or_variant(tmp_path):
    with pytest.raises(ValueError, match="unknown flag"):
        load_scenario(write(tmp_path, changed(injection__flag="paymentFail")), FLAGS)
    with pytest.raises(ValueError, match="unknown variant"):
        load_scenario(write(tmp_path, changed(injection__variant="60%")), FLAGS)


def test_leaky_flag_must_be_marked(tmp_path):
    data = changed(injection={"kind": "flag", "flag": "adManualGc", "variant": "on"})
    with pytest.raises(ValueError, match="leaks_own_name"):
        load_scenario(write(tmp_path, data), FLAGS)
    data["leaks_own_name"] = True
    assert load_scenario(write(tmp_path, data), FLAGS).leaks_own_name


def test_service_to_restart(tmp_path):
    from bench.scenarios import service_to_restart
    assert service_to_restart(load_scenario(write(tmp_path, PAYMENT), FLAGS)) == "payment"
    docker_inj = changed(injection={"kind": "docker", "action": "crash", "service": "cart"})
    assert service_to_restart(load_scenario(write(tmp_path, docker_inj), FLAGS)) == "cart"
    idle = changed(injection={"kind": "none"},
                   expected={"status": "no_incident", "root_cause_service": None,
                             "fault_type": None, "repair": {"action": "none"}})
    assert service_to_restart(load_scenario(write(tmp_path, idle), FLAGS)) is None


def test_repo_scenarios_are_valid():
    """The real files in scenarios/ (flag names checked against the demo when it is there)."""
    try:
        from bench.flags import load_flags
        flags = load_flags()
    except FileNotFoundError:
        flags = None
    ids = {s.id for s in load_all(flags, SCENARIO_DIR)}
    assert {"idle", "payment-failure-50"} <= ids
