import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {Evidence, verify} from './runner.mjs';
import {ToolAdapter, TOOL_NAMES} from './adapters.mjs';
import {roles as defaultRoles} from './scenario.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
export function injectURL(template, params) {
  return template.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, key) => {
    if (!Object.hasOwn(params, key)) throw new Error(`missing_parameter:${key}`);
    return encodeURIComponent(String(params[key]));
  });
}
export function prepare(manifest, {includeGoogle = false, fixture = false} = {}) {
  if (manifest.schema !== 1 || !Array.isArray(manifest.sites) || !Array.isArray(manifest.tools)) throw new Error('invalid_manifest');
  if (manifest.tools.some(tool => !TOOL_NAMES.includes(tool))) throw new Error('unknown_tool');
  if (manifest.limits?.requests_per_tool_site !== 25 || manifest.limits?.body_bytes_per_tool_site !== 8388608) throw new Error('limits_must_match_executor');
  const ids = new Set();
  const sites = manifest.sites.map(site => {
    if (!/^[a-z][a-z0-9-]*$/.test(site.id) || ids.has(site.id)) throw new Error('invalid_or_duplicate_site_id');
    ids.add(site.id);
    const links = { home:injectURL(site.links.home, site.params || {}),
      targets:site.links.targets.map(url => injectURL(url, site.params || {})) };
    for (const url of [links.home, ...links.targets]) {
      const parsed = new URL(url);
      const local = fixture && ['127.0.0.1','localhost'].includes(parsed.hostname) && parsed.protocol === 'http:';
      if ((!local && parsed.protocol !== 'https:') || parsed.username || parsed.password || parsed.hash
        || !site.origins.includes(parsed.origin)) throw new Error('url_outside_site_scope');
    }
    return {...site, links, enabled:site.final_stage ? includeGoogle : site.enabled !== false};
  });
  return {...manifest, sites:sites.filter(s => s.enabled).sort((a,b) => Number(!!a.final_stage)-Number(!!b.final_stage))};
}
export function classifyGate(result) {
  if (result.outcome === 'content_observed') return 'continue';
  if (result.outcome === 'challenge_observed') return 'capture_challenge';
  if (['access_denied_observed','rate_limited_observed'].includes(result.outcome)) return 'site_rejected';
  if (/robots/.test(result.outcome)) return 'robots_stop';
  return 'preflight_or_measurement_failure';
}
/** Replace individual roles in a Skill-authored module; the loop and ledger stay shared. */
export async function runScenario({adapter, site, roles = defaultRoles, recoveryPlan = {}}) {
  const context = {adapter, site, links:site.links, selectors:site.selectors || {}, params:site.params || {}};
  const events = [];
  const invoke = async (role, extra = {}) => {
    const fn = roles[role] || defaultRoles[role];
    const result = await fn({...context, ...extra});
    events.push({role, result});
    return result;
  };
  let result = await invoke('homepage');
  const homeGate = classifyGate(result);
  if (homeGate !== 'continue') return {site:site.id, tool:adapter.tool, state:homeGate, events,
    recovery:homeGate === 'capture_challenge' ? await invoke('recoverSimpleChallenge') : null};
  for (const url of site.links.targets) {
    result = await invoke('target', {url});
    const gate = classifyGate(result);
    if (gate === 'capture_challenge') {
      const recovered = await invoke('recoverSimpleChallenge', {url, observation:result});
      if (recovered.outcome !== 'recovery_confirmed') return {site:site.id, tool:adapter.tool, state:gate, events};
      if (recoveryPlan.return_home_after_success) {
        const home = await invoke('returnHome');
        if (classifyGate(home) !== 'continue') return {site:site.id, tool:adapter.tool, state:'revisit_failed', events};
      }
      await new Promise(resolve => setTimeout(resolve, Math.min(60, recoveryPlan.wait_seconds || 4)*1000));
      const retried = await invoke('target', {url});
      if (classifyGate(retried) !== 'continue') return {site:site.id, tool:adapter.tool, state:'retry_failed', events};
    } else if (gate !== 'continue') return {site:site.id, tool:adapter.tool, state:gate, events};
  }
  return {site:site.id, tool:adapter.tool, state:'navigation_completed', events,
    optionalSelectorRole:'deferred_by_user', extensionRole:'deferred_by_user'};
}
export async function execute({manifest, directory, roles = defaultRoles, fixture = false}) {
  if (!fixture && !(process.env.HTTPS_PROXY || process.env.https_proxy)) throw new Error('environment_proxy_required');
  fs.mkdirSync(path.dirname(directory), {recursive:true});
  const lockPath = directory + '.lock';
  const lock = fs.openSync(lockPath, 'wx');
  let evidence;
  const outcomes = [];
  try {
    evidence = new Evidence(directory);
    evidence.save('scenario.json', manifest);
    evidence.save('framework-environment.json', {node:process.version, fixture,
      source_sha256:evidence.blob(fs.readFileSync(fileURLToPath(import.meta.url))),
      operations:'deferred by user; no selectors, challenge clicks, or extensions executed'});
    for (const site of manifest.sites) for (const tool of manifest.tools) {
      const adapter = new ToolAdapter({tool, site, evidence, fixture});
      try {
        await adapter.open();
        outcomes.push(await runScenario({adapter, site, roles, recoveryPlan:manifest.recovery_plan}));
      } catch (error) { outcomes.push({site:site.id, tool, state:'adapter_error', error_name:error.name}); }
      finally { await adapter.close(); }
      evidence.save('pipeline-results.json', outcomes);
    }
    evidence.save('verification.json', verify(directory));
    return {directory, outcomes:outcomes.map(({tool,site,state})=>({tool,site,state})), verification:verify(directory)};
  } finally { fs.closeSync(lock); fs.unlinkSync(lockPath); }
}
async function main() {
  const [mode = 'plan', directoryArg, ...flags] = process.argv.slice(2);
  if (!['plan','run','verify'].includes(mode)) throw new Error('Usage: framework.mjs plan|run|verify [RUN_DIRECTORY] [--include-google] [--sites=FILE] [--roles=FILE]');
  if (mode === 'verify') { console.log(JSON.stringify(verify(path.resolve(directoryArg)),null,2)); return; }
  const manifestPath = path.resolve(flags.find(x=>x.startsWith('--sites='))?.slice(8) || path.join(here,'sites.json'));
  const manifest = prepare(JSON.parse(fs.readFileSync(manifestPath)), {includeGoogle:flags.includes('--include-google')});
  if (mode === 'plan') { console.log(JSON.stringify(manifest,null,2)); return; }
  if (!directoryArg) throw new Error('run_directory_required');
  let roles = defaultRoles;
  const rolePath = flags.find(x=>x.startsWith('--roles='))?.slice(8);
  if (rolePath) {
    // Until the user resumes the operation section, custom navigation roles are supported.
    // Recovery/selector overrides are deliberately deferred.
    const module = await import(pathToFileURL(path.resolve(rolePath)));
    if (module.roles.recoverSimpleChallenge || module.roles.selectorProbe) throw new Error('operation_roles_deferred_by_user');
    roles = {...defaultRoles, ...module.roles};
  }
  console.log(JSON.stringify(await execute({manifest, directory:path.resolve(directoryArg), roles}),null,2));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error=>{console.error(JSON.stringify({error:error.message}));process.exitCode=1;});
}
