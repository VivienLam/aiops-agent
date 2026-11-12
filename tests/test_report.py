"""Report schema and the after-the-fact checks on a fake run log."""
import json

import pytest
from pydantic import ValidationError

from agent.report import Report, check_evidence, check_root_cause

CALLS = [
    {"call": 1, "tool": "list_services", "output": json.dumps({
        "window_minutes": 5,
        "services": [{"service": "checkout", "requests_per_second": 1.2, "error_rate_percent": 0.0},
                     {"service": "email", "requests_per_second": 0.3, "error_rate_percent": 12.5}],
    }, indent=2)},
    {"call": 2, "tool": "search_logs", "output": "12 matching logs\n- email   ERROR  smtp   connection refused"},
]


def report(**overrides) -> Report:
    data = {
        "status": "incident",
        "impact": {"error_rate_percent": 12.5, "error_count": 12, "window_minutes": 5},
        "root_cause_service": "email",
        "fault_type": "dependency_failure",
        "confidence": 0.7,
        "evidence": [
            # real, with different spacing than the output
            {"step": 2, "quote": "email ERROR smtp connection refused"},
            # invented
            {"step": 1, "quote": "email is down"},
            # call that does not exist
            {"step": 9, "quote": "12 matching logs"},
        ],
        "proposed_action": "Check the SMTP server.",
        "missing_info": "None.",
    }
    data.update(overrides)
    return Report.model_validate(data)


def test_valid_report():
    r = report()
    assert r.impact.latency_ms is None
    assert r.evidence[0].step == 2


def test_no_incident_has_no_fault_type():
    r = report(status="no_incident", fault_type=None, root_cause_service=None)
    assert r.fault_type is None
    with pytest.raises(ValidationError):
        report(status="no_incident", fault_type="unknown")
    with pytest.raises(ValidationError):
        report(status="uncertain", fault_type="latency")


def test_error_count_null_is_not_zero():
    assert report(impact={}).impact.error_count is None
    assert report(impact={"error_count": 0}).impact.error_count == 0
    with pytest.raises(ValidationError):
        report(impact={"error_count": -1})


@pytest.mark.parametrize("field, value", [
    ("status", "broken"),
    ("fault_type", "network"),
    ("confidence", 1.5),
    ("impact", {"error_rate_percent": "high"}),
    ("evidence", [{"step": 0, "quote": "x"}]),
])
def test_invalid_report(field, value):
    with pytest.raises(ValidationError):
        report(**{field: value})


def test_evidence_check():
    result = check_evidence(report(), CALLS)
    assert (result["verified"], result["total"]) == (1, 3)
    assert [d["reason"] for d in result["details"]] == [
        "found", "quote not in output", "no such tool call"]


def test_root_cause_check():
    services = CALLS[0]["output"]
    assert check_root_cause(report(), services)["ok"] is True
    assert check_root_cause(report(root_cause_service="mailer"), services)["ok"] is False
    assert check_root_cause(report(root_cause_service=None), services)["ok"] is True
    assert check_root_cause(report(), "Tool error: timeout")["ok"] is None
