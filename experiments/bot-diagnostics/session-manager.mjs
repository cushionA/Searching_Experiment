import fs from 'node:fs';
import path from 'node:path';
import {LightweightSessionPool} from './session-pool.mjs';
import {normalizeSessionPolicy,retryAfterMs,sessionResponsePolicy} from './session-policy.mjs';
import {sleep,limits} from './runtime.mjs';

const STATE_FILE='session-policy-state.json';
function stateFor(evidence) {
  if(evidence.sessionPolicyState) return evidence.sessionPolicyState;
  const filename=path.join(evidence.directory,STATE_FILE);
  const saved=fs.existsSync(filename)?JSON.parse(fs.readFileSync(filename)):{schema:1,sites:{},cooldowns:{}};
  if(saved.schema!==1 || !saved.sites || !saved.cooldowns) throw new Error('invalid_session_policy_state');
  for(const site of Object.values(saved.sites)) {
    if(!Number.isSafeInteger(site.retries) || site.retries<0 || !Number.isSafeInteger(site.rotations) || site.rotations<0
      || !site.urls || Object.values(site.urls).some(n=>!Number.isSafeInteger(n)||n<0)) throw new Error('invalid_session_policy_state');
  }
  for(const cooldown of Object.values(saved.cooldowns)) {
    if(!Number.isFinite(cooldown.until) || cooldown.until<0) throw new Error('invalid_session_policy_state');
  }
  return evidence.sessionPolicyState=saved;
}

/** Shared by every profile; disabling the pool cannot bypass a recorded origin cooldown. */
export async function waitForSessionCooldown(evidence,url,maxAutoWait=0) {
  const cooldown=stateFor(evidence).cooldowns[new URL(url).origin];
  const delay=Math.max(0,(cooldown?.until||0)-Date.now());
  if(delay>maxAutoWait) return {outcome:'session_cooldown_deferred',retry_at:new Date(cooldown.until).toISOString(),
    wait_ms:delay,reason:cooldown.reason};
  if(delay) await sleep(delay);
  return null;
}

/** Owns only bounded metadata/cookies. The adapter owns one active browser context. */
export class SessionManager {
  constructor({evidence,key,policy}) {
    this.evidence=evidence;this.key=key;this.policy=normalizeSessionPolicy(policy);
    this.state=stateFor(evidence);
    this.shared=this.state.sites[key] ||= {retries:0,rotations:0,urls:{}};
    this.pool=new LightweightSessionPool({maxPoolSize:this.policy.max_pool_size,
      maxAgeMillis:this.policy.max_session_age_seconds*1000,maxUsageCount:this.policy.max_session_uses});
    this.session=null;this.activeId=null;
  }
  save(){this.evidence.save(STATE_FILE,this.state);}
  select() {
    this.session=this.pool.getSession();
    const replace=this.activeId!==null && this.activeId!==this.session.id;
    if(replace && this.shared.rotations>=this.policy.max_session_rotations) throw new Error('session_rotation_limit');
    if(replace) {this.shared.rotations++;this.save();}
    this.activeId=this.session.id;
    return {session:this.session,replace};
  }
  snapshot(cookies){this.pool.snapshotCookies(this.session,cookies);}
  observe(result,url,headers,{canRecoverChallenge=false}={}) {
    const decision=sessionResponsePolicy(result,headers,{canRecoverChallenge});
    const origin=new URL(url).origin;
    if(decision.action==='good') this.pool.markGood(this.session);
    else if(decision.action==='retire') {this.session.uses++;this.pool.retire(this.session);}
    else if(decision.action==='stop' && ['execution_error','navigation_error','navigation_unverified'].includes(decision.reason)) this.pool.markBad(this.session);
    else this.session.uses++;
    // No session retirement for origin-wide rate limits or server overload.
    const info={backend:'lightweight',session_id:this.session.id,action:decision.action,reason:decision.reason,
      uses:this.session.uses,retired:this.session.retired,pool_size:this.pool.state().length,
      cookie_count:this.session.cookies.length,retries_used:this.shared.retries,rotations_used:this.shared.rotations};
    if(['backoff','retire'].includes(decision.action)) {
      const retryHeader=Object.entries(headers||{}).find(([key])=>key.toLowerCase()==='retry-after')?.[1];
      const serverDelay=retryAfterMs(retryHeader);
      const backoff=Math.min(this.policy.max_backoff_ms,this.policy.base_delay_ms*2**this.shared.retries);
      const delay=Math.max(backoff,serverDelay??0);
      // Keep a representable timestamp for arbitrarily large numeric Retry-After.
      const until=Math.min(8.64e15,Date.now()+delay);
      const previous=this.state.cooldowns[origin]?.until||0;
      this.state.cooldowns[origin]={until:Math.max(previous,until),reason:decision.reason};
      info.retry_at=new Date(this.state.cooldowns[origin].until).toISOString();
      info.backoff_ms=delay;
      this.save();
    }
    result.session_pool=info;
    this.evidence.flush();
    return decision;
  }
  retryAvailable(url,decision,minimumRequests=2) {
    if(!['backoff','retire'].includes(decision.action)) return false;
    if(this.shared.retries>=this.policy.max_retries_per_site || (this.shared.urls[url]||0)>=this.policy.max_retries_per_url) return false;
    if(decision.action==='retire' && this.shared.rotations>=this.policy.max_session_rotations) return false;
    const budget=this.evidence.budgets[this.key];
    return !budget || (budget.requests+minimumRequests<=limits.requests_per_client_target && budget.bytes_charged<limits.bytes_per_client_target);
  }
  chargeRetry(url) {
    // Persist before acquiring/replacing a context. An interrupted retry stays spent.
    this.shared.retries++;this.shared.urls[url]=(this.shared.urls[url]||0)+1;this.save();
  }
}
