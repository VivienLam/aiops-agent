"""Scenario files: one YAML file per scenario in scenarios/, validated on load."""

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from agent.report import FaultType, RepairAction

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"

ScenarioId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,60}$")]
FlagName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{1,60}$")]
VariantName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9%]{1,20}$")]
ServiceName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")]
ParamName = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,30}$")]

# Faults that write their own flag name in the logs (see DECISIONS.md, "Fuites des noms de flags").
# intlShippingSlowdown's log is hidden today (shipping logs are dated 1970), but it does write it.
LEAKY_FLAGS = {"kafkaQueueProblems", "kafkaConsumerDead", "adManualGc", "productCatalogFailure",
               "intlShippingSlowdown"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInjection(Strict):
    kind: Literal["none"]


class FlagInjection(Strict):
    kind: Literal["flag"]
    flag: FlagName
    variant: VariantName


class DockerInjection(Strict):
    """Planned, not implemented yet."""

    kind: Literal["docker"]
    action: Literal["fake_deployment", "bad_config", "memory_limit", "crash"]
    service: ServiceName
    params: dict[ParamName, str | int | float] = {}


class Repair(Strict):
    action: RepairAction
    target: ServiceName | None = None

    @model_validator(mode="after")
    def target_matches_action(self):
        if self.action == "none" and self.target is not None:
            raise ValueError('repair.target must be empty when action is "none"')
        if self.action != "none" and self.target is None:
            raise ValueError(f"repair.target is required for action {self.action!r}")
        return self


class Expected(Strict):
    status: Literal["incident", "no_incident", "uncertain"]
    root_cause_service: ServiceName | None
    fault_type: FaultType | None
    # Other labels that are just as defensible (decided before any agent run on the scenario).
    fault_type_also_accepted: list[FaultType] = []
    repair: Repair

    @model_validator(mode="after")
    def consistent(self):
        if self.status != "incident" and self.fault_type is not None:
            raise ValueError('fault_type must be null when status is not "incident"')
        if self.fault_type_also_accepted:
            if self.fault_type is None:
                raise ValueError("fault_type_also_accepted needs a fault_type")
            also = self.fault_type_also_accepted
            if self.fault_type in also or len(set(also)) != len(also):
                raise ValueError("fault_type_also_accepted: no repeat, and not the main fault_type")
        if self.status == "no_incident" and (self.root_cause_service or self.repair.action != "none"):
            raise ValueError("no_incident: root_cause_service must be null and repair.action none")
        return self


class Scenario(Strict):
    id: ScenarioId
    description: str = Field(min_length=1, max_length=200)
    lot: Literal["tuning", "eval"]
    injection: NoInjection | FlagInjection | DockerInjection = Field(discriminator="kind")
    wait_seconds: int = Field(ge=0, le=1800)
    expected: Expected
    leaks_own_name: bool

    @model_validator(mode="after")
    def leak_flag_set(self):
        inj = self.injection
        if inj.kind == "flag" and inj.flag in LEAKY_FLAGS and not self.leaks_own_name:
            raise ValueError(f"{inj.flag} writes its own name in the logs: leaks_own_name must be true")
        return self


def check_flag_exists(scenario: Scenario, flags: dict) -> None:
    """The flag and its variant must exist in the demo's flag file (catches typos)."""
    inj = scenario.injection
    if inj.kind != "flag":
        return
    if inj.flag not in flags:
        raise ValueError(f"unknown flag {inj.flag!r}")
    if inj.variant not in flags[inj.flag]["variants"]:
        known = ", ".join(flags[inj.flag]["variants"])
        raise ValueError(f"unknown variant {inj.variant!r} for {inj.flag} (known: {known})")


def load_scenario(path: Path, flags: dict | None) -> Scenario:
    """Read and validate one scenario. `flags` is the demo's "flags" dict, None to skip that check."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a YAML mapping")
    scenario = Scenario.model_validate(data)
    if scenario.id != path.stem:
        raise ValueError(f"{path.name}: id {scenario.id!r} must match the file name")
    if flags is not None:
        check_flag_exists(scenario, flags)
    return scenario


def find_scenario(name: str) -> Path:
    """Scenario id (or path) from the command line -> file path."""
    if re.fullmatch(r"[a-z0-9-]{1,60}", name):
        path = SCENARIO_DIR / f"{name}.yaml"
    else:
        path = Path(name)
    if not path.is_file():
        raise FileNotFoundError(f"no scenario file {path}")
    return path


def load_all(flags: dict | None, directory: Path = SCENARIO_DIR) -> list[Scenario]:
    return [load_scenario(p, flags) for p in sorted(directory.glob("*.yaml"))]
