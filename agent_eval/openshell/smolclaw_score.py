"""Score smolclaw Gmail / Calendar state for AEH judges.

Judges query the host loopback mock APIs for markers the agent must have
written (created calendar events or sent/imported mail).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple, Union


def _gmail_root() -> str:
    return (
        os.environ.get("SMOLCLAW_GMAIL_URL")
        or os.environ.get("GMAIL_API_URL_HOST")
        or "http://127.0.0.1:8001/gmail/v1/"
    ).rstrip("/") + "/"


def _gcal_root() -> str:
    return (
        os.environ.get("SMOLCLAW_GCAL_URL")
        or os.environ.get("CALENDAR_API_URL_HOST")
        or "http://127.0.0.1:8002/calendar/v3/"
    ).rstrip("/") + "/"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"GET {url} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GET {url} failed: {e}") from e


def _write_hits(outputs: dict, name: str, hits: list) -> None:
    case_dir = outputs.get("case_dir")
    if not (case_dir and hits):
        return
    out_dir = Path(case_dir) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(
        "\n".join(json.dumps(h, ensure_ascii=False) for h in hits) + "\n",
        encoding="utf-8",
    )


def score_calendar_event_with_code(
    outputs: Optional[dict] = None,
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """True when a calendar event contains expected_code and expected_text."""
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}
    code = (ann.get("expected_code") or "").strip()
    marker = (ann.get("expected_text") or ann.get("marker") or "").strip()
    if not code:
        return False, "annotations.expected_code is empty"
    if not marker:
        return False, "annotations.expected_text is empty"

    calendar_id = (
        ((ann.get("smolclaw_seed") or {}).get("calendar_id")) or "primary"
    )
    q = urllib.parse.quote(marker)
    url = f"{_gcal_root()}calendars/{calendar_id}/events?q={q}&maxResults=50"
    try:
        data = _get_json(url)
    except RuntimeError as e:
        return False, str(e)

    hits = []
    for item in data.get("items") or []:
        blob = " ".join(
            str(item.get(k) or "") for k in ("summary", "description", "location")
        )
        if code in blob and marker in blob:
            hits.append(item)

    _write_hits(outputs, "smolclaw-calendar-hits.jsonl", hits)
    if hits:
        return (
            True,
            f"calendar event with code={code!r} and marker ({len(hits)} hit(s))",
        )
    return (
        False,
        f"no calendar event containing both code={code!r} and marker={marker!r}",
    )


def score_gmail_message_with_code(
    outputs: Optional[dict] = None,
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """True when a Gmail message contains expected_code and expected_text."""
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}
    code = (ann.get("expected_code") or "").strip()
    marker = (ann.get("expected_text") or ann.get("marker") or "").strip()
    if not code:
        return False, "annotations.expected_code is empty"
    if not marker:
        return False, "annotations.expected_text is empty"

    user = ((ann.get("smolclaw_seed") or {}).get("user")) or "me"
    q = urllib.parse.quote(f"{marker} {code}")
    list_url = f"{_gmail_root()}users/{user}/messages?q={q}&maxResults=20"
    try:
        listing = _get_json(list_url)
    except RuntimeError as e:
        return False, str(e)

    hits = []
    for stub in listing.get("messages") or []:
        mid = stub.get("id")
        if not mid:
            continue
        msg = _get_json(
            f"{_gmail_root()}users/{user}/messages/{mid}?format=full"
        )
        # Prefer snippet + payload headers/body when present
        parts = [str(msg.get("snippet") or "")]
        payload = msg.get("payload") or {}
        for h in payload.get("headers") or []:
            if h.get("name", "").lower() in {"subject", "from", "to"}:
                parts.append(str(h.get("value") or ""))
        body = payload.get("body") or {}
        if body.get("data"):
            try:
                import base64

                pad = "=" * (-len(body["data"]) % 4)
                parts.append(
                    base64.urlsafe_b64decode(body["data"] + pad).decode(
                        errors="replace"
                    )
                )
            except Exception:
                pass
        blob = "\n".join(parts)
        if code in blob and marker in blob:
            hits.append({"id": mid, "snippet": msg.get("snippet"), "threadId": msg.get("threadId")})

    _write_hits(outputs, "smolclaw-gmail-hits.jsonl", hits)
    if hits:
        return (
            True,
            f"gmail message with code={code!r} and marker ({len(hits)} hit(s))",
        )
    return (
        False,
        f"no gmail message containing both code={code!r} and marker={marker!r}",
    )


def score_gmail_read_extraction(
    outputs: Optional[dict] = None,
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """Verify the agent's response contains all expected codes and none of the forbidden ones.

    Supports both ``expected_code`` (single string) and ``expected_codes`` (list).
    Optional ``forbidden_codes`` list fails the case if any appear in the response.
    """
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}

    expected_codes: list = ann.get("expected_codes") or []
    if isinstance(expected_codes, str):
        expected_codes = [expected_codes]
    if not expected_codes and ann.get("expected_code"):
        expected_codes = [str(ann["expected_code"]).strip()]
    if not expected_codes:
        return False, "annotations.expected_code(s) is empty"

    forbidden_codes: list = ann.get("forbidden_codes") or []
    if isinstance(forbidden_codes, str):
        forbidden_codes = [forbidden_codes]

    response = (outputs.get("output_content") or "").strip()
    if not response:
        for key in outputs:
            if key.endswith("_content") and outputs[key]:
                response = str(outputs[key]).strip()
                break
    if not response:
        return False, "no agent response to check"

    missing = [c for c in expected_codes if c not in response]
    if missing:
        return False, f"response missing code(s): {missing}"

    found_forbidden = [c for c in forbidden_codes if c in response]
    if found_forbidden:
        return False, f"response contains forbidden code(s): {found_forbidden}"

    hit_record = {
        "expected_codes": expected_codes,
        "forbidden_codes": forbidden_codes,
        "all_found": True,
        "none_forbidden": True,
    }
    _write_hits(outputs, "smolclaw-gmail-read-hits.jsonl", [hit_record])

    return True, f"all {len(expected_codes)} expected code(s) found, no forbidden codes"


def score_calendar_read_extraction(
    outputs: Optional[dict] = None,
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """Verify the agent's response contains expected calendar data (codes, event names, times).

    Supports both ``expected_code`` (single string) and ``expected_codes`` (list).
    Optional ``forbidden_codes`` list fails the case if any appear in the response.
    Validates read-only calendar operations (list, get, view, schedule queries).
    """
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}

    expected_codes: list = ann.get("expected_codes") or []
    if isinstance(expected_codes, str):
        expected_codes = [expected_codes]
    if not expected_codes and ann.get("expected_code"):
        expected_codes = [str(ann["expected_code"]).strip()]
    if not expected_codes:
        return False, "annotations.expected_code(s) is empty"

    forbidden_codes: list = ann.get("forbidden_codes") or []
    if isinstance(forbidden_codes, str):
        forbidden_codes = [forbidden_codes]

    response = (outputs.get("output_content") or "").strip()
    if not response:
        for key in outputs:
            if key.endswith("_content") and outputs[key]:
                response = str(outputs[key]).strip()
                break
    if not response:
        return False, "no agent response to check"

    missing = [c for c in expected_codes if c not in response]
    if missing:
        return False, f"response missing code(s): {missing}"

    found_forbidden = [c for c in forbidden_codes if c in response]
    if found_forbidden:
        return False, f"response contains forbidden code(s): {found_forbidden}"

    hit_record = {
        "expected_codes": expected_codes,
        "forbidden_codes": forbidden_codes,
        "all_found": True,
        "none_forbidden": True,
    }
    _write_hits(outputs, "smolclaw-calendar-read-hits.jsonl", [hit_record])

    return True, f"all {len(expected_codes)} expected code(s) found, no forbidden codes"
