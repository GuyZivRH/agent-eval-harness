"""Score Crabline Slack recorder JSONL for AEH judges.

Host-side Crabline (`serve slack --recorder …`) appends one JSON object per
API call. Cases stamp markers / codes into outbound ``chat.postMessage``
bodies; scorers look for accepted calls matching annotations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Tuple, Union


def _recorder_path(
    recorder_path: Optional[str] = None,
    recorder_env: str = "CRABLINE_RECORDER",
) -> Path:
    return Path(recorder_path or os.environ.get(recorder_env, "")).expanduser()


def _iter_accepted_posts(path: Path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("path") != "/api/chat.postMessage":
            continue
        if not event.get("accepted"):
            continue
        yield event


def _write_hits(outputs: dict, hits: list) -> None:
    case_dir = outputs.get("case_dir")
    if not (case_dir and hits):
        return
    out_dir = Path(case_dir) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    hit_path = out_dir / "crabline-hits.jsonl"
    hit_path.write_text(
        "\n".join(json.dumps(h, ensure_ascii=False) for h in hits) + "\n",
        encoding="utf-8",
    )


def score_accepted_post_message(
    outputs: Optional[dict] = None,
    recorder_path: Optional[str] = None,
    recorder_env: str = "CRABLINE_RECORDER",
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """True when an accepted chat.postMessage contains annotations.expected_text."""
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}
    marker = (ann.get("expected_text") or ann.get("marker") or "").strip()
    if not marker:
        return False, "annotations.expected_text (or marker) is empty"

    path = _recorder_path(recorder_path, recorder_env)
    if not path.is_file():
        return False, f"Crabline recorder missing: {path}"

    hits = []
    for event in _iter_accepted_posts(path):
        body = event.get("body") or {}
        text = body.get("text") if isinstance(body, dict) else ""
        if marker in str(text or ""):
            hits.append(event)

    _write_hits(outputs, hits)
    if hits:
        channel = (hits[-1].get("body") or {}).get("channel", "?")
        return True, f"accepted chat.postMessage ({len(hits)} hit(s), channel={channel})"
    return False, f"no accepted chat.postMessage containing marker={marker!r} in {path}"


def score_post_with_code(
    outputs: Optional[dict] = None,
    recorder_path: Optional[str] = None,
    recorder_env: str = "CRABLINE_RECORDER",
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """True when an accepted post contains both expected_code and expected_text.

    Used by read-then-reply cases (case-002): the code must come from Slack
    history, not from inventing a marker-only post.
    """
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}
    code = (ann.get("expected_code") or "").strip()
    marker = (ann.get("expected_text") or ann.get("marker") or "").strip()
    if not code:
        return False, "annotations.expected_code is empty"
    if not marker:
        return False, "annotations.expected_text is empty"

    path = _recorder_path(recorder_path, recorder_env)
    if not path.is_file():
        return False, f"Crabline recorder missing: {path}"

    hits = []
    for event in _iter_accepted_posts(path):
        body = event.get("body") or {}
        text = str((body.get("text") if isinstance(body, dict) else "") or "")
        if code in text and marker in text:
            hits.append(event)

    _write_hits(outputs, hits)
    if hits:
        return True, f"accepted post with code={code!r} and marker ({len(hits)} hit(s))"
    return (
        False,
        f"no accepted chat.postMessage containing both code={code!r} and marker={marker!r}",
    )


def score_threaded_answer(
    outputs: Optional[dict] = None,
    recorder_path: Optional[str] = None,
    recorder_env: str = "CRABLINE_RECORDER",
    **_: Any,
) -> Union[bool, Tuple[bool, str]]:
    """True when an accepted threaded reply contains expected_answer (+ marker).

    Requires ``thread_ts`` on the post body (Slack thread follow-up).
    """
    outputs = outputs or {}
    ann = outputs.get("annotations") or {}
    answer = (ann.get("expected_answer") or "").strip()
    marker = (ann.get("expected_text") or ann.get("marker") or "").strip()
    if not answer:
        return False, "annotations.expected_answer is empty"

    path = _recorder_path(recorder_path, recorder_env)
    if not path.is_file():
        return False, f"Crabline recorder missing: {path}"

    hits = []
    for event in _iter_accepted_posts(path):
        body = event.get("body") or {}
        if not isinstance(body, dict):
            continue
        thread_ts = body.get("thread_ts") or body.get("threadTs")
        if not thread_ts:
            continue
        text = str(body.get("text") or "")
        if answer not in text:
            continue
        if marker and marker not in text:
            continue
        hits.append(event)

    _write_hits(outputs, hits)
    if hits:
        body = hits[-1].get("body") or {}
        return (
            True,
            f"threaded reply ok (thread_ts={body.get('thread_ts')}, "
            f"answer={answer!r}, {len(hits)} hit(s))",
        )
    need = f"answer={answer!r}" + (f" and marker={marker!r}" if marker else "")
    return False, f"no accepted threaded chat.postMessage containing {need}"
