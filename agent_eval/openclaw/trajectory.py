"""OpenClaw trajectory export helpers shared by OpenShell and Harbor."""

from __future__ import annotations

from typing import List, Optional


def build_export_trajectory_argv(
    session_key: str,
    *,
    workspace: str = "/workspace",
    output_name: str = "aeh-export",
) -> List[str]:
    """Argv for ``openclaw sessions export-trajectory`` (Quay 2026.7.x)."""
    return [
        "openclaw",
        "sessions",
        "export-trajectory",
        "--session-key",
        session_key,
        "--workspace",
        workspace,
        "--output",
        output_name,
        "--json",
    ]


def build_sessions_list_argv() -> List[str]:
    """Argv for ``openclaw sessions --json`` (fallback session-key lookup)."""
    return ["openclaw", "sessions", "--json"]


def trajectory_events_path(
    output_name: str,
    *,
    workspace: str = "/workspace",
    output_dir: Optional[str] = None,
) -> str:
    """Default events.jsonl path after a successful trajectory export."""
    if output_dir:
        return f"{output_dir.rstrip('/')}/events.jsonl"
    return f"{workspace.rstrip('/')}/.openclaw/trajectory-exports/{output_name}/events.jsonl"
