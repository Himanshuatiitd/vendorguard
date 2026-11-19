"""Validation rules for vendor onboarding (PS-2 - Vendor Onboarding).

Every rule is a small, declarative function that returns a list of Issue
objects. Rules are grouped by pipeline stage so the orchestrator can run
them stage by stage and surface findings live.

Severity vocabulary (see models.Issue):
  reject  - fraud signal, duplicate, or restricted party: hard stop
  pending - vendor can fix it: missing data, bad format, expired document
  info    - audit trail only

Design note: rules know nothing about each other and never mutate the
submission. That keeps the decision explainable: every issue maps to exactly
one rule, every rule maps to one line in the traceability matrix in
ANALYSIS.md.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .models import Issue

# ---------------------------------------------------------------------------
# Country registry - the per-country rule pack. In production this would live
# in configuration and be much larger; five countries are enough to show the
# pattern (and enough to make the validators real rather than mocked).
# ---------------------------------------------------------------------------
COUNTRIES = {
    "IN": {"name": "India", "tax_id_type": "GSTIN", "currency": "INR", "iban": False},
    "US": {"name": "United States", "tax_id_type": "EIN", "currency": "USD", "iban": False},
    "GB": {"name": "United Kingdom", "tax_id_type": "VAT", "currency": "GBP", "iban": True},
    "DE": {"name": "Germany", "tax_id_type": "VAT", "currency": "EUR", "iban": True},
    "AE": {"name": "United Arab Emirates", "tax_id_type": "TRN", "currency": "AED", "iban": True},
}

# Demo restricted-party list. In production this is a real screening API
# (e.g. OpenSanctions) - the rule shape stays identical.
RESTRICTED_COUNTRIES = {"IR", "KP", "SY"}

# Which banking sort-code field each country requires.
BANK_CODE_FIELD = {"IN": "ifsc", "US": "routing_number", "GB": "iban", "DE": "iban", "AE": "iban"}

REQUIRED_DOCS = {
    "tax_certificate": "tax registration certificate",
    "bank_account_letter": "bank-issued account confirmation letter",
    "incorporation_certificate": "certificate of incorporation",
}

# ---------------------------------------------------------------------------
# Tax ID validators
# ---------------------------------------------------------------------------
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Valid Indian GST state codes (first two digits of a GSTIN).
GST_STATE_CODES = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12",
    "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24",
    "25", "26", "27", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38",
}

EIN_RE = re.compile(r"^\d{2}-\d{7}$")
VAT_RE = re.compile(r"^[A-Z]{2}\d{9}$")
TRN_RE = re.compile(r"^\d{15}$")

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

LEGAL_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "llp", "llc", "inc", "gmbh", "co",
    "company", "corp", "corporation", "ag", "bv", "plc", "kg", "ug",
}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def name_tokens(s: str) -> list:
    return [t for t in norm_text(s).split() if t not in LEGAL_SUFFIXES]


def name_ratio(a: str, b: str) -> float:
    """Similarity between two entity names, tolerant of legal suffixes.

    Takes the better of the raw ratio and the suffix-stripped ratio, because
    'Acme Pvt Ltd' vs 'Acme Limited' is the same company, while
    'Acme Logistics' vs 'Acme Holdings' may not be.
    """
    from difflib import SequenceMatcher

    raw = SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()
    na, nb = " ".join(name_tokens(a)), " ".join(name_tokens(b))
    norm = SequenceMatcher(None, na, nb).ratio() if na and nb else raw
    return max(raw, norm)


def account_ref(account_number: str) -> str:
    """Stable fingerprint of a bank account number (for duplicate detection).

    Production would use tokenised storage; a hash prefix is enough to show
    the pattern without ever persisting the raw number in the master file.
    """
    digits = re.sub(r"\D", "", account_number or "")
    return hashlib.sha256(digits.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Checksum validators (the interesting ones)
# ---------------------------------------------------------------------------
def gstin_checksum_char(first14: str) -> str:
    """Mod-36 check digit used by Indian GSTINs.

    The algorithm: alternate characters are doubled (factor 2 starting at the
    first character), each product is 'horizontally added' in base 36, and the
    check digit is (36 - sum mod 36) mod 36.
    """
    total = 0
    for i, ch in enumerate(first14.upper()):
        val = GSTIN_ALPHABET.index(ch)
        if i % 2 == 1:
            val *= 2
        total += (val // 36) + (val % 36)
    return GSTIN_ALPHABET[(36 - total % 36) % 36]


def gstin_valid(gstin: str) -> bool:
    gstin = (gstin or "").upper().strip()
    return (
        bool(GSTIN_RE.match(gstin))
        and gstin[:2] in GST_STATE_CODES
        and gstin_checksum_char(gstin[:14]) == gstin[14]
    )


def iban_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check (works for any IBAN country)."""
    s = re.sub(r"\s+", "", (iban or "")).upper()
    if not IBAN_RE.match(s):
        return False
    rearranged = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in rearranged)
    return int(num) % 97 == 1


