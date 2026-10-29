"""Push a new tour request to the realtor's phone through ntfy: one HTTPS POST to <server>/<topic>.

The topic works like a password (anyone who knows it can subscribe), so it never appears in an error, a log
line, a lead record or a response. Errors are reduced to the HTTP status or the exception class.
"""
from __future__ import annotations

import base64
import re

import httpx

from .state import Lead

TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def header_value(text: str) -> str:
    """HTTP headers carry Latin-1 at most; ntfy decodes RFC 2047 encoded words, so a buyer named José keeps
    the accent in the notification title."""
    text = " ".join(text.split())
    if text.isascii():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def lead_message(lead: Lead, address: str) -> tuple[str, str]:
    """(title, body) for a lead. The buyer's contact stays out: ntfy.sh is a third party, and the click URL
    opens the lead, contact included, behind the passcode."""
    title = f"Tour request: {lead.name}"
    lines = [f"{address}", f"Wants: {lead.preferred_time}"]
    open_q = [q for q in lead.questions if q.status == "follow_up"]
    if open_q:
        lines.append(f"{len(open_q)} question{'s' if len(open_q) > 1 else ''} need your answer, e.g. “{open_q[0].question}”")
    elif lead.questions:
        lines.append(f"Asked {len(lead.questions)} question{'s' if len(lead.questions) > 1 else ''} on the listing page")
    return title, "\n".join(lines)


def send(topic: str, title: str, body: str, click: str | None, *, server: str = "https://ntfy.sh",
         client: httpx.Client | None = None) -> tuple[bool, str | None]:
    """POST the notification. Returns (sent, plain error or None)."""
    if not TOPIC_RE.match(topic):
        return False, "NTFY_TOPIC has characters ntfy does not accept (letters, digits, '-' and '_' only)."
    headers = {"Title": header_value(title), "Tags": "house", "Priority": "high"}
    if click:
        headers["Click"] = click
    try:
        if client is not None:
            r = client.post(f"{server.rstrip('/')}/{topic}", content=body.encode("utf-8"), headers=headers)
        else:
            r = httpx.post(f"{server.rstrip('/')}/{topic}", content=body.encode("utf-8"), headers=headers, timeout=10)
    except httpx.HTTPError as e:
        return False, f"Could not reach ntfy ({type(e).__name__})."
    if r.status_code >= 300:
        return False, f"ntfy answered HTTP {r.status_code}."
    return True, None
