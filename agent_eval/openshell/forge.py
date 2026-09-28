"""Opt-in provisioning for the published Forge agent image (no AEH persona)."""

import logging
from pathlib import Path

from .sandbox import OpenShellSandbox

logger = logging.getLogger(__name__)


def resolve_forge_user_file(config_path: Path, configured: str | None, installed: str | None) -> Path | None:
    """Use a case-owned mock persona only when the eval explicitly requests one."""
    if not configured:
        return Path(installed) if installed else None
    relative = Path(configured)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise ValueError("runner.settings.forge_user_file must be a relative file below eval.yaml")
    base = config_path.resolve().parent
    selected = (base / relative).resolve()
    if not selected.is_relative_to(base) or not selected.is_file():
        raise ValueError("runner.settings.forge_user_file must name a file below eval.yaml")
    return selected


async def prepare_forge_sandbox(
    sandbox: OpenShellSandbox, name: str, ca_file: Path, *, user_file: Path | None = None
) -> None:
    """Stage upstream trust, reload it, then materialize the image's workspace.

    The supervisor trusts the image's CA path at startup. This is distinct
    from Node's supervisor-issued CA; never overwrite NODE_EXTRA_CA_CERTS.
    """
    # Installation identity is optional, explicit input, never an AEH persona.
    user = user_file.read_bytes() if user_file is not None else None
    if user is not None and not user.strip():
        raise ValueError("Forge installation USER.md must not be empty")
    ca = ca_file.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" not in ca:
        raise ValueError("Forge upstream CA must be a PEM certificate")
    result = await sandbox.exec(
        name,
        ["node", "-e", "const fs=require('fs');"
         "fs.mkdirSync('/sandbox/persist/.forge-tls',{recursive:true});"
         "fs.writeFileSync('/sandbox/persist/.forge-tls/ca.crt',fs.readFileSync(0),{mode:0o400});"],
        stdin=ca,
    )
    if result.return_code:
        raise RuntimeError(f"Forge upstream CA staging failed: {result.stderr}")
    await sandbox.restart(name)
    result = await sandbox.exec(
        name, ["node", "--input-type=module"],
        stdin=Path(__file__).with_name("forge_bootstrap.mjs").read_bytes(),
        timeout_s=90,
    )
    if result.return_code:
        raise RuntimeError(f"Forge image workspace initialization failed: {result.stderr}")
    if "FORGE_IMAGE_WORKSPACE_OK" not in result.stdout:
        raise RuntimeError("Forge workspace validation did not report success")
    logger.info("%s", result.stdout.strip())
    if user is not None:
        result = await sandbox.exec(
            name,
            ["node", "-e", "const fs=require('fs');"
             "const p='/sandbox/USER.md',data=fs.readFileSync(0);"
             "fs.writeFileSync(p,data,{flag:'wx',mode:0o600});"
             "if(!fs.readFileSync(p).equals(data))throw Error('USER.md verification failed');"
             "console.log('FORGE_INSTALLATION_USER_OK');"],
            stdin=user,
        )
        if result.return_code or "FORGE_INSTALLATION_USER_OK" not in result.stdout:
            raise RuntimeError("Forge installation USER.md staging failed")
        logger.info("Forge installation USER.md staged and verified")
