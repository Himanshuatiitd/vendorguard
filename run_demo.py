"""Demo runner for the PS-2 case study.

Usage
-----
  python run_demo.py                     run happy path + all 4 edge cases,
                                         verify decisions, build dashboard.html
  python run_demo.py <a-file.json>       run a single submission
  python run_demo.py --open              same as full demo, opens dashboard after

The console output above each run IS the live run view (each stage prints as
it executes); dashboard.html is the history view across runs.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import dashboard, main as engine  # noqa: E402

try:  # make sure emojis/boxes never crash the console on Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (fixture, expected decision) - the demo self-checks against this table.
FIXTURES = [
    ("SUB-001_happy_path.json", "APPROVED"),
    ("SUB-002_edge1_incomplete.json", "PENDING"),
    ("SUB-003_edge2_name_mismatch.json", "PENDING"),
    ("SUB-004_edge3_bad_gstin.json", "PENDING"),
    ("SUB-005_edge4_duplicate_fraud.json", "REJECTED"),
]


def main() -> None:
    args = sys.argv[1:]
    master = engine.load_master()

    if args and args[0].endswith(".json"):
        engine.run_submission(ROOT / "submissions" / args[0], master)
        out = dashboard.build()
        print(f"dashboard written -> {out}")
        return

    print()
    print("*" * 66)
    print(" VendorGuard - vendor onboarding verification (PS-2)")
    print(" demo sequence: happy path + 4 designed edge cases")
    print("*" * 66)
    print()

    results = []
    for fixture, expected in FIXTURES:
        res = engine.run_submission(ROOT / "submissions" / fixture, master)
        results.append((fixture, expected, res.decision))

    print("=" * 66)
    print(" SELF-CHECK: expected vs actual decision per fixture")
    print("=" * 66)
    ok = True
    for fixture, expected, actual in results:
        mark = "PASS" if expected == actual else "FAIL"
        ok &= expected == actual
        print(f" {mark}  {fixture:<36} expected={expected:<9} actual={actual}")
    print("=" * 66)

    out = dashboard.build()
    print(f" dashboard written -> {out}")
    if ok:
        print(" all fixtures behaved as designed.")
    if "--open" in args:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
