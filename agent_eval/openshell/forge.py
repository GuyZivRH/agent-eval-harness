"""Opt-in provisioning for the published Forge agent image (no AEH persona)."""

import logging
from pathlib import Path

from .sandbox import OpenShellSandbox

logger = logging.getLogger(__name__)


async def validate_forge_evidence(sandbox, name, relative):
    """Reject malformed sealed evidence using the running image's schema."""
    import json

    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("fixture evidence must be workspace-relative")
    result = await sandbox.exec(
        name,
        [
            "node",
            "--input-type=module",
            "-e",
            "import {validateBriefEvidence} from 'file:///sandbox/tools/publish-brief.mjs';"
            "import fs from 'node:fs';"
            "console.log(JSON.stringify(validateBriefEvidence(JSON.parse(fs.readFileSync("
            + json.dumps("/sandbox/" + path.as_posix())
            + ", 'utf8')))));",
        ],
    )
    if result.return_code:
        raise ValueError("image-owned evidence schema validation could not run")
    issues = json.loads(result.stdout)
    if not isinstance(issues, list) or issues:
        raise ValueError("invalid evidence fixture: " + json.dumps(issues))


def forge_input_files(root):
    """Exclude AEH's host-only Git/Claude scaffolding from OpenClaw inputs."""
    return [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.relative_to(root).parts[0] not in (".git", ".claude")
        and p.relative_to(root).as_posix() != "hooks/subagent_stop.py"
    ]


