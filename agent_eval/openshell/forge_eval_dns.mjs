// Resolve only the dedicated evaluation fixture hostname to loopback.
import dns from 'node:dns';
import {syncBuiltinESMExports} from 'node:module';
const original=dns.lookup;
dns.lookup=function(host,options,callback) {
  if(host!=='drafts.eval.test')return original.apply(this,arguments);
  if(typeof options==='function'){callback=options;options={};}
  queueMicrotask(()=>options?.all ? callback(null,[{address:'127.0.0.1',family:4}]) : callback(null,'127.0.0.1',4));
};
syncBuiltinESMExports();
