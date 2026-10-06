import {httpClient, httpSite, openBrowser, browserSite} from './runner.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {decodeBody, classify} from './observations.mjs';
import {TOOL_NAMES, HTTP_USER_AGENT} from './runtime.mjs';
import { providerHints } from './providers.mjs';
import {performSteps, postcondition} from './operations.mjs';
import {SessionManager,waitForSessionCooldown} from './session-manager.mjs';

export {TOOL_NAMES, HTTP_USER_AGENT};
const httpTools = new Set(['wreq-js', 'impit']);

function canonicalTimezone(timezoneId) {
  if (typeof timezoneId !== 'string' || !timezoneId.trim()) throw new Error('invalid_timezone_id');
  try { return new Intl.DateTimeFormat('en', {timeZone:timezoneId}).resolvedOptions().timeZone; }
  catch { throw new Error('invalid_timezone_id'); }
}

/** Each adapter owns one session; all roles share its cookies and the site's budget. */
export class ToolAdapter {
  constructor({ tool, site, evidence, fixture = false, profile = {id:'baseline'}, options = {}, timezoneId, browserOpener = openBrowser, clientOpener = httpClient }) {
    if (!TOOL_NAMES.includes(tool)) throw new Error('Unknown tool');
    if (options.observeMs !== undefined && (!Number.isInteger(options.observeMs) || options.observeMs < 0 || options.observeMs > 6000))
      throw new Error('invalid_observe_ms');
    this.timezoneId = timezoneId;
    this.timezoneRequested = timezoneId !== undefined && timezoneId !== null;
    this.canonicalTimezoneId = this.timezoneRequested ? canonicalTimezone(timezoneId) : null;
    this.tool = tool; this.site = site; this.evidence = evidence; this.fixture = fixture;
    this.profile = profile; this.options = options;
    this.browserOpener = browserOpener; this.clientOpener = clientOpener;
    this.currentURL = null; this.sequence = 0;
    this.capabilities = { fetch: httpTools.has(tool), goto: !httpTools.has(tool),
      session: true, selectorOperations: !httpTools.has(tool) && tool !== '4play', challengeActions: !httpTools.has(tool) && tool !== '4play',
      headful: ['patchright','playwright-baseline','camoufox','4play'].includes(tool),
      extensions: ['patchright','playwright-baseline'].includes(tool) };
    if(tool==='4play') { this.capabilities.timezoneEmulation=false; this.capabilities.extensions=false;
      this.capabilities.sessionContextReplacement=false;
      if(profile.session_pool) throw new Error('unsupported_capability:fourplay_session_pool'); }
    if(profile.session_pool) {
      if(httpTools.has(tool) || profile.extensions?.length) throw new Error('session_pool_requires_browser_without_extensions');
      this.sessions=new SessionManager({evidence,key:`${tool}/${site.id}`,policy:profile.session_pool});
    }
  }
  async open() {
    let observedTimezone = null;
    let timezoneSupported = false;
    if (this.capabilities.fetch) this.client = await this.clientOpener(this.tool, HTTP_USER_AGENT, this.fixture);
    else {
      this.browser = await this.browserOpener(this.tool, {extensions:this.profile.extensions || [],
        lightpanda:this.profile.lightpanda || null,fixture:this.fixture,
        ...(this.timezoneRequested && this.tool!=='4play' ? {timezoneId:this.timezoneId} : {}),
        headful:this.evidence.policy === 'browser_observation' ? this.capabilities.headful : !!this.options.headful,
        profile:this.evidence.policy || 'diagnostic'});
      if(this.timezoneRequested && this.tool==='4play') {
        observedTimezone=null;timezoneSupported=false;this.capabilities.timezoneId=false;
      } else if(this.timezoneRequested) {
        try { observedTimezone = await this.browser.page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone); }
        catch {}
        timezoneSupported = observedTimezone === this.canonicalTimezoneId;
        this.capabilities.timezoneId = timezoneSupported;
      }
      if(this.evidence.policy !== 'browser_observation' && this.browser.runtime?.budgeted_navigation === false) {
        throw new Error('unsupported_capability:budgeted_navigation; redirect interception unavailable');
      }
      if(this.sessions && !this.browser.replaceContext) throw new Error('unsupported_capability:session_context_replacement');
      if(this.tool!=='4play') this.robots = await this.clientOpener(this.tool, this.browser.ua, this.fixture);
      this.sessions?.select();
    }
    this.evidence.result({client:this.tool,target:this.site.id,profile:this.profile.id,role:'adapter_setup',
      outcome:this.browser && this.timezoneRequested && !this.capabilities.timezoneId ? 'unsupported_capability' : 'adapter_ready',
      capabilities:this.capabilities,
      ...(this.browser ? {runtime:{...this.browser.runtime,...(this.timezoneRequested?{timezone:{requested:this.timezoneId,observed:observedTimezone,
        outcome:timezoneSupported?'supported':'unsupported_capability'}}:{})},user_agent:this.browser.ua,headless:this.browser.runtime?.headless??true,
        display:this.browser.runtime?.display??null,viewport:this.browser.runtime?.viewport??null} : {user_agent:HTTP_USER_AGENT})});
    return this;
  }
  assertScope(url) {
    const parsed = new URL(url);
    if (!this.site.origins.includes(parsed.origin) || parsed.username || parsed.password) throw new Error('outside_site_scope');
    return parsed.href;
  }
  async accessLink(url, options = {}) {
    url=this.assertScope(url);
    if(this.accessInFlight) throw new Error('session_adapter_concurrency_limit');
    this.accessInFlight=true;
    const stopped=(outcome,extra={})=>{
      const result={client:this.tool,target:this.site.id,profile:this.profile.id,url,outcome,...extra};
      this.evidence.result(result);return result;
    };
    const observe=async(result,visitedURL)=>{
      if(!this.sessions) return null;
      if(result.outcome==='content_observed') {
        try {this.sessions.snapshot(await this.browser.context.cookies());}
        catch(error) {this.sessions.pool.retire(this.sessions.session);result.session_cookie_error={message:String(error.message)};
          result.outcome='session_cookie_storage_error';this.evidence.flush();return {action:'stop',reason:result.outcome};}
      }
      return this.sessions.observe(result,visitedURL,this.lastObservation?.headers,
        {canRecoverChallenge:!!(this.options.recoverSimple && this.site.recovery)});
    };
    try {
      for(;;) {
        const deferred=await waitForSessionCooldown(this.evidence,url,this.sessions?.policy.max_auto_wait_ms||0);
        if(deferred) return stopped(deferred.outcome,deferred);
        if(this.sessions && !options.operation) {
          let selection;
          try {selection=this.sessions.select();}
          catch(error) {if(error.message==='session_rotation_limit') return stopped('session_rotation_limit');throw error;}
          if(selection.replace) {
            await this.browser.replaceContext(selection.session.cookies);
            this.currentURL=null;this.lastObservation=null;
            // A new session may require the site's entry-page cookies. This
            // bootstrap uses the same robots, evidence and budget path.
            if(url!==this.site.links.home) {
              const home=this.assertScope(this.site.links.home);
              const homeDelay=await waitForSessionCooldown(this.evidence,home,this.sessions.policy.max_auto_wait_ms);
              if(homeDelay) return stopped(homeDelay.outcome,homeDelay);
              const bootstrap=await this.accessLinkOnce(home,{role:'session_homepage'});
              const disposition=await observe(bootstrap,home);
              if(disposition.action!=='good') return bootstrap;
            }
          }
        }
        const result=await this.accessLinkOnce(url,options);
        const decision=await observe(result,url);
        // Native fill/click/recovery operations are never automatically replayed.
        if(!this.sessions || options.operation || !this.sessions.retryAvailable(url,decision,
          decision.action==='retire'&&url!==this.site.links.home?4:2)) return result;
        this.sessions.chargeRetry(url);
        const cooldown=await waitForSessionCooldown(this.evidence,url,this.sessions.policy.max_auto_wait_ms);
        if(cooldown) return stopped(cooldown.outcome,{...cooldown,previous_outcome:result.outcome});
      }
    } finally {this.accessInFlight=false;}
  }
  async accessLinkOnce(url, { role = 'target', method = 'auto', preserveReferrer = false, operation = null } = {}) {
    url = this.assertScope(url);
    const nativeMethod = this.capabilities.fetch ? 'fetch' : 'goto';
    if (method !== 'auto' && method !== nativeMethod) return { outcome: 'unsupported_capability', method, tool: this.tool };
    const index = ++this.sequence;
    this.evidence.stageContext = { role, navigation_method:operation ? 'native_operation' : nativeMethod,
      session_sequence: index, profile:this.profile.id,
      ...(this.sessions?{pool_session_id:this.sessions.session.id,pool_retries_used:this.sessions.shared.retries}: {}) };
    const target = { name: this.site.id, url, artifact_tag: `${this.site.id}-${this.profile.id}-${role}-${index}-${this.evidence.results.length}`,
      capture_screenshot:this.options.captureScreenshots !== false,
      primary_selector:this.site.selectors?.primary,
      ...(this.options.observeMs !== undefined ? {observe_ms:this.options.observeMs} : this.fixture ? {observe_ms:100} : {}),
      robots_redirect_origins:this.options.robotsRedirectOrigins ?? this.site.robots_redirect_origins ?? [],
      robots_unavailable_probe:!!(this.options.robotsUnavailableProbe ?? this.site.robots_unavailable_probe),
      ...(operation ? {operation, previousObservation:this.lastObservation} : {}),
      ...(preserveReferrer && this.currentURL ? {referer: this.currentURL} : {}) };
    try {
      if (this.capabilities.fetch) await httpSite(this.evidence, this.tool, target, this.client);
      else await browserSite(this.evidence, this.tool, target, {browser: this.browser, robots: this.robots});
    const result = this.evidence.results.at(-1);
    const record = this.evidence.records.find(r=>r.index===result.main_record);
    const hash = result.dom_sha256 || record?.body_sha256;
    if (hash) {
      const body = fs.readFileSync(path.join(this.evidence.directory, 'blobs', hash));
      result.provider_observation = providerHints({url:result.final_url || url,
        html:result.dom_sha256 ? body.toString('utf8') : decodeBody(body, record?.headers), headers:record?.headers});
      this.evidence.flush();
    }
    this.currentURL = result.final_url || url;
    this.lastObservation = {...result,headers:record?.headers};
    return result;
    } finally {this.evidence.stageContext = null;}
  }
  homepage(url) { return this.accessLink(url, {role:'homepage'}); }
  fetch(url) { return this.accessLink(url, {method:'fetch'}); }
  goto(url) { return this.accessLink(url, {method:'goto'}); }
  followLink(url) { return this.accessLink(url, {role:'target', preserveReferrer:true}); }
  returnHome(url) { return this.accessLink(url, {role:'return_home', preserveReferrer:true}); }
  async selectorProbe(steps = this.site.operations || []) {
    if(!this.options.selectors) return {outcome:'option_disabled'};
    if(!this.capabilities.selectorOperations) return {outcome:'unsupported_capability',capability:'selectorOperations'};
    if(!steps.length) return {outcome:'not_configured'};
    const result=await this.accessLink(this.currentURL,{role:'selector_probe',
      operation:browser=>performSteps(browser,steps,{humanlike:!!this.profile.humanlike,seed:(this.options.seed||1)+this.sequence})});
    return {outcome:result.operation?.outcome || result.outcome,observation:result};
  }
  async recoverSimpleChallenge(recovery = this.site.recovery) {
    if(!this.options.recoverSimple) return {outcome:'option_disabled'};
    if(!this.capabilities.challengeActions) return {outcome:'unsupported_capability',capability:'challengeActions'};
    if(!recovery || recovery.kind!=='simple_button') return {outcome:'not_configured',reason:'no_observed_simple_challenge_button'};
    const attempts=this.evidence.results.filter(r=>r.client===this.tool&&r.target===this.site.id&&r.role==='recover_simple_challenge').length;
    if(attempts>=(this.options.recoveryMaxAttempts??1)) return {outcome:'recovery_budget_exhausted'};
    if(await postcondition(this.browser.page,recovery.success).catch(()=>true)) return {outcome:'recovery_unverified',reason:'success predicate already satisfied or unreadable before action'};
    const result=await this.accessLink(this.currentURL,{role:'recover_simple_challenge',operation:browser=>
      performSteps(browser,[{op:'click',selector:recovery.selector,expect:recovery.success}],
        {humanlike:!!this.profile.humanlike,seed:(this.options.seed||1)+this.sequence})});
    if(result.operation?.outcome!=='operation_confirmed') return {outcome:result.operation?.outcome||result.outcome,observation:result};
    const html=result.dom_sha256 ? fs.readFileSync(path.join(this.evidence.directory,'blobs',result.dom_sha256),'utf8') : '';
    const domResult=classify(0,html,{});
    const clear=!['challenge_observed','access_denied_observed'].includes(domResult.outcome);
    const expected=await postcondition(this.browser.page,recovery.success).catch(()=>false);
    return {outcome:clear&&expected?'recovery_confirmed':'recovery_unverified',observation:result,
      confirmation:'configured DOM postcondition and absence of challenge/denial; original HTTP status retained'};
  }
  async extensionProbe() {
    if(!this.profile.extensions?.length) return {outcome:'option_disabled'};
    if(!this.capabilities.extensions) return {outcome:'unsupported_capability',capability:'extensions'};
    const expectation=this.profile.extension_probe;
    if(!expectation) return {outcome:'extension_loading_unverified',reason:'no_probe_configured'};
    const confirmed=await postcondition(this.browser.page,expectation).catch(()=>false);
    const result={client:this.tool,target:this.site.id,profile:this.profile.id,role:'extension_probe',
      outcome:confirmed?'extension_loaded':'extension_loading_failed',expectation};
    result.native_extension_ids=this.browser.loaded_extensions;
    this.evidence.result(result);return result;
  }
  async close() {
    try {await this.client?.close();}
    finally {try {await this.robots?.close();} finally {await this.browser?.close();}}
  }
}
