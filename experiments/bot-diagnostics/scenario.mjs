/** Roles receive injected URLs/selectors instead of referencing a particular driver. */
const standardRoles = {
  async homepage({ adapter, links }) { return adapter.homepage(links.home); },
  async target({ adapter, url }) { return adapter.followLink(url); },
  async recoverSimpleChallenge({ adapter, site }) { return adapter.recoverSimpleChallenge(site.recovery); },
  async returnHome({ adapter, links }) { return adapter.returnHome(links.home); },
  async selectorProbe({ adapter, site }) { return adapter.selectorProbe(site.operations); },
  async extensionProbe({ adapter }) { return adapter.extensionProbe(); },
};

export {standardRoles as roles};
export const DEFAULT_RECOVERY_PLAN = Object.freeze({
  max_attempts: 1, return_home_after_success: true, wait_seconds: 4, retry_original_target: true,
});

export function classifyGate(result) {
  if(result.outcome==='session_cooldown_deferred') return 'session_cooldown_deferred';
  if(result.outcome==='content_observed') return 'continue';
  if(result.outcome==='challenge_observed') return 'capture_challenge';
  if(['access_denied_observed','rate_limited_observed'].includes(result.outcome)) return 'site_rejected';
  if(/robots/.test(result.outcome)) return 'robots_stop';
  return 'preflight_or_measurement_failure';
}
/** Small site role modules consume injected links/selectors; budgets and sessions remain shared. */
export async function runScenario({adapter,site,roles=standardRoles,recoveryPlan={},options={},profile={id:'baseline'}}) {
  const context={adapter,site,links:site.links,selectors:site.selectors||{},params:site.params||{}};
  const events=[];
  const invoke=async(role,extra={})=>{
    const result=await (roles[role]||standardRoles[role])({...context,...extra});
    events.push({role,result});return result;
  };
  const finish=state=>({site:site.id,tool:adapter.tool,profile:profile.id,state,events});
  const recovery={...DEFAULT_RECOVERY_PLAN,...recoveryPlan};
  let attempts=0;
  const gate=async(result,{url,homepage=false}={})=>{
    const state=classifyGate(result);
    if(state!=='capture_challenge') return state;
    if(attempts>=recovery.max_attempts) return 'recovery_budget_exhausted';
    attempts++;
    const recovered=await invoke('recoverSimpleChallenge',{url,observation:result});
    if(recovered.outcome!=='recovery_confirmed') return state;
    if(recovery.return_home_after_success && classifyGate(await invoke('returnHome'))!=='continue') return 'revisit_failed';
    await new Promise(resolve=>setTimeout(resolve,Math.max(0,Math.min(60,recovery.wait_seconds))*1000));
    if(!recovery.retry_original_target) return 'recovery_confirmed_without_target_retry';
    return classifyGate(await invoke(homepage?'homepage':'target',{url,retry:true}))==='continue'?'continue':'retry_failed';
  };
  let result=await invoke('homepage');
  if(profile.extensions?.length) {
    const probe=await invoke('extensionProbe');
    if(probe.outcome==='extension_loading_failed') return finish('extension_loading_failed');
  }
  let state=await gate(result,{url:site.links.home,homepage:true});
  if(state!=='continue') return finish(state);
  if(options.selectors) {
    const probe=await invoke('selectorProbe');
    if(probe.observation && classifyGate(probe.observation)!=='continue') {
      state=await gate(probe.observation,{url:site.links.home,homepage:true});
      if(state!=='continue') return finish(state);
    }
  }
  for(const url of site.links.targets) {
    result=await invoke('target',{url});state=await gate(result,{url});
    if(state!=='continue') return finish(state);
  }
  return finish('navigation_completed');
}
