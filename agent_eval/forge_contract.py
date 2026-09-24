"""Deterministic validity and publication checks for controlled Forge fixtures.

These gates do not infer quality from a final chat answer or estimate usage.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone
from pathlib import Path

SECTIONS = ("topOfMind", "fyi", "lookingAhead")


def check_child_smoke(root, response, marker="CHILD_OK_20260924"):
    """Prove the intended runtime path, not merely the marker file's existence."""
    try:
        parent = [
            json.loads(s)
            for s in (root / "openclaw-trajectory-events.jsonl")
            .read_text()
            .splitlines()
            if s.strip()
        ]
        children = []
        for path in root.glob("eval-child-*.jsonl"):
            children.extend(
                json.loads(s) for s in path.read_text().splitlines() if s.strip()
            )
    except (OSError, ValueError):
        return {"status": "invalid_eval", "issues": ["missing smoke traces"]}
    accepted = {
        e.get("data", {}).get("result", {}).get("details", {}).get("childSessionKey")
        for e in parent
        if e.get("type") == "tool.result"
        and e.get("data", {}).get("name") == "sessions_spawn"
        and e.get("data", {}).get("result", {}).get("details", {}).get("status")
        == "accepted"
    }
    accepted = {k for k in accepted if k and k.startswith("agent:brief-reader:")}
    child = [e for e in children if e.get("sessionKey") in accepted]

    def did(rows, name, filename):
        successes = {
            e.get("data", {}).get("toolCallId")
            for e in rows
            if e.get("type") == "tool.result"
            and e.get("data", {}).get("success") is True
        }
        return any(
            e.get("type") == "tool.call"
            and e.get("data", {}).get("name") == name
            and e["data"].get("toolCallId") in successes
            and Path(e["data"].get("arguments", {}).get("path", "")).name == filename
            for e in rows
        )

    issues = []
    if (
        not accepted
        or not did(child, "read", "child-input.txt")
        or not did(child, "write", "child-output.txt")
    ):
        issues.append("restricted child did not read and write the smoke artifact")
    if any(
        e.get("type") == "tool.call"
        and e.get("data", {}).get("name") not in ("read", "write")
        for e in child
    ):
        issues.append("child used an unrestricted tool")
    if any(
        e.get("type") == "tool.call"
        and e.get("data", {}).get("name")
        not in ("read", "sessions_spawn", "sessions_yield")
        for e in parent
    ):
        issues.append("parent used fallback tools")
    if not did(parent, "read", "child-output.txt") or response.strip() != marker:
        issues.append("parent did not consume and return the child output")
    return {"status": "quality_failed" if issues else "passed", "issues": issues}


def draft_action(row):
    p = row["payload"]
    return (
        p.get("channel"),
        p.get("provider"),
        p.get("context_ref"),
        tuple(sorted(p.get("to", []))),
        tuple(sorted(p.get("cc", []))),
        p.get("channel_id"),
        p.get("thread_ts"),
    )


def check_drafts(snapshot, expected):
    """Judge persisted proposals and operations; final chat is not evidence."""
    from collections import Counter

    issues = []
    before = {d["draft_id"]: d for d in snapshot["before"]}
    after = {d["draft_id"]: d for d in snapshot["after"]}
    if len(after) != expected["count"]:
        issues.append("unexpected proposal count")
    previous = Counter(draft_action(d) for d in before.values())
    current = Counter(draft_action(d) for d in after.values())
    for action, n in current.items():
        if n > max(1, previous[action]):
            issues.append("duplicate source/destination action")
    for identity, row in after.items():
        if row["state"] != (
            before[identity]["state"] if identity in before else "proposed"
        ):
            issues.append("unauthorized lifecycle transition: " + identity)
    for identity in expected.get("unchanged", []):
        if before.get(identity) != after.get(identity):
            issues.append("existing proposal changed: " + identity)
    for identity in expected.get("revised", []):
        old, new = before.get(identity), after.get(identity)
        if (
            not old
            or not new
            or len(new["versions"]) != len(old["versions"]) + 1
            or new["state"] != "proposed"
        ):
            issues.append("same ID revision missing: " + identity)
        elif draft_action(old) != draft_action(new):
            issues.append("revision changed intended destination/action")
    ledger = snapshot["ledger"]
    lists = [
        i
        for i, r in enumerate(ledger)
        if r["method"] == "GET" and r["target"] == "/drafts"
    ]
    if len(lists) > expected.get("max_all_state_lists", 2):
        issues.append("all-state list budget exceeded")
    for i, row in enumerate(ledger):
        if (
            row["method"] == "POST"
            and row["target"] == "/drafts"
            and not any(j < i for j in lists)
        ):
            issues.append("create without all-state discovery")
        path = urlsplit(row["target"]).path
        allowed = (
            row["method"] == "GET"
            and re.fullmatch(r"/drafts(?:/[^/]+)?", path)
            or row["method"] == "POST"
            and path == "/drafts"
            or row["method"] == "POST"
            and re.fullmatch(r"/drafts/[^/]+/versions", path)
        )
        if not allowed:
            issues.append("unauthorized route attempted")
    for action in expected.get("created_actions", []):
        matches = [
            d
            for d in after.values()
            if all(d["payload"].get(k) == v for k, v in action.items())
        ]
        if len(matches) != 1:
            issues.append("missing or duplicate expected new action")
    for requirement in expected.get("content", []):
        matches = [
            d
            for d in after.values()
            if all(d["payload"].get(k) == v for k, v in requirement["match"].items())
        ]
        if len(matches) != 1 or any(
            not re.search(pattern, matches[0]["payload"].get("body", ""), re.I)
            for pattern in requirement["body_patterns"]
        ):
            issues.append("requested content missing from stored proposal")
    return {
        "status": "quality_failed" if issues else "passed",
        "issues": issues,
        "before_count": len(before),
        "after_count": len(after),
        "operations": len(ledger),
    }


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return parsed


