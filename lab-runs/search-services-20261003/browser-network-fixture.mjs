/** Offline headful check that legacy gates no longer break browser dependencies. */
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence,verify} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
const root=path.dirname(fileURLToPath(import.meta.url));
const hits=[];
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const close=server=>new Promise(resolve=>server.close(resolve));
const dependencies=http.createServer((req,res)=>{
  hits.push({url:req.url,method:req.method});
  res.setHeader('Access-Control-Allow-Origin','*');
  if(req.url==='/pixel.gif'){res.setHeader('Content-Type','image/gif');res.end(Buffer.from('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==','base64'));}
  else if(req.url==='/frame'){res.setHeader('Content-Type','text/html');res.end('<p>Third-party frame loaded</p>');}
  else {res.setHeader('Content-Type','text/plain');res.end('POST dependency complete');}
});
await listen(dependencies);
const third=`http://127.0.0.1:${dependencies.address().port}`;
const main=http.createServer((req,res)=>{
  hits.push({url:req.url,method:req.method});
  if(req.url==='/robots.txt'){res.end('User-agent: *\nDisallow: /');return;}
  if(req.url==='/sw.js'){res.setHeader('Content-Type','application/javascript');res.end('self.addEventListener("fetch",()=>{});');return;}
  res.setHeader('Content-Type','text/html');
  res.end(`<!doctype html><title>Normal browser fixture</title><main>Ordinary network fixture</main><img src="${third}/pixel.gif"><iframe src="${third}/frame"></iframe><script>fetch('${third}/query',{method:'POST',body:'fixture search'}).then(r=>r.text()).then(t=>document.querySelector('main').textContent=t);navigator.serviceWorker.register('/sw.js');</script>`);
});
await listen(main);
const origin=`http://127.0.0.1:${main.address().port}`;
const evidence=new Evidence(path.join(root,'browser-observation-fixture'),false,{policy:'browser_observation',authorization:'User scoped old restrictions to grounding; offline browser network fixture.'});
evidence.save('fixture-source.json',{source_sha256:evidence.blob(fs.readFileSync(fileURLToPath(import.meta.url))),external_network:false});
let adapter;
try {
  adapter=new ToolAdapter({tool:'patchright',site:{id:'normal-fixture',origins:[origin]},evidence,fixture:true,options:{headful:true}});
  await adapter.open();
  const result=await adapter.accessLink(origin+'/',{role:'target'});
  assert.equal(result.http_status,200);
  assert.equal(result.outcome,'content_observed');
  assert.equal(result.robots_outcome,'not_enforced_browser_observation');
  assert.equal(result.blocked_requests.length,0);
  assert.equal(adapter.browser.runtime.headless,false);
  assert.ok(!adapter.browser.ua.includes('DiscoveryLab'));
  assert.ok(hits.some(x=>x.url==='/pixel.gif'));
  assert.ok(hits.some(x=>x.url==='/frame'));
  assert.ok(hits.some(x=>x.url==='/query'&&x.method==='POST'));
  assert.ok(hits.some(x=>x.url==='/sw.js'));
  assert.ok(!hits.some(x=>x.url==='/robots.txt'));
  assert.ok(evidence.records.some(x=>x.request_method==='POST'));
  assert.ok(evidence.records.some(x=>x.kind==='image'&&x.status===200));
  assert.ok(fs.statSync(path.join(evidence.directory,'blobs',result.dom_sha256)).size>100);
  await adapter.close();adapter=null;
  const checked=verify(evidence.directory);assert.equal(checked.ok,true);
  evidence.save('verification.json',checked);evidence.save('fixture-hits.json',hits);
  console.log(JSON.stringify({headful:true,cross_origin_images_iframe_POST_SW:'passed',robots_not_requested:true,verification:checked}));
} finally {await adapter?.close();await close(main);await close(dependencies);}
