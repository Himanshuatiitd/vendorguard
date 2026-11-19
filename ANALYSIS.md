# PS-2 Analysis — Vendor Onboarding: from submission to approval

This document is the "think before you build" artifact: how the problem was
broken down, what rules the process applies, why the edge cases were chosen,
and what the simplifications are. Everything here is referencable in the demo
video and the live interview.

---

## 1. Understanding the problem

**The job to be done:** before a company can pay a vendor, someone must
verify the vendor is a real, legitimate entity — and that the details on the
submission (legal name, tax registration, bank account) all agree with each
other and with the procurement system's records.

**The actors:**

| Actor | What they want | What they hate |
|---|---|---|
| AP / procurement analyst | to approve good vendors fast | chasing vendors for missing docs; being the audit trail |
| The vendor | to get onboarded quickly | vague "your form was wrong" emails |
| The company | no payment fraud, no compliance exposure | a vendor slipping through with inconsistent details |

**The failure modes that actually cost money** (from the problem statement):

1. **Incomplete data** — missing banking info, no tax document → process stalls, someone has to track the vendor down manually.
2. **Subtle inconsistencies** — the name on the submission doesn't match the bank account, the tax ID format is wrong for the claimed country, currency doesn't fit the country. *Only visible when you cross-reference fields.* These are the fraud signals.
3. **Distrustworthy inputs** — valid-*looking* numbers that are actually mistyped or fabricated.
4. **Duplicate / re-registration** — an entity already approved under one name comes back under a slightly different name with the same tax ID or same bank account. This is the classic payment-fraud pattern.
5. **No audit trail** — decisions live in someone's inbox.

**Key insight that shaped the design:** the hard part is not validation
(any form library can check "is this field filled"). The hard part is
**cross-referencing** — fields that are each fine in isolation but wrong
together, and inputs that are valid in *format* but wrong in *substance*
(checksums, duplicates). So the pipeline is organized around that: separate
stages for completeness → format → consistency → identity.

---

## 2. Process map

```
                       ┌──────────────────────────┐
 submission (JSON) ───▶│ 1. INGEST                │ structurally valid?
                       └────────────┬─────────────┘
                       ┌────────────▼─────────────┐
                       │ 2. COMPLETENESS          │ all required fields
                       │                          │ + 3 required documents?
                       └────────────┬─────────────┘
                       ┌────────────▼─────────────┐
                       │ 3. FORMAT                │ GSTIN checksum, IBAN
                       │                          │ mod-97, ABA, IFSC,
                       │                          │ email, doc expiry
                       └────────────┬─────────────┘
                       ┌────────────▼─────────────┐
                       │ 4. CONSISTENCY           │ account name vs legal
                       │   (cross-reference)      │ name, bank country vs
                       │                          │ country, currency vs
                       │                          │ registry, VAT prefix
                       └────────────┬─────────────┘
                       ┌────────────▼─────────────┐
                       │ 5. IDENTITY              │ duplicate tax ID /
                       │                          │ bank account vs vendor
                       │                          │ master, restricted list
                       └────────────┬─────────────┘
                       ┌────────────▼─────────────┐
                       │ 6. DECISION              │ APPROVED / PENDING /
                       │                          │ REJECTED + risk score
                       └────────────┬─────────────┘
                                    │
              not approved? ────────┼──────▶ follow-up letter to the
              (what's needed,       │        vendor + full audit trail
               human appeal path)   │        (runs/<run_id>/)
```

Every stage appends its findings to one issue list; the decision stage applies
policy to that list. Each run persists: the exact input, stage-by-stage
timeline, every issue (code, severity, message, action), the decision, and —
for anything not approved — the generated follow-up letter.

---

## 3. Decision policy (deliberately small and explainable)

| Severity | Meaning | Examples | Effect |
|---|---|---|---|
| `reject` | nothing the vendor uploads can clear it in-run | duplicate tax ID / bank account, restricted country, gross name mismatch | forces **REJECTED** |
| `pending` | vendor can supply or fix it | missing field/doc, failed checksum, partial name mismatch, currency mismatch | forces **PENDING** with a concrete action list |
| `info` | audit trail only | unrecognized extra document | no effect |

- **REJECTED** if any `reject` issue; **PENDING** if any `pending`; else **APPROVED**.
- **Risk score** = 100 − fixed deduction per issue (reject 45, pending 12).
  Bands: Low ≥ 90, Medium 60–89, High < 60. Transparent on purpose: the buyer
  can reconcile every point; no black box.
- Rejections always include a **human appeal path** (compliance-safe,
  human-in-the-loop pattern); pendings always include a **concrete ask**.

---

## 4. Rule inventory + traceability