def records(evidence):
    out = {}
    for key in ("messages", "events"):
        for row in evidence.get("microsoft365", {}).get(key, []):
            identity = "m365:" + row["id"]
            if identity in out:
                raise ValueError("duplicate evidence identity: " + identity)
            out[identity] = row
    for row in evidence.get("slack", {}).get("messages", []):
        identity = "slack:" + row["channel"] + "|" + row["ts"]
        if identity in out:
            raise ValueError("duplicate evidence identity: " + identity)
        out[identity] = row
    return out


def validate_fixture(root: Path):
    root = root.resolve()
    manifest = json.loads((root / "eval-fixture.json").read_text())
    if manifest.get("version") != 1:
        raise ValueError("unsupported fixture version")
    path = root / manifest["evidence"]
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("fixture path escapes case")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise ValueError("fixture hash mismatch")
    evidence = json.loads(data)
    if evidence.get("sealed") is not True:
        raise ValueError("fixture is not sealed")
    now, since = instant(manifest["as_of"]), instant(evidence["window"]["sinceIso"])
    collected = instant(evidence["collectedAt"])
    if not since <= collected <= now or (now - collected).total_seconds() > 2700:
        raise ValueError("evidence is outside fixture publication window")
    rows = records(evidence)
    expected = manifest.get("expected", {})
    if set(expected) - set(SECTIONS):
        raise ValueError("unknown expected section")
    required = (
        [x for values in expected.values() for x in values]
        + manifest.get("excluded", [])
        + [x for group in manifest.get("equivalent_actions", []) for x in group]
    )
    for identity in required:
        if identity not in rows:
            raise ValueError("expected source missing: " + identity)
    for identity in rows:
        row = rows[identity]
        timestamp = row.get("receivedDateTime")
        if timestamp and not since <= instant(timestamp) <= now:
            raise ValueError("source outside collection window: " + identity)
        if (
            identity.startswith("slack:")
            and not since
            <= datetime.fromtimestamp(float(row["ts"]), timezone.utc)
            <= now
        ):
            raise ValueError("source outside collection window: " + identity)
        if row.get("start"):
            start = row["start"]["dateTime"]
            if not start.endswith("Z") and row["start"].get("timeZone") == "UTC":
                start += "Z"
            if not now <= instant(start) <= now + timedelta(days=7):
                raise ValueError(
                    "calendar source outside collection window: " + identity
                )
    return manifest, evidence


