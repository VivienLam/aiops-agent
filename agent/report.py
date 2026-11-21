"""Final report schema and the checks run on it after the fact."""

import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

FaultType = Literal[
    "application_error", "latency", "crash_or_restart", "resource_limit",
    "bad_config", "bad_deployment", "dependency_failure", "unknown",
]


# Same list as the expected repair of the scenarios (bench/scenarios.py imports it).
RepairAction = Literal[
    "none", "escalate", "rollback_deployment", "restore_config",
    "raise_memory_limit", "restart_container",
]

REPAIR_ACTION_HELP = (
    "none: nothing to do (no incident, or not enough evidence to act). "
    "escalate: bug in the service's code, alert the team that owns the service. "
    "rollback_deployment: a recent deployment of the service broke it, roll it back. "
    "restore_config: a configuration change broke the service, restore the previous configuration. "
    "raise_memory_limit: the service runs out of memory under its limit, raise the limit. "
    "restart_container: the service's container is stuck or crashed, restart it."
)


class ProposedRepair(BaseModel):
    action: RepairAction = Field(description=REPAIR_ACTION_HELP)
    target: str | None = Field(
        None, description='Service the action applies to. null when action is "none"')

    @model_validator(mode="after")
    def target_matches_action(self):
        if self.action == "none" and self.target is not None:
            raise ValueError('target must be null when action is "none"')
        if self.action != "none" and not self.target:
            raise ValueError(f"target is required for action {self.action!r}")
        return self


class Impact(BaseModel):
    """Measured impact. Numbers only; null when not observed."""

    error_rate_percent: float | None = Field(None, ge=0, le=100,
                                             description="Observed error rate, in percent")
    latency_ms: float | None = Field(None, ge=0,
                                     description="Observed latency (p95 or slowest), in ms")
    error_count: int | None = Field(
        None, ge=0,
        description="Number of errors you counted. null if you did not count any; "
                    "0 only if you counted and found zero errors")
    window_minutes: int | None = Field(None, ge=0,
                                       description="Time window the numbers above cover")


class Evidence(BaseModel):
    step: int = Field(ge=1, description="Number N of the tool call, as in [tool call #N]")
    quote: str = Field(description="Short excerpt copied exactly from that tool call's output")


class Report(BaseModel):
    status: Literal["incident", "no_incident", "uncertain"]
    impact: Impact
    root_cause_service: str | None = Field(
        None, description="Service at the origin of the problem, or null")
    fault_type: FaultType | None = Field(
        None, description='Kind of fault. Must be null unless status is "incident"')
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(description="Facts from tool outputs that support the report")
    proposed_action: str = Field(description="What an operator should do next")
    proposed_repair: ProposedRepair = Field(
        description='The repair, from a fixed list. action must be "none" unless status is "incident"')
    missing_info: str = Field(description="What would be needed to conclude with more certainty")

    @model_validator(mode="after")
    def fault_type_only_for_incidents(self):
        if self.status != "incident" and self.fault_type is not None:
            raise ValueError('fault_type must be null when status is not "incident"')
        return self

    @model_validator(mode="after")
    def repair_only_for_incidents(self):
        if self.status != "incident" and self.proposed_repair.action != "none":
            raise ValueError('proposed_repair.action must be "none" when status is not "incident"')
        return self


def squash(text: str) -> str:
    """Remove all whitespace, so re-indented or compacted JSON still matches."""
    return "".join(text.split())


def check_evidence(report: Report, calls: list[dict]) -> dict:
    """Is each quote really in the output of the tool call it cites?

    `calls` are the run log's tool calls, each with "call" (its number) and "output".
    """
    outputs = {c["call"]: squash(c["output"]) for c in calls}
    details = []
    for ev in report.evidence:
        if ev.step not in outputs:
            ok, reason = False, "no such tool call"
        elif not squash(ev.quote):
            ok, reason = False, "empty quote"
        elif squash(ev.quote) in outputs[ev.step]:
            ok, reason = True, "found"
        else:
            ok, reason = False, "quote not in output"
        details.append({"step": ev.step, "ok": ok, "reason": reason})
    return {"verified": sum(d["ok"] for d in details), "total": len(details), "details": details}


def check_root_cause(report: Report, list_services_output: str) -> dict:
    """Is root_cause_service a service that list_services knows?"""
    name = report.root_cause_service
    try:
        known = {s["service"] for s in json.loads(list_services_output)["services"]}
    except (ValueError, KeyError, TypeError):
        return {"service": name, "ok": None, "reason": "list_services output unreadable"}
    if name is None:
        return {"service": None, "ok": True, "reason": "no service named"}
    ok = name in known
    return {"service": name, "ok": ok, "reason": "known" if ok else "not in list_services"}