| # | Rule | Severity | Exercised by |
|---|---|---|---|
| R1 | Required top-level / contact / banking fields present | pending | SUB-002 |
| R2 | Per-country banking sort-code present (IFSC / ABA / IBAN) | pending | SUB-002 |
| R3 | Three required documents attached (tax cert, bank letter, incorporation) | pending | SUB-002 |
| R4 | GSTIN format + valid state code + **mod-36 checksum** | pending | SUB-004 |
| R5 | EIN / VAT / TRN format (VAT = format-level; modulus checks are v2) | pending | — (rule present) |
| R6 | IBAN **mod-97** checksum (ISO 13616) | pending | — (all IBANs in fixtures are valid) |
| R7 | ABA routing check-digit test | pending | — (rule present) |
| R8 | IFSC pattern | pending | — (all IFSCs in fixtures are valid) |
| R9 | Email / phone / account-number sanity | pending | — (rule present) |
| R10 | Document expiry (and unreadable dates) | pending | SUB-005 |
| R11 | Account-holder name vs legal entity (fuzzy, suffix-aware) < 50% | reject | — (policy shown in code) |
| R12 | Same as R11, 50–85% | pending | SUB-003 (79%) |
| R13 | Bank country vs claimed country | pending | — (rule present) |
| R14 | Settlement currency vs country registry | pending | SUB-003 (USD for DE) |
| R15 | VAT prefix vs claimed country | pending | — (rule present) |
| R16 | Restricted-country screening | reject | — (rule present) |
| R17 | Duplicate tax ID vs vendor master | reject | SUB-005 |
| R18 | Duplicate bank account (hashed) vs vendor master | reject | SUB-005 |
| R19 | Near-name match to an approved vendor (≥ 92%, same country) | pending | — (rule present) |

Roughly half the rules are exercised by the fixtures and half are present-but-
unexercised — that's deliberate: the rule *shape* is demonstrated on realistic
cases, and the full inventory is documented here so the interviewer can see
the system's ceiling.

---

## 5. Edge cases — why these four

Chosen to span the three failure classes above, and none of them trivial:

| # | Fixture | Scenario | Why it's realistic | Expected behaviour |
|---|---|---|---|---|
| 1 | SUB-002 | Incomplete submission (no phone, no IFSC, no bank letter) | The problem statement literally opens with "vendors submit incomplete forms… someone has to track down the vendor" | **PENDING** + follow-up letter listing exactly the 3 missing items |
| 2 | SUB-003 | Subtle cross-field inconsistencies: bank account holder only *partially* matches the legal entity (79%), settlement currency USD for a German vendor | "Some submissions look fine on the surface but have subtle inconsistencies that only become visible when you cross-reference the fields" | **PENDING** for manual verification; both findings named in the letter |
| 3 | SUB-004 | GSTIN that passes the format regex but **fails its mod-36 check digit** | Exactly the "valid-looking but wrong" input that defeats naive validators; a single mistyped character is invisible to a human | **PENDING**: re-check the GSTIN against the certificate and re-upload |
| 4 | SUB-005 | Duplicate / suspected fraud: a *slightly different* legal name claiming the same tax ID **and** the same bank account as an approved vendor, with an expired tax cert attached | The highest-cost failure mode: "a vendor who slips through with inconsistent details can cause payment fraud" | **REJECTED** (two hard signals), risk 0/100, human appeal offered |

---

## 6. Assumptions (made explicit, per the guide's FAQ)

1. **Inputs are synthetic** — the guide explicitly allows creating your own
   test inputs. Checksum-valid GSTINs/IBANs were generated with the engine's
   own validators (dogfooding), then corrupted for the failure fixtures.
2. **Documents are structured metadata** (type, filename, issued, expires),
   not real PDFs. OCR/PDF extraction is the obvious v2 intake step; the
   validation logic downstream is unchanged by it.
3. **Country pack is small** (IN, US, GB, DE, AE) to make per-country rules
   real rather than mocked. UK/DE VAT modulus checks are format-level in v1.
4. **Sanctions screening** is a static demo list; the rule shape matches what
   a real screening API (e.g. OpenSanctions) would return.
5. **The vendor master** is a simulated record store; bank accounts are stored
   as hash prefixes, never in the clear.
6. **The process is synchronous** per submission. In production, the "request
   more info" step would be async with reminders and a timeout SLA.

---

## 7. What I'd build next (in priority order)

1. **PDF/OCR intake** — accept real invoice-style documents, extract fields, then run this exact pipeline on the extraction (with confidence scores gating auto-vs-manual review).
2. **Real screening + tax-registry APIs** — OpenSanctions for restricted parties, GSTN/VIES lookup so tax IDs are verified at the source, not just checksummed.
3. **Reviewer queue UI** — pending cases land in an AP worklist; reviewer approves/overrides with a reason that becomes part of the audit trail.
4. **Reminders + SLA clocks** — the follow-up letter already exists; v2 tracks vendor replies, sends reminders at day 5/10, and expires the application after 30 days.
5. **Per-country rule packs as config** — the registry pattern already supports it; adding a country should be a config change, not code.
6. **Write-back to the ERP** — approved vendors land in the vendor master automatically (the master file here is the seam for that).
