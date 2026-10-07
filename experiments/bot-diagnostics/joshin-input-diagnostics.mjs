import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import {execFile, spawn} from 'node:child_process';
import {Evidence, verify, safeError} from './evidence.mjs';
import {snapshotSources, sleep, repo, sha, proxy} from './runtime.mjs';
import {openCamoufoxFourplay, buildCamoufoxLaunchOptions} from './camoufox-fourplay-runtime.mjs';
import {openCamoufox} from './camoufox-runtime.mjs';
import {openFourplayNative} from './fourplay-native-runtime.mjs';

const exec = promisify(execFile);
const [directoryArg, mode] = process.argv.slice(2);
if (!directoryArg || !['--fixture', '--live', '--fixture-ja', '--live-ja', '--live-dom-ja', '--live-dom-en', '--fixture-camoufox-ja', '--live-camoufox-ja', '--fixture-fourplay-ja', '--live-fourplay-ja'].includes(mode))
  throw new Error('Usage: joshin-input-diagnostics.mjs NEW_RUN --fixture|--live|--fixture-ja|--live-ja|--live-dom-ja|--live-dom-en|--fixture-camoufox-ja|--live-camoufox-ja|--fixture-fourplay-ja|--live-fourplay-ja');
const output = path.resolve(directoryArg);
if (fs.existsSync(output)) throw new Error('output_directory_already_exists');
fs.mkdirSync(output, {recursive: true});
const fixture = mode === '--fixture' || mode === '--fixture-ja' || mode === '--fixture-camoufox-ja' || mode === '--fixture-fourplay-ja';
const regional = mode === '--fixture-ja' || mode === '--live-ja' || mode === '--live-dom-ja' || mode === '--fixture-camoufox-ja' || mode === '--live-camoufox-ja' || mode === '--fixture-fourplay-ja' || mode === '--live-fourplay-ja';
const camoufoxPlaywright = mode === '--fixture-camoufox-ja' || mode === '--live-camoufox-ja';
const ordinaryFourplay = mode === '--fixture-fourplay-ja' || mode === '--live-fourplay-ja';
const camoufoxDependencies = path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS || path.join(repo, '.deps/camoufox'));
const camoufoxRequire = createRequire(path.join(camoufoxDependencies, 'package.json'));
const query = 'グローブ';
const homepageURL = fixture ? null : 'https://joshinweb.jp/top.html';
const xdotool = process.env.BOT_DIAGNOSTICS_XDOTOOL;
if (!xdotool) throw new Error('BOT_DIAGNOSTICS_XDOTOOL_required');
const seed = Number.parseInt(process.env.BOT_DIAGNOSTICS_INPUT_SEED || '271828', 10);
const proxyMetadata=proxy?{proxy_configured:true,proxy_scheme:new URL(proxy).protocol.slice(0,-1),proxy_authentication_present:Boolean(new URL(proxy).username||new URL(proxy).password)}
  :{proxy_configured:false,proxy_scheme:null,proxy_authentication_present:false};
let randomState = seed >>> 0;
const random = () => ((randomState = (1664525 * randomState + 1013904223) >>> 0) / 0x100000000);
const writeJson = (file, value) => fs.writeFileSync(path.join(output, file), JSON.stringify(value, null, 2) + '\n');
const xdo = async (...args) => (await exec(xdotool, args, {env: process.env, encoding: 'utf8', timeout: 15000})).stdout;
async function loadCamoufoxPlaywrightDependencies(config) {
  const {firefox} = camoufoxRequire('playwright-core');
  return {firefox, launchOptions:async()=>structuredClone(config.generated)};
}
function launchOrdinaryFourplay({profile, executable, extension}) {
  const prefs={'intl.accept_languages':'ja-JP,ja,en-US,en','intl.locale.privacy.web_exposed':'ja-JP','intl.regional_prefs.use_os_locales':true};
  fs.appendFileSync(path.join(profile,'user.js'),Object.entries(prefs).map(([key,value])=>`user_pref(${JSON.stringify(key)}, ${JSON.stringify(value)});`).join('\n')+'\n');
  const fourplayDependencies=path.resolve(process.env.BOT_DIAGNOSTICS_FOURPLAY_DEPS||path.join(repo,'.deps/fourplay'));
  const launcher=path.join(fourplayDependencies,'node_modules','.bin','web-ext');
  return spawn(launcher,['run','--source-dir',extension,'--firefox',executable,'--firefox-profile',profile,'--keep-profile-changes','--no-reload','--no-input'],
    {stdio:'ignore',detached:true,env:{...process.env,TZ:'Asia/Tokyo'}});
}
const makeFixture = async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/top.html') {
      res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
      res.end(`<!doctype html><meta charset="utf-8"><title>Joshin Input Fixture</title><form id="search" action="/srhzs.html" method="get" accept-charset="UTF-8"><input id="suggest_input" name="q"><a href="javascript:changeSubmit()" aria-label="検索"><span>検索</span></a></form><script>function changeSubmit(){document.querySelector('form').requestSubmit()}</script>`);
      return;
    }
    if (url.pathname === '/srhzs.html') {
      const value = url.searchParams.get('q') || '';
      res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
      res.end(`<!doctype html><meta charset="utf-8"><title>Fixture results</title><main data-query="${value.replaceAll('&', '&amp;').replaceAll('"', '&quot;')}">検索結果: ${value}</main>`);
      return;
    }
    res.writeHead(404); res.end();
  });
  await new Promise((resolve, reject) => {server.once('error', reject); server.listen(0, '127.0.0.1', resolve);});
  return {server, url: `http://127.0.0.1:${server.address().port}/top.html`, close: () => new Promise(resolve => server.close(resolve))};
};

