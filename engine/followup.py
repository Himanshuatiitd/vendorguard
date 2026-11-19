"""Generates the follow-up communication for anything not approved.

PS-2 explicitly requires that anything not approved gets a clear answer about
*what is needed*. This module produces that communication as a markdown
letter, saved next to the run artifacts and rendered in the dashboard.

The letter is written from AP Operations to the vendor contact - ready to be
pasted into email. For rejections it always offers a human appeal path
(human-in-the-loop), which is the compliance-safe pattern.
"""

from __future__ import annotations

PENDING_TITLE = "Action required to complete your vendor onboarding"
REJECTED_TITLE = "Vendor onboarding decision: declined"


def build_letter(sub: dict, result: dict) -> tuple[str, str]:
    """Returns (filename, markdown_text) for the follow-up letter."""
    decision = result["decision"]
    contact = sub.get("contact") or {}
    name = contact.get("name") or "there"
    first = name.split()[0]
    email = contact.get("email") or ""
    issues = result["issues"]

    if decision == "REJECTED":
        title = REJECTED_TITLE
        relevant = [i for i in issues if i["severity"] == "reject"] or issues
        intro = (
            f"Thank you for submitting vendor onboarding for "
            f"{sub.get('legal_name', 'your company')} ({result['submission_id']}). "
            "After automated verification, we are not able to approve this "
            "application. The specific reasons are listed below."
        )
    else:
        title = PENDING_TITLE
        relevant = [i for i in issues if i["severity"] == "pending"]
        intro = (
            f"Thank you for submitting vendor onboarding for "
            f"{sub.get('legal_name', 'your company')} ({result['submission_id']}). "
            f"Verification is underway - we need the {len(relevant)} item(s) "
            "below from you before we can approve you as a vendor."
        )

    items = []
    for n, i in enumerate(relevant, 1):
        items.append(f"{n}. **{i['code'].replace('_', ' ').title()}** - {i['message']}")
        if i.get("action"):
            items.append(f"   *What we need:* {i['action']}")
    body = "\n".join(items) if items else "(no specific items recorded)"

    if decision == "REJECTED":
        closing = (
            "If you believe this decision was made in error, reply to this "
            "email to appeal. A member of the AP team will review your case "
            "manually within 2 business days."
        )
    else:
        closing = (
            "Please reply with the requested items within 10 business days. "
            "Your application stays in *pending* status until then - this is "
            "not a rejection, we simply cannot complete verification with the "
            "current information."
        )

    text = "\n".join([
        f"# {title}",
        "",
        f"**To:** {name} <{email}>",
        "**From:** AP Operations - Vendor Onboarding",
        f"**Re:** Submission {result['submission_id']} - {sub.get('legal_name', '')}",
        (f"**Run:** {result['run_id']} - decision: {decision} - "
         f"risk {result['risk_score']}/100 ({result['risk_band']})"),
        "",
        f"Hi {first},",
        "",
        intro,
        "",
        body,
        "",
        closing,
        "",
        "- AP Operations (automated verification)",
        "",
    ])
    return "followup.md", text
