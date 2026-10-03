import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {here, snapshotSources} from './runtime.mjs';
import {Evidence, verify, safeError} from './evidence.mjs';
import {ToolAdapter} from './adapters.mjs';
import {roles as defaultRoles, runScenario} from './scenario.mjs';
import {prepare, extensionFiles} from './manifest.mjs';

// Existing entry points remain available to callers after the responsibility split.
export {injectURL, prepare} from './manifest.mjs';
export {classifyGate, runScenario} from './scenario.mjs';

export function latestOutcomes(outcomes) {
  return [...new Map(outcomes.map(o=>[`${o.tool}/${o.site}/${o.profile}`,o])).values()];
}
export async function execute({manifest,directory,roles=defaultRoles,fixture=false,resume=false,roleSource=null}) {
  if(!fixture && !(process.env.HTTPS_PROXY||process.env.https_proxy)) throw new Error('environment_proxy_required');
  fs.mkdirSync(path.dirname(directory),{recursive:true});
  const lockPath=directory+'.lock',lock=fs.openSync(lockPath,'wx');
  fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString()}));
  const outcomes=[];
  try {
    const evidence=new Evidence(directory,resume,{policy:manifest.options?.executionPolicy??'browser_observation',
      authorization:manifest.policy_authorization??'Bot diagnostics uses browser observation; legacy limits apply only to explicit grounding workflows.'});
    if(resume) {
      if(JSON.stringify(JSON.parse(fs.readFileSync(path.join(directory,'scenario.json'))))!==JSON.stringify(manifest)) throw new Error('cannot_change_resumed_scenario');
      outcomes.push(...JSON.parse(fs.readFileSync(path.join(directory,'pipeline-results.json'))));
      for(const record of evidence.records) if(record.status==='interrupted') evidence.fail(record,new Error('previous_process_interrupted; full reserved body cap retained'));
    } else {evidence.save('scenario.json',manifest);evidence.flush();evidence.save('pipeline-results.json',outcomes);}
    const invocation={started_at:new Date().toISOString(),node:process.version,fixture,resume,
      headless:!manifest.options?.headful,display:manifest.options?.headful?(process.env.DISPLAY||process.env.WAYLAND_DISPLAY||null):null,
      viewport:manifest.options?.headful?{width:1280,height:720}:null,
      sources:snapshotSources(evidence,roleSource),extension_sources:{},
      execution_policy:evidence.policy,
      session_policy:resume?'fresh browser/client cookies; retained request/retry/rotation budgets and origin cooldowns':'one browser per tool/site/profile; optional bounded session/context replacement',
      tls_fingerprint_at_origin:'not measured; use inherited proxy and verified CA trust'};
    for(const profile of manifest.profiles) for(const dir of profile.extensions||[]) {
      invocation.extension_sources[dir]=Object.fromEntries(extensionFiles(dir).map(file=>[path.relative(dir,file),evidence.blob(fs.readFileSync(file))]));
    }
    const historyPath=path.join(directory,'framework-invocations.json');
    evidence.save('framework-invocations.json',[...(fs.existsSync(historyPath)?JSON.parse(fs.readFileSync(historyPath)):[]),invocation]);
    const completed=new Set(latestOutcomes(outcomes).filter(o=>!['adapter_error','environment_policy_blocked','preflight_or_measurement_failure','session_cooldown_deferred'].includes(o.state))
      .map(o=>`${o.tool}/${o.site}/${o.profile}`));
    for(const site of manifest.sites) for(const tool of manifest.tools) for(const profile of manifest.profiles) {
      const cell=`${tool}/${site.id}/${profile.id}`;
      if(resume&&completed.has(cell)) continue;
      const adapter=new ToolAdapter({tool,site,evidence,fixture,profile,options:manifest.options});
      if(manifest.options?.headfulExplicit && adapter.capabilities.goto && !adapter.capabilities.headful) {
        outcomes.push({site:site.id,tool,profile:profile.id,state:'unsupported_capability',capability:'headful'});
        evidence.save('pipeline-results.json',outcomes);continue;
      }
      if((profile.extensions?.length&&!adapter.capabilities.extensions) || (profile.humanlike&&!adapter.capabilities.selectorOperations)) {
        outcomes.push({site:site.id,tool,profile:profile.id,state:'unsupported_capability',
          capability:profile.extensions?.length?'extensions':'native_humanlike_input'});
        evidence.save('pipeline-results.json',outcomes);continue;
      }
      try {
        await adapter.open();
        outcomes.push(await runScenario({adapter,site,roles,recoveryPlan:manifest.recovery_plan,options:manifest.options,profile}));
      }catch(error){outcomes.push({site:site.id,tool,profile:profile.id,
        state:/disabled by the administrator|blocked by.*policy/i.test(error.message)?'environment_policy_blocked':'adapter_error',error:safeError(error)});}
      finally{try{await adapter.close();}catch(error){outcomes.at(-1).close_error=safeError(error);}}
      evidence.save('pipeline-results.json',outcomes);
    }
    const verification=verify(directory);evidence.save('verification.json',verification);
    const latest=latestOutcomes(outcomes);
    return {directory,outcomes:latest.map(({tool,site,profile,state})=>({tool,site,profile,state})),verification,
      has_runtime_failures:latest.some(o=>['adapter_error','environment_policy_blocked'].includes(o.state)||o.close_error)};
  }finally{fs.closeSync(lock);fs.unlinkSync(lockPath);}
}
export function parseCLI(args) {
  const mode=args.shift()||'plan',flags=args.filter(x=>x.startsWith('--')),positional=args.filter(x=>!x.startsWith('--'));
  if(!['plan','run','verify'].includes(mode) || positional.length>1 || flags.some(x=>!['--include-google','--selectors','--recover-simple','--humanlike','--extensions','--all-options','--resume','--headful','--grounding'].includes(x)
    && !/^--(?:sites|roles|extension)=.+/.test(x))) throw new Error('Usage: framework.mjs plan|run|verify [RUN_DIRECTORY] [--sites=FILE] [--roles=FILE] [--all-options] [--resume]');
  if(mode!=='plan'&&!positional[0]) throw new Error('run_directory_required');
  return {mode,directory:positional[0],flags};
}
async function main() {
  const {mode,directory,flags}=parseCLI(process.argv.slice(2));
  if(mode==='verify') {
    const result=verify(path.resolve(directory));console.log(JSON.stringify(result,null,2));
    if(!result.ok) process.exitCode=1;return;
  }
  const manifestPath=path.resolve(flags.find(x=>x.startsWith('--sites='))?.slice(8)||path.join(here,'sites.json'));
  const all=flags.includes('--all-options');
  const manifest=prepare(JSON.parse(fs.readFileSync(manifestPath)),{baseDirectory:path.dirname(manifestPath),headful:flags.includes('--headful'),
    ...(flags.includes('--grounding')?{executionPolicy:'grounding'}:{}),
    includeGoogle:all||flags.includes('--include-google'),selectors:all||flags.includes('--selectors'),
    recoverSimple:all||flags.includes('--recover-simple'),humanlike:all||flags.includes('--humanlike'),
    extensions:all||flags.includes('--extensions'),extensionPaths:flags.filter(x=>x.startsWith('--extension=')).map(x=>path.resolve(x.slice(12)))});
  if(mode==='plan'){console.log(JSON.stringify(manifest,null,2));return;}
  const roleSource=flags.find(x=>x.startsWith('--roles='))?.slice(8);
  let roles=defaultRoles;
  if(roleSource) {
    const module=await import(pathToFileURL(path.resolve(roleSource)));
    if(!module.roles || Object.keys(module.roles).some(role=>!Object.hasOwn(defaultRoles,role))) throw new Error('invalid_role_module');
    roles={...defaultRoles,...module.roles};
  }
  const result=await execute({manifest,directory:path.resolve(directory),roles,roleSource,
    resume:flags.includes('--resume')});
  console.log(JSON.stringify(result,null,2));
  if(!result.verification.ok || result.has_runtime_failures) process.exitCode=1;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  main().catch(error=>{console.error(JSON.stringify(safeError(error)));process.exitCode=1;});
}
