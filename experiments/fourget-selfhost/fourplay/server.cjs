/* Firefox extension bridge for 4get rendering and browser-observation adapters. */
const fplay = require('@lawlers/4play');
const http = require('node:http');
const fs = require('node:fs');
const crypto = require('node:crypto');
const password = fs.readFileSync('/run/fourplay-password.txt', 'utf8').trim();
const timeout = 28000;
const sessions = new Map();
let ws = null;
let ua = null;
let browserReady = false;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const idOf = value => typeof value === 'string' ? value : value?.id;
const runtime = {engine:'firefox',transport:'4play-extension',headless:false,display:':99',
  viewport:{width:1280,height:720},display_backend:'Xvfb',hardware_gpu_verified:false,
  screenshots:false,budgeted_navigation:false,response_headers:false};
const supportedSources = ['main_frame','sub_frame','stylesheet','script','image','object',
  'xmlhttprequest','ping','font','media','csp_report','imageset','web_manifest','other'];

function authorize(value) {
  const supplied = Buffer.from(value || '');
  const expected = Buffer.from(password);
  return supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected);
}
function proxyConfig(uri) {
    const proxy = new URL(uri);
  if (!['http:','https:'].includes(proxy.protocol)) throw new Error('http_proxy_required');
  const host=proxy.hostname===process.env.FOURPLAY_CLOUD_PROXY_HOST
    ? process.env.FOURPLAY_CLOUD_PROXY_IP : proxy.hostname;
  return {type:proxy.protocol.slice(0,-1),host,
    port:Number(proxy.port || (proxy.protocol === 'https:' ? 443 : 80)),
    username:decodeURIComponent(proxy.username),password:decodeURIComponent(proxy.password),proxyDNS:true};
}
function lineProxy(line) {
  const [type,host,port,username,...tail] = line.split(':');
  if (!['http','https'].includes(type) || !host || !port) throw new Error('cloud_http_proxy_required');
  return {type,host,port:Number(port),username:username || '',password:tail.join(':'),proxyDNS:true};
}
function checkedURL(value, fixture = false) {
  const url = new URL(value);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password) throw new Error('invalid_navigation_url');
  if (fixture && !['localhost','127.0.0.1'].includes(url.hostname)) throw new Error('fixture_must_be_loopback');
  return url.href;
}
async function createSession({proxy,fixture = false}, render = false) {
  if (!browserReady) throw new Error('browser_not_connected');
  const config = fixture ? null : proxyConfig(proxy);
  const container = await fplay.container_create(ws);
  if (!container) throw new Error('container_create_failed');
  if (config && !await fplay.container_attach_proxy(ws,container,config)) {
    await fplay.container_delete(ws,container);
    throw new Error('container_proxy_failed');
  }
  const session = {id:crypto.randomUUID(),container,fixture,render,tab:null,responses:[],errors:[],active:false,
    touched:Date.now(),busy:false};
  sessions.set(session.id,session);
  return session;
}
async function dispose(session) {
  session.active = false;
  if (session.tab) await fplay.tab_close(ws,session.tab).catch(()=>{});
  await fplay.container_delete(ws,session.container).catch(()=>{});
  sessions.delete(session.id);
}
async function evaluate(session, expression) {
  if (!session.tab) throw new Error('no_current_tab');
  const value = await fplay.tab_inject_js(ws,session.tab,expression,true);
  if (value.status !== true) throw new Error('evaluation_failed: '+String(value.status));
  const frame = Array.isArray(value.result) ? value.result.find(item=>item.frameId === 0) || value.result[0] : null;
  return frame ? frame.result : value.result;
}
function forPage(page, callback) {
  for (const session of sessions.values()) {
    if (!session.active) continue;
    if (page.container === idOf(session.container) || page.id === session.tab?.id) callback(session);
  }
}
fplay.event.on('web_response',page=>forPage(page,session=>{
  session.responses.push({id:page.id,url:page.url,status:page.status,type:page.type,method:page.method,
    container:page.container,headers:{},body_base64:page.body.toString('base64')});
  if (session.responses.length>512) { session.active=false;session.errors.push({error:'response_capture_limit'}); }
}));
fplay.event.on('dom_load_fail',page=>forPage(page,session=>{
  session.errors.push({url:page.url,error:page.error});
}));
fplay.event.on('browser_connect',async connection=>{
  browserReady=false;ws=connection;
  try {
    ua = await fplay.get_ua(ws);
    await fplay.close_all_tabs(ws);
    await fplay.delete_all_containers(ws);
    sessions.clear();
    await fplay.web_response_whitelist(ws,supportedSources);
    browserReady=typeof ua === 'string';
    console.log('4play Firefox connected');
  } catch(error) { console.error('4play connection setup failed:',error.message); }
});
fplay.event.on('browser_disconnect',()=>{browserReady=false;ws=null;sessions.clear();});

