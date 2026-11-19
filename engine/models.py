"""Shared data structures for the onboarding verification pipeline (PS-2)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Issue:
    """One finding produced by a rule.

    severity vocabulary:
      reject  - hard stop. Fraud signal, duplicate, or restricted party.
                No vendor action can clear it inside this run.
      pending - cannot approve yet, but the vendor can fix it
                (missing data, wrong format, needs a re-upload).
      info    - recorded for the audit trail; does not affect the decision.
    """

    code: str          # machine-readable, e.g. "MISSING_FIELD"
    severity: str
    stage: str         # pipeline stage that raised it
    message: str       # human-readable explanation
    action: str = ""   # what the vendor (or AP team) must do next

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StageResult:
    """Outcome of one pipeline stage, used for the live run view."""

    name: str
    status: str  # "OK" | "WARN" | "FAIL"
    findings: list = field(default_factory=list)  # short human-readable lines

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    """Everything one submission produced - written to runs/<run_id>/result.json."""

    run_id: str
    submission_id: str
    label: str
    vendor_name: str
    country: str
    decision: str          # "APPROVED" | "PENDING" | "REJECTED"
    risk_score: int
    risk_band: str
    issues: list           # list[Issue.to_dict()]
    stages: list           # list[StageResult.to_dict()]
    started_at: str
    duration_ms: int
    input_file: str
    followup_file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
