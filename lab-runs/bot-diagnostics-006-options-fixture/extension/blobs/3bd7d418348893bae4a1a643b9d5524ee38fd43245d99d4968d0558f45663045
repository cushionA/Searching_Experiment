import { httpClient, httpSite, openBrowser, browserSite } from './runner.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { decodeBody, classify } from './runner.mjs';
import { providerHints } from './providers.mjs';
import {performSteps, postcondition} from './operations.mjs';

export const TOOL_NAMES = ['wreq-js', 'impit', 'patchright', 'rebrowser-lightpanda', 'playwright-baseline'];
const httpTools = new Set(['wreq-js', 'impit']);
export const HTTP_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 DiscoveryLab/0.1';

/** Each adapter owns one session; all roles share its cookies and the site's budget. */
export class ToolAdapter {
  constructor({ tool, site, evidence, fixture = false, profile = {id:'baseline'}, options = {} }) {
    if (!TOOL_NAMES.includes(tool)) throw new Error('Unknown tool');
    this.tool = tool; this.site = site; this.evidence = evidence; this.fixture = fixture;
    this.profile = profile; this.options = options;
    this.currentURL = null; this.sequence = 0;
    this.capabilities = { fetch: httpTools.has(tool), goto: !httpTools.has(tool),
      session: true, selectorOperations: !httpTools.has(tool), challengeActions: !httpTools.has(tool),
      extensions: ['patchright','playwright-baseline'].includes(tool) };
  }
  async open() {
    if (this.capabilities.fetch) this.client = await httpClient(this.tool, HTTP_USER_AGENT, this.fixture);
    else {
      this.browser = await openBrowser(this.tool, {extensions:this.profile.extensions || []});
      this.robots = await httpClient(this.tool, this.browser.ua, this.fixture);
    }
    this.evidence.result({client:this.tool,target:this.site.id,profile:this.profile.id,role:'adapter_setup',
      outcome:'adapter_ready',capabilities:this.capabilities,
      ...(this.browser ? {runtime:this.browser.runtime,user_agent:this.browser.ua} : {user_agent:HTTP_USER_AGENT})});
    return this;
  }
  assertScope(url) {
    const parsed = new URL(url);
    if (!this.site.origins.includes(parsed.origin) || parsed.username || parsed.password) throw new Error('outside_site_scope');
    return parsed.href;
  }
  async accessLink(url, { role = 'target', method = 'auto', preserveReferrer = false, operation = null } = {}) {
    url = this.assertScope(url);
    const nativeMethod = this.capabilities.fetch ? 'fetch' : 'goto';
    if (method !== 'auto' && method !== nativeMethod) return { outcome: 'unsupported_capability', method, tool: this.tool };
    const index = ++this.sequence;
    this.evidence.stageContext = { role, navigation_method:operation ? 'native_operation' : nativeMethod,
      session_sequence: index, profile:this.profile.id };
    const target = { name: this.site.id, url, artifact_tag: `${this.site.id}-${this.profile.id}-${role}-${index}-${this.evidence.results.length}`,
      primary_selector:this.site.selectors?.primary,
      ...(this.fixture ? {observe_ms:100} : {}),
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
    await this.client?.close(); await this.robots?.close(); await this.browser?.close();
  }
}