async def stage_skill_variant(sandbox, name, root, replacements):
    """Explicit opt-in skill-only replacement in a temporary eval workspace."""
    import hashlib

    pending = []
    for skill, record in replacements.items():
        if skill not in ("daily-briefing", "forge-drafts"):
            raise ValueError("unsupported skill override")
        path = root / record["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("skill source path escapes fixture")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("skill override hash mismatch")
        pending.append((skill, data, record["sha256"]))
    for skill, data, digest in pending:
        result = await sandbox.exec(
            name,
            [
                "node",
                "-e",
                "const fs=require('fs'),crypto=require('crypto');"
                "const p='/sandbox/skills/'+process.argv[1]+'/SKILL.md',b=fs.readFileSync(0);"
                "fs.writeFileSync(p,b);"
                "if(crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')!==process.argv[2])throw Error('skill verification failed');",
                skill,
                digest,
            ],
            stdin=data,
        )
        if result.return_code:
            raise RuntimeError("eval skill replacement failed: " + skill)


async def fingerprint_workspace(sandbox, name):
    import json

    result = await sandbox.exec(
        name,
        [
            "node",
            "-e",
            "const fs=require('fs'),crypto=require('crypto'),out={};"
            "for(const p of fs.readdirSync('/sandbox').filter(p=>p.endsWith('.md'))){"
            "out[p]=crypto.createHash('sha256').update(fs.readFileSync('/sandbox/'+p)).digest('hex');}"
            "for(const d of ['skills','tools']){for(const e of fs.readdirSync('/sandbox/'+d,{recursive:true})){"
            "const p=d+'/'+e;if(!fs.statSync('/sandbox/'+p).isFile())continue;"
            "out[p]=crypto.createHash('sha256').update(fs.readFileSync('/sandbox/'+p)).digest('hex');}}"
            "console.log(JSON.stringify(out));",
        ],
    )
    if result.return_code:
        raise RuntimeError("cannot verify loaded skills and tools")
    return json.loads(result.stdout)


async def collect_forge_usage(sandbox, name, root, env):
    """Export all accepted child sessions, and reconcile per-run aggregates.

    An absent export is a measurement failure, never a zero-token child.
    Raw traces stay in the report; only usage metadata enters the summary.
    """
    import hashlib
    import json
    from agent_eval.forge_contract import account_usage, reconcile_requests

    events, expected, visited, errors = [], set(), set(), []
    queue = [root / "openclaw-trajectory-events.jsonl"]
    while queue:
        path = queue.pop(0)
        try:
            rows = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
        except (OSError, ValueError):
            errors.append("missing or malformed trajectory: " + path.name)
            continue
        events.extend(rows)
        expected.update(
            e["runId"]
            for e in rows
            if e.get("runId")
            and e.get("type")
            in ("session.started", "prompt.submitted", "model.completed")
        )
        for event in rows:
            if event.get("type") != "tool.result":
                continue
            details = event.get("data", {}).get("result", {}).get("details") or {}
            key = details.get("childSessionKey")
            if details.get("status") != "accepted" or not key:
                continue
            if details.get("runId"):
                expected.add(details["runId"])
            if key in visited:
                continue
            visited.add(key)
            if len(visited) > 64:
                errors.append("child trace inventory exceeds limit")
                continue
            export = "eval-child-" + hashlib.sha256(key.encode()).hexdigest()[:16]
            result = await sandbox.exec(
                name,
                [
                    "openclaw",
                    "sessions",
                    "export-trajectory",
                    "--session-key",
                    key,
                    "--workspace",
                    "/sandbox",
                    "--output",
                    export,
                    "--json",
                ],
                env=env,
                workdir="/sandbox",
                timeout_s=60,
            )
            if result.return_code:
                errors.append("child trajectory export failed: " + key)
                continue
            result = await sandbox.exec(
                name,
                ["cat", f"/sandbox/.openclaw/trajectory-exports/{export}/events.jsonl"],
            )
            if result.return_code:
                errors.append("child trajectory missing: " + key)
                continue
            child_path = root / (export + ".jsonl")
            child_path.write_text(result.stdout)
            queue.append(child_path)
    usage = account_usage(events, expected_runs=expected)
    requests = reconcile_requests(events, usage)
    usage["issues"].extend(requests["issues"])
    usage["requests"] = len(requests["requests"])
    (root / "request-usage.json").write_text(json.dumps(requests, indent=2))
    usage["issues"].extend(errors)
    usage["complete"] = not usage["issues"]
    usage["sessions"] = 1 + len(visited)
    usage["source"] = "openclaw-run-aggregates"
    usage["tool_calls"] = len(
        {
            (e.get("runId"), e.get("sourceSeq", e.get("seq")))
            for e in events
            if e.get("type") == "tool.call"
        }
    )
    (root / "usage.json").write_text(json.dumps(usage, indent=2))
    return usage


async def collect_required_artifacts(sandbox, name, root, paths):
    errors = []
    for raw in paths:
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or str(path) == ".":
            raise ValueError("artifact path must be relative to the workspace")
        try:
            await sandbox.download(name, "/sandbox/" + str(path), root / path)
            if not (root / path).is_file():
                raise ValueError("required artifact is not a regular file")
        except Exception:
            errors.append("required artifact unavailable: " + str(path))
    return errors


async def prepare_forge_sandbox(
    sandbox: OpenShellSandbox,
    name: str,
    ca_file: Path,
    *,
    user_file: Path | None = None,
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
        [
            "node",
            "-e",
            "const fs=require('fs');"
            "fs.mkdirSync('/sandbox/persist/.forge-tls',{recursive:true});"
            "fs.writeFileSync('/sandbox/persist/.forge-tls/ca.crt',fs.readFileSync(0),{mode:0o400});",
        ],
        stdin=ca,
    )
    if result.return_code:
        raise RuntimeError(f"Forge upstream CA staging failed: {result.stderr}")
    await sandbox.restart(name)
    result = await sandbox.exec(
        name,
        ["node", "--input-type=module"],
        stdin=Path(__file__).with_name("forge_bootstrap.mjs").read_bytes(),
        timeout_s=90,
    )
    if result.return_code:
        raise RuntimeError(
            f"Forge image workspace initialization failed: {result.stderr}"
        )
    if "FORGE_IMAGE_WORKSPACE_OK" not in result.stdout:
        raise RuntimeError("Forge workspace validation did not report success")
    logger.info("%s", result.stdout.strip())
    if user is not None:
        result = await sandbox.exec(
            name,
            [
                "node",
                "-e",
                "const fs=require('fs');"
                "const p='/sandbox/USER.md',data=fs.readFileSync(0);"
                "fs.writeFileSync(p,data,{flag:'wx',mode:0o600});"
                "if(!fs.readFileSync(p).equals(data))throw Error('USER.md verification failed');"
                "console.log('FORGE_INSTALLATION_USER_OK');",
            ],
            stdin=user,
        )
        if result.return_code or "FORGE_INSTALLATION_USER_OK" not in result.stdout:
            raise RuntimeError("Forge installation USER.md staging failed")
        logger.info("Forge installation USER.md staged and verified")


async def run_forge_gateway(sandbox, name, prompt, *, env, timeout_s, effort, case_id):
    """Run through an ephemeral loopback gateway inside this eval sandbox."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", case_id):
        raise ValueError("invalid eval case id")
    if env.get("AGENT_EVAL_DRAFT_FIXTURE") == "1":
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(directory / "eval-tls.key"),
                    "-out",
                    str(directory / "eval-tls.crt"),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost.localdomain",
                    "-addext",
                    "subjectAltName=DNS:localhost.localdomain",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            material = {p.name: p.read_bytes() for p in directory.iterdir()}
        material.update(
            {
                f: Path(__file__).with_name(f).read_bytes()
                for f in ("forge_draft_fixture.mjs",)
            }
        )
        for filename, data in material.items():
            staged = await sandbox.exec(
                name,
                [
                    "node",
                    "-e",
                    "const fs=require('fs');fs.mkdirSync('/sandbox/.openclaw',{recursive:true});"
                    "fs.writeFileSync('/sandbox/.openclaw/'+process.argv[1],fs.readFileSync(0),{mode:0o600});",
                    filename,
                ],
                stdin=data,
            )
            if staged.return_code:
                raise RuntimeError("draft fixture staging failed")
    script = Path(__file__).with_name("forge_gateway.mjs").read_bytes()
    staged = await sandbox.exec(
        name,
        [
            "node",
            "-e",
            "const fs=require('fs');fs.mkdirSync('/sandbox/.openclaw',{recursive:true});"
            "fs.writeFileSync('/sandbox/.openclaw/eval-gateway.mjs',fs.readFileSync(0),{mode:0o600});",
        ],
        stdin=script,
    )
    if staged.return_code:
        raise RuntimeError("failed to stage isolated gateway runner")
    helper = Path(__file__).with_name("forge_completion.mjs").read_bytes()
    helper_result = await sandbox.exec(
        name,
        [
            "node",
            "-e",
            "const fs=require('fs');fs.writeFileSync('/sandbox/.openclaw/forge_completion.mjs',fs.readFileSync(0),{mode:0o600});",
        ],
        stdin=helper,
    )
    if helper_result.return_code:
        raise RuntimeError("failed to stage gateway completion helper")
    gateway_env = dict(
        env,
        AGENT_EVAL_CASE_ID=case_id,
        AGENT_EVAL_TIMEOUT=str(timeout_s),
        AGENT_EVAL_EFFORT=effort or "high",
    )
    return await sandbox.exec(
        name,
        ["node", "/sandbox/.openclaw/eval-gateway.mjs", prompt],
        env=gateway_env,
        workdir="/sandbox",
        timeout_s=timeout_s + 120,
    )
