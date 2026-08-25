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


def _create_gmail_label(root: str, user: str, name: str) -> dict:
    """Create a Gmail label via POST users/<user>/labels."""
    url = f"{root}users/{user}/labels"
    return _http_json("POST", url, {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    })


def _create_calendar(root: str, name: str) -> dict:
    """Create a secondary calendar via POST /calendars."""
    url = f"{root}calendars"
    return _http_json("POST", url, {"summary": name})


def _post_one_event(
    root: str, calendar_id: str, summary: str, description: str,
    start: str, end: str,
) -> dict:
    """POST a single event. Returns the API response dict."""
    url = f"{root}calendars/{calendar_id}/events"
    return _http_json(
        "POST",
        url,
        {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        },
    )


def _default_start_end(
    event: dict, offset_days: int = 1,
) -> tuple[str, str]:
    """Return (start, end) ISO strings from an event dict or defaults."""
    start = event.get("start") or (
        datetime.now(timezone.utc) + timedelta(days=offset_days)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end = event.get("end") or (
        datetime.now(timezone.utc) + timedelta(days=offset_days, hours=1)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return start, end


def _seed_calendar(seed: dict) -> dict[str, Any]:
    root = _gcal_root()

    # Phase 1: create secondary calendars if requested
    create_calendars = seed.get("create_calendars") or []
    created_calendars: dict[str, str] = {}
    for cal_name in create_calendars:
        cal_data = _create_calendar(root, cal_name)
        created_calendars[cal_name] = cal_data.get("id", "")
        logger.info(
            "smolclaw calendar created: name=%r id=%s",
            cal_name, created_calendars[cal_name],
        )

    calendar_id = (seed.get("calendar_id") or "primary").strip()

    # Phase 2: seed events
    events_list = seed.get("events")
    if events_list and isinstance(events_list, list):
        event_ids: list[str] = []
        for i, ev in enumerate(events_list):
            s = (ev.get("summary") or "").strip()
            if not s:
                raise ValueError("each event in smolclaw_seed.events needs a summary")
            d = (ev.get("description") or "").strip()
            start, end = _default_start_end(ev, offset_days=1 + i)
            posted = _post_one_event(root, calendar_id, s, d, start, end)
            event_ids.append(posted.get("id", ""))
            logger.info("smolclaw calendar batch seed: id=%s summary=%r", event_ids[-1], s[:80])

        result: dict[str, Any] = {
            "ok": True,
            "kind": "calendar",
            "calendar_id": calendar_id,
            "event_id": event_ids[-1],
            "event_ids": event_ids,
            "api_root": root,
        }
        if created_calendars:
            result["created_calendars"] = created_calendars
        return result

    summary = (seed.get("summary") or "").strip()
    if not summary:
        if created_calendars:
            return {
                "ok": True,
                "kind": "calendar",
                "api_root": root,
                "created_calendars": created_calendars,
            }
        raise ValueError("smolclaw_seed.summary is required for kind=calendar")

    description = (seed.get("description") or "").strip()
    start, end = _default_start_end(seed)

    posted = _post_one_event(root, calendar_id, summary, description, start, end)
    result = {
        "ok": True,
        "kind": "calendar",
        "calendar_id": calendar_id,
        "event_id": posted.get("id"),
        "summary": summary,
        "api_root": root,
    }
    if created_calendars:
        result["created_calendars"] = created_calendars
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
    extra_label_ids: Optional[list[str]] = None,
) -> dict:
    """Import a single message into the mock. Returns the API response dict."""
    label_ids = ["INBOX", "UNREAD"]
    if extra_label_ids:
        label_ids.extend(extra_label_ids)
    payload: dict[str, Any] = {"raw": raw, "labelIds": label_ids}
    if thread_id:
        payload["threadId"] = thread_id
    try:
        return _http_json("POST", f"{root}users/{user}/messages/import", payload)
    except RuntimeError:
        return _http_json("POST", f"{root}users/{user}/messages/send", payload)


def _seed_gmail(seed: dict) -> dict[str, Any]:
    root = _gmail_root()
    user = (seed.get("user") or "me").strip()

    # Phase 1: create custom labels if requested
    create_labels = seed.get("create_labels") or []
    created_labels: dict[str, str] = {}
    for label_name in create_labels:
        label_data = _create_gmail_label(root, user, label_name)
        created_labels[label_name] = label_data.get("id", "")
        logger.info(
            "smolclaw gmail label created: name=%r id=%s",
            label_name, created_labels[label_name],
        )

    # Resolve extra label IDs to attach to seeded messages
    extra_label_ids: list[str] = []
    seed_to_label = seed.get("seed_to_label")
    if seed_to_label and seed_to_label in created_labels:
        extra_label_ids.append(created_labels[seed_to_label])

    # Phase 2: seed messages
    messages = seed.get("messages")
    if messages and isinstance(messages, list):
        threaded = seed.get("threaded", True)
        result = _seed_gmail_thread(
            seed, messages, threaded=threaded, extra_label_ids=extra_label_ids,
        )
        if created_labels:
            result["created_labels"] = created_labels
        return result

    subject = (seed.get("subject") or "").strip()
    if not subject:
        if created_labels:
            return {
                "ok": True,
                "kind": "gmail",
                "api_root": root,
                "created_labels": created_labels,
            }
        raise ValueError("smolclaw_seed.subject is required for kind=gmail")

    body = (seed.get("body") or "").strip()
    to = (seed.get("to") or "me").strip()
    frm = (seed.get("from") or "aeh-seed@example.com").strip()

    raw = _rfc822_raw(subject=subject, body=body or subject, to=to, frm=frm)
    posted = _import_one_gmail(root, user, raw, extra_label_ids=extra_label_ids)

    result = {
        "ok": True,
        "kind": "gmail",
        "message_id": posted.get("id"),
        "thread_id": posted.get("threadId"),
        "subject": subject,
        "api_root": root,
    }
    if created_labels:
        result["created_labels"] = created_labels
    logger.info(
        "smolclaw gmail seed: id=%s subject=%r",
        result["message_id"],
        subject[:80],
    )
    return result


def _seed_gmail_thread(
    seed: dict,
    messages: list[dict],
    *,
    threaded: bool = True,
    extra_label_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Seed multiple messages, optionally as a single thread."""
    if not messages:
        raise ValueError("smolclaw_seed.messages list is empty")
    user = (seed.get("user") or "me").strip()
    root = _gmail_root()
    thread_id: Optional[str] = None
    message_ids: list[str] = []
    mode = "thread" if threaded else "batch"

    for msg in messages:
        frm = (msg.get("from") or "aeh-seed@example.com").strip()
        to = (msg.get("to") or "me").strip()
        subject = (msg.get("subject") or "").strip()
        body = (msg.get("body") or subject).strip()
        if not subject:
            raise ValueError("each message in smolclaw_seed.messages needs a subject")
        raw = _rfc822_raw(subject=subject, body=body, to=to, frm=frm)
        posted = _import_one_gmail(
            root, user, raw,
            thread_id=thread_id if threaded else None,
            extra_label_ids=extra_label_ids,
        )
        mid = posted.get("id") or ""
        message_ids.append(mid)
        if threaded and thread_id is None:
            thread_id = posted.get("threadId")
        logger.info("smolclaw gmail %s seed: id=%s subject=%r", mode, mid, subject[:80])

    result: dict[str, Any] = {
        "ok": True,
        "kind": "gmail",
        "message_id": message_ids[-1],
        "message_ids": message_ids,
        "thread_id": thread_id,
        "subject": messages[-1].get("subject", ""),
        "api_root": root,
    }
    logger.info(
        "smolclaw gmail %s seed complete: %d messages",
        mode,
        len(message_ids),
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