function makeTraceInitializer() { return () => { const k='joshin-input-trace';const old=JSON.parse(sessionStorage.getItem(k)||'[]');const log=e=>{const t=e.target,s=t?.matches?.('#suggest_input')?'#suggest_input':t?.closest?.('a[href*="changeSubmit"]')?'changeSubmit':'other';const row={type:e.type,isTrusted:e.isTrusted,timeStamp:e.timeStamp,target:s};if(e.type==='keydown'&&s==='#suggest_input')row.key=e.key;if(e.type==='click'||e.type==='pointermove')Object.assign(row,{clientX:e.clientX,clientY:e.clientY});if(e.type==='input'||e.type==='change')row.value=t?.value;old.push(row);if(old.length>300)old.splice(0,old.length-300);sessionStorage.setItem(k,JSON.stringify(old))};for(const n of ['pointermove','pointerdown','pointerup','click','keydown','keyup','input','change','wheel','scroll'])document.addEventListener(n,log,true);return true; }; }

function curve(start, end, straight = false) {
  const count = 20 + Math.floor(random() * 21);
  const dx = end.x - start.x, dy = end.y - start.y;
  const bend = straight ? 0 : (random() < .5 ? -1 : 1) * (4 + random() * 12);
  const p1 = {x: start.x + dx * .28 - dy * .28 * bend / 10, y: start.y + dy * .28 + dx * .28 * bend / 10};
  const p2 = {x: start.x + dx * .72 - dy * .18 * bend / 10, y: start.y + dy * .72 + dx * .18 * bend / 10};
  return Array.from({length: count}, (_, i) => {const t=(i+1)/count,u=1-t;return {x:Math.round(u*u*u*start.x+3*u*u*t*p1.x+3*u*t*t*p2.x+t*t*t*end.x),y:Math.round(u*u*u*start.y+3*u*u*t*p1.y+3*u*t*t*p2.y+t*t*t*end.y)};});
}

async function pointerPath(from, to, straight, actions) {
  const points = curve(from, to, straight);
  const intervals = [];
  for (const point of points) {
    await xdo('mousemove', String(point.x), String(point.y));
    const interval = 30 + Math.floor(random() * 31); intervals.push(interval); await sleep(interval);
  }
  actions.push({kind: 'pointer_path', points, intervals_ms: intervals, straight});
}

async function windowForTitle(title) {
  const ids = (await xdo('search', '--onlyvisible', '--name', '.')).trim().split(/\s+/).filter(Boolean);
  const found = [];
  for (const id of ids) {
    const name = (await xdo('getwindowname', id)).trim();
    if (name === title || name === `${title} - Mozilla Firefox` || name.startsWith(`${title} — `)) found.push({id, name});
  }
  if (found.length !== 1) throw new Error(`browser_window_not_unique_for_observed_title:${found.length}`);
  await xdo('windowraise', found[0].id);
  await xdo('windowfocus', '--sync', found[0].id);
  const focused=(await xdo('getwindowfocus')).trim();
  const focusedName=(await xdo('getwindowname',focused)).trim();
  const targetPid=(await xdo('getwindowpid',found[0].id)).trim();
  const focusedPid=(await xdo('getwindowpid',focused)).trim();
  if(focused!==found[0].id&&focusedName!==found[0].name&&(!targetPid||targetPid!==focusedPid))
    throw new Error('focused_window_does_not_match_observed_browser_window');
  const geometry = await xdo('getwindowgeometry', '--shell', found[0].id);
  return {...found[0],focus:{window_id:focused,title:focusedName,pid:focusedPid,matched:focused===found[0].id||focusedName===found[0].name||Boolean(targetPid&&targetPid===focusedPid)}, geometry: Object.fromEntries(geometry.split(/\r?\n/).filter(x => x.includes('=')).map(x => {const n=x.indexOf('=');return [x.slice(0,n), x.slice(n+1)];}))};
}

