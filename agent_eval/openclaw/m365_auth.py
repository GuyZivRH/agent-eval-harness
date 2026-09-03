"""M365 Graph file-auth helpers for OpenClaw 8.1+ secret-env redaction."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, MutableMapping, Optional, Union

# Defaults match OpenShell retained state under the sandbox workdir.
DEFAULT_M365_HEADER_PATH = "/workspace/.openclaw/tmp/m365.header"
DEFAULT_M365_CURL_PATH = "/workspace/.openclaw/tmp/graph-curl"

_SECRET_ENV_KEYS = ("M365_ACCESS_TOKEN", "M365_CLIENT_SECRET")


def apply_m365_file_auth_env(
    sandbox_env: MutableMapping[str, str],
    *,
    header_path: str = DEFAULT_M365_HEADER_PATH,
    curl_path: str = DEFAULT_M365_CURL_PATH,
) -> Optional[str]:
    """Point env at file-based Graph auth; drop secret-named token env.

    Returns the bearer token when file auth should be installed, else None.
    OpenClaw 8.1 substitutes ``***`` for secret env values inside exec/curl, so
    agents must use ``curl -H @$M365_AUTH_HEADER_FILE`` (or ``$M365_GRAPH_CURL``)
    instead of ``Authorization: Bearer $M365_ACCESS_TOKEN``.
    """
    token = sandbox_env.get("M365_ACCESS_TOKEN")
    if not token:
        return None
    sandbox_env["M365_AUTH_HEADER_FILE"] = header_path
    sandbox_env["M365_GRAPH_CURL"] = curl_path
    for key in _SECRET_ENV_KEYS:
        sandbox_env.pop(key, None)
    return token


def m365_header_body(token: str) -> str:
    """Authorization header file contents for ``curl -H @file``."""
    return f"Authorization: Bearer {token}\n"


def graph_curl_script(header_path: str) -> str:
    """Shell helper that wraps curl with the Graph Authorization header file."""
    return (
        "#!/bin/sh\n"
        "# AEH helper: Graph auth via header file (OpenClaw 8.1-safe)\n"
        f'exec curl -sS -H @"{header_path}" "$@"\n'
    )


def write_m365_graph_auth_files(
    env: MutableMapping[str, str],
    *,
    header_path: Union[str, Path] = DEFAULT_M365_HEADER_PATH,
    curl_path: Union[str, Path] = DEFAULT_M365_CURL_PATH,
) -> bool:
    """Write header + curl helper on the local filesystem; scrub secret env.

    Used by the Harbor OpenClaw agent (runs commands inside the trial via
    ``environment.exec``, but can also stage files when the workspace is a
    bind-mounted host path). Returns True when files were written.
    """
    header = str(header_path)
    curl = str(curl_path)
    token = apply_m365_file_auth_env(env, header_path=header, curl_path=curl)
    if not token:
        return False
    header_p = Path(header)
    curl_p = Path(curl)
    header_p.parent.mkdir(parents=True, exist_ok=True)
    curl_p.parent.mkdir(parents=True, exist_ok=True)
    header_p.write_text(m365_header_body(token), encoding="utf-8")
    header_p.chmod(0o600)
    curl_p.write_text(graph_curl_script(header), encoding="utf-8")
    curl_p.chmod(0o755)
    return True


def m365_auth_present(env: Mapping[str, str]) -> bool:
    """True when a Graph token is available to install as file auth."""
    return bool(env.get("M365_ACCESS_TOKEN"))
