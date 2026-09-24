import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import agent_eval.openshell.forge as forge


def test_required_artifact_download_failure_is_returned_as_invalid(tmp_path):
    fn = getattr(forge, "collect_required_artifacts", None)
    assert callable(fn), "required artifacts must not be silently dropped"
    sandbox = SimpleNamespace(
        download=AsyncMock(side_effect=RuntimeError("missing source"))
    )
    result = asyncio.run(fn(sandbox, "eval", tmp_path, ["brief.json"]))
    assert result == ["required artifact unavailable: brief.json"]


def test_required_artifact_cannot_escape_workspace(tmp_path):
    fn = getattr(forge, "collect_required_artifacts", None)
    assert callable(fn)
    with pytest.raises(ValueError, match="relative"):
        asyncio.run(fn(None, "eval", tmp_path, ["../secret"]))
