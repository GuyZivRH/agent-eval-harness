import asyncio
from unittest.mock import AsyncMock

import pytest

from agent_eval.openshell.forge import prepare_forge_sandbox
from agent_eval.openshell.sandbox import ExecResult


def test_provisions_trust_before_restart_and_checks_workspace(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_text('-----BEGIN CERTIFICATE-----\npublic-ca\n')
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [ExecResult('', '', 0), ExecResult('FORGE_IMAGE_WORKSPACE_OK', '', 0)]
    asyncio.run(prepare_forge_sandbox(sandbox, 'probe', ca))
    assert [c[0] for c in sandbox.mock_calls] == ['exec', 'restart', 'exec']
    assert sandbox.exec.call_args_list[0].kwargs['stdin'] == ca.read_bytes()
    bootstrap = sandbox.exec.call_args_list[1].kwargs['stdin']
    assert b'/opt/forge/agent-workspace' in bootstrap
    assert b'ensureOpenClawAgentDatabaseSchema' in bootstrap
    assert b'IMAGE_FILE_READABLE' in bootstrap


@pytest.mark.parametrize('code,stdout', [(1, ''), (0, '')])
def test_fails_closed_for_missing_workspace(tmp_path, code, stdout):
    ca = tmp_path / 'ca.crt'
    ca.write_text('-----BEGIN CERTIFICATE-----\npublic-ca\n')
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [ExecResult('', '', 0), ExecResult(stdout, 'missing', code)]
    with pytest.raises(RuntimeError):
        asyncio.run(prepare_forge_sandbox(sandbox, 'probe', ca))


def test_explicit_installation_identity_is_copied_without_overwriting(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_text('-----BEGIN CERTIFICATE-----\npublic-ca\n')
    user = tmp_path / 'USER.md'
    user.write_text('# User\nInstallation identity\n')
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [
        ExecResult('', '', 0), ExecResult('FORGE_IMAGE_WORKSPACE_OK', '', 0),
        ExecResult('FORGE_INSTALLATION_USER_OK', '', 0),
    ]
    asyncio.run(prepare_forge_sandbox(sandbox, 'probe', ca, user_file=user))
    call = sandbox.exec.call_args_list[-1]
    assert call.kwargs['stdin'] == user.read_bytes()
    assert "flag:'wx'" in call.args[1][-1]


def test_empty_installation_identity_fails_before_mutation(tmp_path):
    user = tmp_path / 'USER.md'
    user.write_text(' ')
    sandbox = AsyncMock()
    with pytest.raises(ValueError, match='must not be empty'):
        asyncio.run(prepare_forge_sandbox(sandbox, 'probe', tmp_path / 'ca', user_file=user))
    sandbox.exec.assert_not_called()


@pytest.mark.parametrize('code,stdout', [(1, ''), (0, '')])
def test_installation_identity_staging_fails_closed(tmp_path, code, stdout):
    ca = tmp_path / 'ca.crt'
    ca.write_text('-----BEGIN CERTIFICATE-----\npublic-ca\n')
    user = tmp_path / 'USER.md'
    user.write_text('# User\nInstallation identity\n')
    sandbox = AsyncMock()
    sandbox.exec.side_effect = [
        ExecResult('', '', 0), ExecResult('FORGE_IMAGE_WORKSPACE_OK', '', 0),
        ExecResult(stdout, 'write failed', code),
    ]
    with pytest.raises(RuntimeError, match='USER.md staging failed'):
        asyncio.run(prepare_forge_sandbox(sandbox, 'probe', ca, user_file=user))