async function openTab(session,url) {
  if (session.tab) await fplay.tab_close(ws,session.tab);
  session.responses=[];session.errors=[];session.active=true;session.touched=Date.now();
  session.tab=await fplay.tab_open(ws,checkedURL(url,session.fixture),false,session.container);
  if (!session.tab) throw new Error('tab_open_failed');
}
async function navigate(session,{url,observe_ms = 1000}) {
  if (!Number.isInteger(observe_ms) || observe_ms<0 || observe_ms>6000) throw new Error('invalid_observe_ms');
  await openTab(session,url);
  // Return observations before the adapter's 25-second HTTP deadline.
  const deadline=Date.now()+18000;
  let completed=false;
  while (Date.now()<deadline) {
    const tabs=await fplay.get_tab_list(ws);
    if (Array.isArray(tabs) && tabs.find(tab=>tab.id===session.tab.id)?.status==='complete') {completed=true;break;}
    if (session.errors.length && !session.responses.some(p=>p.type==='main_frame')) break;
    await wait(100);
  }
  if(!completed && !session.errors.length) session.errors.push({url,error:'navigation_timeout'});
  await wait(observe_ms);
  let dom;
  try {dom=await evaluate(session,'({url:location.href,title:document.title,dom:document.documentElement.outerHTML})');}
  catch(error) {
    session.errors.push({url,error:error.message});
    const tabs=await fplay.get_tab_list(ws);
    const tab=Array.isArray(tabs)?tabs.find(item=>item.id===session.tab.id):null;
    dom={url:tab?.url||url,title:tab?.title||null,dom:null};
  }
  await evaluate(session,'window.stop(); true').catch(()=>{});
  session.active=false;
  return {...dom,responses:session.responses,errors:session.errors};
}
async function readJSON(req) {
  let size=0;const parts=[];
  for await (const part of req) {
    size+=part.length;if(size>1048576) throw new Error('request_body_too_large');parts.push(part);
  }
  return JSON.parse(Buffer.concat(parts).toString() || '{}');
}
function sendJSON(res,status,data) {
  res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));
}
function findSession(id) {
  const session=sessions.get(id);
  if (!session) throw new Error('session_not_found');
  return session;
}
async function render(req,res,parsed) {
  if (!authorize(parsed.searchParams.get('pw'))) throw new Error('unauthorized');
  if (!browserReady) throw new Error('browser_not_connected');
  const proxyLine=parsed.searchParams.get('proxy') || '';
  const config=lineProxy(proxyLine);
  const uri=new URL(`${config.type}://${config.host}:${config.port}`);
  if (config.username) uri.username=config.username;
  if (config.password) uri.password=config.password;
  const container=parsed.searchParams.get('container');
  let session=[...sessions.values()].find(s=>s.render && idOf(s.container)===container);
  if (!session) session=await createSession({proxy:uri.href},true);
  if(session.busy) throw new Error('session_busy');
  session.busy=true;
  try {
    const match=parsed.searchParams.get('url_match');
    const contentMatch=parsed.searchParams.get('content_match');
    const fail=parsed.searchParams.get('fail_url_match');
    const failContent=parsed.searchParams.get('fail_content_match');
    await openTab(session,parsed.searchParams.get('url'));
    const deadline=Date.now()+timeout;
    let seen=0;
    while(Date.now()<deadline && !res.destroyed) {
      for(const page of session.responses.slice(seen)) {
        seen++;
        if(page.type!=='main_frame') continue;
        const body=Buffer.from(page.body_base64,'base64').toString();
        if((fail && new RegExp(fail,'i').test(page.url)) || (failContent && new RegExp(failContent,'i').test(body))) {
          res.writeHead(400,{'Content-Type':'text/plain','X-Container-ID':idOf(session.container)});
          res.end('Upstream browser reached the configured failure page');return;
        }
        if((!match || new RegExp(match,'i').test(page.url)) && (!contentMatch || new RegExp(contentMatch,'i').test(body))) {
          res.writeHead(200,{'Content-Type':'text/plain;charset=UTF-8','X-Container-ID':idOf(session.container)});
          res.end(Buffer.from(page.body_base64,'base64'));return;
        }
      }
      if(session.errors.length && !session.responses.length) throw new Error(session.errors[0].error);
      await wait(50);
    }
    throw new Error('render_timeout_awaiting_matching_response');
  } finally {
    session.active=false;session.busy=false;
    if(session.tab) {await fplay.tab_close(ws,session.tab).catch(()=>{});session.tab=null;}
  }
}

