// Eval-only HTTPS fixture for the agent fence. No send route or provider I/O.
// Wire shape follows forge-drafts-service/src/api_agent.rs and fdh1 vectors.
import fs from 'node:fs';
import https from 'node:https';
import crypto from 'node:crypto';
export const canonical = v => JSON.stringify(v, function(k,x) {
  return x && !Array.isArray(x) && typeof x === 'object'
    ? Object.fromEntries(Object.entries(x).sort(([a],[b])=>Buffer.compare(Buffer.from(a),Buffer.from(b)))) : x;
});
const hash = v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const closed=new Set(['withdrawn','discarded','consumed']);
const refusal=(status,error)=>({status,body:{error,reason:error}});
export class DraftStore {
  constructor(seed) {
    this.drafts=structuredClone(seed.drafts||[]);this.ledger=[];this.counter=0;
    this.loseCreateResponse=seed.lose_create_response===true;
    for(const draft of this.drafts) {
      draft.versions??=[{version_id:hash(draft.payload),parent:null,created_at:1790251200,purged:false}];
      draft.channel=draft.payload.channel;draft.context_ref=draft.payload.context_ref;
      draft.created_at??=1790251200;draft.updated_at??=1790251200;
    }
  }
  request(method,target,payload,ifMatch) {
    const result=this.handle(method,target,payload,ifMatch);
    this.ledger.push({method,target,payload,ifMatch,status:result.status,draft_id:result.body?.draft_id});
    return result;
  }
  handle(method,target,payload,ifMatch) {
    const url=new URL(target,'https://drafts.eval.test');
    const route=url.pathname;
    if(method==='GET'&&route==='/drafts')return {status:200,body:{drafts:this.drafts.filter(d=>!url.searchParams.has('state')||d.state===url.searchParams.get('state')).map(d=>({draft_id:d.draft_id,channel:d.channel,context_ref:d.context_ref,state:d.state,latest_version:d.versions.at(-1).version_id,versions:d.versions.length,created_at:d.created_at,updated_at:d.updated_at,pending_send_id:d.state==='accepted'?'eval-pending':null,accepted_at:null,pending_send_at:null,accepted_version:null}))}};
    const match=/^\/drafts\/([a-f0-9]{32})(\/versions)?$/.exec(route);
    const draft=match&&this.drafts.find(d=>d.draft_id===match[1]);
    if(match&&!draft)return refusal(404,'no_such_draft');
    if(method==='GET'&&draft&&!match[2])return {status:200,body:structuredClone(draft)};
    if(method==='DELETE'&&draft&&!match[2]) {
      if(draft.state==='accepted')return refusal(409,'send_pending');
      if(!closed.has(draft.state))draft.state='withdrawn';
      return {status:204};
    }
    if(method!=='POST'||!(route==='/drafts'||(draft&&match[2])))return refusal(404,'not_found');
    if(!payload||typeof payload.context_ref!=='string'||!payload.context_ref.trim())return refusal(400,'invalid_payload');
    if(payload.channel==='mail') {
      if(payload.provider!=='microsoft365'||!Array.isArray(payload.to)||!payload.to.length||typeof payload.subject!=='string'||typeof payload.body!=='string')return refusal(400,'invalid_payload');
    } else if(payload.channel==='slack') {
      if(typeof payload.channel_id!=='string'||typeof payload.text!=='string')return refusal(400,'invalid_payload');
    } else return refusal(400,'invalid_payload');
    const version=hash(payload);
    if(draft) {
      if(closed.has(draft.state))return refusal(410,'draft_closed');
      if(draft.state==='accepted')return refusal(409,'send_pending');
      if(!ifMatch)return refusal(428,'precondition_required');
      if(ifMatch.replace(/^"|"$/g,'')!==draft.versions.at(-1).version_id)return refusal(412,'superseded');
      if(payload.channel!==draft.channel)return refusal(409,'channel_mismatch');
      if(version!==draft.versions.at(-1).version_id)draft.versions.push({version_id:version,parent:draft.versions.at(-1).version_id,created_at:1790251200,purged:false});
      draft.payload=structuredClone(payload);draft.context_ref=payload.context_ref;draft.state='proposed';
      return {status:200,body:{draft_id:draft.draft_id,version_id:version,binding:'fdh1'}};
    }
    // Exact-content idempotency deliberately does not hide source/action duplicates.
    const existing=this.drafts.find(d=>!closed.has(d.state)&&d.channel===payload.channel&&d.context_ref===payload.context_ref&&d.versions.at(-1).version_id===version);
    if(existing)return {status:200,body:{draft_id:existing.draft_id,version_id:version,binding:'fdh1'}};
    let id;
    do {id=hash({counter:++this.counter}).slice(0,32);}while(this.drafts.some(d=>d.draft_id===id));
    this.drafts.push({draft_id:id,channel:payload.channel,context_ref:payload.context_ref,state:'proposed',created_at:1790251200,updated_at:1790251200,versions:[{version_id:version,parent:null,created_at:1790251200,purged:false}],payload:structuredClone(payload)});
    return {status:201,body:{draft_id:id,version_id:version,binding:'fdh1'}};
  }
}

export async function startDraftFixture(root='/sandbox',port=18443) {
  const store=new DraftStore(JSON.parse(fs.readFileSync(root+'/eval/drafts-seed.json','utf8')));
  const before=structuredClone(store.drafts);
  const token=crypto.randomBytes(24).toString('hex');
  const server=https.createServer({key:fs.readFileSync(root+'/.openclaw/eval-tls.key'),cert:fs.readFileSync(root+'/.openclaw/eval-tls.crt')},async(req,res)=>{
    if(req.headers.authorization!=='Bearer '+token){res.writeHead(401);res.end();return;}
    const chunks=[];let size=0;
    for await(const chunk of req){size+=chunk.length;if(size>1048576){res.writeHead(413);res.end();return;}chunks.push(chunk);}
    let body;
    try{body=size?JSON.parse(Buffer.concat(chunks).toString()):undefined;}catch{res.writeHead(400);res.end();return;}
    const result=store.request(req.method,req.url,body,req.headers['if-match']);
    if(store.loseCreateResponse&&req.method==='POST'&&req.url==='/drafts'&&result.status===201){store.loseCreateResponse=false;req.socket.destroy();return;}
    res.writeHead(result.status,{'content-type':'application/json'});res.end(result.body?JSON.stringify(result.body):undefined);
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
  return {token,port:server.address().port,clearLedger:()=>{store.ledger=[];},close:()=>new Promise(resolve=>{fs.writeFileSync(root+'/draft-snapshot.json',JSON.stringify({before,after:store.drafts,ledger:store.ledger},null,2));server.close(resolve);})};
}
