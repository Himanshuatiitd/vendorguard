"""Orchestrator: runs one vendor submission through the six pipeline stages.

Stages
------
1. INGEST        load + structural sanity of the submission
2. COMPLETENESS  is everything present? (fields + required documents)
3. FORMAT        is everything well-formed? (checksums, patterns, expiry)
4. CONSISTENCY   do the fields agree with each other? (cross-reference)
5. IDENTITY      is this vendor new? (duplicates, restricted parties)
6. DECISION      policy -> APPROVED / PENDING / REJECTED + risk score

Everything the run does is written to runs/<run_id>/ for the audit trail:
result.json, the exact input (submission.json) and - for anything not
approved - the follow-up letter generated for the vendor.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import dashboard, decide as decide_mod, followup, rules
from .models import Issue, RunResult, StageResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"
DATA_DIR = PROJECT_ROOT / "data"

STAGE_NAMES = ["INGEST", "COMPLETENESS", "FORMAT", "CONSISTENCY", "IDENTITY", "DECISION"]
_STATUS_TAG = {"OK": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}


def load_master() -> dict:
    """Approved-vendor master used for duplicate detection."""
    p = DATA_DIR / "vendor_master.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"vendors": []}


def _stage_status(issues: list) -> str:
    if any(i.severity == "reject" for i in issues):
        return "FAIL"
    if any(i.severity == "pending" for i in issues):
        return "WARN"
    return "OK"


def _print_stage(n: int, st: StageResult) -> None:
    print(f" [ {n}/6 ] {st.name:<13} {_STATUS_TAG[st.status]}")
    for f in st.findings:
        print(f"           - {f}")


def run_submission(path, master: dict, verbose: bool = True) -> RunResult:
    """Run a submission stored on disk (CLI / demo path)."""
    path = Path(path)
    parse_error = None
    sub: dict = {}
    raw_text = ""
    try:
        raw_text = path.read_text(encoding="utf-8")
        sub = json.loads(raw_text)
        if not isinstance(sub, dict):
            raise ValueError("top-level JSON value must be an object")
    except Exception as exc:  # unreadable file: fatal for this run
        parse_error = exc
    extra = {"submission.raw": raw_text} if parse_error is not None else None
    return run_submission_data(
        sub, path.stem, master, verbose=verbose, parse_error=parse_error,
        extra_files=extra,
    )


def run_submission_data(sub: dict, stem: str, master: dict, verbose: bool = True,
                        parse_error: Exception | None = None,
                        extra_files: dict | None = None) -> RunResult:
    """Run an already-loaded submission dict through the pipeline.

    Used by the interactive server, where the input may come from a form, pasted
    text or an uploaded file and therefore has no path on disk. The exact input
    is still persisted to runs/<run_id>/submission.json for the audit trail.
    """
    started = time.perf_counter()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    issues: list = []
    stages: list = []
    stem = stem or "submission"

    if verbose:
        print("=" * 66)
        print(f" RUN {stem}")
        print("=" * 66)

    # ---- stage 1: INGEST ------------------------------------------------
    if not isinstance(sub, dict):
        sub = {}
        parse_error = parse_error or ValueError("submission must be a JSON object")
    ing_issues: list = []
    if parse_error is not None:
        ing_issues = [Issue(
            "SUBMISSION_UNPARSEABLE", "reject", "INGEST",
            f"Submission file could not be read as JSON ({parse_error}).",
            "",
        )]
    else:
        for req in ("submission_id", "legal_name", "country"):
            if not sub.get(req):
                ing_issues.append(Issue(
                    "INVALID_SUBMISSION", "pending", "INGEST",
                    f"Submission is missing '{req}'.",
                    f"Re-submit with '{req}' filled in.",
                ))
    stages.append(StageResult(
        "INGEST", _stage_status(ing_issues),
        [i.message for i in ing_issues] or ["loaded submission, structure valid"],
    ))
    issues += ing_issues
    if verbose:
        _print_stage(1, stages[-1])

    parseable = bool(sub) and not any(
        i.code == "SUBMISSION_UNPARSEABLE" for i in issues)

    # ---- stages 2-5: rule packs ------------------------------------------
    if parseable:
        stage_defs = [
            ("COMPLETENESS", lambda: rules.check_completeness(sub)),
            ("FORMAT", lambda: rules.check_formats(sub)),
            ("CONSISTENCY", lambda: rules.check_consistency(sub)),
            ("IDENTITY", lambda: rules.check_identity(sub, master)),
        ]
        for n, (name, fn) in enumerate(stage_defs, start=2):
            st_issues = fn()
            stages.append(StageResult(
                name, _stage_status(st_issues),
                [i.message for i in st_issues] or ["no findings"],
            ))
            issues += st_issues
            if verbose:
                _print_stage(n, stages[-1])
    else:
        for n, name in enumerate(STAGE_NAMES[1:5], start=2):
            stages.append(StageResult(name, "OK", ["skipped - submission unparseable"]))
            if verbose:
                _print_stage(n, stages[-1])

    # ---- stage 6: DECISION ------------------------------------------------
    if parseable:
        decision, score, band = decide_mod.decide(issues)
        dec_status = {"APPROVED": "OK", "PENDING": "WARN", "REJECTED": "FAIL"}[decision]
        counts = decide_mod.counts(issues)
        dec_findings = [
            f"{len(issues)} issue(s) evaluated "
            f"(reject: {counts['reject']}, pending: {counts['pending']}, info: {counts['info']})",
            f"policy outcome: {decision} - risk {score}/100 ({band})",
        ]
    else:
        decision, score, band = "REJECTED", 0, "High"
        dec_status, dec_findings = "FAIL", ["no decision possible - unparseable submission"]
    stages.append(StageResult("DECISION", dec_status, dec_findings))
    if verbose:
        _print_stage(6, stages[-1])

    # ---- finalize: artifacts + audit trail ---------------------------------
    sid = re.sub(r"[^A-Za-z0-9._-]+", "-", str(sub.get("submission_id") or stem)).strip(".-")
    sid = (sid or "submission")[:60]
    base_id = f"{sid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_id = base_id
    n = 2
    while (RUNS_DIR / run_id).exists():  # same id submitted twice in one second
        run_id = f"{base_id}-{n}"
        n += 1
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "submission.json").write_text(
        json.dumps(sub if isinstance(sub, dict) else {}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    for name, content in (extra_files or {}).items():
        if not name or name == "submission.json":
            continue
        (run_dir / name).write_text(str(content), encoding="utf-8")

    result = RunResult(
        run_id=run_id,
        submission_id=sid,
        label=sub.get("label", ""),
        vendor_name=sub.get("legal_name") or "(unknown vendor)",
        country=(sub.get("country") or "").upper(),
        decision=decision,
        risk_score=score,
        risk_band=band,
        issues=[i.to_dict() for i in issues],
        stages=[s.to_dict() for s in stages],
        started_at=now,
        duration_ms=int((time.perf_counter() - started) * 1000),
        input_file="submission.json",
    )

    followup_name = ""
    if parseable and decision != "APPROVED":
        followup_name, letter = followup.build_letter(sub, result.to_dict())
        (run_dir / followup_name).write_text(letter, encoding="utf-8")
        result.followup_file = followup_name

    (run_dir / "result.json").write_text(
        json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    index_path = RUNS_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    index = [e for e in index if e.get("run_id") != run_id]
    index.append(result.to_dict())
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    if verbose:
        print(" " + "-" * 64)
        print(f" DECISION: {decision}  -  risk {score}/100 ({band})")
        if followup_name:
            print(f" follow-up letter: {run_dir / followup_name}")
        else:
            print(" no follow-up needed - vendor cleared for activation")
        print()

    return result
