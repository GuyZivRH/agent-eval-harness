#!/usr/bin/env python3
"""Seed tbx-demo3 mailbox + calendar via Microsoft Graph (delegated token).

Expects either:
  - M365_ACCESS_TOKEN in the environment, or
  - .tmp/forge-real/m365-token.json from device-code login

Creates a coherent executive inbox aligned with IBM Forge Slack themes
(watson-orchestrate / quantum / exec-product-strategy) and writes a ground-truth
sheet to .tmp/forge-real/ground-truth.json for annotation updates.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp" / "forge-real"
USER = os.environ.get("M365_USER", "tbx-demo3@dev.mscloud.ibm.com")


def _token() -> str:
    tok = os.environ.get("M365_ACCESS_TOKEN", "").strip()
    if tok:
        return tok
    path = OUT / "m365-token.json"
    if path.is_file():
        data = json.loads(path.read_text())
        tok = (data.get("access_token") or "").strip()
        if tok:
            return tok
    raise SystemExit(
        "Missing M365_ACCESS_TOKEN / .tmp/forge-real/m365-token.json — "
        "complete device-code login first"
    )


def _graph(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        raise RuntimeError(f"{method} {path} -> {e.code}: {err[:500]}") from e


def _mail_payloads() -> list[dict]:
    return [
        {
            "subject": "ACTION: Watson Orchestrate GA blocker — exec decision by 11:00",
            "from": "priya.nair@ibm.com",
            "body": (
                "Priya (WO PM): GA checklist has one open blocker — partner connector "
                "certification slipped. Need your go/no-go by 11:00 today so we can "
                "keep the press embargo. Thread also in #ibm-watson-orchestrate-news."
            ),
            "importance": "high",
        },
        {
            "subject": "Quantum roadmap review — conflicting cost notes from finance",
            "from": "marcus.chen@ibm.com",
            "body": (
                "Marcus (Finance): Quantum hardware refresh CapEx in the board pack "
                "is $42M; eng Slack says $38M after vendor concession. Please reconcile "
                "before Thursday's #ibm-quantum-news deep-dive. I recommend we use $38M "
                "with a $4M contingency line."
            ),
            "importance": "normal",
        },
        {
            "subject": "Weekly Digest: IBM Product Newsletter",
            "from": "newsletter@ibm.com",
            "body": (
                "This week: campus parking update, new hire welcome, cafeteria menu. "
                "No action required."
            ),
            "importance": "low",
        },
        {
            "subject": "URGENT: Strategic customer escalation — Contoso renewals at risk",
            "from": "elena.vasquez@ibm.com",
            "body": (
                "Elena (CS): Contoso threatened to pause $8M renewals unless we provide "
                "an exec-level response on Orchestrate SLA misses by EOD. Talking points "
                "drafted; they asked for your voice specifically."
            ),
            "importance": "high",
        },
        {
            "subject": "Prep: Exec product views & strategy offsite talking points",
            "from": "rachel.kim@ibm.com",
            "body": (
                "Rachel (Chief of Staff): Offsite tomorrow — need your 5-bullet view on "
                "Orchestrate vs Quantum investment split. Draft in the strategy channel."
            ),
            "importance": "normal",
        },
        {
            "subject": "FYI: Quantum community webinar recording",
            "from": "events-bot@ibm.com",
            "body": "Automated: webinar recording is available. No action.",
            "importance": "low",
        },
    ]


def _calendar_payloads(now: datetime) -> list[dict]:
    start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    return [
        {
            "subject": "Decision: Watson Orchestrate GA go/no-go",
            "start": start,
            "end": start + timedelta(minutes=30),
            "body": "Priya Nair / press embargo. Decision needed before 11:00.",
        },
        {
            "subject": "Quantum roadmap deep-dive (finance vs eng numbers)",
            "start": start + timedelta(days=2),
            "end": start + timedelta(days=2, hours=1),
            "body": "Reconcile CapEx $42M vs $38M before board pack freeze.",
        },
        {
            "subject": "Interview: Distro noise — campus ambassador (skip-level)",
            "start": start + timedelta(days=1, hours=3),
            "end": start + timedelta(days=1, hours=3, minutes=45),
            "body": "Low priority HR interview; can decline if overloaded.",
        },
    ]


def seed_mail(token: str) -> list[dict]:
    created = []
    for item in _mail_payloads():
        _graph(
            "POST",
            "/me/sendMail",
            token,
            {
                "message": {
                    "subject": item["subject"],
                    "body": {
                        "contentType": "Text",
                        "content": f"[persona:{item['from']}]\n\n{item['body']}",
                    },
                    "importance": item["importance"],
                    "toRecipients": [{"emailAddress": {"address": USER}}],
                },
                "saveToSentItems": True,
            },
        )
        created.append(
            {
                "subject": item["subject"],
                "from_persona": item["from"],
                "importance": item["importance"],
            }
        )
        print(f"mail seeded: {item['subject'][:70]}")
    return created


def seed_calendar(token: str, now: datetime) -> list[dict]:
    created = []
    for item in _calendar_payloads(now):
        body = {
            "subject": item["subject"],
            "body": {"contentType": "Text", "content": item["body"]},
            "start": {
                "dateTime": item["start"].strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": item["end"].strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
        }
        resp = _graph("POST", "/me/events", token, body)
        created.append(
            {
                "id": resp.get("id"),
                "subject": item["subject"],
                "start": body["start"],
                "end": body["end"],
            }
        )
        print(f"cal seeded: {item['subject'][:70]}")
    return created


def verify(token: str) -> dict:
    mail = _graph(
        "GET",
        "/me/messages?$top=20&$select=subject,from,receivedDateTime"
        "&$orderby=receivedDateTime%20desc",
        token,
    )
    cal = _graph(
        "GET",
        "/me/calendar/events?$top=20&$select=subject,start,end",
        token,
    )
    return {
        "mail_count": len(mail.get("value") or []),
        "mail": [
            {
                "subject": m.get("subject"),
                "from": ((m.get("from") or {}).get("emailAddress") or {}).get(
                    "address"
                ),
                "received": m.get("receivedDateTime"),
            }
            for m in (mail.get("value") or [])
        ],
        "cal_count": len(cal.get("value") or []),
        "calendar": [
            {"subject": e.get("subject"), "start": e.get("start")}
            for e in (cal.get("value") or [])
        ],
    }


def main() -> int:
    for k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(k, None)
    OUT.mkdir(parents=True, exist_ok=True)
    token = _token()
    now = datetime.now(timezone.utc)
    verify_only = "--verify-only" in sys.argv
    if not verify_only:
        mail = seed_mail(token)
        cal = seed_calendar(token, now)
    else:
        mail, cal = [], []
    state = verify(token)
    ground = {
        "user": USER,
        "seeded_mail": mail,
        "seeded_calendar": cal,
        "verified": state,
        "channels": [
            "ibm-watson-orchestrate-news",
            "ibm-quantum-news",
            "ibm-exec-product-views-n-strategy",
        ],
        "annotations_hint": {
            "expected_top_of_mind": [
                "Watson Orchestrate GA go/no-go — 11:00 decision",
                "Contoso escalation — $8M renewals at risk",
            ],
            "expected_fyi": [
                "Quantum CapEx conflict $42M vs $38M",
                "Exec offsite talking points prep",
            ],
            "expected_excluded": [
                "Weekly product newsletter",
                "Quantum webinar bot mail",
                "Campus ambassador interview noise",
            ],
            "expected_first": "Watson Orchestrate GA go/no-go",
            "analysis_focus": "Watson Orchestrate GA blocker",
        },
    }
    (OUT / "ground-truth.json").write_text(json.dumps(ground, indent=2))
    print(
        f"verify mail_count={state['mail_count']} cal_count={state['cal_count']} "
        f"-> {OUT / 'ground-truth.json'}"
    )
    if state["mail_count"] < 1 or state["cal_count"] < 1:
        print("ERROR: mailbox or calendar still empty after seed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
