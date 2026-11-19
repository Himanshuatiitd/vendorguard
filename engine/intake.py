"""Flexible intake for the interactive dashboard.

Turns the three input shapes the dashboard offers into one submission dict:
  * JSON  - a pasted/uploaded object matching submissions/*.json
  * text  - human "key: value" lines, with labels, sections and document rows
  * form  - nested/flat key-value data posted by the dashboard form

The pipeline itself is unchanged: this module only normalises input, it never
decides anything. Anything it cannot map is returned as a warning and ignored.
"""

from __future__ import annotations

import json
import re
from datetime import date

# ---------------------------------------------------------------------------
# Friendly labels -> canonical field paths
# ---------------------------------------------------------------------------
ALIASES = {
    # identity of the submission
    "submission_id": "submission_id",
    "submission": "submission_id",
    "id": "submission_id",
    "label": "label",
    "notes": "label",
    "note": "label",
    "submitted_at": "submitted_at",
    "submission_date": "submitted_at",
    "date": "submitted_at",
    # company
    "legal_name": "legal_name",
    "legal_entity": "legal_name",
    "company": "legal_name",
    "company_name": "legal_name",
    "vendor": "legal_name",
    "vendor_name": "legal_name",
    "name": "legal_name",
    "country": "country",
    "country_code": "country",
    "jurisdiction": "country",
    "address": "registered_address",
    "registered_address": "registered_address",
    "registered_office": "registered_address",
    "tax_id": "tax_id",
    "tax": "tax_id",
    "taxid": "tax_id",
    "tax_number": "tax_id",
    "gst": "tax_id",
    "gstin": "tax_id",
    "gst_number": "tax_id",
    "vat": "tax_id",
    "vat_id": "tax_id",
    "vat_number": "tax_id",
    "ein": "tax_id",
    "trn": "tax_id",
    # contact
    "contact_name": "contact.name",
    "contact_person": "contact.name",
    "representative": "contact.name",
    "email": "contact.email",
    "e_mail": "contact.email",
    "contact_email": "contact.email",
    "phone": "contact.phone",
    "telephone": "contact.phone",
    "mobile": "contact.phone",
    "contact_phone": "contact.phone",
    # banking
    "account_name": "banking.account_name",
    "account_holder": "banking.account_name",
    "bank_account_name": "banking.account_name",
    "beneficiary": "banking.account_name",
    "beneficiary_name": "banking.account_name",
    "account_number": "banking.account_number",
    "bank_account_number": "banking.account_number",
    "bank": "banking.bank_name",
    "bank_name": "banking.bank_name",
    "ifsc": "banking.ifsc",
    "ifsc_code": "banking.ifsc",
    "routing_number": "banking.routing_number",
    "aba": "banking.routing_number",
    "iban": "banking.iban",
    "bank_country": "banking.bank_country",
    "bank_country_code": "banking.bank_country",
    "currency": "banking.currency",
    "settlement_currency": "banking.currency",
}

COUNTRY_NAMES = {
    "india": "IN",
    "ind": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "germany": "DE",
    "deutschland": "DE",
    "uae": "AE",
    "united arab emirates": "AE",
}

DOC_TYPES = ("tax_certificate", "bank_account_letter", "incorporation_certificate")
DOC_FIELDS = {
    "filename": "filename",
    "file": "filename",
    "name": "filename",
    "issued": "issued",
    "issued_on": "issued",
    "issue_date": "issued",
    "expires": "expires",
    "expiry": "expires",
    "expires_on": "expires",
    "expiry_date": "expires",
    "type": "type",
}

SECTIONS = {"contact", "banking", "document", "documents"}


