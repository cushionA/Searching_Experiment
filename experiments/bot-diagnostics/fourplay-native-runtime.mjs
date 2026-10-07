import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import http from 'node:http';
import {spawn, execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {EventEmitter} from 'node:events';
import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';
import {repo, state, proxy} from './runtime.mjs';

const deps = path.resolve(process.env.BOT_DIAGNOSTICS_FOURPLAY_DEPS || path.join(repo, '.deps/fourplay'));
const localRequire = createRequire(path.join(deps, 'package.json'));

export function initializeFourplayServer(fplay,{port,password,commandTimeout}) {
  return fplay.init({port,host:'127.0.0.1'},password,commandTimeout);
}

if (!isMainThread && workerData?.fourplayServer) {
  const fplay = localRequire('@lawlers/4play');
  let ws = null;
  const send = (name,data={})=>parentPort.postMessage({type:'event',name,data});
  for (const name of ['server_ready','browser_connect','browser_disconnect','web_request','web_response','dom_ready','dom_load_fail']) {
    fplay.event.on(name,value=>{
      if (name==='browser_connect') ws=value;
      if (name==='browser_disconnect') ws=null;
      if (name==='browser_connect' || name==='browser_disconnect') send(name,{});
      else send(name,value);
    });
  }
  const api={get_ua:(...args)=>fplay.get_ua(ws,...args),close_all_tabs:(...args)=>fplay.close_all_tabs(ws,...args),
    delete_all_containers:(...args)=>fplay.delete_all_containers(ws,...args),container_create:(...args)=>fplay.container_create(ws,...args),
    tab_close:(tab,...args)=>fplay.tab_close(ws,tab,...args),
    container_attach_proxy:(container,config)=>fplay.container_attach_proxy(ws,container,config),
    tab_open:(url,awaitReady,container)=>fplay.tab_open(ws,url,awaitReady,container),
    tab_inject_js:(tab,script,isolated)=>fplay.tab_inject_js(ws,tab,script,isolated),
    web_response_whitelist:sources=>fplay.web_response_whitelist(ws,sources)};
  parentPort.on('message',async message=>{
    if(message.type==='call') {
      try { const value=await api[message.method](...(message.args||[])); parentPort.postMessage({type:'result',id:message.id,value}); }
      catch(error) { parentPort.postMessage({type:'result',id:message.id,error:String(error?.message||error)}); }
    }
  });
  initializeFourplayServer(fplay,workerData);
}

function createServer({port,password,commandTimeout}) {
  const worker=new Worker(new URL(import.meta.url),{workerData:{fourplayServer:true,port,password,commandTimeout}});
  const events=new EventEmitter(), pending=new Map(); let id=0;
  worker.on('message',message=>{
    if(message.type==='event') events.emit(message.name,message.data);
    if(message.type==='result') {
      const request=pending.get(message.id); if(!request) return;
      pending.delete(message.id); message.error?request.reject(new Error(`fourplay_command_failed:${request.method}: ${message.error}`)):request.resolve(message.value);
    }
  });
  worker.on('error',error=>{api.failure=error;events.emit('worker_error',error);});
  worker.on('exit',code=>{
    if(code!==0) { api.failure=new Error(`fourplay_worker_exit:${code}`); events.emit('worker_error',api.failure); }
    for(const request of pending.values()) request.reject(new Error('fourplay_worker_stopped'));
    pending.clear();
  });
  const api={event:events,failure:null,call:(method,...args)=>new Promise((resolve,reject)=>{
    const requestId=++id;
    const timer=setTimeout(()=>{pending.delete(requestId);reject(new Error(`fourplay_command_timeout:${method}`));},Math.max(30000,commandTimeout+5000));
    pending.set(requestId,{method,resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
    worker.postMessage({type:'call',id:requestId,method,args});
  }),stop:()=>worker.terminate()};
  return api;
}

function evaluateSource(fn, args) {
  return `(${fn.toString()})(...${JSON.stringify(args)})`;
}

function checkedResult(value, operation) {
  if (!value || value.status !== true) {
    const reason = typeof value?.status === 'string' ? value.status : `${operation}_failed`;
    throw new Error(`fourplay_${operation}_failed: ${reason}`);
  }
  if (!Array.isArray(value.result)) return value.result;
  const frame=value.result.find(item=>item?.frameId===0) || value.result[0];
  return frame?.result;
}

function headersObject(headers) {
  if (Array.isArray(headers)) return Object.fromEntries(headers.map(line => {
    const index = String(line).indexOf(':');
    return index < 0 ? [String(line).trim().toLowerCase(), ''] : [String(line).slice(0,index).trim().toLowerCase(), String(line).slice(index+1).trim()];
  }));
  return headers && typeof headers === 'object' ? {...headers} : {};
}

function nativeRequest(event, sequence, mainFrame) {
  const url = String(event.url || '');
  const method = String(event.method || 'GET');
  const resourceType = String(event.type || 'other').toLowerCase();
  const request = {
    id: event.id ?? null, tabId:event.id??null, container:event.container??null, sequence, url:()=>url, method:()=>method,
    resourceType:()=>resourceType === 'main_frame' ? 'document' : resourceType === 'xmlhttprequest' ? 'xhr' : resourceType,
    isNavigationRequest:()=>resourceType === 'main_frame',
    frame:()=>resourceType === 'main_frame' ? mainFrame : null,
    failure:()=>null,
  };
  return request;
}

class FourplayContext extends EventEmitter {
  constructor() { super(); this.browser = {version:()=>this.version || 'Firefox'}; }
  pages() { return this.page ? [this.page] : []; }
  async newPage() { return this.page; }
  async close() { this.closed = true; }
  async route() { throw new Error('unsupported_capability:route'); }
  async setOffline() { throw new Error('unsupported_capability:offline'); }
}

function makePage(server, container, context, onNavigate, waitForDomReady) {
  const page = new EventEmitter();
  let tab = null;
  const mainFrame={url:()=>page.url()};
  const ensureTab = () => { if (!tab) throw new Error('fourplay_tab_not_open'); return tab; };
  page._setTab = value => { tab = value; page._currentTab=value; page._tabId=value?.id; };
  page.url = () => tab?.url || 'about:blank';
  page.mainFrame = () => mainFrame;
  page.goto = async (url, options={}) => {
    const started=performance.now();
    const timing={};
    page.lastNavigationTiming=timing;
    const previous=tab;
    tab=null;
    onNavigate(String(url));
    const opened = await server.call('tab_open',String(url),false,container);
    timing.tab_open_ms=performance.now()-started;
    if (!opened || opened === false) throw new Error('fourplay_navigation_failed');
    page._setTab(opened);
    const closeStarted=performance.now();
    if(previous?.id != null) await server.call('tab_close',previous.id);
    timing.previous_tab_close_ms=performance.now()-closeStarted;
    onNavigate(null);
    const gateStarted=performance.now();
    const ready=await waitForDomReady(opened.id,options.timeout);
    timing.remaining_dom_gate_ms=performance.now()-gateStarted;
    timing.total_ms=performance.now()-started;
    page._setTab({...opened,url:ready.url});
    page.emit('domcontentloaded'); page.emit('load');
    return null;
  };
  page.evaluate = async (fn, ...args) => {
    if (typeof fn !== 'function') throw new TypeError('fourplay_evaluate_requires_function');
    const output = await server.call('tab_inject_js',ensureTab(),evaluateSource(fn,args),true);
    return checkedResult(output, 'evaluate');
  };
  page.content = async () => page.evaluate(()=>document.documentElement?.outerHTML || '');
  page.exposeFunction = async () => { throw new Error('unsupported_capability:exposeFunction'); };
  page.screenshot = async () => { throw new Error('unsupported_capability:screenshots'); };
  page.locator = selector => {
    const run = async (kind, args=[]) => page.evaluate((sel, action, values)=>{
      const nodes=[...document.querySelectorAll(sel)];
      if(action==='count') return nodes.length;
      if(action==='allTextContents') return nodes.map(node=>node.textContent || '');
      if(action==='getAttribute') return nodes[0]?.getAttribute(values[0]) ?? null;
      if(action==='innerText') return nodes[0]?.innerText ?? '';
      if(action==='inputValue') return nodes[0]?.value ?? null;
      if(action==='fill') {
        const node=nodes[0]; if(!node) throw new Error('locator_not_found');
        node.focus(); node.value=values[0]; node.dispatchEvent(new Event('input',{bubbles:true}));
        node.dispatchEvent(new Event('change',{bubbles:true})); return true;
      }
      throw new Error('unsupported_capability:locator_'+action);
    }, selector, kind, args);
    return {count:()=>run('count'),allTextContents:()=>run('allTextContents'),getAttribute:name=>run('getAttribute',[name]),
      innerText:()=>run('innerText'),inputValue:()=>run('inputValue'),fill:value=>run('fill',[value])};
  };
  page.context = () => context;
  return page;
}

function defaultLaunch({profile, executable, extension}) {
  if (!fs.existsSync(executable)) throw new Error(`fourplay_firefox_missing: ${executable}`);
  if (!fs.existsSync(extension)) throw new Error(`fourplay_extension_missing: ${extension}`);
  if (!fs.statSync(extension).isDirectory()) throw new Error('fourplay_extension_source_must_be_directory');
  const launcher=path.join(deps,'node_modules','.bin','web-ext');
  if (!fs.existsSync(launcher)) throw new Error(`fourplay_web_ext_missing: install web-ext in ${deps}`);
  const child = spawn(launcher, ['run','--source-dir',extension,'--firefox',executable,'--firefox-profile',profile,
    '--keep-profile-changes','--no-reload','--no-input'], {
    stdio:'ignore', env:{...process.env}, detached:true,
  });
  return child;
}

async function stopFirefox(child, graceMs=3000) {
  if (!child) return;
  const ended=()=>child.exitCode!==null&&child.exitCode!==undefined || child.signalCode!==null&&child.signalCode!==undefined;
  if (ended()) return;
  if (process.platform!=='win32' && child.pid) {
    try { process.kill(-child.pid,'SIGTERM'); } catch { child.kill?.('SIGTERM'); }
  } else child.kill?.('SIGTERM');
  if (typeof child.once!=='function') return;
  await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,graceMs))]);
  if (ended()) return;
  if (process.platform!=='win32' && child.pid) {
    try { process.kill(-child.pid,'SIGKILL'); } catch {}
  } else child.kill?.('SIGKILL');
  await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,1000))]);
}

