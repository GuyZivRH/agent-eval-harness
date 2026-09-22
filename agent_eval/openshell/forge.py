"""Opt-in provisioning for the published Forge agent image (no AEH persona)."""

import logging
from pathlib import Path

from .sandbox import OpenShellSandbox

logger = logging.getLogger(__name__)


async def prepare_forge_sandbox(
    sandbox: OpenShellSandbox, name: str, ca_file: Path
) -> None:
    """Stage upstream trust, reload it, then materialize the image's workspace.

    The supervisor trusts the image's CA path at startup. This is distinct
    from Node's supervisor-issued CA; never overwrite NODE_EXTRA_CA_CERTS.
    """
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