def _norm_key(key: str) -> str:
    """Lowercase, collapse punctuation/whitespace to single underscores."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (key or "").lower())).strip("_")


def _clean(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return value


def _assign_dotted(target: dict, path: str, value) -> None:
    parts = [p for p in path.split(".") if p]
    node = target
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):  # conflicting scalar earlier; overwrite
            node = {}
    if parts:
        node[parts[-1]] = _clean(value)


def _parse_doc_key(norm: str):
    """Return (doc_type, field) for document.* style keys, else None."""
    for prefix in ("documents_", "document_", "doc_"):
        if norm.startswith(prefix):
            rest = norm[len(prefix):]
            for dtype in DOC_TYPES:
                if rest == dtype:
                    return dtype, "type"
                if rest.startswith(dtype + "_"):
                    field = rest[len(dtype) + 1:]
                    return dtype, DOC_FIELDS.get(field, field)
            return None
    return None


def _split_doc_value(value: str) -> list:
    """Split 'type | file | issued | expires' or 'type, file, issued'."""
    if "|" in value:
        return [p.strip() for p in value.split("|")]
    return [p.strip() for p in re.split(r"\s*[,;]\s*", value)]


def _set_document(docs: dict, dtype: str, parts: list, warnings: list) -> None:
    dtype = _norm_key(dtype)
    if dtype not in DOC_TYPES:
        warnings.append(f"Unknown document type '{dtype}' ignored.")
        return
    entry = docs.setdefault(dtype, {"type": dtype})
    for field, value in zip(("filename", "issued", "expires"), parts):
        if value:
            entry[field] = value


def parse_text(text: str) -> tuple[dict, list]:
    """Parse human-readable text into a submission dict.

    Supported shapes (all can be mixed, blank lines and # comments ignored):

        legal_name: Anvi Precision Components Pvt Ltd
        Country = India
        contact.email: rohit@anviprecision.in
        [banking]
        account_number: 50100234567890

        document: tax_certificate | gst.pdf | 2025-04-10 | 2027-08-30
        documents: tax_certificate, bank_account_letter, incorporation_certificate
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("no input text provided")

    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"looks like JSON but could not be parsed: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError("JSON input must be an object")
        return normalise_submission(obj)

    sub: dict = {}
    docs: dict = {}
    warnings: list = []
    section = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        # [section] header
        header = re.match(r"^\[([A-Za-z_ ]+)\]$", line)
        if header:
            section = _norm_key(header.group(1))
            if section not in SECTIONS and section not in DOC_TYPES:
                warnings.append(f"Unknown section '[{header.group(1)}]' ignored.")
                section = ""
            continue

        # split on first ':' or '='
        sep = None
        for i, ch in enumerate(line):
            if ch in ":=":
                sep = i
                break
        if sep is None:
            parts = _split_doc_value(line)
            if parts and _norm_key(parts[0]) in DOC_TYPES:
                _set_document(docs, parts[0], parts[1:], warnings)
            else:
                warnings.append(f"Ignored line without a 'key: value' separator: {line!r}")
            continue

        key, value = line[:sep].strip(), line[sep + 1:].strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            warnings.append(f"Ignored line with an empty field name: {line!r}")
            continue
        if not value:
            continue

        norm = _norm_key(key)

        # section-scoped documents: [document] tax_certificate: file | issued | expires
        if section in ("document", "documents"):
            _set_document(docs, norm, _split_doc_value(value), warnings)
            continue
        if section in DOC_TYPES:
            entry = docs.setdefault(section, {"type": section})
            entry[DOC_FIELDS.get(norm, norm)] = value
            continue

        # explicit document rows
        if norm in ("document", "doc"):
            _set_document(docs, _split_doc_value(value)[0] if _split_doc_value(value) else "",
                          _split_doc_value(value)[1:], warnings)
            continue
        if norm in ("documents", "docs"):
            for item in _split_doc_value(value):
                _set_document(docs, item, [], warnings)
            continue

        doc_key = _parse_doc_key(norm)
        if doc_key:
            dtype, field = doc_key
            if dtype not in docs:
                docs[dtype] = {"type": dtype}
            docs[dtype][field] = value
            continue

        if section in ("contact", "banking"):
            # section gives the prefix; allow aliases that already include it
            target = ALIASES.get(norm)
            if target and target.startswith(section + "."):
                path = target
            else:
                path = f"{section}.{norm}"
        else:
            path = ALIASES.get(norm, norm)

        _assign_dotted(sub, path, value)

    if docs:
        sub["documents"] = list(docs.values())
    return normalise_submission(sub, warnings)


def normalise_submission(raw, warnings: list | None = None) -> tuple[dict, list]:
    """Coerce a raw submission object into the pipeline's canonical shape."""
    warnings = list(warnings or [])
    if not isinstance(raw, dict):
        raise ValueError("submission must be a JSON object")

    sub: dict = {}

    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(key, str) and "." in key:
            _assign_dotted(sub, key, value)
            continue
        if key == "documents":
            continue  # handled below
        if key in ("contact", "banking") and isinstance(value, dict):
            sub[key] = {k: _clean(v) for k, v in value.items() if v is not None}
        elif isinstance(value, dict):
            warnings.append(f"Unexpected nested object '{key}' flattened.")
            sub[key] = {k: _clean(v) for k, v in value.items() if v is not None}
        else:
            sub[key] = _clean(value)

    # documents: list[dict] | dict[type -> details] | list[str] | str
    docs = raw.get("documents")
    doc_list: list = []
    if isinstance(docs, dict):
        for dtype, detail in docs.items():
            entry = {"type": dtype}
            if isinstance(detail, dict):
                entry.update({k: _clean(v) for k, v in detail.items() if v is not None})
            elif detail:
                entry["filename"] = _clean(str(detail))
            doc_list.append(entry)
    elif isinstance(docs, str):
        for item in _split_doc_value(docs):
            if item:
                doc_list.append({"type": item})
    elif isinstance(docs, list):
        for item in docs:
            if isinstance(item, dict) and item.get("type"):
                entry = {k: _clean(v) for k, v in item.items() if v is not None}
                if "file" in entry and "filename" not in entry:
                    entry["filename"] = entry.pop("file")
                doc_list.append(entry)
            elif isinstance(item, str) and item.strip():
                doc_list.append({"type": item.strip()})
    if doc_list:
        sub["documents"] = doc_list

    # tidy well-known scalars
    if sub.get("country"):
        raw_country = str(sub["country"]).strip()
        sub["country"] = COUNTRY_NAMES.get(raw_country.lower(), raw_country.upper())
    for path in ("tax_id", "banking.bank_country", "banking.currency",
                 "banking.ifsc", "banking.iban", "banking.routing_number"):
        node = sub
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.get(p) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and isinstance(node.get(parts[-1]), str):
            node[parts[-1]] = node[parts[-1]].strip().upper()

    if isinstance(sub.get("contact"), dict):
        sub["contact"] = {k: v for k, v in sub["contact"].items() if v not in ("", None)}
    if isinstance(sub.get("banking"), dict):
        sub["banking"] = {k: v for k, v in sub["banking"].items() if v not in ("", None)}

    return sub, warnings


def default_metadata(sub: dict, prefix: str = "WEB") -> dict:
    """Fill in the audit-trail metadata the pipeline expects."""
    import uuid

    sub = dict(sub)
    if not sub.get("submission_id"):
        sub["submission_id"] = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
    sub.setdefault("label", "submitted from interactive dashboard")
    sub.setdefault("submitted_at", date.today().isoformat())
    return sub