function filteredHeaders(request) {
  const headers = Object.fromEntries(Object.entries(request.headers?.() || {}).map(([k,v]) => [k.toLowerCase(), v]));
  const allowed = new Set(['user-agent','accept','accept-language','accept-encoding','referer','origin','sec-fetch-site','sec-fetch-mode','sec-fetch-dest','sec-fetch-user','upgrade-insecure-requests']);
  return {request_headers:Object.fromEntries(Object.entries(headers).filter(([k])=>allowed.has(k))),cookie_names:String(headers.cookie||'').split(';').map(x=>x.trim().split('=')[0]).filter(Boolean)};
}

const main = async () => {
  const localFixture = fixture ? await makeFixture() : null;
  try {
  const targetURL = localFixture?.url || homepageURL;
  let config=null,fingerprintHash=null,fingerprintReusedFrom=null,regionalCondition=null,fingerprintKeyPresence=null;
  if(!ordinaryFourplay){
    config=await buildCamoufoxLaunchOptions();
    let fingerprintEnv=Object.fromEntries(Object.entries(config.generated.env||{}).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k)));
    if(process.env.BOT_DIAGNOSTICS_INPUT_FINGERPRINT_FROM){
      fingerprintReusedFrom=path.resolve(process.env.BOT_DIAGNOSTICS_INPUT_FINGERPRINT_FROM);
      const saved=JSON.parse(fs.readFileSync(path.join(fingerprintReusedFrom,'fingerprint.json'),'utf8'));
      fingerprintEnv=Object.fromEntries(Object.entries(saved).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k)));
      if(!Object.keys(fingerprintEnv).length)throw new Error('saved_camoufox_fingerprint_missing');
      const savedConfig=JSON.parse(Object.entries(fingerprintEnv).sort(([a],[b])=>Number(a.slice(13))-Number(b.slice(13))).map(([,v])=>v).join(''));
      const fonts=Array.isArray(savedConfig.fonts)?savedConfig.fonts:[];
      config={...config,generated:{...config.generated,env:{...config.generated.env,...fingerprintEnv}},metadata:{...config.metadata,
        config_sha256:sha(JSON.stringify(savedConfig)),font_count:fonts.length,font_list_sha256:sha(JSON.stringify(fonts))}};
    }
    const originalFingerprintJSON=Object.entries(fingerprintEnv).sort(([a],[b])=>Number(a.slice(13))-Number(b.slice(13))).map(([,v])=>v).join('');
    const fingerprint=JSON.parse(originalFingerprintJSON);
    const originalFingerprintHash=sha(JSON.stringify(fingerprint));
    regionalCondition=regional||mode==='--live-dom-en'?{mode,original_config_sha256:originalFingerprintHash,applied_keys:{timezone:'Asia/Tokyo',...(regional?{'locale:language':'ja','locale:region':'JP'}:{})}}:null;
    if(regionalCondition){Object.assign(fingerprint,regionalCondition.applied_keys);
      for(const key of Object.keys(config.generated.env||{}))if(/^CAMOU_CONFIG_\d+$/.test(key))delete config.generated.env[key];
      fingerprintEnv={CAMOU_CONFIG_1:JSON.stringify(fingerprint)};config.generated.env={...config.generated.env,...fingerprintEnv};
      const fonts=Array.isArray(fingerprint.fonts)?fingerprint.fonts:[];
      config.metadata={...config.metadata,config_sha256:sha(JSON.stringify(fingerprint)),font_count:fonts.length,font_list_sha256:sha(JSON.stringify(fonts)),firefox_user_pref_count:Object.keys(config.generated.firefoxUserPrefs||{}).length};}
    else fingerprintEnv=Object.fromEntries(Object.entries(config.generated.env||{}).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k)));
    fingerprintHash=sha(JSON.stringify(fingerprint));
    if(config.metadata.config_sha256!==fingerprintHash)throw new Error('camoufox_fingerprint_metadata_hash_mismatch');
    if(camoufoxPlaywright)config.metadata={...config.metadata,playwright_control:true,launch_backend:'Playwright persistent context'};
    const keys=[];const visit=value=>{if(Array.isArray(value)){for(const item of value)visit(item)}else if(value&&typeof value==='object'){for(const [key,item] of Object.entries(value)){keys.push(key);visit(item)}}};visit(fingerprint);
    fingerprintKeyPresence={timezone_keys_present:keys.some(key=>/time.?zone/i.test(key)),locale_keys_present:keys.some(key=>/(locale|language)/i.test(key))};
    writeJson('fingerprint.json',fingerprintEnv);
  }
  const sourceEvidence = new Evidence(path.join(output, 'source-capture'), false, {policy:'browser_observation', authorization:'User-authorized Joshin search and input-condition diagnostics using the configured browser transport; browser observation only.'});
  sourceEvidence.save('sources.json', snapshotSources(sourceEvidence));
  if(config)sourceEvidence.save('camoufox-launch.json',config.metadata);
  sourceEvidence.flush(true);
  const nativeFourplayCondition=ordinaryFourplay?{timezone_env:'Asia/Tokyo',firefox_user_prefs:{'intl.accept_languages':'ja-JP,ja,en-US,en','intl.locale.privacy.web_exposed':'ja-JP','intl.regional_prefs.use_os_locales':true},locale_environment:{LOCPATH:process.env.LOCPATH||null,LC_ALL:process.env.LC_ALL||null,LANG:process.env.LANG||null}}:null;
  const common = {source_commit:process.env.BOT_DIAGNOSTICS_SOURCE_COMMIT||null,node:process.version,mode,query,homepage_url:targetURL,
    browser_control:ordinaryFourplay?'Firefox + native 4play':camoufoxPlaywright?'Camoufox + Playwright':'Camoufox + native 4play WebExtension',transport:fixture?'fixture_loopback':proxy?'proxy_browser':'direct_browser',...proxyMetadata,fixture_network_route:fixture?(camoufoxPlaywright?'loopback direct in Playwright; no 4play proxy':'loopback through isolated 4play proxy'):null,home_settle_ms:6000,seed,
    fingerprint_sha256:fingerprintHash,fingerprint_reused_from:fingerprintReusedFrom,regional_condition:regionalCondition,native_fourplay_condition:nativeFourplayCondition,fingerprint_config_key_presence:fingerprintKeyPresence,camoufox_metadata:config?.metadata??null,profile:'fresh for every arm; retained for its homepage-to-search navigation',
    prior_evidence:{source:'experiments/bot-diagnostics/joshin-product-search.mjs',selection:'assembled arm from the separately saved earlier run',submission:'DOM fill plus DOM click; input and pointer events were untrusted'},
    capture_scope:'main-frame documents and same-origin XHR or POST script only; all other resources load normally',cookies:'names only; values are never stored',
    proxy_environment_present:Object.fromEntries(['HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy','ALL_PROXY','all_proxy'].map(name=>[name,Boolean(process.env[name])])),tls_verification_enabled:true};
  writeJson('conditions.json', common);
  const plans = [{id:'full', offsetClick:true, curved:true, slow:true, scroll:true, baseline:false}];
  const rows = [];
  let accepted = plans[0];
  const runPlan = async (plan, index) => {
    const armDir = path.join(output, `${String(index+1).padStart(2,'0')}-${plan.id}`);
    const evidence = new Evidence(armDir, false, {policy:'browser_observation',flush_interval_ms:500,
      authorization:fixture?'User-authorized local loopback fixture to prove trusted native Unicode input and event capture.':'User-authorized Joshin search behavior and input-condition diagnostics within the stated 6-live-attempt limit.'});
    evidence.save('sources.json', snapshotSources(evidence));
    evidence.save('conditions.json', {...common,arm:plan,attempt:index+1,attempt_limit:fixture?1:6,
      operations:plan.baseline?'existing DOM-fill plus observed DOM-click control':{click_position:plan.offsetClick?'inside at deterministic 20% offset':'element center',pointer_movement:plan.curved?'multi-curve path':'straight path',typing:plan.slow?'xdotool key dispatch delay 120ms plus 200-500ms inter-character pause':'xdotool key dispatch delay 120ms with no extra inter-character pause',key_dispatch_delay_ms:120,scroll:plan.scroll?'one small down/up pair before input':'none'}});
    const measurement={arm:plan.id,attempt:index+1,query,home_settle_ms:6000,homepage:null,search:null,search_success:false,
      products:null,result_dom_obtained:false,stop_reason:null};
    const records=[], documents=[], pending=new Set(), actions=[];
    const requestRecords=new WeakMap();let sequence=0,browser,stage='homepage',windowInfo=null,observedOrigin=new URL(targetURL).origin;
    const recordRequest=request=>{
      if(requestRecords.has(request))return requestRecords.get(request);
      const url=request.url(), method=request.method(), type=request.resourceType();
      let parsed;try{parsed=new URL(url)}catch{return null;}
      const frame=request.frame?.();
      const eligible=request.isNavigationRequest()&&(!frame||typeof frame.parentFrame!=='function'||frame.parentFrame()===null)
        || observedOrigin&&parsed.origin===observedOrigin&&(type==='xhr'||(method==='POST'&&type==='script'));
      if(!eligible)return null;
      const rec=evidence.reserve(`${plan.id}/joshin-glove`,url,request.isNavigationRequest()?'document':type);
      rec.stage=stage;rec.method=method;rec.resource_type=type;rec.request_sequence=++sequence;rec.route=fixture?(camoufoxPlaywright?'fixture_loopback_direct':'fixture_loopback_relay'):proxy?'proxy_browser':'direct_browser';
      Object.assign(rec,filteredHeaders(request));requestRecords.set(request,rec);records.push(rec);return rec;
    };
    try {
      browser=ordinaryFourplay
        ? await openFourplayNative({fixture,profile:`joshin-input-${index+1}`,launch:launchOrdinaryFourplay})
        : camoufoxPlaywright
        ? await openCamoufox({headful:true,fixture,profile:`joshin-input-${index+1}`,loadDependencies:()=>loadCamoufoxPlaywrightDependencies(config)})
        : await openCamoufoxFourplay({fixture,profile:`joshin-input-${index+1}`,headful:true,
          loadLaunchOptions:async()=>async()=>structuredClone(config.generated)});
      measurement.runtime=browser.runtime;
      measurement.runtime_metadata=ordinaryFourplay?browser.runtime:config?.metadata??null;
      browser.context.on('request',recordRequest);
      browser.context.on('response',response=>{
        const request=response.request();const rec=recordRequest(request);if(!rec)return;
        if(request.isNavigationRequest()){const requestObservation=filteredHeaders(request);documents.push({url:response.url(),status:response.status(),stage,method:request.method(),request_headers:requestObservation.request_headers,cookie_names:requestObservation.cookie_names,at:new Date().toISOString()})}
        const task=(async()=>{try{evidence.finish(rec,response.status(),response.headers(),await response.body())}catch(error){evidence.fail(rec,error)}})();pending.add(task);task.finally(()=>pending.delete(task));
      });
      browser.context.on('requestfailed',request=>{const rec=recordRequest(request);if(rec)evidence.fail(rec,new Error('browser_request_failed'))});
      await browser.page.goto(targetURL,{timeout:30000});
      await sleep(6000);
      measurement.homepage=await browser.page.evaluate(()=>({url:location.href,title:document.title,readyState:document.readyState,
        status:performance.getEntriesByType('navigation')[0]?.responseStatus||null,viewport:{width:innerWidth,height:innerHeight},screen:{width:screen.width,height:screen.height},scroll_y:scrollY,
        screenX,screenY,mozInnerScreenX,mozInnerScreenY,outerWidth,outerHeight,devicePixelRatio,ua:navigator.userAgent,webdriver:navigator.webdriver,
        cookie_names:document.cookie.split(';').map(x=>x.trim().split('=')[0]).filter(Boolean),input_count:document.querySelectorAll('#suggest_input').length,
        input_rect:(()=>{const r=document.querySelector('#suggest_input')?.getBoundingClientRect();return r&&{x:r.x,y:r.y,width:r.width,height:r.height}})(),
        control:(()=>{const i=document.querySelector('#suggest_input'),a=[...(i?.form?.querySelectorAll('a[href]')||[])].filter(x=>x.getAttribute('href')?.includes('changeSubmit'));return {form_action:i?.form?.action||null,form_method:i?.form?.method||null,accept_charset:i?.form?.acceptCharset||null,icon_count:a.length,icon_href:a[0]?.getAttribute('href')||null,icon_rect:(()=>{const r=a[0]?.getBoundingClientRect();return r&&{x:r.x,y:r.y,width:r.width,height:r.height}})()}})()}));
      const homeResponse=documents.filter(x=>x.url===measurement.homepage.url).at(-1);measurement.homepage.http_status=homeResponse?.status??null;
      const environment=await browser.page.evaluate(()=>{
        const fontNames=['Arial','Arimo','Noto Sans CJK JP','Noto Sans JP','Noto Serif CJK JP','monospace'];
        const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
        const input=document.querySelector('#suggest_input'),computed=input?getComputedStyle(input):null;
        const fonts=fontNames.map(family=>{if(context)context.font=`32px "${family.replaceAll('"','')}"`;return {family,document_font_check:document.fonts?.check(`32px "${family.replaceAll('"','')}"`)??null,latin_width:context?.measureText('Hamburgefontsiv 0123456789').width??null,japanese_width:context?.measureText('日本語グローブ').width??null}});
        const fontResources=performance.getEntriesByType('resource').map(entry=>{try{const url=new URL(entry.name);if(!/\.(woff2?|ttf|otf)$/i.test(url.pathname))return null;return `${url.origin}${url.pathname}`}catch{return null}}).filter(Boolean);
        return {intl_datetime_format:Intl.DateTimeFormat().resolvedOptions(),date_timezone_offset_minutes:{now:new Date().getTimezoneOffset(),jan_1_2026:new Date('2026-01-01T00:00:00Z').getTimezoneOffset(),jul_1_2026:new Date('2026-07-01T00:00:00Z').getTimezoneOffset()},
          navigator:{language:navigator.language,languages:Array.from(navigator.languages),platform:navigator.platform},document:{lang:document.documentElement.lang,character_set:document.characterSet},
          search_input_computed_font:computed?{font:computed.font,font_family:computed.fontFamily,font_size:computed.fontSize,font_weight:computed.fontWeight}:null,
          font_measurements:fonts,font_set_status:document.fonts?.status??null,loaded_font_resource_urls:[...new Set(fontResources)],cookie_names:document.cookie.split(';').map(part=>part.trim().split('=')[0]).filter(Boolean)};
      });
      const homepageDocument=documents.filter(item=>item.url===measurement.homepage.url&&item.stage==='homepage').at(-1);
      environment.cookie_comparison={request_cookie_names:homepageDocument?.cookie_names||[],document_cookie_names:environment.cookie_names,
        request_only_names:(homepageDocument?.cookie_names||[]).filter(name=>!environment.cookie_names.includes(name)),document_only_names:environment.cookie_names.filter(name=>!(homepageDocument?.cookie_names||[]).includes(name))};
      if(regional)environment.request_accept_language=homepageDocument?.request_headers?.['accept-language']||null;
      measurement.homepage.environment=environment;evidence.save('environment-observation.json',environment);
      if(measurement.homepage.http_status!==200||measurement.homepage.input_count!==1||measurement.homepage.control.icon_count!==1)throw new Error('homepage_or_search_controls_unavailable');
      observedOrigin=new URL(measurement.homepage.url).origin;
      await browser.page.evaluate(makeTraceInitializer());
      const activationBefore=await browser.page.evaluate(()=>({isActive:navigator.userActivation?.isActive??null,hasBeenActive:navigator.userActivation?.hasBeenActive??null}));
      windowInfo=await windowForTitle(measurement.homepage.title);
      const d=await xdo('getmouselocation','--shell');const start={x:Number(/X=(\d+)/.exec(d)?.[1]||0),y:Number(/Y=(\d+)/.exec(d)?.[1]||0)};
      let rect=measurement.homepage.input_rect, icon=measurement.homepage.control.icon_rect;
      const point=(r,offset)=>({x:Math.round(measurement.homepage.mozInnerScreenX+r.x+r.width/2+(offset?r.width*(random()<.5?-.2:.2):0)),y:Math.round(measurement.homepage.mozInnerScreenY+r.y+r.height/2+(offset?r.height*(random()<.5?-.2:.2):0))});
      let inputPoint=point(rect,plan.offsetClick), iconPoint=point(icon,plan.offsetClick);
      const coordinateEvidence={window:windowInfo,geometry_observation:{screenX:measurement.homepage.screenX,screenY:measurement.homepage.screenY,
        mozInnerScreenX:measurement.homepage.mozInnerScreenX,mozInnerScreenY:measurement.homepage.mozInnerScreenY,innerWidth:measurement.homepage.viewport.width,innerHeight:measurement.homepage.viewport.height,
        outerWidth:measurement.homepage.outerWidth,outerHeight:measurement.homepage.outerHeight,screen:measurement.homepage.screen},input_rect:rect,icon_rect:icon,input_click:inputPoint,icon_click:iconPoint};
      if(process.env.BOT_DIAGNOSTICS_X11_CAPTURE){
        const capture=path.resolve(process.env.BOT_DIAGNOSTICS_X11_CAPTURE);
        await exec('python3',[capture,path.join(armDir,'before-input.png')],{env:process.env,encoding:'utf8',timeout:15000});
      }
      if(plan.baseline){
        await browser.page.evaluate(value=>{const input=document.querySelector('#suggest_input');input.focus();input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))},query);actions.push({kind:'dom_fill',trusted:false,value:query});
        stage='search';
        await browser.page.evaluate(()=>{const a=[...document.querySelector('#suggest_input').form.querySelectorAll('a[href]')].find(x=>x.getAttribute('href').includes('changeSubmit'));a.click()});
        actions.push({kind:'dom_click',trusted:false});
      }else{
        if(plan.scroll){
          await xdo('mousemove',String(inputPoint.x),String(inputPoint.y));await xdo('click','5');await sleep(120);await xdo('click','4');await sleep(180);
          const afterScroll=await browser.page.evaluate(()=>({scroll_y:scrollY,input:(()=>{const r=document.querySelector('#suggest_input').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}})(),icon:(()=>{const a=[...document.querySelector('#suggest_input').form.querySelectorAll('a[href]')].find(x=>x.getAttribute('href').includes('changeSubmit')),r=a.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}})()}));
          rect=afterScroll.input;icon=afterScroll.icon;inputPoint=point(rect,plan.offsetClick);iconPoint=point(icon,plan.offsetClick);
          coordinateEvidence.input_rect=rect;coordinateEvidence.icon_rect=icon;coordinateEvidence.input_click=inputPoint;coordinateEvidence.icon_click=iconPoint;
          actions.push({kind:'scroll_pair',direction:['down','up'],native_input:true,point:inputPoint,scroll_y_before:measurement.homepage.scroll_y,scroll_y_after:afterScroll.scroll_y,scroll_y_restored:measurement.homepage.scroll_y===afterScroll.scroll_y});
        }
        await pointerPath(start,inputPoint,!plan.curved,actions);
        await xdo('click','1');actions.push({kind:'click_input',point:inputPoint,position:plan.offsetClick?'offset':'center'});
        for(const char of query){const delay=plan.slow?200+Math.floor(random()*301):0;await exec(xdotool,['type','--clearmodifiers','--delay','120',char],{env:process.env,encoding:'utf8',timeout:15000});actions.push({kind:'type_character',key_dispatch_delay_ms:120,inter_character_delay_ms:delay,codepoint:char.codePointAt(0)});if(delay)await sleep(delay)}
        const typed=await browser.page.evaluate(()=>document.querySelector('#suggest_input')?.value||'');
        if(typed!==query)throw new Error(`native_japanese_input_mismatch:${typed.length}`);
        await pointerPath(inputPoint,iconPoint,!plan.curved,actions);stage='search';await xdo('click','1');actions.push({kind:'click_submit_icon',point:iconPoint,position:plan.offsetClick?'offset':'center'});
      }
      evidence.save('native-input-actions.json',{seed,actions,coordinates:coordinateEvidence});
      const deadline=Date.now()+30000;
      while(Date.now()<deadline){const s=await browser.page.evaluate(()=>({url:location.href,state:document.readyState,title:document.title})).catch(()=>null);if(s&&s.url!==measurement.homepage.url&&s.state==='complete'&&documents.some(x=>x.url===s.url))break;await sleep(150)}
      const searchState=await browser.page.evaluate(()=>({url:location.href,title:document.title,readyState:document.readyState,body_text:(document.body?.innerText||'').slice(0,3000),dom:document.documentElement?.outerHTML||'',trace:JSON.parse(sessionStorage.getItem('joshin-input-trace')||'[]'),user_activation:{isActive:navigator.userActivation?.isActive??null,hasBeenActive:navigator.userActivation?.hasBeenActive??null},cookie_names:document.cookie.split(';').map(x=>x.trim().split('=')[0]).filter(Boolean),search_fields:[...document.querySelectorAll('#suggest_input')].map(x=>({value:x.value,name:x.name})),resource_scripts:[...document.scripts].map(x=>x.src).filter(Boolean)}));
      const response=documents.filter(x=>x.url===searchState.url).at(-1);measurement.search={...searchState,http_status:response?.status??null,dom_sha256:evidence.blob(Buffer.from(searchState.dom)),documents_seen:documents.length};
      evidence.result({client:'camoufox-fourplay',target:'joshin-glove',stage:'search',url:searchState.url,title:searchState.title,http_status:measurement.search.http_status,dom_sha256:measurement.search.dom_sha256});
      measurement.result_dom_obtained=Boolean(searchState.dom&&searchState.title&&response&&searchState.readyState==='complete'
        &&new URL(searchState.url).pathname==='/srhzs.html'&&searchState.body_text.trim());
      measurement.search_success=measurement.search.http_status===200&&measurement.result_dom_obtained;
      measurement.products=null;
      measurement.stop_reason=measurement.search.http_status===403?'http_403_not_zero_products':measurement.search_success?'search_200_dom_obtained':'search_status_or_dom_unconfirmed';
      measurement.user_activation_before_after={before:null,after:searchState.user_activation};
      measurement.user_activation_before_after.before=activationBefore;
      measurement.fixture_verification=fixture?{query_in_get:new URL(searchState.url).searchParams.get('q')===query,
        typed_events_trusted:searchState.trace.some(x=>x.type==='input'&&x.target==='#suggest_input'&&x.isTrusted===true&&x.value===query),
        key_events_trusted:searchState.trace.some(x=>x.type==='keydown'&&x.target==='#suggest_input'&&x.isTrusted===true),
        pointer_movement_trusted:searchState.trace.some(x=>x.type==='pointermove'&&x.isTrusted===true),click_trusted:searchState.trace.some(x=>x.type==='click'&&x.isTrusted===true&&x.target==='changeSubmit'),
        same_window:measurement.search.url.startsWith(observedOrigin),http_status_200:measurement.search.http_status===200,
        ...(mode==='--fixture-ja'||mode==='--fixture-camoufox-ja'||mode==='--fixture-fourplay-ja'?{timezone_asia_tokyo:measurement.homepage.environment?.intl_datetime_format?.timeZone==='Asia/Tokyo',intl_locale_ja_jp:measurement.homepage.environment?.intl_datetime_format?.locale==='ja-JP',navigator_language_ja_jp:measurement.homepage.environment?.navigator?.language==='ja-JP',date_offset_minus_540:measurement.homepage.environment?.date_timezone_offset_minutes?.now===-540,jan_1_2026_offset_minus_540:measurement.homepage.environment?.date_timezone_offset_minutes?.jan_1_2026===-540,jul_1_2026_offset_minus_540:measurement.homepage.environment?.date_timezone_offset_minutes?.jul_1_2026===-540,request_accept_language_ja_jp:String(measurement.homepage.environment?.request_accept_language||'').includes('ja-JP')}: {})}:null;
      evidence.save('trace.json',searchState.trace);
      if(process.env.BOT_DIAGNOSTICS_X11_CAPTURE){
        const capture=path.resolve(process.env.BOT_DIAGNOSTICS_X11_CAPTURE);
        await exec('python3',[capture,path.join(armDir,'search-result.png')],{env:process.env,encoding:'utf8',timeout:15000});
      }
      evidence.save('search.json',{...measurement.search,dom:undefined});
      evidence.save('measurement.json',measurement);
    }catch(error){measurement.error=safeError(error);measurement.stop_reason||='experiment_error'}
    finally{
      if(browser&&!fs.existsSync(path.join(armDir,'trace.json'))){
        try{evidence.save('trace.json',await browser.page.evaluate(()=>JSON.parse(sessionStorage.getItem('joshin-input-trace')||'[]')))}catch{evidence.save('trace.json',[])}
      }
      await Promise.allSettled([...pending]);await browser?.close?.().catch(error=>measurement.close_error=safeError(error));
      for(const record of records)if(record.status==='interrupted')evidence.fail(record,new Error('capture_unfinished_at_close'));
      if(!fs.existsSync(path.join(armDir,'native-input-actions.json')))evidence.save('native-input-actions.json',{seed,actions,coordinates:windowInfo});
      if(!fs.existsSync(path.join(armDir,'trace.json')))evidence.save('trace.json',[]);
      measurement.documents=documents;measurement.finished_at=new Date().toISOString();evidence.flush(true);measurement.verification=verify(armDir);evidence.save('measurement.json',measurement);
      rows.push(measurement);writeJson('results.json',rows);
      console.log(JSON.stringify({arm:plan.id,status:measurement.search?.http_status,success:measurement.search_success,fixture_verification:measurement.fixture_verification,verify:measurement.verification.ok,error:measurement.error}));
    }
    return measurement;
  };

    if(fixture){const result=await runPlan(plans[0],0);const v=result.fixture_verification||{};if(!result.verification.ok||!v.http_status_200||!v.query_in_get||!v.typed_events_trusted||!v.key_events_trusted||!v.pointer_movement_trusted||!v.click_trusted||!v.same_window||((mode==='--fixture-ja'||mode==='--fixture-camoufox-ja'||mode==='--fixture-fourplay-ja')&&(!v.timezone_asia_tokyo||!v.intl_locale_ja_jp||!v.navigator_language_ja_jp||!v.date_offset_minus_540||!v.jan_1_2026_offset_minus_540||!v.jul_1_2026_offset_minus_540||!v.request_accept_language_ja_jp)))process.exitCode=1;return;}
    if(mode==='--live-dom-ja'||mode==='--live-dom-en'){
      await runPlan({id:'dom-control',offsetClick:false,curved:false,slow:false,scroll:false,baseline:true},0);return;
    }
    if(mode==='--live-camoufox-ja'){
      await runPlan({id:'dom-control',offsetClick:false,curved:false,slow:false,scroll:false,baseline:true},0);return;
    }
    if(mode==='--live-fourplay-ja'){
      await runPlan({id:'dom-control',offsetClick:false,curved:false,slow:false,scroll:false,baseline:true},0);return;
    }
    let full=await runPlan(accepted,0);
    if(mode==='--live-ja'&&!full.search_success)return;
    if(!full.search_success){
      accepted={...accepted,id:'full-recheck'};full=await runPlan(accepted,1);
      if(!full.search_success){await runPlan({id:'dom-control',offsetClick:false,curved:false,slow:false,scroll:false,baseline:true},2);return;}
    }
    const removals=[['center-click',p=>({...p,id:'center-click',offsetClick:false})],['straight-movement',p=>({...p,id:'straight-movement',curved:false})],['fast-typing',p=>({...p,id:'fast-typing',slow:false})],['no-scroll',p=>({...p,id:'no-scroll',scroll:false})]];
    for(const [name,remove] of removals){const trial=remove(accepted);const result=await runPlan(trial,rows.length);if(result.search_success)accepted=trial;else accepted={...accepted,id:`${accepted.id}-restored-after-${name}`}}
  }finally{await localFixture?.close()}
};

await main();
