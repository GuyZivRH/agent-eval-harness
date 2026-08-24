"""Host-side smolclaw (Gmail / Calendar) seeding for OpenShell cases.

Cases declare ``annotations.smolclaw_seed`` with ``kind: gmail|calendar``.
AEH posts the needle via the host loopback API before the agent runs.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _gmail_root() -> str:
    explicit = os.environ.get("SMOLCLAW_GMAIL_URL") or os.environ.get("GMAIL_API_URL_HOST")
    if explicit:
        return explicit if explicit.endswith("/") else explicit + "/"
    return "http://127.0.0.1:8001/gmail/v1/"


def _gcal_root() -> str:
    explicit = os.environ.get("SMOLCLAW_GCAL_URL") or os.environ.get("CALENDAR_API_URL_HOST")
    if explicit:
        return explicit if explicit.endswith("/") else explicit + "/"
    return "http://127.0.0.1:8002/calendar/v3/"


def _http_json(method: str, url: str, body: Optional[dict] = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}") from e


def _seed_calendar(seed: dict) -> dict[str, Any]:
    summary = (seed.get("summary") or "").strip()
    description = (seed.get("description") or "").strip()
    if not summary:
        raise ValueError("smolclaw_seed.summary is required for kind=calendar")
    calendar_id = (seed.get("calendar_id") or "primary").strip()
    start = seed.get("start") or (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end = seed.get("end") or (
        datetime.now(timezone.utc) + timedelta(days=1, hours=1)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    root = _gcal_root()
    url = f"{root}calendars/{calendar_id}/events"
    posted = _http_json(
        "POST",
        url,
        {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        },
    )
    result = {
        "ok": True,
        "kind": "calendar",
        "calendar_id": calendar_id,
        "event_id": posted.get("id"),
        "summary": summary,
        "api_root": root,
    }
    logger.info(
        "smolclaw calendar seed: id=%s summary=%r",
        result["event_id"],
        summary[:80],
    )
    return result


def _rfc822_raw(*, subject: str, body: str, to: str, frm: str) -> str:
    msg = (
        f"From: {frm}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}\r\n"
    )
    return base64.urlsafe_b64encode(msg.encode()).decode().rstrip("=")


def _import_one_gmail(
    root: str, user: str, raw: str, *, thread_id: Optional[str] = None,
) -> dict:
    """Import a single message into the mock. Returns the API response dict."""
    payload: dict[str, Any] = {"raw": raw, "labelIds": ["INBOX", "UNREAD"]}
    if thread_id:
        payload["threadId"] = thread_id
    try:
        return _http_json("POST", f"{root}users/{user}/messages/import", payload)
    except RuntimeError:
        return _http_json("POST", f"{root}users/{user}/messages/send", payload)


def _seed_gmail(seed: dict) -> dict[str, Any]:
    messages = seed.get("messages")
    if messages and isinstance(messages, list):
        return _seed_gmail_thread(seed, messages)

    subject = (seed.get("subject") or "").strip()
    body = (seed.get("body") or "").strip()
    if not subject:
        raise ValueError("smolclaw_seed.subject is required for kind=gmail")
    to = (seed.get("to") or "me").strip()
    frm = (seed.get("from") or "aeh-seed@example.com").strip()
    user = (seed.get("user") or "me").strip()

    root = _gmail_root()
    raw = _rfc822_raw(subject=subject, body=body or subject, to=to, frm=frm)
    posted = _import_one_gmail(root, user, raw)

    result = {
        "ok": True,
        "kind": "gmail",
        "message_id": posted.get("id"),
        "thread_id": posted.get("threadId"),
        "subject": subject,
        "api_root": root,
    }
    logger.info(
        "smolclaw gmail seed: id=%s subject=%r",
        result["message_id"],
        subject[:80],
    )
    return result


def _seed_gmail_thread(seed: dict, messages: list[dict]) -> dict[str, Any]:
    """Seed multiple messages as a single thread."""
    if not messages:
        raise ValueError("smolclaw_seed.messages list is empty")
    user = (seed.get("user") or "me").strip()
    root = _gmail_root()
    thread_id: Optional[str] = None
    message_ids: list[str] = []

    for msg in messages:
        frm = (msg.get("from") or "aeh-seed@example.com").strip()
        to = (msg.get("to") or "me").strip()
        subject = (msg.get("subject") or "").strip()
        body = (msg.get("body") or subject).strip()
        if not subject:
            raise ValueError("each message in smolclaw_seed.messages needs a subject")
        raw = _rfc822_raw(subject=subject, body=body, to=to, frm=frm)
        posted = _import_one_gmail(root, user, raw, thread_id=thread_id)
        mid = posted.get("id") or ""
        message_ids.append(mid)
        if thread_id is None:
            thread_id = posted.get("threadId")
        logger.info("smolclaw gmail thread seed: id=%s subject=%r", mid, subject[:80])

    result: dict[str, Any] = {
        "ok": True,
        "kind": "gmail",
        "message_id": message_ids[-1],
        "message_ids": message_ids,
        "thread_id": thread_id,
        "subject": messages[0].get("subject", ""),
        "api_root": root,
    }
    logger.info(
        "smolclaw gmail thread seed complete: %d messages, thread=%s",
        len(message_ids),
        thread_id,
    )
    return result


def seed_smolclaw_for_case(annotations: dict) -> Optional[dict[str, Any]]:
    """Post annotations.smolclaw_seed. Return metadata or None."""
    seed = (annotations or {}).get("smolclaw_seed")
    if not seed:
        return None
    kind = (seed.get("kind") or "").strip().lower()
    if kind == "calendar":
        return _seed_calendar(seed)
    if kind == "gmail":
        return _seed_gmail(seed)
    raise ValueError(f"smolclaw_seed.kind must be gmail|calendar, got {kind!r}")
