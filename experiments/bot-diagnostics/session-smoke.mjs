import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {ToolAdapter} from './adapters.mjs';
import {Evidence,verify} from './evidence.mjs';
import {snapshotSources} from './runtime.mjs';

if(process.argv.length!==3) throw new Error('Usage: node session-smoke.mjs NEW_OUTPUT_DIRECTORY');
const root=path.resolve(process.argv[2]);
if(fs.existsSync(root)) throw new Error('Output directory already exists');
fs.mkdirSync(path.dirname(root),{recursive:true});
fs.mkdirSync(root);
const checks=[];
const openAdapters=new Set();
const fixtureConfig={schema:1,loopback_only:true,cases:['good_cookie_reuse','bad_cookie_rotation_and_home_bootstrap','retry_after_same_session','persistent_403_bounded','cooldown_persists_across_resume','robots_blocked_without_retry'],retry_after_seconds:{rate:1,defer:120},max_auto_wait_ms:{defer:0},robots:{robots:'Disallow /robots/target'}};

// Browser-backed session recovery checks. The fixture is loopback only.
const requests=[];
const counters={};
let robotsDeny=false;
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture');
  const key=url.pathname.split('/')[1]||'';
  const count=counters[url.pathname]=(counters[url.pathname]||0)+1;
  const cookie=req.headers.cookie||'';
  requests.push({path:url.pathname,cookie_present:cookie.includes('lab_session='),cookie_good:/(?:^|;\s*)lab_session=good=1(?:;|$)/.test(cookie),cookie_poison:/(?:^|;\s*)poison=1(?:;|$)/.test(cookie)});
  if(url.pathname==='/robots.txt') {res.end(robotsDeny?'User-agent: *\nDisallow: /robots/target\n':'User-agent: *\nAllow: /');return;}
  if(url.pathname.endsWith('/favicon.ico')) {res.writeHead(404);res.end();return;}
  const page=()=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end('<!doctype html><title>Session fixture</title><p>Fixture content</p>');};
  if(url.pathname.endsWith('/home')) {
    if(key==='good') res.setHeader('Set-Cookie','lab_session=good=1; Path=/; SameSite=Lax');
    if(key==='rotate') {
      res.setHeader('Set-Cookie',`lab_session=${count===1?'bad=1':'good=1'}; Path=/; SameSite=Lax`);
      if(count===1) res.appendHeader('Set-Cookie','poison=1; Path=/; SameSite=Lax');
    }
    if(key==='rate') res.setHeader('Set-Cookie','lab_session=stable; Path=/; SameSite=Lax');
    if(key==='defer') res.setHeader('Set-Cookie','lab_session=stable; Path=/; SameSite=Lax');
    page();return;
  }
  const target=url.pathname.endsWith('/target');
  if(key==='good'&&target) {if(cookie.includes('lab_session=good')) page();else {res.writeHead(403);res.end('<title>Access Denied</title>');}return;}
  if(key==='rotate'&&target) {if(cookie.includes('lab_session=good')&&!cookie.includes('poison=1')) page();else {res.writeHead(403);res.end('<title>Access Denied</title>');}return;}
  if(key==='rate'&&target&&count===1) {res.writeHead(429,{'Retry-After':'1'});res.end('Rate limited');return;}
  if(key==='rate'&&target) {if(cookie.includes('lab_session=stable')) page();else {res.writeHead(403);res.end('<title>Access Denied</title>');}return;}
  if(key==='persistent'&&target) {res.writeHead(403);res.end('<title>Access Denied</title>');return;}
  if(key==='defer'&&target) {res.writeHead(429,{'Retry-After':'120'});res.end('Rate limited');return;}
  page();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
