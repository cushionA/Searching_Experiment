import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {EventEmitter} from 'node:events';

const root=fs.mkdtempSync(path.join(os.tmpdir(),'fourplay-runtime-test-'));
process.env.BOT_DIAGNOSTICS_STATE=path.join(root,'state');
process.env.BOT_DIAGNOSTICS_FIREFOX=path.join(root,'firefox');
process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=path.join(root,'upstream','ext');
process.env.DISPLAY='fixture:0';
fs.writeFileSync(process.env.BOT_DIAGNOSTICS_FIREFOX,'fixture');
fs.mkdirSync(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,{recursive:true});
fs.writeFileSync(path.join(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,'bg.js'),
  'async function ws_init(){\n\tvar config = await browser.storage.local.get();\n\t\n\ttry{\n\t\tglobal_ws = await ws_connect_timeout(config.ws_url, config.ws_timeout);\n\t}\n}\n');
fs.writeFileSync(path.join(root,'upstream-commit.txt'),'fixture-upstream-commit\n');
const {openFourplayNative,startFixtureRelay,initializeFourplayServer}=await import('./fourplay-native-runtime.mjs');

test('native controller binds loopback and chooses a fresh unlogged session token',async()=>{
  let initArgs;
  initializeFourplayServer({init:(...args)=>{initArgs=args;}},{port:32001,password:'fresh-token',commandTimeout:2000});
  assert.deepEqual(initArgs,[{port:32001,host:'127.0.0.1'},'fresh-token',2000]);
  const tokens=[];
  for(let index=0;index<2;index++) {
    const server=fixtureServer();
    const browser=await openFourplayNative({fixture:true,serverFactory:options=>{tokens.push(options.password);return server;},
      launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
    await browser.close();
  }
  assert.notEqual(tokens[0],tokens[1]);
  assert.ok(tokens.every(token=>/^[a-f0-9]{48}$/.test(token)));
});

function fixtureServer({emitNavigationEvents=false,httpErrorStatus=null}={}) {
  const event=new EventEmitter(), calls=[];
  let pageUrl='about:blank';
  const server={event,calls,stopped:false,async stop(){server.stopped=true;},async call(method,...args){
    calls.push({method,args});
    if(method==='get_ua') return 'Mozilla/5.0 Firefox fixture';
    if(method==='close_all_tabs') return {id:1,url:'about:blank'};
    if(method==='delete_all_containers'||method==='web_response_whitelist') return 1;
    if(method==='container_create') return {id:'container-1'};
    if(method==='tab_open'){
      pageUrl=args[0];
      if(emitNavigationEvents) {
        event.emit('web_request',{id:1,container:'container-1',url:'http://127.0.0.1:8080/favicon.ico',type:'other',method:'GET',headers:[]});
        event.emit('web_request',{id:2,container:'container-1',url:pageUrl,type:'main_frame',method:'GET',headers:[]});
        if(httpErrorStatus) event.emit('dom_load_fail',{id:2,url:pageUrl,error:`HTTP/2 ${httpErrorStatus} Forbidden`});
        event.emit('web_response',{id:2,container:'container-1',url:pageUrl,type:'main_frame',method:'GET',status:httpErrorStatus||200,headers:[],body:Buffer.from('native response')});
      }
      event.emit('dom_ready',{id:2,container:'container-1',url:pageUrl,status:'complete'});
      return {id:2,url:pageUrl,container:'container-1'};
    }
    if(method==='tab_close'||method==='container_attach_proxy') return true;
    if(method==='tab_inject_js') {
      const source=args[1];
      if(source.includes('fixture evaluation failure')) return {status:'fixture evaluation failure',result:null};
      if(source.includes('document.documentElement?.outerHTML')) return {status:true,result:[{frameId:0,result:'<html><body><input id="q"></body></html>'}]};
      if(source.includes(',"inputValue",[]]')) return {status:true,result:[{frameId:0,result:'query'}]};
      if(source.includes(',"count",[]]')) return {status:true,result:[{frameId:0,result:1}]};
      return {status:true,result:[{frameId:0,result:'native-result'}]};
    }
    throw new Error(`unexpected method ${method}`);
  }};
  queueMicrotask(()=>event.emit('server_ready',{}));
  return server;
}

test('adapts official request/response events and evaluates in the extension isolated world',async t=>{
  const server=fixtureServer();
  let sessionPassword;
  const browser=await openFourplayNative({fixture:true,serverFactory:options=>{sessionPassword=options.password;return server;},
    launch:({extension})=>{assert.equal(fs.existsSync(path.join(extension,'bg.js')),true);queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  assert.match(fs.readFileSync(path.join(browser.runtime.extension_copy||path.join(process.env.BOT_DIAGNOSTICS_STATE,fs.readdirSync(process.env.BOT_DIAGNOSTICS_STATE)[0],'extension'),'bg.js'),'utf8'),new RegExp(`127\\.0\\.0\\.1:3030/${sessionPassword}`));
  assert.match(fs.readFileSync(path.join(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,'bg.js'),'utf8'),/var config = await browser\.storage\.local\.get\(\);/);
  const firefoxPrefs=fs.readFileSync(path.join(process.env.BOT_DIAGNOSTICS_STATE,fs.readdirSync(process.env.BOT_DIAGNOSTICS_STATE)[0],'user.js'),'utf8');
  assert.match(firefoxPrefs,/network\.proxy\.failover_direct", false/);
  await browser.page.goto('http://127.0.0.1:8080/fixture');
  assert.equal(browser.kind,'fourplay-native');
  assert.equal(await browser.page.evaluate(()=>42),'native-result');
  assert.equal(await browser.page.locator('#q').count(),1);
  assert.equal(await browser.page.locator('#q').inputValue(),'query');
  const observed=[];
  browser.context.on('request',request=>observed.push(request));
  browser.context.on('response',async response=>observed.push({status:response.status(),body:(await response.body()).toString()}));
  server.event.emit('web_request',{id:2,container:'container-1',url:'http://127.0.0.1:8080/fixture',status:0,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:['accept: text/html']});
  server.event.emit('web_request',{id:99,container:'container-1',url:'http://127.0.0.1:8080/other-tab',status:0,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:[]});
  server.event.emit('web_request',{id:2,container:'another-container',url:'http://127.0.0.1:8080/other-container',status:0,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:[]});
  server.event.emit('web_response',{id:2,container:'container-1',url:'http://127.0.0.1:8080/fixture',status:207,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:['content-type: text/html'],body:Buffer.from('real body')});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(observed[0].id,2);
  assert.equal(observed[0].frame(),browser.page.mainFrame());
  assert.equal(observed[0].method(),'GET');
  assert.equal(observed[0].isNavigationRequest(),true);
  assert.deepEqual(observed[1],{status:207,body:'real body'});
  assert.equal(browser.runtime.screenshots,false);
  assert.equal(browser.runtime.selector_operations,false);
  assert.equal(browser.runtime.budgeted_navigation,false);
  assert.equal(browser.runtime.fourplay_upstream_commit,'fixture-upstream-commit');
  await browser.page.goto('http://127.0.0.1:8080/second');
  assert.equal(server.calls.filter(call=>call.method==='tab_close'&&call.args[0]===2).length,1);
  assert.equal(browser.runtime.headless,false);
});

test('surfaces native evaluation errors and terminates server/profile on close',async()=>{
  const server=fixtureServer();
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  await browser.page.goto('http://127.0.0.1:8080/fixture');
  const profile=fs.readdirSync(process.env.BOT_DIAGNOSTICS_STATE).map(name=>path.join(process.env.BOT_DIAGNOSTICS_STATE,name))[0];
  await assert.rejects(browser.page.evaluate(()=>{throw new Error('fixture evaluation failure');}),/fourplay_evaluate_failed/);
  await browser.close();
  assert.equal(server.stopped,true);
  assert.equal(fs.existsSync(profile),false);
});

test('ignores stale favicon traffic while binding navigation events to the target main frame',async t=>{
  const server=fixtureServer({emitNavigationEvents:true});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  const requests=[]; browser.context.on('request',request=>requests.push(request));
  const responses=[]; browser.context.on('response',response=>responses.push({status:response.status(),body:response.body()}));
  await browser.page.goto('http://127.0.0.1:8080/redirect-target');
  assert.equal(requests.length,1);
  assert.equal(requests[0].url(),'http://127.0.0.1:8080/redirect-target');
  assert.equal(requests[0].isNavigationRequest(),true);
  assert.equal(requests[0].frame(),browser.page.mainFrame());
  assert.equal(responses.length,1);
  assert.equal(responses[0].status,200);
  assert.equal((await responses[0].body).toString(),'native response');
  assert.equal(server.calls.some(call=>call.method==='tab_open'&&call.args[1]===false),true);
  assert.equal(browser.runtime.viewport,null);
  assert.ok(browser.runtime.observation_limits.includes('viewport_not_measured'));
});

test('HTTP rejection notifications remain native responses and do not fail DOM navigation',async t=>{
  const server=fixtureServer({emitNavigationEvents:true,httpErrorStatus:403});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  const responses=[], failures=[];
  browser.context.on('response',response=>responses.push(response));
  browser.context.on('requestfailed',request=>failures.push(request));
  await browser.page.goto('http://127.0.0.1:8080/rejected');
  assert.equal(responses[0].status(),403);
  assert.equal((await responses[0].body()).toString(),'native response');
  server.event.emit('dom_load_fail',{id:2,url:'http://127.0.0.1:8080/favicon.ico',error:'HTTP/2 404 Not Found'});
  assert.equal(failures.length,0);
});

test('fixture relay forwards loopback HTTP and rejects external and CONNECT traffic',async t=>{
  const origin=http.createServer((request,response)=>{response.writeHead(200,{'content-type':'text/plain'});response.end('fixture');});
  await new Promise(resolve=>origin.listen(0,'127.0.0.1',resolve));
  const relay=await startFixtureRelay();
  t.after(async()=>{await relay.close();await new Promise(resolve=>origin.close(resolve));});
  const request=(target)=>new Promise((resolve,reject)=>{
    const req=http.request({host:'127.0.0.1',port:relay.port,path:target,method:'GET'},response=>{
      let body='';response.setEncoding('utf8');response.on('data',chunk=>body+=chunk);response.on('end',()=>resolve({status:response.statusCode,body}));
    }); req.on('error',reject);req.end();
  });
  assert.deepEqual(await request(`http://127.0.0.1:${origin.address().port}/fixture`),{status:200,body:'fixture'});
  assert.equal((await request('http://example.com/')).status,403);
  const connectStatus=await new Promise((resolve,reject)=>{
    const req=http.request({host:'127.0.0.1',port:relay.port,method:'CONNECT',path:'example.com:443'});
    req.on('connect',(response,socket)=>{socket.destroy();resolve(response.statusCode);});req.on('error',reject);req.end();
  });
  assert.equal(connectStatus,403);
});

test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
