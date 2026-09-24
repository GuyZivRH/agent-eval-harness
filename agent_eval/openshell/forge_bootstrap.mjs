// Headless counterpart of Forge's image staging and database bootstrap.
// Persona, tools, skills, and schema all come from the immutable agent image.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {DatabaseSync} from 'node:sqlite';

fs.cpSync('/opt/forge/agent-workspace', '/sandbox', {recursive: true, dereference: true});
fs.cpSync('/opt/forge/skills', '/sandbox/skills', {recursive: true, dereference: true});
for (const file of ['AGENTS.md', 'IDENTITY.md', 'skills/daily-briefing/SKILL.md', 'skills/microsoft365/SKILL.md']) {
  const data = fs.readFileSync('/sandbox/' + file);
  if (!data.length) throw new Error('Empty image file: ' + file);
  console.log('IMAGE_FILE_READABLE ' + file + ' bytes=' + data.length);
}

const agentId = 'main'; // agent exec's default agent, not Forge UI's 'default'.
const dir = '/sandbox/.openclaw/agents/' + agentId + '/agent';
fs.mkdirSync(dir, {recursive: true, mode: 0o700});
const databasePath = path.join(dir, 'openclaw-agent.sqlite');
const db = new DatabaseSync(databasePath);
try {
  if (db.prepare('SELECT count(*) AS count FROM sqlite_schema').get().count === 0) {
    // beta.3's incremental-auto-vacuum initialization fails on FTS shadow
    // tables. Match Forge's bootstrap, using the image's own schema owner.
    db.exec('PRAGMA auto_vacuum = NONE; VACUUM;');
    const dist = '/opt/openclaw/node_modules/openclaw/dist';
    const initializerName = 'ensureOpenClawAgentDatabaseSchema';
    const initializers = new Set();
    for (const name of fs.readdirSync(dist).sort()) {
      if (!/^openclaw-agent-db-[\w-]+\.m?js$/.test(name)) continue;
      const modulePath = path.join(dist, name);
      if (!fs.readFileSync(modulePath, 'utf8').includes(initializerName)) continue;
      const module = await import(pathToFileURL(modulePath).href);
      for (const value of Object.values(module)) {
        if (typeof value === 'function' && value.name === initializerName) initializers.add(value);
      }
    }
    if (initializers.size !== 1) throw new Error(`Expected one image database initializer, found ${initializers.size}`);
    const [initialize] = initializers;
    initialize(db, {agentId, path: databasePath});
  }
  const meta = db.prepare('SELECT role, schema_version, agent_id FROM schema_meta WHERE meta_key = ?').get('primary');
  if (meta?.role !== 'agent' || meta?.agent_id !== agentId || !Number.isInteger(meta?.schema_version)) {
    throw new Error('Invalid image database ownership');
  }
} finally {
  db.close();
}
console.log('FORGE_IMAGE_WORKSPACE_OK');