fixtureConfig.origin=origin;
const siteFor=(id,home,targets)=>({id,origins:[origin],links:{home:`${origin}/${home}`,targets:targets.map(p=>`${origin}/${p}`)},selectors:{}});
const profile=(extra={})=>({id:'pooled',lightpanda:{release:'1.0.0',profile:'compat'},session_pool:{...extra}});
function saveSources(evidence){
  const testPath=fileURLToPath(import.meta.url);
  evidence.save('sources.json',{...snapshotSources(evidence),source_tests:{'session-smoke.mjs':evidence.blob(fs.readFileSync(testPath))}});
  evidence.save('fixture-config.json',fixtureConfig);
}
async function run(id,home,targets,policy={},evidence=null) {
  const site=siteFor(id,home,targets);
  const ev=evidence||new Evidence(path.join(root,id));
  saveSources(ev);
  const adapter=new ToolAdapter({tool:'rebrowser-lightpanda',site,evidence:ev,fixture:true,profile:profile(policy)});
  openAdapters.add(adapter);
  await adapter.open();
  return {adapter,evidence:ev,home:await adapter.homepage(site.links.home),targets:[]};
}
async function finish(item){await item.adapter.close();openAdapters.delete(item.adapter);const check=verify(item.evidence.directory);assert.equal(check.ok,true,JSON.stringify(check));return check;}
try {
  // A good homepage session survives multiple target navigations in one context.
  {
    const item=await run('good','good/home',['good/target']);
    const browser=item.adapter.browser, context=browser.context, page=browser.page;
    for(let i=0;i<2;i++) item.targets.push(await item.adapter.followLink(`${origin}/good/target`));
    assert.ok(item.targets.every(x=>x.outcome==='content_observed'));
    assert.equal(item.adapter.browser,browser);assert.equal(item.adapter.browser.context,context);assert.equal(item.adapter.browser.page,page);
    assert.equal(new Set(item.targets.map(x=>x.session_pool.session_id)).size,1);
    assert.ok(requests.filter(x=>x.path==='/good/target').every(x=>x.cookie_present));
    checks.push({case:'good_cookie_reuse',evidence:await finish(item)});
  }
  // A poisoned session is retired, home is bootstrapped again, and only the good cookie reaches retry.
  {
    const item=await run('rotate','rotate/home',['rotate/target']);
    const pid=item.adapter.browser.child?.pid;
    assert.ok(Number.isInteger(pid)&&pid>0,'Lightpanda child PID must be available');
    const originalContext=item.adapter.browser.context;
    const first=await item.adapter.followLink(`${origin}/rotate/target`);item.targets.push(first);
    assert.equal(first.outcome,'content_observed');
    assert.ok(first.session_pool?.session_id);
    const scoped=requests.filter(x=>x.path==='/rotate/target');
    assert.equal(scoped.length,2);assert.equal(scoped[0].cookie_good,false);assert.equal(scoped[0].cookie_poison,true);
    assert.equal(scoped[1].cookie_good,true);assert.equal(scoped[1].cookie_poison,false);
    const homes=requests.filter(x=>x.path==='/rotate/home');
    assert.equal(homes.length,2);assert.equal(homes[1].cookie_poison,false);
    assert.ok(item.evidence.results.some(x=>x.role==='session_homepage'));
    assert.equal(item.evidence.records.every(x=>x.key==='rebrowser-lightpanda/rotate'),true);
    assert.equal(item.adapter.browser.child?.pid,pid);
    assert.notEqual(item.adapter.browser.context,originalContext);
    const targetRecords=item.evidence.records.filter(x=>x.url===`${origin}/rotate/target`);
    assert.equal(targetRecords.length,2);
    assert.ok(targetRecords[0].pool_session_id&&targetRecords[1].pool_session_id);
    assert.notEqual(targetRecords[0].pool_session_id,targetRecords[1].pool_session_id);
    checks.push({case:'bad_cookie_rotation_and_home_bootstrap',evidence:await finish(item)});
  }
  // Rate limiting waits for Retry-After and reuses the same session.
  {
    const item=await run('rate','rate/home',['rate/target']);
    const result=await item.adapter.followLink(`${origin}/rate/target`);item.targets.push(result);
    assert.equal(result.outcome,'content_observed');
    const records=item.evidence.records.filter(x=>x.url===`${origin}/rate/target`&&x.status!==undefined);
    assert.equal(records.length,2);
    assert.ok(Date.parse(records[1].started_at)-Date.parse(records[0].started_at)>=900);
    assert.ok(records[0].pool_session_id&&records[1].pool_session_id);
    assert.equal(records[0].pool_session_id,records[1].pool_session_id);
    assert.ok(item.evidence.records.every(x=>x.key==='rebrowser-lightpanda/rate'));
    checks.push({case:'retry_after_same_session',evidence:await finish(item)});
  }
  // Persistent denial is bounded to one retry and retires the bad session.
  {
    const item=await run('persistent','persistent/home',['persistent/target']);
    const result=await item.adapter.followLink(`${origin}/persistent/target`);item.targets.push(result);
    assert.equal(item.evidence.records.filter(x=>x.url===`${origin}/persistent/target`).length,2);
    assert.equal(result.session_pool?.retired,true);
    assert.equal(item.evidence.records.every(x=>x.key==='rebrowser-lightpanda/persistent'),true);
    checks.push({case:'persistent_403_bounded',evidence:await finish(item)});
  }
  // Long cooldown is persisted and deferred by a fresh adapter without another request.
  {
    const id='defer',policy={max_auto_wait_ms:0},site=siteFor(id,'defer/home',['defer/target']);
    const directory=path.join(root,id),ev=new Evidence(directory);
    saveSources(ev);
    const adapter=new ToolAdapter({tool:'rebrowser-lightpanda',site,evidence:ev,fixture:true,profile:profile(policy)});
    openAdapters.add(adapter);
    await adapter.open();
    await adapter.homepage(site.links.home);const res=await adapter.followLink(`${origin}/defer/target`);assert.equal(res.outcome,'session_cooldown_deferred');
    await adapter.close();openAdapters.delete(adapter);
    const before=requests.length, resumedEvidence=new Evidence(directory,true);
    const resumed=new ToolAdapter({tool:'rebrowser-lightpanda',site,evidence:resumedEvidence,fixture:true,profile:profile(policy)});
    openAdapters.add(resumed);
    await resumed.open();
    const resumedResult=await resumed.followLink(`${origin}/defer/target`);assert.equal(resumedResult.outcome,'session_cooldown_deferred');assert.equal(requests.length,before);
    await resumed.close();openAdapters.delete(resumed);
    const deferredVerify=verify(directory);assert.equal(deferredVerify.ok,true,JSON.stringify(deferredVerify));
    checks.push({case:'cooldown_persists_across_resume',evidence:deferredVerify});
  }
  // robots denial stops before the target reaches the fixture server.
  {
    robotsDeny=true;
    const item=await run('robots','robots/home',['robots/target']);
    const result=await item.adapter.followLink(`${origin}/robots/target`);item.targets.push(result);
    assert.equal(result.outcome,'robots_denied');
    assert.equal(requests.some(x=>x.path==='/robots/target'),false);
    assert.equal(item.evidence.records.filter(x=>x.url===`${origin}/robots/target`).length,0);
    checks.push({case:'robots_blocked_without_retry',evidence:await finish(item)});
    robotsDeny=false;
  }
  fs.writeFileSync(path.join(root,'fixture-server-requests.json'),JSON.stringify(requests,null,2)+'\n');
  const summary={fixture_only:true,checks,request_count:requests.length};
  fs.writeFileSync(path.join(root,'summary.json'),JSON.stringify(summary,null,2)+'\n');
  console.log(JSON.stringify(summary,null,2));
} catch(error) {console.error(error.stack||error);process.exitCode=1;}
finally {
  await Promise.allSettled([...openAdapters].map(adapter=>adapter.close()));
  await new Promise(resolve=>server.close(resolve));
}
