import fs from 'node:fs';
import path from 'node:path';
import {here, TOOL_NAMES, limits} from './runtime.mjs';
import {validateLightpanda} from './lightpanda-runtime.mjs';
import {normalizeSessionPolicy} from './session-policy.mjs';
import {DEFAULT_RECOVERY_PLAN} from './scenario.mjs';
import {validateSteps, validateExpectation} from './operations.mjs';

export function injectURL(template,params) {
  return template.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g,(_,key)=>{
    if(!Object.hasOwn(params,key)) throw new Error(`missing_parameter:${key}`);
    return encodeURIComponent(String(params[key]));
  });
}
const injectValue=(value,params)=>value.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g,(_,key)=>{
  if(!Object.hasOwn(params,key)) throw new Error(`missing_parameter:${key}`);
  return String(params[key]);
});
export function extensionFiles(directory) {
  const files=[];
  const walk=(current,depth=0)=>{
    if(depth>4) throw new Error('extension_directory_too_deep');
    for(const entry of fs.readdirSync(current,{withFileTypes:true})) {
      if(entry.name.startsWith('.')) continue;
      const filename=path.join(current,entry.name);
      if(entry.isSymbolicLink()) throw new Error('extension_symlinks_unsupported');
      if(entry.isDirectory()) walk(filename,depth+1);
      else if(entry.isFile()) {
        if(files.length>=32 || fs.statSync(filename).size>262144) throw new Error('extension_source_limit');
        files.push(filename);
      }
    }
  };
  walk(directory);return files;
}
function checkExtension(directory) {
  const manifest=JSON.parse(fs.readFileSync(path.join(directory,'manifest.json')));
  // Background-network traffic is outside this page-scoped interception boundary.
  if(manifest.manifest_version!==3 || manifest.background || manifest.permissions?.length || manifest.host_permissions?.length
    || manifest.web_accessible_resources?.length) throw new Error('unsupported_capability:extension_requires_unaccounted_background_or_permissions');
  extensionFiles(directory);return directory;
}
export function prepare(manifest,{includeGoogle=false,fixture=false,selectors=false,recoverSimple=false,
  humanlike=false,extensions=false,extensionPaths=[],baseDirectory=here}={}) {
  if(manifest.schema!==1 || !Array.isArray(manifest.sites) || !Array.isArray(manifest.tools)) throw new Error('invalid_manifest');
  if(manifest.tools.some(tool=>!TOOL_NAMES.includes(tool)) || new Set(manifest.tools).size!==manifest.tools.length) throw new Error('unknown_or_duplicate_tool');
  if(manifest.limits?.requests_per_tool_site!==limits.requests_per_client_target || manifest.limits?.body_bytes_per_tool_site!==limits.bytes_per_client_target) throw new Error('limits_must_match_executor');
  const recoveryPlan={...DEFAULT_RECOVERY_PLAN,...manifest.recovery_plan};
  if(![0,1].includes(recoveryPlan.max_attempts) || !Number.isFinite(recoveryPlan.wait_seconds)
    || recoveryPlan.wait_seconds<0 || recoveryPlan.wait_seconds>60) throw new Error('invalid_recovery_budget_or_wait');
  const options={selectors:!!(selectors||humanlike||manifest.options?.selectors),
    recoverSimple:!!(recoverSimple||manifest.options?.recoverSimple),seed:manifest.options?.seed??1,
    recoveryMaxAttempts:recoveryPlan.max_attempts};
  if(!Number.isInteger(options.seed)) throw new Error('integer_seed_required');
  const profiles=structuredClone(manifest.profiles||[{id:'baseline',humanlike:false,extensions:[]}]);
  const lightpandaDefault=profiles.find(profile=>profile.lightpanda?.profile==='compat')?.lightpanda
    || profiles.find(profile=>profile.lightpanda)?.lightpanda;
  if(humanlike) profiles.push({id:'humanlike',humanlike:true,extensions:[],
    ...(lightpandaDefault?{lightpanda:{...lightpandaDefault}}:{}),
    ...(profiles.find(p=>p.session_pool)?.session_pool?{session_pool:structuredClone(profiles.find(p=>p.session_pool).session_pool)}:{})});
  if(extensions) profiles.push({id:'extension',humanlike:false,extensions:[path.join(here,'extensions/observation-probe')],
    extension_probe:{kind:'attribute',selector:'html',name:'data-bot-diagnostics-extension',value:'loaded'}});
  if(extensionPaths.length) profiles.push({id:'custom-extension',humanlike:false,extensions:extensionPaths});
  const profileIDs=new Set();
  for(const profile of profiles) {
    if(!/^[a-z][a-z0-9-]*$/.test(profile.id) || profileIDs.has(profile.id)) throw new Error('invalid_or_duplicate_profile_id');
    profileIDs.add(profile.id);
    if(profile.session_pool!==undefined) {
      profile.session_pool=normalizeSessionPolicy(profile.session_pool);
      if(profile.session_pool && (manifest.tools.some(tool=>['wreq-js','impit'].includes(tool)) || profile.extensions?.length)) throw new Error('session_pool_requires_browser_without_extensions');
    }
    if(profile.lightpanda) {
      validateLightpanda(profile.lightpanda);
      if(manifest.tools.some(tool=>tool!=='rebrowser-lightpanda')) throw new Error('lightpanda_profile_requires_lightpanda');
    }
    profile.extensions=(profile.extensions||[]).map(dir=>checkExtension(path.resolve(baseDirectory,dir)));
    if(profile.extension_probe) validateExpectation(profile.extension_probe);
  }
  const ids=new Set();
  const sites=manifest.sites.map(site=>{
    if(!/^[a-z][a-z0-9-]*$/.test(site.id) || ids.has(site.id)) throw new Error('invalid_or_duplicate_site_id');
    ids.add(site.id);
    const params=site.params||{};
    const links={home:injectURL(site.links.home,params),targets:site.links.targets.map(url=>injectURL(url,params))};
    for(const url of [links.home,...links.targets]) {
      const parsed=new URL(url),local=fixture&&['127.0.0.1','localhost'].includes(parsed.hostname)&&parsed.protocol==='http:';
      if((!local&&parsed.protocol!=='https:') || parsed.username || parsed.password || parsed.hash
        || !site.origins.includes(parsed.origin)) throw new Error('url_outside_site_scope');
    }
    const operations=(site.operations||[]).map(step=>({...step,...(step.value!==undefined?{value:injectValue(step.value,params)}:{}),
      ...(step.expect?{expect:{...step.expect,value:step.expect.kind==='url'?injectURL(step.expect.value,params):step.expect.value}}:{})}));
    validateSteps(operations);
    if(site.recovery) {
      if(site.recovery.kind!=='simple_button' || typeof site.recovery.selector!=='string') throw new Error('only_configured_simple_button_recovery_supported');
      validateExpectation(site.recovery.success);
    }
    return {...site,links,operations,enabled:site.final_stage?includeGoogle:site.enabled!==false};
  });
  return {...manifest,options,recovery_plan:recoveryPlan,profiles,
    sites:sites.filter(s=>s.enabled).sort((a,b)=>Number(!!a.final_stage)-Number(!!b.final_stage))};
}
