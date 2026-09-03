"""OpenClaw provider-config builders shared by OpenShell and Harbor."""

from __future__ import annotations

import json
from typing import Any, Mapping, MutableMapping, Optional

# Default path written into Harbor trial workspaces (OpenShell uses /sandbox/...).
DEFAULT_OPENCLAW_CONFIG_PATH = "/workspace/openclaw-eval.json"


def build_openclaw_provider_config(
    model: str,
    providers: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an OpenClaw config dict with custom ``models.providers``.

    Custom providers (e.g. ``inference`` pointing at a host OpenAI-compatible
    proxy) must be registered in openclaw.json and passed via ``--config``.
    ``--auth-env-only`` skips config entirely, so it cannot be paired with
    provider registration.
    """
    config: dict[str, Any] = {
        "agents": {
            "defaults": {
                "model": {"primary": model},
            }
        },
        "models": {
            "mode": "merge",
            "providers": {},
        },
    }
    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, Mapping):
            continue
        provider_entry: dict[str, Any] = {
            "baseUrl": provider_cfg.get("baseUrl", ""),
            "apiKey": provider_cfg.get("apiKey", "empty"),
            "api": provider_cfg.get("api", "openai-completions"),
            "models": [],
        }
        for m in provider_cfg.get("models") or []:
            if not isinstance(m, Mapping):
                continue
            model_entry: dict[str, Any] = {
                "id": m.get("id", ""),
                "name": m.get("name", m.get("id", "")),
                "reasoning": False,
                "input": ["text"],
                "cost": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                },
                "contextWindow": 200000,
                "maxTokens": 8192,
            }
            if m.get("api"):
                model_entry["api"] = m["api"]
            provider_entry["models"].append(model_entry)
        config["models"]["providers"][provider_name] = provider_entry
    return config


def dump_openclaw_provider_config(config: Mapping[str, Any]) -> str:
    """Serialize provider config for ``tee`` / file write."""
    return json.dumps(config, indent=2)


def providers_from_env(
    env: Mapping[str, str],
    *,
    model: str,
    provider_name: str = "inference",
) -> Optional[dict[str, Any]]:
    """Build a single-provider map from Harbor/host env when eval.yaml has none.

    Harbor trials do not get OpenShell's ``inference.local`` gateway. Point
    ``OPENCLAW_INFERENCE_BASE_URL`` (or ``ANTHROPIC_BASE_URL`` /
    ``OPENAI_BASE_URL``) at a host-reachable OpenAI-compatible proxy instead
    (e.g. ``http://host.containers.internal:8000/v1``).
    """
    base_url = (
        env.get("OPENCLAW_INFERENCE_BASE_URL")
        or env.get("ANTHROPIC_BASE_URL")
        or env.get("OPENAI_BASE_URL")
        or ""
    ).strip()
    if not base_url:
        return None
    api_key = (
        env.get("OPENCLAW_INFERENCE_API_KEY")
        or env.get("ANTHROPIC_API_KEY")
        or env.get("OPENAI_API_KEY")
        or "empty"
    )
    model_id = (
        env.get("OPENCLAW_INFERENCE_MODEL")
        or env.get("ANTHROPIC_MODEL")
        or env.get("OPENAI_MODEL")
        or model.rsplit("/", 1)[-1]
        or "claude-sonnet-4"
    )
    catalog_id = model_id
    if "/" in model and model.split("/", 1)[0] == provider_name:
        catalog_id = model.split("/", 1)[1]
    return {
        provider_name: {
            "baseUrl": base_url,
            "apiKey": api_key,
            "api": "openai-completions",
            "models": [
                {
                    "id": catalog_id,
                    "name": catalog_id,
                    "api": "openai-completions",
                }
            ],
        }
    }


def resolve_openclaw_model_ref(
    model: str,
    providers: Optional[Mapping[str, Any]],
) -> str:
    """Pick the OpenClaw ``--model`` value (usually ``provider/id``)."""
    if not providers:
        return model
    if "/" in model:
        return model
    for provider_name, provider_cfg in providers.items():
        models = (provider_cfg or {}).get("models") or []
        if models and isinstance(models[0], Mapping) and models[0].get("id"):
            return f"{provider_name}/{models[0]['id']}"
        return f"{provider_name}/{model}"
    return model


def stamp_config_env(
    env: MutableMapping[str, str],
    config_path: str,
) -> None:
    """Record config path for child processes / debugging."""
    env["OPENCLAW_CONFIG_PATH"] = config_path
