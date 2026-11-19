"""Decision policy: turns the issue list into APPROVED / PENDING / REJECTED.

The policy is deliberately small and explainable - in the interview we need
to justify every point of risk score, so there is no hidden ML here.

Policy
------
- any *reject* issue  -> REJECTED  (fraud / duplicate / restricted party:
                                    nothing the vendor uploads can clear it)
- else any *pending*  -> PENDING   (vendor can supply or fix the item;
                                    AP re-runs the process when they reply)
- else                -> APPROVED

Risk score
----------
100 minus a fixed deduction per issue. Transparent on purpose: the buyer can
reconcile every point. Bands: Low >= 90, Medium 60-89, High < 60.
"""

from __future__ import annotations

SEVERITY_WEIGHTS = {"reject": 45, "pending": 12, "info": 0}


def decide(issues) -> tuple[str, int, str]:
    rejected = any(i.severity == "reject" for i in issues)
    pending = any(i.severity == "pending" for i in issues)

    decision = "REJECTED" if rejected else "PENDING" if pending else "APPROVED"
    score = max(0, 100 - sum(SEVERITY_WEIGHTS[i.severity] for i in issues))
    band = "Low" if score >= 90 else "Medium" if score >= 60 else "High"
    return decision, score, band


def counts(issues) -> dict:
    out = {"reject": 0, "pending": 0, "info": 0}
    for i in issues:
        out[i.severity] = out.get(i.severity, 0) + 1
    return out
