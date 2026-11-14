import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {selectLinks} from './runner.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(root, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const robotsParser = require('robots-parser');
const argv = process.argv.slice(2);
const option = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
const run = path.resolve(option('--run', 'lab-runs/structure-routing-20261115'));
const id = option('--id');
const budget = Number(option('--documents', '180'));
if (!id || !Number.isInteger(budget) || budget < 1 || budget > 200) throw new Error('Specify --id and --documents 1..200');
const target = fs.readFileSync(path.join(run, 'targets.jsonl'), 'utf8').trim().split(/\r?\n/).map(JSON.parse).find(x => x.id === id);
if (!target?.practice) throw new Error('Stage C requires an authorized practice site');
const directory = path.join(run, 'artifacts', id, 'C1', 'bulk');
if (fs.existsSync(path.join(directory, 'result.json'))) throw new Error('Existing continuous bulk run will not be restarted');
fs.mkdirSync(directory, {recursive: true});
const ruleFile = path.join(run, 'artifacts', id, 'C1', 'cold', 'rules.json');
const rules = JSON.parse(fs.readFileSync(ruleFile, 'utf8'));
const allowedHosts = new Set(target.allowed_hosts);
const safe = url => {
  try {
    const parsed = new URL(url);
    return ['http:', 'https:'].includes(parsed.protocol) && allowedHosts.has(parsed.hostname) &&
      !/\/(login|logout|register|cart|checkout|account|api|privacy|terms)(?:[/?#]|$)/i.test(parsed.pathname);
  } catch { return false; }
};
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const started = Date.now();
const ledger = {id, method: 'C1', stage: 'C', started_at: new Date().toISOString(),
  rules_sha256: crypto.createHash('sha256').update(fs.readFileSync(ruleFile)).digest('hex'),
  external_llm_calls: 0, local_inference_calls: 0, html_cache_reuse: false,
  document_attempts: 0, document_responses: 0, requests: 0, xhr_fetch: 0, operations: 0, bytes: 0,
  observations: [], milestones: [], stop_reason: null};
const persist = () => fs.writeFileSync(path.join(directory, 'ledger.json'), JSON.stringify(ledger, null, 2));
const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
const context = await browser.newContext({locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
const page = await context.newPage();
const client = await context.newCDPSession(page);
await client.send('Network.enable');
await client.send('Network.setCacheDisabled', {cacheDisabled: true});
client.on('Network.loadingFinished', value => { ledger.bytes += value.encodedDataLength || 0; });
page.on('request', request => {
  ledger.requests++;
  if (['xhr', 'fetch'].includes(request.resourceType())) ledger.xhr_fetch++;
});
page.on('response', response => { if (response.request().resourceType() === 'document') ledger.document_responses++; });
let previousDocument = 0;
await page.route('**/*', async route => {
  const request = route.request();
  if (request.isNavigationRequest()) {
    const mainNavigation = request.frame() === page.mainFrame();
    if ((mainNavigation && !safe(request.url())) || ledger.document_attempts >= budget) return route.abort('blockedbyclient');
    ledger.document_attempts++;
    persist();
    if (mainNavigation) {
      await wait(Math.max(0, 1000 - (Date.now() - previousDocument)));
      previousDocument = Date.now();
    }
  }
  return route.continue();
});
const robots = new Map();
async function permitted(url) {
  const origin = new URL(url).origin;
  if (!robots.has(origin)) {
    ledger.requests++;
    const response = await context.request.get(`${origin}/robots.txt`, {timeout: 15000});
    const text = await response.text();
    ledger.bytes += Buffer.byteLength(text);
    robots.set(origin, response.status() === 200 ? robotsParser(`${origin}/robots.txt`, text) : null);
  }
  return robots.get(origin)?.isAllowed(url, '*') !== false;
}
async function load(url, kind, referrer) {
  if (ledger.document_attempts >= budget) throw new Error('document_budget');
  if (!safe(url) || !await permitted(url)) throw new Error('access_condition');
  const before = Date.now();
  const response = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 30000, ...(referrer ? {referer: referrer} : {})});
  await wait(1000);
  const body = response ? await response.body() : Buffer.alloc(0);
  const dom = await page.evaluate(() => {
    const links = [...document.querySelectorAll('a[href]')].map(a => ({url: a.href,
      text: (a.innerText || a.textContent || '').trim(),
      parent: {tag: a.parentElement?.tagName.toLowerCase(), classes: [...(a.parentElement?.classList || [])]},
      region: a.closest('nav,header,footer') ? 'navigation' : a.closest('main,article,section') ? 'main' : 'body'}));
    return {html: document.documentElement.outerHTML, text: document.body?.innerText || '', title: document.title,
      url: location.href, links, heading: document.querySelector('h1,h2')?.textContent || ''};
  });
  const index = String(ledger.observations.length).padStart(3, '0');
  fs.writeFileSync(path.join(directory, `${index}.source.html`), body);
  fs.writeFileSync(path.join(directory, `${index}.dom.html`), dom.html);
  const blocked = /access denied|just a moment|verify.*human|robot check|attention required|メンテナンス/i.test(`${dom.title} ${dom.heading}`);
  const valid = response?.status() === 200 && !blocked && dom.text.trim().length > 100;
  const record = {index, url: dom.url, requested_url: url, kind, source_url: referrer, status: response?.status() ?? null,
    valid_html: valid, title: dom.title, heading: dom.heading, elapsed_ms: Date.now() - before,
    cumulative_ms: Date.now() - started, document_attempts: ledger.document_attempts,
    document_responses: ledger.document_responses, requests: ledger.requests, xhr_fetch: ledger.xhr_fetch,
    bytes: ledger.bytes, source_html: `${index}.source.html`, rendered_dom: `${index}.dom.html`};
  ledger.observations.push(record);
  persist();
  if (blocked || [401, 403, 429].includes(record.status)) throw new Error(`access_refused:${record.status}`);
  return dom;
}
try {
  const listQueue = [{url: target.start_url, from: null}];
  const seenLists = new Set();
  const candidates = new Map();
  while (listQueue.length && candidates.size < 100 && seenLists.size < 25) {
    const item = listQueue.shift();
    if (seenLists.has(item.url)) continue;
    seenLists.add(item.url);
    const dom = await load(item.url, 'structure_reuse', item.from);
    const chosen = selectLinks(dom, 'C1', rules, new Set(), allowedHosts).filter(a => safe(a.url));
    const next = chosen.filter(a => /^(next|older|次へ|次|›|»|→)/i.test(a.text.trim()));
    const itemLinks = chosen.filter(a => !next.includes(a) && !/^(home|all|categories|category|books|catalog|computers|laptops|tablets)$/i.test(a.text.trim()));
    for (const link of itemLinks) {
      if (link.url !== dom.url && !candidates.has(link.url)) candidates.set(link.url, {url: link.url, from: dom.url, text: link.text});
    }
    for (const link of next) if (!seenLists.has(link.url)) listQueue.push({url: link.url, from: dom.url});
    if (!next.length && candidates.size < 100) {
      for (const link of chosen.filter(a => !itemLinks.includes(a)).slice(0, 4))
        if (!seenLists.has(link.url)) listQueue.push({url: link.url, from: dom.url});
    }
    if (Date.now() - started > 600000) throw new Error('time_budget');
  }
  fs.writeFileSync(path.join(directory, 'discovered-candidates.json'), JSON.stringify([...candidates.values()], null, 2));
  let validCount = 0;
  for (const item of candidates.values()) {
    const dom = await load(item.url, 'rule_selected_target', item.from);
    const record = ledger.observations.at(-1);
    if (record.valid_html) validCount++;
    if ([10, 30, 100].includes(validCount)) ledger.milestones.push({valid_candidate_pages: validCount,
      cumulative_ms: Date.now() - started, document_attempts: ledger.document_attempts,
      requests: ledger.requests, xhr_fetch: ledger.xhr_fetch, bytes: ledger.bytes,
      independent_target_checkpoint: 'requires_separate_evaluation'});
    if (validCount >= 100) break;
    if (Date.now() - started > 600000) throw new Error('time_budget');
  }
  ledger.stop_reason = candidates.size >= 100 ? '100_candidate_pages_attempted' : 'discovery_exhausted';
} catch (error) {
  ledger.stop_reason = error.message;
} finally {
  ledger.elapsed_ms = Date.now() - started;
  ledger.finished_at = new Date().toISOString();
  await context.close();
  await browser.close();
  persist();
  fs.writeFileSync(path.join(directory, 'result.json'), JSON.stringify(ledger, null, 2));
  console.log(JSON.stringify({id, documents: ledger.document_attempts, stop_reason: ledger.stop_reason,
    target_attempts: ledger.observations.filter(x => x.kind === 'rule_selected_target').length}));
}
