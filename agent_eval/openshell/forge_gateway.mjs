// Eval-only supervisor: never connects to the live Forge gateway.
import fs from 'node:fs';
import crypto from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {DatabaseSync} from 'node:sqlite';
import {terminalReply} from './forge_completion.mjs';
const configPath = process.env.OPENCLAW_CONFIG_PATH;
if (!configPath?.startsWith('/sandbox/')) throw Error('eval config must be inside sandbox');
const input = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const model = input.agents.defaults.model.primary;
const deadline=Date.now()+(Number(process.env.AGENT_EVAL_TIMEOUT)+60)*1000;
const state = '/sandbox/.openclaw';
const env = {...process.env, HOME:'/sandbox', OPENCLAW_STATE_DIR:state,
  OPENCLAW_CONFIG_PATH:configPath, OPENCLAW_WORKSPACE_DIR:'/sandbox',
  OPENCLAW_PROVIDERS:JSON.stringify(input.models.providers),
  OPENCLAW_DEFAULT_MODEL:model, OPENCLAW_GATEWAY_TOKEN:crypto.randomBytes(32).toString('hex'),
  OPENCLAW_ALLOWED_PLUGINS:'[]', FORGE_EMBEDDING_MODEL_PATH:'',
  NO_PROXY:[process.env.NO_PROXY,'127.0.0.1','localhost'].filter(Boolean).join(','),
  no_proxy:[process.env.no_proxy,'127.0.0.1','localhost'].filter(Boolean).join(','),
  SQLITE_TMPDIR:state, TMPDIR:state};
function checked(argv) {
  const r=spawnSync(argv[0],argv.slice(1),{env,encoding:'utf8',timeout:90000});
  if(r.status!==0)throw Error('eval setup failed: '+(r.stderr||r.error?.message||'unknown'));
}
// Use image-owned profiles/permissions, rather than a second implementation.
checked(['node','/opt/forge/configure-openclaw.mjs']);
const config=JSON.parse(fs.readFileSync(configPath,'utf8'));
config.gateway.bind='loopback';
config.gateway.auth.mode='token';
config.gateway.port=18789;
config.agents.defaults.reasoningDefault='off';
if(!config.agents.entries?.default?.subagents?.allowAgents?.includes('brief-reader'))
  throw Error('image lacks brief-reader contract');
