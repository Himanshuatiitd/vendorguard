# VendorGuard — vendor onboarding verification (PS-2)

Take-home build for the **AI Solutions Analyst** case study, problem
statement **PS-2 · Operations · Vendor onboarding: from submission to
approval**.

**What it does:** takes a vendor submission as input and produces a clear
status — **APPROVED / PENDING / REJECTED** — with the full reasoning visible.
Anything not approved automatically gets a follow-up letter saying exactly
what is needed. Rejections always offer a human appeal path.

---

## Quick start

```bash
# interactive dashboard: fill fields, paste JSON/text, upload a file,
# get a REAL / NEEDS CHECK / FAKE verdict (pipeline runs in background)
python server.py --open      # serves http://127.0.0.1:8765

python run_demo.py           # happy path + 4 edge cases + self-check + dashboard
python run_demo.py --open    # same, opens dashboard.html in the browser
python run_demo.py SUB-003_edge2_name_mismatch.json   # one submission only
```

Requires Python 3.10+. **No third-party dependencies** — stdlib only.

- **Live check view** (`python server.py`): the dashboard accepts a real
  submission four ways — form fields, pasted JSON, labelled text, or an
  uploaded `.json`/`.txt` file. The Python pipeline runs on a background
  worker and the page polls for the verdict. Decision → plain-language
  verdict: **APPROVED → REAL**, **PENDING → NOT VERIFIED (needs more
  information)**, **REJECTED → FAKE / FRAUD RISK**. Every live check is
  written to `runs/` exactly like a CLI run.
- The **console output** of `run_demo.py` is the live run view: each pipeline
  stage prints as it executes, with its findings.
- **`dashboard.html`** is the history view: every run with decision, risk
  score, stage timeline, issues and the follow-up letter. Single
  self-contained file for reuse — the live-check panel needs `server.py`,
  but history still works when the file is opened directly.

**Want a public link?** See **[DEPLOYMENT.md](DEPLOYMENT.md)** for free
hosting (Render / Hugging Face Spaces for the live app, GitHub Pages or
Netlify for a static snapshot) and how to embed it in Notion.


## The pipeline

```
INGEST → COMPLETENESS → FORMAT → CONSISTENCY → IDENTITY → DECISION
```

| Stage | Answers |
|---|---|
| INGEST | is the submission structurally valid? |
| COMPLETENESS | are all required fields and the 3 required documents present? |
| FORMAT | are checksums/patterns valid? (GSTIN mod-36, IBAN mod-97, ABA, IFSC, doc expiry) |
| CONSISTENCY | do the fields agree with each other? (account name vs legal name, bank country vs country, currency vs registry) |
| IDENTITY | is this vendor new? (duplicate tax ID / bank account vs vendor master, restricted list) |
| DECISION | policy → APPROVED / PENDING / REJECTED + explainable risk score |

Decision policy and the full rule inventory (with a traceability matrix of
which fixture exercises which rule) are in **[ANALYSIS.md](ANALYSIS.md)**.

## What is in the box

```
ps2-vendor-onboarding/
├── server.py                    local dashboard server + background job API
├── run_demo.py                  demo runner (all fixtures + self-check + dashboard)
├── dashboard.html               interactive check panel + history across all runs
├── DEPLOYMENT.md                free hosting / Notion embedding guide
├── render.yaml                  one-click Render free web service
├── Dockerfile                   container deploy (Hugging Face Spaces, Cloud Run)
├── requirements.txt             empty: stdlib only, hosts detect Python
├── ANALYSIS.md                  problem analysis, process map, rule inventory
├── DEMO_SCRIPT.md               5-minute demo video script
├── engine/
│   ├── main.py                  orchestrator + live run view + audit trail
│   ├── rules.py                 all validation rules, grouped by stage
│   ├── decide.py                decision policy + risk score
│   ├── followup.py              follow-up letters for PENDING / REJECTED
│   ├── intake.py                JSON / labelled-text / form input normaliser
│   ├── dashboard.py             dashboard.html generator
│   └── models.py                Issue / StageResult / RunResult
├── submissions/                 5 test inputs (1 happy path + 4 edge cases)
├── data/vendor_master.json      approved vendors (for duplicate detection)
└── runs/                        audit trail: one folder per run
    └── <run_id>/                input copy + result.json + followup.md
```

## The four edge cases (and why they're not trivial)

| Fixture | Scenario | Decision | The point |
|---|---|---|---|
| SUB-002 | Incomplete submission — no phone, no IFSC, no bank letter | PENDING | The process must *ask for exactly what's missing*, not reject vaguely |
| SUB-003 | Subtle cross-field inconsistencies — bank account holder only 79% match to the legal entity; USD settlement for a German vendor | PENDING | Fields are individually fine; only cross-referencing reveals the problem |
| SUB-004 | GSTIN passes the format check but **fails its mod-36 check digit** | PENDING | Defeats naive validators: format-valid, substance-wrong |
| SUB-005 | Duplicate/fraud — a slightly different legal name claiming the **same tax ID and same bank account** as an approved vendor (+ expired tax cert) | REJECTED | The highest-cost failure mode; process must catch it and offer a human appeal |

`run_demo.py` self-checks every fixture's actual decision against the
expected one and prints a PASS/FAIL table — the demo can't silently break.

## Design decisions worth defending in an interview

1. **Rules are declarative and stage-grouped** — every issue maps to one rule,
   every rule to one line in the traceability matrix. Decisions are explainable
   end-to-end (which is what PS-2 asks for: "with the reasoning visible").
2. **Three decision outcomes, not binary** — APPROVED / PENDING / REJECTED
   with a policy that distinguishes "fixable by the vendor" (pending) from
   "hard stop" (reject). PENDINGS always carry a concrete action list; the
   follow-up letter is generated, not hand-written.
3. **Checksums over regexes** — GSTIN mod-36, IBAN mod-97 and ABA check-digit
   validators catch mistyped/fabricated numbers that pattern checks wave
   through. This is the "valid-looking but wrong" input class.
4. **Cross-reference before identity** — consistency checks are separate from
   duplicate detection because they catch different fraud patterns at
   different costs.
5. **Audit trail as a first-class artifact** — every run persists its exact
   input, stage timeline, issues and output under `runs/`. The "audit trail is
   whatever's in someone's inbox" problem is solved by construction.
6. **Human in the loop where it matters** — rejections offer a manual appeal;
   borderline name matches route to AP verification rather than auto-reject.

## Honest limitations (and what I'd build next)

- Inputs are JSON, labelled text or dashboard form fields; real intake would be
  PDF/OCR — the pipeline sits *behind* extraction, so the validation core
  doesn't change.
- VAT (GB/DE) checks are format-level in v1; modulus checks and real
  registry/screening APIs (GSTN, VIES, OpenSanctions) are v2.
- Single-country-pack registry of 5 countries; adding a country is config,
  not code, but the packs themselves are hand-written.
- No reviewer worklist yet — pendings generate the letter but a human queue
  with SLA reminders is the next build item.

Full list with priorities: **[ANALYSIS.md §7](ANALYSIS.md)**.
