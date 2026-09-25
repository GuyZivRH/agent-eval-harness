import subprocess
import json
from pathlib import Path


def test_fixture_hash_matches_authoritative_service_payload_vectors():
    module = Path(__file__).parents[1] / "agent_eval/openshell/forge_draft_fixture.mjs"
    vectors = json.loads(
        (Path(__file__).parent / "fixtures/forge-fdh1.json").read_text()
    )["vectors"]
    script = """
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import {canonical} from MODULE;
for (const v of JSON.parse(fs.readFileSync(0,'utf8'))) {
  const value=canonical(JSON.parse(v.input));
  assert.equal(value,v.canonical,v.name);
  assert.equal(crypto.createHash('sha256').update(value).digest('hex'),v.sha256,v.name);
}
""".replace("MODULE", repr(module.as_uri()))
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(vectors),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_store_preserves_real_create_idempotency_and_revision_lifecycle():
    module = Path(__file__).parents[1] / "agent_eval/openshell/forge_draft_fixture.mjs"
    script = """
import assert from 'node:assert/strict';
import {DraftStore} from MODULE;
const payload={channel:'mail',provider:'microsoft365',context_ref:'m365:one',to:['a@example.test'],subject:'Review',body:'First'};
const store=new DraftStore({drafts:[]});
const a=store.request('POST','/drafts',payload);
assert.equal(a.status,201);
assert.equal(store.request('POST','/drafts',payload).body.draft_id,a.body.draft_id);
const b=store.request('POST','/drafts',{...payload,body:'Changed'});
assert.notEqual(b.body.draft_id,a.body.draft_id); // reproduce actual defect, no source dedup
assert.equal(store.request('POST','/drafts/'+a.body.draft_id+'/versions',payload,'bad').status,412);
store.drafts[0].state='accepted';
assert.equal(store.request('GET','/drafts?state=proposed').body.drafts.length,1);
assert.equal(store.request('GET','/drafts').body.drafts.length,2);
assert.equal(store.request('POST','/drafts/'+a.body.draft_id+'/versions',payload,a.body.version_id).body.error,'send_pending');
store.drafts[0].state='discarded';
assert.equal(store.request('POST','/drafts/'+a.body.draft_id+'/versions',payload,a.body.version_id).status,410);
assert.equal(store.request('POST','/send',payload).status,404);
""".replace("MODULE", repr(module.as_uri()))
    run = subprocess.run(
        ["node", "--input-type=module", "-e", script], text=True, capture_output=True
    )
    assert run.returncode == 0, run.stderr
