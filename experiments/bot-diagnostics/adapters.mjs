import { httpClient, httpSite, openBrowser, browserSite } from './runner.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { decodeBody } from './runner.mjs';
import { providerHints } from './providers.mjs';

export const TOOL_NAMES = ['wreq-js', 'impit', 'patchright', 'rebrowser-lightpanda', 'playwright-baseline'];
const httpTools = new Set(['wreq-js', 'impit']);
export const HTTP_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 DiscoveryLab/0.1';

/** Each adapter owns one session; all roles share its cookies and the site's budget. */
export class ToolAdapter {
  constructor({ tool, site, evidence, fixture = false }) {
    if (!TOOL_NAMES.includes(tool)) throw new Error('Unknown tool');
    this.tool = tool; this.site = site; this.evidence = evidence; this.fixture = fixture;
    this.currentURL = null; this.sequence = 0;
    this.capabilities = { fetch: httpTools.has(tool), goto: !httpTools.has(tool),
      session: true, selectorOperations: false, challengeActions: false, extensions: false };
  }
  async open() {
    if (this.capabilities.fetch) this.client = await httpClient(this.tool, HTTP_USER_AGENT, this.fixture);
    else {
      this.browser = await openBrowser(this.tool);
      this.robots = await httpClient(this.tool, this.browser.ua, this.fixture);
    }
    return this;
  }
  assertScope(url) {
    const parsed = new URL(url);
    if (!this.site.origins.includes(parsed.origin) || parsed.username || parsed.password) throw new Error('outside_site_scope');
    return parsed.href;
  }
  async accessLink(url, { role = 'target', method = 'auto', preserveReferrer = false } = {}) {
    url = this.assertScope(url);
    const nativeMethod = this.capabilities.fetch ? 'fetch' : 'goto';
    if (method !== 'auto' && method !== nativeMethod) return { outcome: 'unsupported_capability', method, tool: this.tool };
    const index = ++this.sequence;
    this.evidence.stageContext = { role, navigation_method: nativeMethod, session_sequence: index };
    const target = { name: this.site.id, url, artifact_tag: `${this.site.id}-${role}-${index}`,
      primary_selector:this.site.selectors?.primary,
      ...(preserveReferrer && this.currentURL ? {referer: this.currentURL} : {}) };
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
    this.evidence.stageContext = null;
    return result;
  }
  homepage(url) { return this.accessLink(url, {role:'homepage'}); }
  fetch(url) { return this.accessLink(url, {method:'fetch'}); }
  goto(url) { return this.accessLink(url, {method:'goto'}); }
  followLink(url) { return this.accessLink(url, {role:'target', preserveReferrer:true}); }
  returnHome(url) { return this.accessLink(url, {role:'return_home', preserveReferrer:true}); }
  selectorProbe() { return {outcome:'deferred_by_user', role:'selector_probe'}; }
  recoverSimpleChallenge() { return {outcome:'deferred_by_user', role:'recover_simple_challenge'}; }
  async close() {
    await this.client?.close(); await this.robots?.close(); await this.browser?.close();
  }
}
