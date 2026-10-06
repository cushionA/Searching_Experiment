import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';

const here=path.dirname(fileURLToPath(import.meta.url));
const repo=path.resolve(here,'../..');
const camoufoxDeps=path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS||path.join(repo,'.deps/camoufox'));
const camoufoxBrowser=path.join(camoufoxDeps,'browser');
const camoufoxExecutable=path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_BINARY||path.join(camoufoxBrowser,'camoufox-bin'));
const fourplayDeps=path.resolve(process.env.BOT_DIAGNOSTICS_FOURPLAY_DEPS||path.join(repo,'.deps/fourplay'));
const fourplayExtension=path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION||path.join(repo,'.deps/fourplay-camoufox/ext'));
const webExt=path.join(fourplayDeps,'node_modules','.bin','web-ext');

let camoufoxOptionsLoader;
async function loadCamoufoxLaunchOptions() {
  if(camoufoxOptionsLoader) return camoufoxOptionsLoader;
  const had=Object.hasOwn(process.env,'CAMOUFOX_INSTALL_DIR'), previous=process.env.CAMOUFOX_INSTALL_DIR;
  process.env.CAMOUFOX_INSTALL_DIR=camoufoxBrowser;
  const localRequire=createRequire(path.join(camoufoxDeps,'package.json'));
  const entry=localRequire.resolve('camoufox-js');
  camoufoxOptionsLoader=import(pathToFileURL(path.join(path.dirname(entry),'utils.js')).href)
    .then(module=>module.launchOptions)
    .finally(()=>{if(had)process.env.CAMOUFOX_INSTALL_DIR=previous;else delete process.env.CAMOUFOX_INSTALL_DIR;});
  try { return await camoufoxOptionsLoader; }
  catch(error) { camoufoxOptionsLoader=undefined; throw error; }
}

