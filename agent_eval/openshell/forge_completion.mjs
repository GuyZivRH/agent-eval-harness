export function terminalReply(messages) {
  const last=messages.at(-1);
  if(last?.role!=='assistant'||last.stopReason!=='stop')return null;
  const text=(last.content||[]).filter(b=>{
    if(b.type!=='text')return false;
    let signature;try{signature=JSON.parse(b.textSignature);}catch{}
    return signature?.phase!=='commentary';
  }).map(b=>b.text).join('\n').trim();
  return text||null;
}