def aba_valid(routing: str) -> bool:
    """ABA routing number check digit: 3(d1+d4+d7)+7(d2+d5+d8)+(d3+d6+d9) mod 10 == 0."""
    r = re.sub(r"\D", "", routing or "")
    if len(r) != 9:
        return False
    d = [int(c) for c in r]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


# ---------------------------------------------------------------------------
# Stage 2: COMPLETENESS - is everything present?
# ---------------------------------------------------------------------------
def check_completeness(sub: dict) -> list:
    issues = []

    def miss(path: str, label: str):
        issues.append(Issue(
            "MISSING_FIELD", "pending", "COMPLETENESS",
            f"Required field '{path}' is missing.",
            f"Provide the {label} ('{path}').",
        ))

    for key in ("legal_name", "country", "tax_id"):
        if not sub.get(key):
            miss(key, key.replace("_", " "))

    contact = sub.get("contact") or {}
    for key in ("name", "email", "phone"):
        if not contact.get(key):
            miss(f"contact.{key}", f"contact {key}")

    banking = sub.get("banking") or {}
    for key in ("account_name", "account_number", "bank_name", "bank_country", "currency"):
        if not banking.get(key):
            miss(f"banking.{key}", f"banking {key.replace('_', ' ')}")

    country = (sub.get("country") or "").upper()
    code_field = BANK_CODE_FIELD.get(country)
    if country in COUNTRIES and code_field and not banking.get(code_field):
        miss(f"banking.{code_field}", f"bank {code_field.replace('_', ' ')} "
                                      f"required for vendors in {COUNTRIES[country]['name']}")

    docs = {d.get("type"): d for d in sub.get("documents") or [] if isinstance(d, dict)}
    for dtype, label in REQUIRED_DOCS.items():
        if dtype not in docs:
            issues.append(Issue(
                "MISSING_DOCUMENT", "pending", "COMPLETENESS",
                f"Required document '{dtype}' ({label}) was not attached.",
                f"Upload the {label}.",
            ))
    return issues


