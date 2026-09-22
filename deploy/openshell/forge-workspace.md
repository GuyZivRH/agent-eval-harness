# Image-owned Forge workspace

Set `AGENT_EVAL_OPENSHELL_WORKSPACE=forge-image` to stage the pinned image's
workspace and skills. `AGENT_EVAL_FORGE_AI_GATEWAY_CA_FILE` must point to the
installation's upstream PEM CA. Provisioning stages that CA, restarts the
sandbox supervisor, and validates the image files and agent database.

Optionally set `AGENT_EVAL_FORGE_USER_FILE` to a readable, nonempty
installation-owned `USER.md` mounted into the orchestrator. It is copied to
`/sandbox/USER.md` and verified byte-for-byte. An existing destination causes
failure rather than silently replacing an image-owned file. Source this file
from the deployment's installation identity, not an AEH-authored persona.
Do not copy live sessions, memories, or unrelated workspace files.

Without this explicit input, no USER.md is synthesized. Persona instructions,
skills, tools, and database schema continue to come exclusively from the image.

Keep provider credentials as OpenClaw environment references in readable
configuration, for example `${FORGE_AI_GATEWAY_BEARER}`. Never materialize
injected credential values into files the agent may read into its context.