fs.writeFileSync(configPath,JSON.stringify(config),{mode:0o600});
const dist='/opt/openclaw/node_modules/openclaw/dist';
const initializers=new Set();
for(const name of fs.readdirSync(dist).sort()) {
  if(!/^openclaw-agent-db-[\w-]+\.m?js$/.test(name))continue;
  if(!fs.readFileSync(dist+'/'+name,'utf8').includes('ensureOpenClawAgentDatabaseSchema'))continue;
  for(const fn of Object.values(await import(pathToFileURL(dist+'/'+name).href)))
    if(typeof fn==='function'&&fn.name==='ensureOpenClawAgentDatabaseSchema')initializers.add(fn);
}
if(initializers.size!==1)throw Error('ambiguous database schema initializer');
for(const id of Object.keys(config.agents.entries)) {
  if(!/^[A-Za-z0-9_-]+$/.test(id))throw Error('invalid agent id');
  const dir=state+'/agents/'+id+'/agent';fs.mkdirSync(dir,{recursive:true});
  const path=dir+'/openclaw-agent.sqlite';const db=new DatabaseSync(path);
  try {if(db.prepare('SELECT count(*) AS n FROM sqlite_schema').get().n===0) {
    db.exec('PRAGMA auto_vacuum = NONE; VACUUM;');[...initializers][0](db,{agentId:id,path});
  }}finally{db.close();}
}
const log=fs.openSync(state+'/eval-gateway.log','a',0o600);
let gateway;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
let child;
let draftFixture;
let stopping;
function stop() {
  return stopping ??= (async()=>{
    child?.kill('SIGTERM');
    gateway?.kill('SIGTERM');
    if(gateway)await Promise.race([new Promise(r=>gateway.once('exit',r)),delay(3000)]);
    if(gateway?.exitCode===null)gateway.kill('SIGKILL');
    if(child?.exitCode===null)child.kill('SIGKILL');
    if(draftFixture)await draftFixture.close();
    fs.closeSync(log);
  })();
}
process.once('SIGTERM',()=>{stop().finally(()=>process.exit(143));});
try {
  if(env.AGENT_EVAL_DRAFT_FIXTURE==='1') {
    const {startDraftFixture}=await import('./forge_draft_fixture.mjs');
    draftFixture=await startDraftFixture();
    const prior=env.NODE_EXTRA_CA_CERTS ? fs.readFileSync(env.NODE_EXTRA_CA_CERTS) : Buffer.alloc(0);
    const trust=state+'/eval-combined-ca.crt';
    fs.writeFileSync(trust,Buffer.concat([prior,Buffer.from('\n'),fs.readFileSync(state+'/eval-tls.crt')]),{mode:0o600});
    Object.assign(env,{FORGE_DRAFTS_ENDPOINT:'https://localhost.localdomain:18443',FORGE_DRAFTS_TOKEN:draftFixture.token,
      NODE_EXTRA_CA_CERTS:trust,
      NO_PROXY:env.NO_PROXY+',localhost.localdomain',no_proxy:env.no_proxy+',localhost.localdomain'});
    // Test the actual launcher/TLS route before paying for a model turn.
    await new Promise((resolve,reject)=>{
      const probe=spawn('/sandbox/bin/forge-draft',['list'],{env,stdio:['ignore','ignore','pipe']});
      let errors='';probe.stderr.on('data',b=>errors+=b);
      const timer=setTimeout(()=>probe.kill('SIGKILL'),15000);
      probe.once('error',error=>{clearTimeout(timer);reject(error);});
      probe.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error('draft fixture preflight failed: '+errors));});
    });
    draftFixture.clearLedger();
  }
  gateway=spawn('openclaw',['gateway','run','--bind','loopback','--port','18789'],{env,stdio:['ignore',log,log]});
  let gatewayError;
  gateway.once('error',error=>{gatewayError=error;});
  let ready=false;
  for(let i=0;i<60;i++) {
    if(gatewayError||gateway.exitCode!==null)throw Error('isolated gateway exited; see eval-gateway.log');
    try {const r=await fetch('http://127.0.0.1:18789/healthz',{signal:AbortSignal.timeout(1000)});ready=r.ok;}catch{}
    if(ready)break;await delay(1000);
  }
  if(!ready)throw Error('isolated gateway readiness timed out');
  const sessionKey='agent:default:aeh-'+process.env.AGENT_EVAL_CASE_ID;
  const argv=['agent','--agent','default','--session-key',sessionKey,'--model',model,
    '--thinking',process.env.AGENT_EVAL_EFFORT,'--timeout',process.env.AGENT_EVAL_TIMEOUT,
    '--json','--message',process.argv[2]];
  child=spawn('openclaw',argv,{env,stdio:['ignore','pipe','pipe']});
  let stdout='',stderr='';child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);
  const timer=setTimeout(()=>child.kill('SIGTERM'),(Number(process.env.AGENT_EVAL_TIMEOUT)+30)*1000);
  let code;
  try {code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',resolve);});}
  finally {clearTimeout(timer);}
  if(code!==0){process.stderr.write(stderr);process.exitCode=code||1;}
  else {
    let payload;
    for(let i=0;i<stdout.length;i++)if(stdout[i]==='{')try{payload=JSON.parse(stdout.slice(i));break;}catch{}
    if(!payload)throw Error('gateway agent response was not JSON');
    const result=payload.result||payload;
    // A yielded parent CLI call has ended, but its logical task has not.
    let finalText=null;
    while(Date.now()<deadline) {
      const history=spawnSync('openclaw',['gateway','call','sessions.get','--json',
        '--params',JSON.stringify({key:sessionKey,limit:1000})],{env,encoding:'utf8',timeout:15000});
      if(history.status!==0)throw Error('cannot inspect eval session completion');
      const messages=JSON.parse(history.stdout).messages||[];
      finalText=terminalReply(messages);
      if(finalText)break;
      await delay(2000);
    }
    if(!finalText)throw Error('parent did not finish after child completion before deadline');
    result.meta??={};
    result.meta.finalAssistantVisibleText=finalText;
    result.meta.stopReason='stop';
    result.sessionKey=sessionKey;
    result.sessionId=result.meta?.agentMeta?.sessionId||result.sessionId;
    console.log(JSON.stringify(result));
  }
} finally {await stop();}