# ---------------------------------------------------------------------------
# Stage 3: FORMAT - is everything well-formed and internally valid?
# ---------------------------------------------------------------------------
def check_formats(sub: dict) -> list:
    issues = []
    country = (sub.get("country") or "").upper()
    tax_id = (sub.get("tax_id") or "").upper().strip()

    if country and country not in COUNTRIES:
        issues.append(Issue(
            "UNSUPPORTED_COUNTRY", "pending", "FORMAT",
            f"Country code '{country}' is not in the supported registry; tax "
            "and banking validation rules are unknown for it.",
            "Confirm the two-letter ISO country code.",
        ))

    if tax_id and country in COUNTRIES:
        issues += _tax_id_issues(country, tax_id)

    banking = sub.get("banking") or {}
    issues += _banking_code_issues(banking, country)

    contact = sub.get("contact") or {}
    email = (contact.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        issues.append(Issue(
            "INVALID_EMAIL", "pending", "FORMAT",
            f"'{email}' is not a valid email address.",
            "Provide a working contact email.",
        ))

    phone_digits = re.sub(r"\D", "", contact.get("phone") or "")
    if contact.get("phone") and len(phone_digits) < 8:
        issues.append(Issue(
            "INVALID_PHONE", "pending", "FORMAT",
            "Contact phone number looks too short to be dialable.",
            "Provide a full phone number including country code.",
        ))

    acct = re.sub(r"[\s-]", "", banking.get("account_number") or "")
    if acct and not re.match(r"^[A-Za-z0-9]{6,34}$", acct):
        issues.append(Issue(
            "INVALID_ACCOUNT_NUMBER", "pending", "FORMAT",
            "Bank account number is malformed (expected 6-34 letters/digits).",
            "Check the account number with the vendor's bank.",
        ))

    issues += _document_issues(sub)
    return issues


def _tax_id_issues(country: str, tax_id: str) -> list:
    out = []
    if country == "IN":
        if not GSTIN_RE.match(tax_id):
            out.append(Issue(
                "TAX_ID_INVALID", "pending", "FORMAT",
                f"Tax ID '{tax_id}' does not match GSTIN format (15 chars: "
                "state code + PAN + entity code + 'Z' + check digit).",
                "Upload a legible GST registration certificate and correct the tax ID.",
            ))
        elif tax_id[:2] not in GST_STATE_CODES:
            out.append(Issue(
                "TAX_ID_STATE_UNKNOWN", "pending", "FORMAT",
                f"GSTIN state code '{tax_id[:2]}' is not a valid Indian state code.",
                "Verify the first two digits of the GSTIN.",
            ))
        elif gstin_checksum_char(tax_id[:14]) != tax_id[14]:
            out.append(Issue(
                "TAX_ID_CHECKSUM_FAIL", "pending", "FORMAT",
                f"GSTIN '{tax_id}' passes the format check but fails its "
                "mod-36 check digit - the number is mistyped or fabricated.",
                "Re-check the GSTIN against the GST certificate and re-upload "
                "the corrected certificate.",
            ))
    elif country == "US":
        if not EIN_RE.match(tax_id):
            out.append(Issue(
                "TAX_ID_INVALID", "pending", "FORMAT",
                f"Tax ID '{tax_id}' does not match US EIN format (XX-XXXXXXX).",
                "Provide the IRS-issued EIN in the correct format.",
            ))
    elif country in ("GB", "DE"):
        # Format-level check only; the UK/DE modulus checks are a v2 item
        # (documented in ANALYSIS.md - honest about simplifications).
        if not VAT_RE.match(tax_id) or not tax_id.startswith(country):
            out.append(Issue(
                "TAX_ID_INVALID", "pending", "FORMAT",
                f"Tax ID '{tax_id}' does not match {country} VAT format "
                f"({country} + 9 digits).",
                f"Provide a valid {COUNTRIES[country]['name']} VAT registration number.",
            ))
    elif country == "AE":
        if not TRN_RE.match(tax_id):
            out.append(Issue(
                "TAX_ID_INVALID", "pending", "FORMAT",
                f"Tax ID '{tax_id}' does not match UAE TRN format (15 digits).",
                "Provide the 15-digit Federal Tax Authority registration number.",
            ))
    return out


def _banking_code_issues(banking: dict, country: str) -> list:
    out = []
    if country == "IN":
        ifsc = (banking.get("ifsc") or "").upper().strip()
        if ifsc and not IFSC_RE.match(ifsc):
            out.append(Issue(
                "INVALID_IFSC", "pending", "FORMAT",
                f"IFSC code '{ifsc}' is malformed (expected e.g. HDFC0000123).",
                "Confirm the IFSC with the vendor's bank.",
            ))
    elif country == "US":
        r = re.sub(r"\s", "", banking.get("routing_number") or "")
        if r and not aba_valid(r):
            out.append(Issue(
                "INVALID_ROUTING_NUMBER", "pending", "FORMAT",
                f"ABA routing number '{r}' fails its check-digit test.",
                "Confirm the 9-digit ABA routing number with the vendor's bank.",
            ))
    if country in ("GB", "DE", "AE"):
        iban = re.sub(r"\s+", "", (banking.get("iban") or "").upper())
        if iban and not iban_valid(iban):
            out.append(Issue(
                "INVALID_IBAN", "pending", "FORMAT",
                f"IBAN '{iban}' fails the ISO 13616 mod-97 checksum.",
                "Confirm the IBAN with the vendor's bank.",
            ))
    return out


def _document_issues(sub: dict) -> list:
    out = []
    today = datetime.now(timezone.utc).date()
    for doc in sub.get("documents") or []:
        dtype = doc.get("type") or "(untyped)"
        if dtype not in REQUIRED_DOCS and doc.get("filename"):
            out.append(Issue(
                "UNRECOGNIZED_DOCUMENT", "info", "FORMAT",
                f"Document '{dtype}' is not a required type; stored for the audit trail.",
                "",
            ))
        exp = doc.get("expires")
        if exp:
            try:
                expired = datetime.strptime(exp, "%Y-%m-%d").date() < today
            except ValueError:
                expired = None
            if expired is None:
                out.append(Issue(
                    "INVALID_DATE", "pending", "FORMAT",
                    f"Document '{dtype}' has an unreadable expiry date '{exp}' "
                    "(expected YYYY-MM-DD).",
                    "Re-upload with a legible expiry date.",
                ))
            elif expired:
                out.append(Issue(
                    "DOC_EXPIRED", "pending", "FORMAT",
                    f"Document '{dtype}' expired on {exp}.",
                    f"Upload a current {REQUIRED_DOCS.get(dtype, dtype)}.",
                ))
    return out


# ---------------------------------------------------------------------------
# Stage 4: CONSISTENCY - do the fields agree with each other?
# This is where PS-2's "subtle inconsistencies that only become visible when
# you cross-reference the fields" live.
# ---------------------------------------------------------------------------
def check_consistency(sub: dict) -> list:
    issues = []
    legal = sub.get("legal_name") or ""
    country = (sub.get("country") or "").upper()
    tax_id = (sub.get("tax_id") or "").upper().strip()
    banking = sub.get("banking") or {}
    account_name = banking.get("account_name") or ""

    if legal and account_name:
        r = name_ratio(legal, account_name)
        if r < 0.50:
            issues.append(Issue(
                "NAME_MISMATCH_HARD", "reject", "CONSISTENCY",
                f"Bank account holder '{account_name}' does not resemble the "
                f"legal entity '{legal}' (match {r:.0%}). This is a fraud indicator.",
                "",
            ))
        elif r < 0.85:
            issues.append(Issue(
                "NAME_MISMATCH_SUSPECTED", "pending", "CONSISTENCY",
                f"Bank account holder '{account_name}' only partially matches "
                f"the legal entity '{legal}' (match {r:.0%}).",
                "Provide a bank-issued letter confirming the account is held "
                "by the legal entity, or correct the account name.",
            ))

    bank_country = (banking.get("bank_country") or "").upper()
    if country and bank_country and bank_country != country:
        issues.append(Issue(
            "BANK_COUNTRY_MISMATCH", "pending", "CONSISTENCY",
            f"Bank account is held in '{bank_country}' but the vendor claims "
            f"to be established in '{country}'.",
            "Confirm where the settlement account is held; if it differs from "
            "the country of registration, provide a reason.",
        ))

    c = COUNTRIES.get(country)
    currency = (banking.get("currency") or "").upper()
    if c and currency and currency != c["currency"]:
        issues.append(Issue(
            "CURRENCY_MISMATCH", "pending", "CONSISTENCY",
            f"Settlement currency '{currency}' is unusual for a vendor "
            f"registered in {c['name']} (expected {c['currency']}).",
            f"Confirm the settlement currency; expected {c['currency']} for "
            f"{c['name']}.",
        ))

    if country in ("GB", "DE") and len(tax_id) >= 2 and tax_id[:2] not in ("", country):
        issues.append(Issue(
            "TAX_ID_COUNTRY_MISMATCH", "pending", "CONSISTENCY",
            f"Tax ID prefix '{tax_id[:2]}' does not match the claimed "
            f"country '{country}'.",
            "Verify the tax registration country against the tax certificate.",
        ))
    return issues


# ---------------------------------------------------------------------------
# Stage 5: IDENTITY - is this vendor who they say they are?
# ---------------------------------------------------------------------------
def check_identity(sub: dict, master: dict) -> list:
    issues = []
    legal = sub.get("legal_name") or ""
    country = (sub.get("country") or "").upper()
    tax_norm = re.sub(r"\s+", "", (sub.get("tax_id") or "").upper())
    acct = account_ref((sub.get("banking") or {}).get("account_number") or "")

    if country in RESTRICTED_COUNTRIES:
        issues.append(Issue(
            "RESTRICTED_COUNTRY", "reject", "IDENTITY",
            f"Vendor's country '{country}' is on the restricted-party list. "
            "Onboarding is blocked pending compliance review.",
            "",
        ))

    for v in master.get("vendors", []):
        v_tax = re.sub(r"\s+", "", (v.get("tax_id") or "").upper())
        if tax_norm and v_tax and tax_norm == v_tax:
            issues.append(Issue(
                "DUPLICATE_TAX_ID", "reject", "IDENTITY",
                f"Tax ID already registered to approved vendor "
                f"{v.get('vendor_id', '?')} ({v.get('legal_name', '?')}). "
                "A different legal name claiming the same tax registration "
                "is a fraud pattern.",
                "",
            ))
        if acct and v.get("bank_account_ref") and acct == v["bank_account_ref"]:
            issues.append(Issue(
                "DUPLICATE_BANK_ACCOUNT", "reject", "IDENTITY",
                f"Bank account already registered to approved vendor "
                f"{v.get('vendor_id', '?')} ({v.get('legal_name', '?')}). "
                "Payments to this account would duplicate an existing vendor.",
                "",
            ))
        if (country and v.get("country") == country
                and legal and v.get("legal_name")
                and name_ratio(legal, v["legal_name"]) >= 0.92):
            issues.append(Issue(
                "POSSIBLE_DUPLICATE", "pending", "IDENTITY",
                f"Name closely resembles approved vendor "
                f"{v.get('vendor_id', '?')} ({v.get('legal_name', '?')}) - "
                "possible re-registration.",
                "AP to confirm whether this is a new entity or an amendment "
                "to the existing vendor record.",
            ))
    return issues