function waitForServerEvent(server,name,timeoutMs) {
  return new Promise((resolve,reject)=>{
    const finish=(error,value)=>{
      clearTimeout(timer); server.event.off(name,onEvent); server.event.off('worker_error',onError);
      error?reject(error):resolve(value);
    };
    const onEvent=value=>finish(null,value);
    const onError=error=>finish(error);
    const timer=setTimeout(()=>finish(new Error(`fourplay_${name}_timeout`)),timeoutMs);
    if(server.failure) { finish(server.failure); return; }
    server.event.once(name,onEvent); server.event.once('worker_error',onError);
  });
}

export function prepareExtension(source, destination, port, password) {
  fs.cpSync(source,destination,{recursive:true});
  const bgPath=path.join(destination,'bg.js');
  const original=fs.readFileSync(bgPath,'utf8');
  const anchor='var config = await browser.storage.local.get();\n\t\n\ttry{';
  const hits=original.split(anchor).length-1;
  if(hits!==1) throw new Error('fourplay_extension_patch_anchor_mismatch');
  const wsUrl=`ws://127.0.0.1:${port}/${password}`;
  const replacement=`var config = await browser.storage.local.get();\n\tif(typeof config.ws_url == "undefined"){ config.ws_url = ${JSON.stringify(wsUrl)}; }\n\tif(typeof config.ws_timeout == "undefined"){ config.ws_timeout = 5000; }\n\tawait browser.storage.local.set({"ws_url": config.ws_url, "ws_timeout": config.ws_timeout});\n\t\n\ttry{`;
  const patched=original.replace(anchor,replacement);
  fs.writeFileSync(bgPath,patched);
  const commitPath=path.resolve(source,'../../upstream-commit.txt');
  const commit=fs.existsSync(commitPath)?fs.readFileSync(commitPath,'utf8').trim():'unknown';
  return {path:destination,commit,sourceHash:crypto.createHash('sha256').update(original).digest('hex'),
    patchedHash:crypto.createHash('sha256').update(patched).digest('hex'),url:wsUrl};
}

