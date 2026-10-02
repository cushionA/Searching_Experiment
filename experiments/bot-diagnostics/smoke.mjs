import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {execute,prepare} from './framework.mjs';

// Native clients and browser sessions against a local fixture only; no real-site requests.
const requests=[];
const server=http.createServer((req,res)=>{
  requests.push({path:req.url,cookie_present:(req.headers.cookie||'').includes('lab_session=present')});
  if(req.url==='/robots.txt'){res.end('User-agent: *\nAllow: /');return;}
  if(req.url==='/'){res.setHeader('Set-Cookie','lab_session=present; Path=/; SameSite=Lax');}
  else if(req.url.startsWith('/inside')||req.url==='/redirect'){
    if(!(req.headers.cookie||'').includes('lab_session=present')){res.writeHead(403);res.end('<title>Access Denied</title>Missing fixture session');return;}
  }
  if(req.url==='/redirect'){res.writeHead(302,{Location:'/inside'});res.end();return;}
  if(req.url==='/favicon.ico'){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.end('<!doctype html><title>Fixture page</title><a id="fixture-link" href="/redirect">内部リンク</a><p>Local session fixture</p>');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const directory=path.resolve(process.argv[2]||path.join(repo,'.lab-output/framework-fixture-smoke'));
try{
  const manifest=prepare({schema:1,tools:['wreq-js','impit','patchright','rebrowser-lightpanda','playwright-baseline'],
    limits:{requests_per_tool_site:25,body_bytes_per_tool_site:8388608},
    sites:[{id:'fixture',origins:[origin],links:{home:origin+'/',targets:[origin+'/redirect',origin+'/inside2']},params:{},selectors:{primary:'#fixture-link'}}]}, {fixture:true});
  const result=await execute({manifest,directory,fixture:true});
  fs.writeFileSync(path.join(directory,'fixture-server-requests.json'),JSON.stringify(requests,null,2)+'\n');
  assert.equal(result.verification.ok,true);
  for(const outcome of result.outcomes)assert.equal(outcome.state,'navigation_completed',JSON.stringify(outcome));
  assert.ok(requests.filter(r=>r.path==='/inside'||r.path==='/inside2').every(r=>r.cookie_present));
  const ledger=JSON.parse(fs.readFileSync(path.join(directory,'ledger.json')));
  for(const tool of manifest.tools){
    assert.ok(ledger.records.some(r=>r.key===tool+'/fixture'&&r.status===302));
    assert.ok(ledger.records.some(r=>r.key===tool+'/fixture'&&r.url===origin+'/inside'&&r.status===200));
  }
  console.log(JSON.stringify({...result,fixture_only:true,session_cookie_verified:true,redirect_accounting_verified:true},null,2));
}finally{await new Promise(resolve=>server.close(resolve));}
