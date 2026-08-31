"""Host-side Crabline seeding for OpenShell cases.

Cases that need Slack state *before* the agent runs declare
``annotations.crabline_seed`` (users + text). AEH posts that message via the
host loopback API (not ``host.openshell.internal``) so history/threads exist
when OpenClaw starts.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


def _api_root() -> str:
    explicit = os.environ.get("CRABLINE_API_URL") or os.environ.get("SLACK_API_URL_HOST")
    if explicit:
        return explicit if explicit.endswith("/") else explicit + "/"
    # Host-side default (sandbox uses host.openshell.internal).
    return "http://127.0.0.1:8787/api/"


def _bot_token() -> str:
    token = os.environ.get("SLACK_BOT_TOKEN") or ""
    if token:
        return token
    ready = os.environ.get("CRABLINE_READY_FILE", "")
    if ready and Path(ready).is_file():
        data = json.loads(Path(ready).read_text(encoding="utf-8"))
        return str(data.get("botToken") or "")
    # Common spike path
    default_ready = Path(".tmp/crabline/ready/slack-server.json")
    if default_ready.is_file():
        data = json.loads(default_ready.read_text(encoding="utf-8"))
        return str(data.get("botToken") or "")
    return ""


def _slack_form(api_root: str, method: str, token: str, fields: dict) -> dict:
    url = f"{api_root}{method}"
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def load_case_annotations(config, case_id: str) -> dict:
    """Load annotations.yaml for a case from the dataset path."""
    try:
        dataset_root = config.resolve_path(config.dataset.path)
    except Exception:
        return {}
    path = Path(dataset_root) / case_id / "annotations.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _seed_one(
    seed: dict,
    api_root: str,
    token: str,
) -> dict[str, Any]:
    """Post a single seed item to Crabline. Return metadata."""
    text = (seed.get("text") or "").strip()
    if not text:
        raise ValueError("crabline seed text is required")
    direct_channel = (seed.get("channel") or "").strip()
    users = (seed.get("users") or "").strip()
    if not direct_channel and not users:
        users = "UCANARY01"

    if direct_channel:
        channel = direct_channel
    else:
        opened = _slack_form(api_root, "conversations.open", token, {"users": users})
        if not opened.get("ok"):
            raise RuntimeError(f"conversations.open failed: {opened}")
        channel = (opened.get("channel") or {}).get("id")
        if not channel:
            raise RuntimeError(f"conversations.open missing channel id: {opened}")

    posted = _slack_form(
        api_root,
        "chat.postMessage",
        token,
        {"channel": channel, "text": text},
    )
    if not posted.get("ok"):
        raise RuntimeError(f"chat.postMessage seed failed: {posted}")

    ts = posted.get("ts") or (posted.get("message") or {}).get("ts")
    result: dict[str, Any] = {
        "ok": True,
        "channel": channel,
        "ts": ts,
        "text": text,
    }
    if users:
        result["users"] = users
    logger.info(
        "Crabline seed: channel=%s ts=%s text=%r",
        channel,
        ts,
        text[:80],
    )
    return result


def seed_crabline_for_case(
    annotations: dict,
    *,
    api_root: Optional[str] = None,
    token: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Post annotations.crabline_seed (or crabline_seeds) to Crabline.

    Supports:
    - ``crabline_seed`` (singular): one seed with ``users`` or ``channel`` + ``text``
    - ``crabline_seeds`` (plural): list of seeds for multi-item discovery tests

    Returns seed metadata dict (single) or dict with ``seeds`` list (multi), or None.
    """
    api_root = api_root or _api_root()
    token = token or _bot_token()
    if not token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN (or Crabline ready file) required to seed Crabline"
        )

    seeds_list = (annotations or {}).get("crabline_seeds")
    if seeds_list and isinstance(seeds_list, list):
        results = []
        for item in seeds_list:
            results.append(_seed_one(item, api_root, token))
        timestamps = [r["ts"] for r in results if r.get("ts")]
        oldest = None
        if timestamps:
            # Slack oldest is exclusive — subtract 1 microsecond so the first
            # seeded message is included in conversations.history results.
            parts = timestamps[0].split(".")
            micro = int(parts[1]) - 1 if len(parts) == 2 else 0
            oldest = f"{parts[0]}.{micro:06d}"
        return {
            "ok": True,
            "seeds": results,
            "channels": [r["channel"] for r in results],
            "oldest_ts": oldest,
            "api_root": api_root,
        }

    seed = (annotations or {}).get("crabline_seed")
    if not seed:
        return None
    result = _seed_one(seed, api_root, token)
    result["api_root"] = api_root
    return result

def seed_crabline_for_scene(
    seeds: list,
    *,
    api_root: Optional[str] = None,
    token: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Seed a list of Slack messages into Crabline. Return list of metadata dicts.

    Each seed has ``text`` (required) and either ``channel`` (post to a
    public/private channel) or ``users`` (open a DM first, then post).
    """
    api_root = api_root or _api_root()
    token = token or _bot_token()
    if not token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN (or Crabline ready file) required to seed Crabline"
        )
    results = []
    for i, seed in enumerate(seeds or []):
        text = (seed.get("text") or "").strip()
        if not text:
            raise ValueError(f"crabline_seeds[{i}].text is required")
        channel = (seed.get("channel") or "").strip()
        users = (seed.get("users") or "").strip()
        if not channel and not users:
            raise ValueError(
                f"crabline_seeds[{i}] must have either 'channel' or 'users'"
            )
        # Do not default users for scene seeds — require explicit channel or users.
        item = {"text": text}
        if channel:
            item["channel"] = channel
        if users:
            item["users"] = users
        # _seed_one defaults users to UCANARY01 when both missing; we already validated.
        if not channel and not users:
            raise ValueError(f"crabline_seeds[{i}] must have either 'channel' or 'users'")
        # Temporarily avoid _seed_one's UCANARY01 default by requiring channel|users above.
        # Call low-level path through a thin wrapper that doesn't invent users.
        result_channel = channel
        if users and not channel:
            opened = _slack_form(api_root, "conversations.open", token, {"users": users})
            if not opened.get("ok"):
                raise RuntimeError(f"conversations.open failed for seed {i}: {opened}")
            result_channel = (opened.get("channel") or {}).get("id")
            if not result_channel:
                raise RuntimeError(f"conversations.open missing channel id for seed {i}")
        posted = _slack_form(
            api_root, "chat.postMessage", token, {"channel": result_channel, "text": text},
        )
        if not posted.get("ok"):
            raise RuntimeError(f"chat.postMessage failed for seed {i}: {posted}")
        ts = posted.get("ts") or (posted.get("message") or {}).get("ts")
        results.append({
            "ok": True,
            "channel": result_channel,
            "ts": ts,
            "text": text,
            "api_root": api_root,
        })
        logger.info(
            "Crabline scene seed [%d]: channel=%s ts=%s text=%r",
            i, result_channel, ts, text[:80],
        )
    return results