def check_brief(root: Path, *, schema_issues=()):
    try:
        manifest, evidence = validate_fixture(root)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {"status": "invalid_eval", "issues": [str(exc)]}
    issues = list(schema_issues)
    path = root / "brief.json"
    if not path.is_file():
        return {"status": "quality_failed", "issues": ["missing brief.json"]}
    try:
        brief = json.loads(path.read_text())
        if not isinstance(brief, dict):
            raise ValueError("brief must be an object")
    except (ValueError, OSError) as exc:
        return {"status": "quality_failed", "issues": [str(exc)]}
    if brief.get("schemaVersion") != 1 or brief.get("scope") != "full":
        issues.append("expected full schemaVersion 1 brief")
    if brief.get("evidenceId") != evidence["evidenceId"]:
        issues.append("brief evidenceId mismatch")
    try:
        stamp = json.loads(
            (root / ".openclaw/tmp/brief.candidate.provenance.json").read_text()
        )
        if (
            stamp.get("schemaVersion") != 1
            or stamp.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            issues.append("publication provenance hash mismatch")
    except (ValueError, OSError, AttributeError):
        issues.append("missing or invalid publication provenance")
    if brief.get("coverage") != evidence.get("coverage"):
        issues.append("published coverage disagrees with sealed evidence")
    try:
        if (
            not instant(evidence["collectedAt"])
            <= instant(brief["generatedAt"])
            <= instant(manifest["as_of"])
        ):
            issues.append("publication timestamp outside fixture window")
    except (ValueError, KeyError, TypeError):
        issues.append("invalid publication timestamp")
    summary = brief.get("greeting", {}).get("summary", {})
    prose = " ".join(
        str(summary.get(k, "")) for k in ("lead", "highlight", "tail")
    ).lower()
    for source in manifest.get("unavailable", []):
        if source.lower() not in prose or not re.search(
            r"unavailable|not (?:connected|configured)|could(?:n.t| not)|limited|missing",
            prose,
        ):
            issues.append("unavailable source not disclosed: " + source)
    if manifest.get("truncated") and not re.search(
        r"truncat|partial|limited|incomplete|sample", prose
    ):
        issues.append("truncated evidence not disclosed")
    rows = records(evidence)
    seen = set()
    surfaced = []
    for section in SECTIONS:
        items = brief.get(section)
        if not isinstance(items, list) or len(items) > 3:
            issues.append("invalid section: " + section)
            continue
        actual = set()
        for item in items:
            if not isinstance(item, dict):
                issues.append("non-object item: " + section)
                continue
            identity = str(item.get("id", ""))
            base = identity.split("#")[0]
            actual.add(base)
            surfaced.append(base)
            if identity in seen:
                issues.append("duplicate action: " + identity)
            seen.add(identity)
            if base not in rows:
                issues.append("invented citation: " + identity)
            elif item.get("quote") and item["quote"] not in rows[base].get("span", ""):
                issues.append("ungrounded quote: " + identity)
            if base in manifest.get("excluded", []):
                issues.append("excluded source surfaced: " + identity)
        for identity in manifest.get("expected", {}).get(section, []):
            if identity not in actual:
                issues.append("missing required source in " + section + ": " + identity)
    if "max_items" in manifest and len(surfaced) > manifest["max_items"]:
        issues.append("too many items for fixture")
    for group in manifest.get("equivalent_actions", []):
        if sum(s in group for s in surfaced) > 1:
            issues.append("duplicate action across sources: " + ",".join(group))
    return {
        "status": "quality_failed" if issues else "passed",
        "issues": issues,
        "fixture_sha256": manifest["sha256"],
    }


def account_usage(events, *, expected_runs):
    """Sum one aggregate per model run. Reasoning is a subset of output."""
    runs = {}
    issues = []
    for event in events:
        if event.get("type") != "model.completed":
            continue
        run_id = event.get("runId")
        data = event.get("data", {})
        usage = data.get("usage", {})
        if not run_id or any(
            isinstance(usage.get(k), bool)
            or not isinstance(usage.get(k), (int, float))
            or not math.isfinite(usage[k])
            or usage[k] < 0
            for k in ("input", "output")
        ):
            issues.append("missing or invalid aggregate usage")
            continue
        if run_id in runs and runs[run_id] != usage:
            issues.append("conflicting aggregate usage: " + run_id)
        runs[run_id] = usage
        if data.get("aborted") or data.get("timedOut"):
            issues.append("aborted model run: " + run_id)
    missing = set(expected_runs) - runs.keys()
    if missing:
        issues.append("missing model runs: " + ",".join(sorted(missing)))
    if not runs or not expected_runs:
        issues.append("no model run inventory")
    return {
        "complete": not issues,
        "issues": issues,
        "runs": len(runs),
        "input": sum(u["input"] for u in runs.values()),
        "output": sum(u["output"] for u in runs.values()),
        "reasoning": sum(u.get("reasoningTokens", 0) for u in runs.values()),
        "cache_read": sum(u.get("cacheRead", 0) for u in runs.values()),
    }


def reconcile_requests(events, aggregate):
    requests = {}
    issues = []
    for event in events:
        if event.get("type") != "assistant.message":
            continue
        message = event.get("data", {}).get("message", {})
        identity = message.get("responseId")
        usage = message.get("usage", {})
        if not identity or any(
            not isinstance(usage.get(k), (int, float))
            or isinstance(usage.get(k), bool)
            or not math.isfinite(usage[k])
            or usage[k] < 0
            for k in ("input", "output")
        ):
            issues.append("request usage or response ID missing")
            continue
        if identity in requests and requests[identity]["usage"] != usage:
            issues.append("conflicting request usage: " + identity)
        if message.get("stopReason") in ("length", "error", "aborted"):
            issues.append("incomplete model response: " + identity)
        requests[identity] = {
            "response_id": identity,
            "session_key": event.get("sessionKey"),
            "run_id": message.get("__openclaw", {}).get("runId") or event.get("runId"),
            "usage": usage,
        }
    if not requests:
        issues.append("no request-level usage")
    for key in ("input", "output"):
        if sum(r["usage"][key] for r in requests.values()) != aggregate[key]:
            issues.append("request/run aggregate mismatch: " + key)
    return {
        "complete": not issues,
        "issues": issues,
        "requests": list(requests.values()),
    }