export async function buildCamoufoxLaunchOptions({loadLaunchOptions=loadCamoufoxLaunchOptions,
  binary=camoufoxExecutable,env=process.env,window=[1280,720]}={}) {
  if(!fs.existsSync(binary)) throw new Error(`hybrid_camoufox_binary_missing: ${binary}`);
  const launchOptions=await loadLaunchOptions();
  const generated=await launchOptions({
    headless:false,os:'linux',humanize:false,geoip:false,exclude_addons:['UBO'],config:{allowAddonNewtab:true},
    executable_path:binary,window,
    env:{...env,FONTCONFIG_PATH:path.join(camoufoxBrowser,'fontconfig','linux')},
  });
  const chunks=Object.entries(generated.env||{}).filter(([key])=>/^CAMOU_CONFIG_\d+$/.test(key))
    .sort(([a],[b])=>Number(a.slice(13))-Number(b.slice(13)));
  if(!chunks.length) throw new Error('hybrid_camoufox_fingerprint_config_missing');
  const fingerprint=JSON.parse(chunks.map(([,value])=>value).join(''));
  const fontList=Array.isArray(fingerprint.fonts)?fingerprint.fonts:[];
  return {generated,binary,metadata:{camoufox_version:readCamoufoxVersion(),camoufox_js:'0.12.0',
    executable_path:binary,config_sha256:crypto.createHash('sha256').update(JSON.stringify(fingerprint)).digest('hex'),
    font_count:fontList.length,font_list_sha256:crypto.createHash('sha256').update(JSON.stringify(fontList)).digest('hex'),
    firefox_user_pref_count:Object.keys(generated.firefoxUserPrefs||{}).length,
    playwright_control:false,playwright_core_direct_import_statement:/from\s+['"]playwright(?:-core)?['"]|import\(['"]playwright(?:-core)?['"]\)/.test(fs.readFileSync(fileURLToPath(import.meta.url),'utf8')),
    addon_tab_creation_enabled:true,launch_backend:'web-ext + native Firefox process'}};
}

function readCamoufoxVersion() {
  const metadataPath=path.join(camoufoxDeps,'browser-runtime.json');
  try { return JSON.parse(fs.readFileSync(metadataPath,'utf8')).browser_version||'unknown'; }
  catch { return 'unknown'; }
}

function prefsSource(prefs={}) {
  return Object.entries(prefs).map(([key,value])=>`user_pref(${JSON.stringify(key)}, ${JSON.stringify(value)});`).join('\n')+'\n';
}

export function createCamoufoxLaunchHook({launchOptions,spawnProcess=spawn,launcher=webExt}={}) {
  if(!launchOptions?.generated || !launchOptions?.binary) throw new TypeError('camoufox_launch_options_required');
  return ({profile,extension=fourplayExtension})=>{
    if(!fs.existsSync(extension)) throw new Error(`hybrid_fourplay_extension_missing: ${extension}`);
    if(!fs.existsSync(launcher)) throw new Error(`hybrid_web_ext_missing: ${launcher}`);
    const userJs=path.join(profile,'user.js');
    fs.appendFileSync(userJs,prefsSource({...launchOptions.generated.firefoxUserPrefs,'privacy.userContext.enabled':true}),{encoding:'utf8'});
    const args=['run','--source-dir',extension,'--firefox',launchOptions.binary,'--firefox-profile',profile,
      '--keep-profile-changes','--no-reload','--no-input'];
    if(launchOptions.generated.args?.length) args.push('--args',launchOptions.generated.args.join(' '));
    return spawnProcess(launcher,args,{stdio:'ignore',detached:true,env:{...process.env,...launchOptions.generated.env,
      CAMOUFOX_INSTALL_DIR:camoufoxBrowser}});
  };
}

export async function openCamoufoxFourplay({fixture=false,profile='diagnostic',port=Number(process.env.BOT_DIAGNOSTICS_FOURPLAY_PORT||3030),
  connectTimeout=30000,headful=true,extensions=[],timezoneId,openFourplayImpl,
  loadLaunchOptions=loadCamoufoxLaunchOptions,spawnProcess=spawn,binary=camoufoxExecutable,
  extensionPath=fourplayExtension,launcherPath=webExt}={}) {
  if(!headful) throw new Error('unsupported_capability:headless');
  if(extensions.length) throw new Error('unsupported_capability:extra_extensions');
  if(timezoneId) throw new Error('unsupported_capability:timezone');
  const loader=openFourplayImpl || (await import(pathToFileURL(path.join(repo,'experiments/bot-diagnostics/fourplay-native-runtime.mjs')).href)).openFourplayNative;
  const config=await buildCamoufoxLaunchOptions({loadLaunchOptions,binary});
  const require=createRequire(import.meta.url);
  const playwrightCacheBefore=Object.keys(require.cache).filter(file=>file.includes(`${path.sep}playwright-core${path.sep}`));
  const launch=createCamoufoxLaunchHook({launchOptions:config,spawnProcess,launcher:launcherPath});
  const opened=await loader({fixture,profile,port,connectTimeout,launch,executable:config.binary,extension:extensionPath});
  const playwrightCacheAfter=Object.keys(require.cache).filter(file=>file.includes(`${path.sep}playwright-core${path.sep}`));
  opened.runtime={...opened.runtime,...config.metadata,browser_engine:'Camoufox',
    browser_control:'4play WebExtension',fourplay_connected:true,playwright_control:false,
    playwright_core_peer_declared:'<1.61.0',
    playwright_core_require_cache_before:playwrightCacheBefore.length,
    playwright_core_require_cache_after:playwrightCacheAfter.length,
    playwright_core_cjs_cache_present_after:playwrightCacheAfter.length>0,
    browser_binary_reused:true,
    required_firefox_pref_overrides:['privacy.userContext.enabled=true'],
    third_party_proxy_policy:'4play target container controls proxy routing',
    background_routing_not_measured:true};
  opened.kind='fourplay-native';
  return opened;
}