function trustProxyCa(profile, fixture) {
  const caFile=process.env.BOT_DIAGNOSTICS_CA || (!fixture?process.env.CODEX_PROXY_CERT:null);
  if(!caFile) return false;
  if(!fs.existsSync(caFile) || !fs.statSync(caFile).isFile()) throw new Error('fourplay_ca_setup_failed: configured proxy CA file is unavailable');
  const run=args=>execFileSync('certutil',args,{stdio:'ignore'});
  try { run(['-N','--empty-password','-d',`sql:${profile}`]); }
  catch(error) { throw new Error(`fourplay_ca_setup_failed: certutil could not initialize Firefox trust store (${error?.code||'unknown'})`); }
  const bytes=fs.readFileSync(caFile), text=bytes.toString('utf8');
  const certificates=[...text.matchAll(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g)].map(match=>match[0]+'\n');
  const files=[];
  try {
    const inputs=certificates.length?certificates:[bytes];
    for(let index=0;index<inputs.length;index++) {
      const pem=certificates.length>0, file=path.join(profile,`.proxy-ca-${index+1}.${pem?'pem':'der'}`);
      fs.writeFileSync(file,inputs[index],{mode:0o600,flag:'wx'}); files.push(file);
      run(['-A','-n',`bot-diagnostics-proxy-ca-${index+1}`,'-t','C,,','-d',`sql:${profile}`,'-i',file]);
    }
  } catch(error) { throw new Error(`fourplay_ca_setup_failed: certutil could not import proxy CA (${error?.code||'unknown'})`); }
  finally { for(const file of files) fs.rmSync(file,{force:true}); }
  return true;
}