const fixture = '<!doctype html><html><head><meta charset="utf-8"><title>4play fixture</title></head><body><h1>4play browser fixture</h1><p id="js">pending</p><a href="/fixture/next">Next</a><script>document.querySelector("#js").textContent="javascript executed"</script></body></html>';
const server=http.createServer(async(req,res)=>{
  const parsed=new URL(req.url,'http://localhost');
  try {
    if(parsed.pathname==='/health') {sendJSON(res,200,{browser_connected:browserReady,ua,runtime});return;}
    if(parsed.pathname.startsWith('/fixture')) {res.writeHead(200,{'Content-Type':'text/html;charset=UTF-8'});res.end(fixture);return;}
    if(parsed.pathname==='/' && req.method==='GET') {await render(req,res,parsed);return;}
    if(!authorize((req.headers.authorization || '').replace(/^Bearer /,''))) {sendJSON(res,401,{error:'unauthorized'});return;}
    if(!browserReady) {sendJSON(res,503,{error:'browser_not_connected'});return;}
    if(req.method!=='POST') {sendJSON(res,405,{error:'post_required'});return;}
    const data=await readJSON(req);
    if(parsed.pathname==='/diagnostics/session') {
      const session=await createSession(data);
      sendJSON(res,200,{session_id:session.id,ua,runtime});return;
    }
    const session=findSession(data.session_id);
    if(session.busy) throw new Error('session_busy');
    session.busy=true;
    try {
      if(parsed.pathname==='/diagnostics/navigate') sendJSON(res,200,await navigate(session,data));
      else if(parsed.pathname==='/diagnostics/evaluate') sendJSON(res,200,{result:await evaluate(session,data.expression)});
      else if(parsed.pathname==='/diagnostics/close') {await dispose(session);sendJSON(res,200,{ok:true});}
      else sendJSON(res,404,{error:'unknown_endpoint'});
    } finally {session.busy=false;}
  } catch(error) {
    if(res.headersSent || res.destroyed) return;
    if(parsed.pathname==='/') {res.writeHead(400,{'Content-Type':'text/plain'});res.end(error.message);}
    else sendJSON(res,400,{error:error.message});
  }
});
server.listen(3000,'0.0.0.0',()=>console.log('4play HTTP renderer ready on port 3000'));
fplay.init(3030,password,30000);
setInterval(async()=>{
  if(!browserReady) return;
  for(const session of sessions.values()) if(!session.busy && Date.now()-session.touched>900000) await dispose(session);
},60000).unref();
