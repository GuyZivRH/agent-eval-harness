"""Shared OpenClaw helpers used by OpenShell and Harbor backends."""

from agent_eval.openclaw.config import (
    DEFAULT_OPENCLAW_CONFIG_PATH,
    build_openclaw_provider_config,
    dump_openclaw_provider_config,
    providers_from_env,
    resolve_openclaw_model_ref,
    stamp_config_env,
)
from agent_eval.openclaw.m365_auth import (
    DEFAULT_M365_CURL_PATH,
    DEFAULT_M365_HEADER_PATH,
    apply_m365_file_auth_env,
    graph_curl_script,
    m365_auth_present,
    m365_header_body,
    write_m365_graph_auth_files,
)
from agent_eval.openclaw.trajectory import (
    build_export_trajectory_argv,
    build_sessions_list_argv,
    trajectory_events_path,
)

__all__ = [
    "DEFAULT_M365_CURL_PATH",
    "DEFAULT_M365_HEADER_PATH",
    "DEFAULT_OPENCLAW_CONFIG_PATH",
    "apply_m365_file_auth_env",
    "build_export_trajectory_argv",
    "build_openclaw_provider_config",
    "build_sessions_list_argv",
    "dump_openclaw_provider_config",
    "graph_curl_script",
    "m365_auth_present",
    "m365_header_body",
    "providers_from_env",
    "resolve_openclaw_model_ref",
    "stamp_config_env",
    "trajectory_events_path",
    "write_m365_graph_auth_files",
]