export async function startFixtureRelay() {
  const server=http.createServer((incoming,outgoing)=>{
    let destination;
    try { destination=new URL(incoming.url); }
    catch { outgoing.writeHead(400); outgoing.end(); return; }
    const hostname=destination.hostname.toLowerCase();
    if(destination.protocol!=='http:' || !['127.0.0.1','localhost','[::1]'].includes(hostname)
      || destination.username || destination.password) {
      outgoing.writeHead(403); outgoing.end(); return;
    }
    const headers={...incoming.headers};
    delete headers['proxy-authorization']; delete headers['proxy-connection'];
    const upstream=http.request({protocol:'http:',hostname:hostname==='localhost'?'127.0.0.1':hostname==='[::1]'?'::1':hostname,port:destination.port||80,
      method:incoming.method,path:destination.pathname+destination.search,headers},response=>{
      outgoing.writeHead(response.statusCode||502,response.headers); response.pipe(outgoing);
    });
    upstream.on('error',()=>{if(!outgoing.headersSent) outgoing.writeHead(502); outgoing.end();});
    incoming.pipe(upstream);
  });
  server.on('connect',(request,socket)=>{socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');});
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  return {server,port:server.address().port,close:()=>new Promise(resolve=>server.close(resolve))};
}

export async function openFourplayNative({fixture=false, profile='diagnostic', serverFactory=createServer,
  launch=defaultLaunch, port=3030, password=crypto.randomBytes(24).toString('hex'), commandTimeout=30000, connectTimeout=30000,
  executable=process.env.BOT_DIAGNOSTICS_FIREFOX, extension=process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION}={}) {
  const started=performance.now(), startupTiming={};
  if (!executable || !extension) throw new Error('fourplay_setup_required: set BOT_DIAGNOSTICS_FIREFOX and BOT_DIAGNOSTICS_FOURPLAY_EXTENSION');
  if (!(process.env.DISPLAY || process.env.WAYLAND_DISPLAY)) throw new Error('fourplay_headful_requires_display');
  fs.mkdirSync(state,{recursive:true});
  const profileDirectory = fs.mkdtempSync(path.join(state,'fourplay-profile-'));
  let extensionMetadata;
  try { extensionMetadata=prepareExtension(extension,path.join(profileDirectory,'extension'),port,password); }
  catch(error) { fs.rmSync(profileDirectory,{recursive:true,force:true}); throw error; }
  if (fixture) fs.writeFileSync(path.join(profileDirectory,'user.js'),[
    'user_pref("network.proxy.type", 1);',
    'user_pref("network.proxy.http", "127.0.0.1");',
    'user_pref("network.proxy.http_port", 9);',
    'user_pref("network.proxy.ssl", "127.0.0.1");',
    'user_pref("network.proxy.ssl_port", 9);',
    'user_pref("network.proxy.no_proxies_on", "127.0.0.1,localhost");',
    'user_pref("network.proxy.failover_direct", false);',
    '',
  ].join('\n'));
  let proxyCaTrusted=false;
  try { proxyCaTrusted=trustProxyCa(profileDirectory,fixture); }
  catch(error) { fs.rmSync(profileDirectory,{recursive:true,force:true}); throw error; }
  let child, server, fixtureRelay;
  const context = new FourplayContext();
  try {
    server=serverFactory({port,password,commandTimeout});
    if (!server?.event?.on || typeof server.call!=='function') throw new Error('fourplay_setup_invalid: @lawlers/4play worker API unavailable');
    const ready=waitForServerEvent(server,'server_ready',connectTimeout);
    await ready;
    startupTiming.profile_and_server_ms=performance.now()-started;
    const connected=waitForServerEvent(server,'browser_connect',connectTimeout);
    const launchStarted=performance.now();
    child=launch({profile:profileDirectory,executable,extension:extensionMetadata.path,headful:true});
    if (!child || typeof child.kill!=='function') throw new Error('fourplay_firefox_launch_failed');
    child.stderr?.on('data',()=>{});
    await connected;
    startupTiming.launch_and_extension_connect_ms=performance.now()-launchStarted;
    const sessionStarted=performance.now();
    const ua=await server.call('get_ua');
    if(typeof ua!=='string' || !ua) throw new Error('fourplay_user_agent_unavailable');
    const container=await server.call('container_create',`bot-diagnostics-${profile}`);
    if (!container) throw new Error('fourplay_container_create_failed');
    if(fixture) fixtureRelay=await startFixtureRelay();
    if (fixture || proxy) {
      const parsed=fixture?new URL(`http://127.0.0.1:${fixtureRelay.port}`):new URL(proxy);
      const type=parsed.protocol.replace(':','');
      const proxyConfig={type:type==='socks5'?'socks':type,host:parsed.hostname,port:Number(parsed.port || (type==='https'?443:80)),proxyDNS:true};
      if (!fixture && parsed.username) proxyConfig.username=decodeURIComponent(parsed.username);
      if (!fixture && parsed.password) proxyConfig.password=decodeURIComponent(parsed.password);
      if (!await server.call('container_attach_proxy',container,proxyConfig)) throw new Error('fourplay_proxy_attach_failed');
    }
    let activeTabId=null,navigatingUrl=null;
    const domReadyEvents=new Map(),domFailures=new Map(),domWaiters=new Map();
    const waitForDomReady=(id,timeout=25000)=>{
      if(domReadyEvents.has(id)) return Promise.resolve(domReadyEvents.get(id));
      if(domFailures.has(id)) return Promise.reject(new Error(`fourplay_navigation_failed: ${domFailures.get(id)}`));
      return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{domWaiters.delete(id);reject(new Error('fourplay_dom_ready_timeout'));},timeout);
        domWaiters.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});
      });
    };
    const page=makePage(server,container,context,url=>{
      navigatingUrl=url;
      activeTabId=url===null?page._tabId:null;
    },waitForDomReady);
    context.page=page;
    const mainFrame=page.mainFrame();
    const pending=new Map(); let sequence=0;
    const fixtureEvents=[];
    const accepts=event=>{
      if(String(event.container)!==String(container.id)) return false;
      if(activeTabId===null && navigatingUrl && event.type==='main_frame' && event.url===navigatingUrl) activeTabId=event.id;
      return activeTabId!==null && event.id===activeTabId;
    };
    const keyFor=event=>event.id != null
      ? `tab:${event.id}|method:${event.method||'GET'}|type:${event.type||'other'}|url:${event.url}`
      : `url:${event.url}:${++sequence}`;
    const onRequest=event=>{
      if(fixture) fixtureEvents.push({kind:'request',id:event.id,url:event.url,type:event.type,container:event.container,active_tab:activeTabId,navigating_url:navigatingUrl});
      if(!accepts(event)) return;
      const request=nativeRequest(event,++sequence,mainFrame);
      const key=keyFor(event), queue=pending.get(key)||[]; queue.push(request); pending.set(key,queue);
      request.headers=()=>headersObject(event.headers);
      context.emit('request',request); page.emit('request',request);
    };
    const onResponse=event=>{
      if(fixture) fixtureEvents.push({kind:'response',id:event.id,url:event.url,type:event.type,container:event.container,status:event.status,active_tab:activeTabId,navigating_url:navigatingUrl});
      if(!accepts(event)) return;
      const key=keyFor(event), queue=pending.get(key)||[], request=queue.shift() || nativeRequest(event,++sequence,mainFrame);
      if(queue.length) pending.set(key,queue); else pending.delete(key);
      const raw=event.body ?? Buffer.alloc(0);
      const response={request:()=>request,url:()=>String(event.url||''),status:()=>Number(event.status),
        headers:()=>headersObject(event.headers),allHeaders:async()=>headersObject(event.headers),body:async()=>Buffer.from(raw)};
      context.emit('response',response); page.emit('response',response);
    };
    const onFailure=event=>{
      if(/^HTTP\/[\d.]+\s+[45]\d\d\b/i.test(String(event.error||''))) return;
      const failureMatches=event.id===activeTabId || (activeTabId===null && event.url===navigatingUrl);
      if(!failureMatches || (event.container!=null && String(event.container)!==String(container.id))) return;
      const entry=[...pending].find(([,queue])=>queue.some(request=>request.id===event.id && request.url()===event.url));
      const queue=entry?.[1]||[], request=queue.shift();
      if(request?.isNavigationRequest() || event.url===navigatingUrl) {
        if(activeTabId===null && event.url===navigatingUrl) activeTabId=event.id;
        domFailures.set(event.id,event.error||'network_error');
        domWaiters.get(event.id)?.reject(new Error(`fourplay_navigation_failed: ${event.error||'network_error'}`));
        domWaiters.delete(event.id);
      }
      if (!request) return;
      if(!queue.length) pending.delete(entry[0]);
      request.failure=()=>({errorText:String(event.error || 'network_error')});
      context.emit('requestfailed',request); page.emit('requestfailed',request);
    };
    const onDomReady=event=>{
      if(String(event.container)!==String(container.id)) return;
      domReadyEvents.set(event.id,event);
      domWaiters.get(event.id)?.resolve(event); domWaiters.delete(event.id);
      if(activeTabId===event.id) page._setTab({...page._currentTab,url:event.url});
    };
    await server.call('web_response_whitelist',['main_frame','xmlhttprequest','sub_frame','script','stylesheet','image','font','media','object','ping','other']);
    startupTiming.session_setup_ms=performance.now()-sessionStarted;
    startupTiming.total_ms=performance.now()-started;
    server.event.on('web_request',onRequest); server.event.on('web_response',onResponse);
    server.event.on('dom_ready',onDomReady); server.event.on('dom_load_fail',onFailure);
    const runtime={engine:'firefox',startup_timing_ms:startupTiming,version:ua.match(/Firefox\/([\d.]+)/)?.[1]||'unknown',fourplay:'@lawlers/4play',fourplay_upstream_commit:extensionMetadata.commit,
      extension_bg_original_sha256:extensionMetadata.sourceHash,extension_bg_config_patch_sha256:extensionMetadata.patchedHash,
      proxy_ca_trusted:proxyCaTrusted,headless:false,
      display:process.env.DISPLAY||process.env.WAYLAND_DISPLAY||null,viewport:null,screenshots:false,
      selector_operations:false,challenge_actions:false,initial_tabs_preserved:true,
      ...(fixture?{fixture_event_trace:fixtureEvents}:{}),
      fixture_network_isolation:fixture?'container proxy forwards only loopback HTTP; external destinations and CONNECT are denied':null,
      wait_condition:'tab_open returns the native tab id; then waits for tabs.onUpdated status=complete (dom_ready)',
      budgeted_navigation:false,redirect_chain_reporting:false,request_id_source:'not_exposed; correlation uses tab_id+method+type+url+arrival_order',frame_matching:'active_container_and_tab_id',
      observation_limits:['screenshots_unsupported','viewport_not_measured','frame_matching_incomplete','redirect_chain_not_reported','response_headers_not_provided_by_4play','websocket_frames_not_recorded']};
    const close=async()=>{
      server.event.off('web_request',onRequest); server.event.off('web_response',onResponse);
      server.event.off('dom_ready',onDomReady); server.event.off('dom_load_fail',onFailure);
      try { await stopFirefox(child); } finally {
        await server.stop(); await fixtureRelay?.close(); fs.rmSync(profileDirectory,{recursive:true,force:true});
      }
    };
    return {browser:context.browser,context,page,ua,kind:'fourplay-native',runtime,container,close};
  } catch(error) {
    try { await stopFirefox(child); await server?.stop?.(); await fixtureRelay?.close(); }
    finally { fs.rmSync(profileDirectory,{recursive:true,force:true}); }
    throw error;
  }
}
