import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const deps = path.resolve(process.env.BOT_DIAGNOSTICS_DEPS || path.join(repo, '.deps/bot-diagnostics'));
const require = createRequire(path.join(deps, 'package.json'));
const config = JSON.parse(fs.readFileSync(path.join(here, 'config.json')));
const L = config.limits;
const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
const local = url => ['127.0.0.1', 'localhost'].includes(new URL(url).hostname);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');

export function classify(status, html, headers = {}) {
  const text = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  const title = (html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || '').trim();
  const patterns = [
    ['human_verification', /verify (?:that )?you (?:are|['’]re) human|verify that you(?:'re| are) not a robot|ロボットではないことを確認|私はロボットではありません|robot check|enter the characters you see|人間であること|just a moment|checking your browser|additional verification required/i],
    ['access_denied_message', /access denied|request (?:was )?blocked|automated access|sorry[^.]{0,80}robot|unusual traffic|不正なアクセス|アクセスが制限|アクセスを拒否|アクセスが集中/i],
  ];
  const signals = [];
  if (/AwsWafIntegration/.test(html) && /token\.awswaf\.com\//.test(html)) {
    signals.push({ name: 'aws_waf_challenge', evidence: 'AWS WAF token challenge script and AwsWafIntegration in response HTML' });
  }
  for (const [name, re] of patterns) {
    const match = text.match(re);
    if (match) signals.push({ name, evidence: text.slice(Math.max(0, match.index - 80), match.index + 180) });
  }
  if (headers['cf-mitigated'] === 'challenge') signals.push({ name: 'cf_mitigated', evidence: 'challenge' });
  let outcome = 'response_observed';
  if (signals.some(x => ['human_verification', 'cf_mitigated', 'aws_waf_challenge'].includes(x.name))) outcome = 'challenge_observed';
  else if (status === 403 || signals.some(x => x.name === 'access_denied_message')) outcome = 'access_denied_observed';
  else if (status === 429) outcome = 'rate_limited_observed';
  else if (status >= 500) outcome = 'server_error_observed';
  else if (status >= 300 && status < 400) outcome = 'redirect_observed';
  else if (status >= 200 && status < 300) outcome = 'content_observed';
  return { outcome, title, visible_text_prefix: text.slice(0, 1500), signals,
    site_block_reason: null, reason_certainty: 'response evidence only; site policy/logs unavailable' };
}

export function decodeBody(body, headers = {}) {
  const meta = body.subarray(0, 4096).toString('ascii').match(/charset\s*=\s*["'\s]*([\w-]+)/i);
  const charset = headers['content-type']?.match(/charset\s*=\s*["']?([\w-]+)/i)?.[1] || meta?.[1] || 'utf-8';
  try { return new TextDecoder(charset).decode(body); }
  catch { return body.toString('utf8'); }
}

function safeError(error) {
  let message = String(error?.message || error);
  for (const [name, value] of Object.entries(process.env)) {
    if (value && /PASSWORD|TOKEN|SECRET|API_KEY/i.test(name)) message = message.split(value).join('[redacted]');
  }
  message = message.replace(/(https?:\/\/)[^\s/@]+(?::[^\s/@]*)?@/g, '$1[redacted]@');
  message = message.replace(/(?:__cf_bm|_cfuvid|(?:[\w-]*token[\w-]*))=[^\s;"\n]+/gi, '[redacted-cookie]');
  return { name: error?.name || 'Error', message: message.slice(0, 1800) };
}
function keptHeaders(headers) {
  const retained = ['content-type', 'content-length', 'content-encoding', 'server', 'location', 'cf-ray',
    'cf-mitigated', 'retry-after', 'via', 'x-cache', 'x-amz-cf-id', 'x-amz-cf-pop', 'x-request-id',
    'x-denied-reason', 'x-blocked-by', 'content-security-policy', 'x-cdn', 'x-sucuri-id',
    'x-sucuri-block', 'x-akamai-transformed', 'x-amzn-waf-action', 'akamai-grn'];
  return Object.fromEntries(Object.entries(headers).filter(([key]) => retained.includes(key.toLowerCase())));
}

export class Evidence {
  constructor(directory, resume = false) {
    if (resume) {
      this.directory = directory;
      const ledger = JSON.parse(fs.readFileSync(path.join(directory, 'ledger.json')));
      const saved = JSON.parse(fs.readFileSync(path.join(directory, 'config.json')));
      if (JSON.stringify(saved) !== JSON.stringify(config)) throw new Error('Cannot change an existing run configuration');
      this.budgets = ledger.budgets;
      this.records = ledger.records;
      this.results = JSON.parse(fs.readFileSync(path.join(directory, 'results.json')));
      this.last = {};
      for (const r of this.records) this.last[new URL(r.url).origin] = Math.max(this.last[new URL(r.url).origin] || 0, Date.parse(r.finished_at || r.started_at));
      this.delayChain = Promise.resolve();
      return;
    }
    if (fs.existsSync(directory)) throw new Error('Existing run cannot be overwritten');
    this.directory = directory;
    fs.mkdirSync(path.join(directory, 'blobs'), { recursive: true });
    this.budgets = {};
    this.records = [];
    this.results = [];
    this.last = {};
    this.delayChain = Promise.resolve();
    this.save('config.json', config);
  }
  save(name, value) {
    const destination = path.join(this.directory, name);
    fs.writeFileSync(destination + '.tmp', JSON.stringify(value, null, 2) + '\n');
    fs.renameSync(destination + '.tmp', destination);
  }
  blob(body) {
    const hash = sha(body);
    fs.writeFileSync(path.join(this.directory, 'blobs', hash), body);
    return hash;
  }
  reserve(key, url, kind) {
    const b = this.budgets[key] ||= { requests: 0, bytes_charged: 0 };
    if (b.requests >= L.requests_per_client_target) throw new Error('request_budget');
    const cap = Math.min(L.bytes_per_response, L.bytes_per_client_target - b.bytes_charged);
    if (cap <= 0) throw new Error('byte_budget');
    b.requests++;
    b.bytes_charged += cap;
    const record = { index: this.records.length + 1, key, url, kind, cap,
      started_at: new Date().toISOString(), status: 'interrupted', bytes_charged: cap,
      route: local(url) ? 'local_fixture' : 'environment_proxy', ...(this.stageContext || {}) };
    this.records.push(record);
    this.flush();
    return record;
  }
  flush() {
    this.save('ledger.json', { budgets: this.budgets, records: this.records });
    this.save('results.json', this.results);
  }
  async wait(url, extraDelay = 0) {
    const run = this.delayChain.then(async () => {
      const origin = new URL(url).origin;
      const delay = local(url) ? 0 : Math.max(L.delay_seconds, extraDelay) * 1000;
      await sleep(Math.max(0, (this.last[origin] || 0) + delay - Date.now()));
      this.last[origin] = Date.now();
    });
    this.delayChain = run.catch(() => {});
    await run;
  }
  finish(record, status, headers, body, truncated = false) {
    body = Buffer.from(body);
    if (body.length > record.cap) { body = body.subarray(0, record.cap); truncated = true; }
    this.budgets[record.key].bytes_charged -= record.cap - body.length;
    Object.assign(record, { status, headers: keptHeaders(headers), bytes_charged: body.length,
      body_sha256: this.blob(body), truncated, finished_at: new Date().toISOString() });
    this.flush();
  }
  fail(record, error) {
    Object.assign(record, { status: 'error', error: safeError(error), finished_at: new Date().toISOString() });
    this.flush();
  }
  result(value) { this.results.push({ ...value, ...(this.stageContext || {}) }); this.flush(); }
}

async function cappedBody(response, cap) {
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0, truncated = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const n = Math.min(value.length, cap - length);
      chunks.push(Buffer.from(value.subarray(0, n)));
      length += n;
      if (value.length > n || length === cap) { truncated = true; await reader.cancel(); break; }
    }
  } finally { reader.releaseLock(); }
  return { body: Buffer.concat(chunks), truncated };
}

export async function httpClient(name, ua, isLocal = false) {
  if (name === 'wreq-js') {
    const { createSession } = require('wreq-js');
    const session = await createSession({ browser: 'chrome_149', os: 'linux', proxy: isLocal ? undefined : proxy,
      timeout: L.navigation_timeout_ms, headers: { 'user-agent': ua }, trustStore: 'combined' });
    return { get: (url, headers = {}) => session.fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(L.navigation_timeout_ms) }),
      close: () => session.close() };
  }
  if (name === 'impit') {
    const { Impit } = require('impit');
    const { CookieJar } = require('tough-cookie');
    const client = new Impit({ browser: 'chrome', proxyUrl: isLocal ? undefined : proxy, ignoreTlsErrors: false,
      timeout: L.navigation_timeout_ms, followRedirects: false, cookieJar: new CookieJar(), headers: { 'user-agent': ua } });
    return { get: (url, headers = {}) => client.fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(L.navigation_timeout_ms) }), close: () => {} };
  }
  // Browser API requests fetch robots only. Main-document traffic uses the browser's network stack.
  const { request } = require('playwright');
  const context = await request.newContext({ userAgent: ua, proxy: isLocal ? undefined : { server: proxy }, ignoreHTTPSErrors: false,
    timeout: L.navigation_timeout_ms });
  return { get: async (url, headers = {}) => {
    const response = await context.get(url, { headers, maxRedirects: 0 });
    const body = await response.body();
    return { status: response.status(), headers: new Headers(keptHeaders(response.headers())),
      body: new ReadableStream({ start(controller) { controller.enqueue(body); controller.close(); } }) };
  }, close: () => context.dispose() };
}

async function get(evidence, key, client, url, kind, delay = 0, headers = {}) {
  const record = evidence.reserve(key, url, kind);
  try {
    await evidence.wait(url, delay);
    const response = await client.get(url, headers);
    const { body, truncated } = await cappedBody(response, record.cap);
    evidence.finish(record, response.status, Object.fromEntries(response.headers), body, truncated);
    return { record, body };
  } catch (error) { evidence.fail(record, error); throw error; }
}

async function robots(evidence, key, client, target, ua) {
  const url = new URL('/robots.txt', target.url).href;
  const { record, body } = await get(evidence, key, client, url, 'robots');
  if (![200, 404, 410].includes(record.status) || record.truncated) return { outcome: 'robots_unavailable', robots_record: record.index };
  const rules = record.status === 200 ? body.toString('utf8') : 'User-agent: *\nAllow: /';
  if (record.status === 200 && /^\s*(?:<!doctype|<html)/i.test(rules)) return { outcome: 'robots_invalid_html', robots_record: record.index };
  const parser = require('robots-parser')(url, rules);
  const allowed = parser.isAllowed(target.url, ua);
  const delay = parser.getCrawlDelay(ua) || 0;
  if (allowed === false) return { outcome: 'robots_denied', robots_record: record.index };
  if (delay > 60) return { outcome: 'robots_delay_requires_review', robots_record: record.index };
  return { allowed: true, parser, delay, robots_record: record.index };
}

async function startFixture() {
  const botd = fs.readFileSync(path.join(deps, 'node_modules/@fingerprintjs/botd/dist/botd.esm.js'));
  const botdPage = `<!doctype html><meta charset="utf-8"><title>BotD 2.0.0 fixture</title>
  <textarea id="result">pending</textarea><script type="module">
  import {load} from '/botd.esm.js';
  try { const agent=await load({monitoring:false}); const result=agent.detect();
    document.querySelector('#result').value=JSON.stringify({result,components:agent.getComponents(),detections:agent.getDetections()});
  } catch(e) { document.querySelector('#result').value=JSON.stringify({error:{name:e.name,message:e.message}}); }
  </script>`;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname === '/botd') { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(botdPage); return; }
    if (url.pathname === '/botd.esm.js') { response.setHeader('Content-Type', 'application/javascript'); response.end(botd); return; }
    if (url.pathname === '/robots.txt') { response.end('User-agent: *\nAllow: /'); return; }
    const name = { '/rebrowser': 'index.html', '/index.js': 'index.js', '/index.css': 'index.css' }[url.pathname];
    if (!name) { response.writeHead(404); response.end(); return; }
    response.setHeader('Content-Type', name.endsWith('.js') ? 'application/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html; charset=utf-8');
    response.end(fs.readFileSync(path.join(deps, 'detector', name)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { origin: `http://127.0.0.1:${server.address().port}`, close: () => new Promise(resolve => server.close(resolve)) };
}

export async function openBrowser(name) {
  if (name !== 'rebrowser-lightpanda') {
    const { chromium } = require(name === 'patchright' ? 'patchright' : 'playwright');
    const browser = await chromium.launch({ executablePath: process.env.BOT_DIAGNOSTICS_CHROMIUM || '/usr/bin/chromium',
      headless: true, chromiumSandbox: false, proxy: { server: proxy, bypass: '127.0.0.1,localhost' },
      env: { ...process.env, XDG_DATA_HOME: path.join(deps, 'chromium-data') } });
    const probe = await browser.newContext();
    const page = await probe.newPage();
    const ua = await page.evaluate(() => navigator.userAgent) + ' ' + config.identification;
    await probe.close();
    const context = await browser.newContext({ userAgent: ua, serviceWorkers: 'block', ignoreHTTPSErrors: false });
    return { browser, context, page: await context.newPage(), ua, kind: 'playwright', close: () => browser.close() };
  }
  const log = [];
  const server = http.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const args = ['serve', '--host', '127.0.0.1', '--port', String(port), '--http-proxy', proxy,
    '--user-agent-suffix', config.identification, '--http-max-response-size', String(L.bytes_per_response),
    '--http-timeout', String(L.navigation_timeout_ms), '--http-max-concurrent', '1', '--log-level', 'warn'];
  const cert = process.env.SSL_CERT_FILE || '/etc/ssl/certs/ca-certificates.crt';
  args.push('--ca-cert', cert);
  const child = spawn(path.join(deps, 'lightpanda'), args, { env: { ...process.env, XDG_DATA_HOME: path.join(deps, 'lightpanda-data'), LIGHTPANDA_DISABLE_TELEMETRY: 'true', LIGHTPANDA_DISABLE_CORE_DUMP: 'true' } });
  child.stderr.on('data', chunk => log.push(chunk.toString()));
  let browser;
  try {
    const puppeteer = require('rebrowser-puppeteer-core');
    for (let i = 0; i < 30; i++) {
      if (child.exitCode !== null) throw new Error('Lightpanda exited: ' + log.join('').slice(-1200));
      try { browser = await puppeteer.connect({ browserWSEndpoint: `ws://127.0.0.1:${port}`, defaultViewport: null, protocolTimeout: 12000 }); break; }
      catch (error) { if (i === 29) throw error; await sleep(100); }
    }
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    const ua = await page.evaluate(() => navigator.userAgent);
    return { browser, context, page, ua, kind: 'puppeteer', log,
      close: async () => { await browser.disconnect(); child.kill('SIGTERM'); } };
  } catch (error) { browser?.disconnect(); child.kill('SIGTERM'); throw error; }
}

async function detectorRun(evidence, name, origin) {
  const key = `${name}/detectors`;
  if (['wreq-js', 'impit'].includes(name)) {
    const client = await httpClient(name, config.identification, true);
    try {
      for (const detector of ['rebrowser', 'botd']) {
        const { record } = await get(evidence, key, client, origin + '/' + detector, 'detector_html');
        evidence.result({ client: name, detector, outcome: 'not_applicable_no_javascript', html_status: record.status,
          note: 'HTML fetch is not a detector pass. No navigator/DOM/JavaScript runtime.' });
      }
    } finally { await client.close(); }
    return;
  }
  let b;
  try {
    b = await openBrowser(name);
    for (const detector of ['rebrowser', 'botd']) {
      const result = { client: name, detector, user_agent: b.ua, trigger_errors: [], page_errors: [] };
      const page = b.page;
      const onerror = error => result.page_errors.push(safeError(error));
      page.on('pageerror', onerror);
      try {
      // Detector fixture has no third-party assets; preserve source CSP and block external traffic.
      if (b.kind === 'playwright') await b.context.route('**/*', route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        return route.abort('blockedbyclient');
      });
      else {
        await page.setRequestInterception(true);
        page.removeAllListeners('request');
        page.on('request', request => new URL(request.url()).origin === origin ? request.continue() : request.abort('blockedbyclient'));
      }
      await page.goto(origin + '/' + detector, { waitUntil: 'load', timeout: L.navigation_timeout_ms });
      await sleep(500);
      if (detector === 'rebrowser' && b.kind === 'puppeteer') {
        result.startup = await page.mainFrame().mainRealm().evaluate(() => ({
          readyState: document.readyState, initTests: typeof initTests, onload: typeof window.onload,
          detections: typeof window.detections, detectionsLength: window.detections?.length,
          insertRow: typeof document.querySelector('tbody')?.insertRow,
          userAgentData: typeof navigator.userAgentData,
        }));
      }
      if (detector === 'rebrowser') {
        for (const [trigger, action] of [
          ['dummyFn', () => page.evaluate(() => window.dummyFn())],
          ['exposeFunctionLeak', () => page.exposeFunction('exposedFn', () => true)],
          ['sourceUrlLeak', () => page.evaluate(() => document.getElementById('detections-json'))],
          ['mainWorldExecution', () => b.kind === 'puppeteer'
            ? page.mainFrame().isolatedRealm().evaluate(() => document.getElementsByClassName('div'))
            : page.evaluate(() => document.getElementsByClassName('div'))],
        ]) {
          try { await action(); } catch (error) { result.trigger_errors.push({ trigger, ...safeError(error) }); }
        }
        await sleep(L.observe_ms);
        const raw = await page.evaluate(() => document.querySelector('#detections-json').value);
        result.raw_output = raw;
        result.detections = JSON.parse(raw);
        result.outcome = 'detector_completed';
        result.red_flags = result.detections.filter(x => x.rating === 1).map(x => x.type);
        result.unassessed = result.detections.filter(x => x.rating >= 0 && x.rating < 1).map(x => x.type);
        if (result.page_errors.length || result.trigger_errors.length) result.outcome = 'detector_partial';
      } else {
        await sleep(1200);
        const raw = await page.evaluate(() => document.querySelector('#result').value);
        result.raw_output = raw;
        result.botd = JSON.parse(raw);
        result.outcome = result.botd.error ? 'detector_error' : 'detector_completed';
      }
      } catch (error) {
        result.outcome = 'detector_error';
        result.error = safeError(error);
      }
      try { result.dom_sha256 = evidence.blob(Buffer.from(await page.content())); }
      catch (error) { result.dom_error = safeError(error); }
      if (b.kind === 'playwright') {
        await page.screenshot({ path: path.join(evidence.directory, `${name}-${detector}.png`), fullPage: true });
        await b.context.unroute('**/*');
      }
      page.off('pageerror', onerror);
      if (b.log) result.log = b.log.join('').slice(-3000);
      evidence.result(result);
      console.log(JSON.stringify({ client: name, detector, outcome: result.outcome, red_flags: result.red_flags, botd: result.botd?.result }));
    }
  } catch (error) {
    evidence.result({ client: name, detector: 'browser_setup_or_execution', outcome: 'execution_error', error: safeError(error), log: b?.log?.join('').slice(-2000) });
    console.log(JSON.stringify({ client: name, outcome: 'execution_error', error: safeError(error) }));
  } finally { await b?.close(); }
}

export async function httpSite(evidence, name, target, sharedClient = null) {
  const ua = `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 ${config.identification}`;
  const client = sharedClient || await httpClient(name, ua);
  const key = `${name}/${target.name}`;
  try {
    const rules = await robots(evidence, key, client, target, ua);
    if (!rules.allowed) { evidence.result({ client: name, target: target.name, url: target.url, ...rules }); return; }
    let url = target.url;
    for (let hop = 0; hop <= L.max_redirects; hop++) {
      if (rules.parser.isAllowed(url, ua) === false) throw new Error('robots_denied_redirect');
      const { record, body } = await get(evidence, key, client, url, 'main_document', rules.delay, target.referer ? {referer: target.referer} : {});
      if ([301, 302, 303, 307, 308].includes(record.status) && record.headers.location) {
        const next = new URL(record.headers.location, url).href;
        if (new URL(next).origin !== new URL(target.url).origin) {
          evidence.result({ client: name, target: target.name, url, ...classify(record.status, body.toString(), record.headers),
            outcome: 'redirect_outside_scope', redirect_to: next, main_record: record.index }); return;
        }
        if (hop === L.max_redirects) throw new Error('redirect_limit');
        url = next; continue;
      }
      evidence.result({ client: name, target: target.name, url: target.url, final_url: url, user_agent: ua,
        http_status: record.status, main_record: record.index, truncated: record.truncated,
        ...classify(record.status, decodeBody(body, record.headers), record.headers) });
      return;
    }
  } catch (error) { evidence.result({ client: name, target: target.name, url: target.url, outcome: 'execution_error', error: safeError(error) }); }
  finally { if (!sharedClient) await client.close(); }
}

export async function browserSite(evidence, name, target, sharedSession = null) {
  let b, robotsClient;
  let cdpSession;
  const listeners = [];
  const key = `${name}/${target.name}`;
  const result = { client: name, target: target.name, url: target.url, blocked_requests: [], page_errors: [] };
  try {
    b = sharedSession?.browser || await openBrowser(name);
    result.user_agent = b.ua;
    robotsClient = sharedSession?.robots || await httpClient(name, b.ua);
    const rules = await robots(evidence, key, robotsClient, target, b.ua);
    if (!rules.allowed) { evidence.result({ ...result, ...rules }); return; }
    const origin = new URL(target.url).origin;
    const records = new Map(), tasks = [], documentResponses = [], intercepted = [];
    let redirects = 0, stopped = false;
    const watch = (event, listener) => { b.page.on(event, listener); listeners.push([event, listener]); };
    watch('pageerror', error => result.page_errors.push(safeError(error)));
    const admit = async request => {
      const url = request.url();
      const reason = stopped ? 'observation_end' : request.method() !== 'GET' ? 'non_get' : new URL(url).origin !== origin ? 'outside_scope'
        : ['image', 'media', 'font'].includes(request.resourceType()) ? 'resource_policy'
        : rules.parser.isAllowed(url, b.ua) === false ? 'robots_denied'
        : request.isNavigationRequest() && request.redirectChain?.().length > L.max_redirects ? 'redirect_limit' : null;
      if (reason) { result.blocked_requests.push({ url, reason }); return null; }
      if (request.isNavigationRequest() && request.redirectedFrom?.() && ++redirects > L.max_redirects) {
        result.blocked_requests.push({ url, reason: 'redirect_limit' }); return null;
      }
      let record;
      try {
        record = evidence.reserve(key, url, request.isNavigationRequest() ? 'main_document' : request.resourceType());
        records.set(request, record);
        await evidence.wait(url, rules.delay);
        if (stopped) { evidence.fail(record, new Error('observation_end')); return null; }
        return record;
      } catch (error) { result.blocked_requests.push({ url, reason: error.message }); return null; }
    };
    if (b.kind === 'playwright') {
      // Playwright routes do not intercept subsequent automatic redirect hops.
      // CDP request interception does; count and authorize each hop before continuing it.
      const session = await b.context.newCDPSession(b.page);
      cdpSession = session;
      await session.send('Network.setCacheDisabled', { cacheDisabled: true });
      session.on('Fetch.requestPaused', event => {
        const task = (async () => {
          const request = {
            url: () => event.request.url, method: () => event.request.method,
            resourceType: () => event.resourceType.toLowerCase(),
            isNavigationRequest: () => event.resourceType === 'Document',
            redirectedFrom: () => event.redirectedRequestId || null,
          };
          const record = await admit(request);
          try {
            if (record) {
              intercepted.push(record);
              await session.send('Fetch.continueRequest', { requestId: event.requestId });
            } else await session.send('Fetch.failRequest', { requestId: event.requestId, errorReason: 'BlockedByClient' });
          } catch (error) { if (record) evidence.fail(record, error); }
        })();
        tasks.push(task);
      });
      await session.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
      // Every WebSocket is excluded from this read-only GET benchmark.
      if (b.context.routeWebSocket) await b.context.routeWebSocket('**/*', socket => socket.close());
    } else {
      await b.page.setRequestInterception(true);
      watch('request', request => {
        const task = admit(request).then(record => record ? request.continue() : request.abort('blockedbyclient')).catch(() => {});
        tasks.push(task);
      });
    }
    watch('response', response => {
      const task = (async () => {
        const request = response.request();
        const record = records.get(request) || intercepted.find(x => x.url === request.url() && x.status === 'interrupted' && !x.response_received);
        if (!record || record.status !== 'interrupted') return;
        records.set(request, record);
        record.response_received = true;
        try {
          const headers = b.kind === 'playwright' ? await response.allHeaders() : response.headers();
          record.response_status = response.status();
          record.response_headers = keptHeaders(headers);
          evidence.flush();
          const redirect = [301, 302, 303, 307, 308].includes(response.status());
          const raw = redirect ? Buffer.alloc(0) : b.kind === 'playwright' ? await response.body() : await response.buffer();
          evidence.finish(record, response.status(), headers, raw, raw.length > record.cap);
          if (request.isNavigationRequest() && request.frame() === b.page.mainFrame()) documentResponses.push({ record, body: raw.subarray(0, record.cap) });
        } catch (error) { evidence.fail(record, error); }
      })();
      tasks.push(task);
    });
    watch('requestfailed', request => {
      const record = records.get(request) || intercepted.find(x => x.url === request.url() && x.status === 'interrupted');
      if (record?.status === 'interrupted') evidence.fail(record, new Error(request.failure()?.errorText || 'request_failed'));
    });
    if (b.kind === 'playwright') await b.context.setOffline(false);
    try { await b.page.goto(target.url, { waitUntil: 'domcontentloaded', timeout: L.navigation_timeout_ms, ...(target.referer ? {referer: target.referer} : {}) }); }
    catch (error) { result.navigation_error = safeError(error); }
    await sleep(L.observe_ms);
    // Stop subsequent network activity before draining response bodies and saving artifacts.
    stopped = true;
    if (b.kind === 'playwright') await b.context.setOffline(true);
    await Promise.allSettled(tasks);
    const final = documentResponses.at(-1);
    result.final_url = b.page.url();
    const dom = Buffer.from(await b.page.content());
    result.dom_sha256 = evidence.blob(dom.subarray(0, L.bytes_per_response));
    result.dom_truncated = dom.length > L.bytes_per_response;
    if (target.primary_selector) {
      try {
        result.selector_observation = { selector:target.primary_selector,
          match_count:await b.page.evaluate(selector=>document.querySelectorAll(selector).length, target.primary_selector),
          actions_executed:false };
      } catch (error) { result.selector_observation = {selector:target.primary_selector, error:safeError(error), actions_executed:false}; }
    }
    if (b.kind === 'playwright') {
      result.screenshot = `${name}-${target.artifact_tag || target.name}.png`;
      await b.page.screenshot({ path: path.join(evidence.directory, result.screenshot), fullPage: false });
    }
    if (final) Object.assign(result, classify(final.record.status, dom.toString('utf8'), final.record.headers),
      { http_status: final.record.status, main_record: final.record.index });
    else result.outcome = 'navigation_unverified';
    result.resource_policy = config.browser_resources;
    if (result.blocked_requests.some(x => /budget/.test(x.reason))) result.budget_limited = true;
    evidence.result(result);
  } catch (error) { evidence.result({ ...result, outcome: 'execution_error', error: safeError(error) }); }
  finally {
    if (cdpSession) {
      try { await cdpSession.send('Fetch.disable'); await cdpSession.detach(); } catch { /* context may already be closed */ }
    }
    for (const [event, listener] of listeners) b?.page.off(event, listener);
    if (!sharedSession) { await robotsClient?.close(); await b?.close(); }
    for (const record of evidence.records) if (record.key === key && record.status === 'interrupted') {
      evidence.fail(record, new Error('observation_window_closed_without_response'));
    }
  }
}

export function verify(directory) {
  const ledger = JSON.parse(fs.readFileSync(path.join(directory, 'ledger.json')));
  const results = JSON.parse(fs.readFileSync(path.join(directory, 'results.json')));
  const errors = [];
  for (const record of ledger.records) {
    if (record.body_sha256) {
      const body = fs.readFileSync(path.join(directory, 'blobs', record.body_sha256));
      if (sha(body) !== record.body_sha256 || body.length !== record.bytes_charged) errors.push(`blob:${record.index}`);
    }
    if (record.bytes_charged > record.cap) errors.push(`response_cap:${record.index}`);
    if (record.status === 'interrupted') errors.push(`inflight:${record.index}`);
  }
  for (const [key, b] of Object.entries(ledger.budgets)) {
    const records = ledger.records.filter(x => x.key === key);
    if (records.length !== b.requests || records.reduce((sum, x) => sum + x.bytes_charged, 0) !== b.bytes_charged) errors.push(`accounting:${key}`);
    if (b.requests > L.requests_per_client_target || b.bytes_charged > L.bytes_per_client_target) errors.push(`budget:${key}`);
  }
  for (const result of results) if (result.dom_sha256) {
    const body = fs.readFileSync(path.join(directory, 'blobs', result.dom_sha256));
    if (sha(body) !== result.dom_sha256) errors.push(`dom:${result.client}/${result.target || result.detector}`);
  }
  return { ok: !errors.length, errors, records: ledger.records.length, results: results.length,
    note: 'body storage/accounting caps verified; Chromium transfer bytes can exceed retained-body cap' };
}

function selectedClients() {
  const requested = process.env.BOT_DIAGNOSTICS_CLIENTS?.split(',') || config.clients;
  if (requested.some(name => !config.clients.includes(name))) throw new Error('Unknown client');
  return requested;
}

function selectedTargets() {
  const requested = process.env.BOT_DIAGNOSTICS_TARGETS?.split(',') || config.targets.map(x => x.name);
  if (requested.some(name => !config.targets.some(x => x.name === name))) throw new Error('Unknown target');
  return config.targets.filter(x => requested.includes(x.name));
}

async function main() {
  const args = process.argv.slice(2);
  if (args[0] === 'verify') { console.log(JSON.stringify(verify(path.resolve(args[1])), null, 2)); return; }
  if (!proxy) throw new Error('This runner requires the inherited HTTPS proxy; no direct-network fallback');
  const mode = args[0] || 'all';
  if (!['all', 'detectors', 'sites', 'resume-sites'].includes(mode)) throw new Error('Usage: runner.mjs [all|detectors|sites|resume-sites|verify] RUN_DIRECTORY');
  const directory = path.resolve(args[1] || path.join(repo, 'lab-runs/bot-diagnostics-001'));
  fs.mkdirSync(path.dirname(directory), { recursive: true });
  const lockPath = directory + '.lock';
  const lock = fs.openSync(lockPath, 'wx');
  fs.writeSync(lock, JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }));
  try {
  const evidence = new Evidence(directory, mode === 'resume-sites');
  const invocation = { started_at: new Date().toISOString(), mode, clients: selectedClients(), targets: selectedTargets().map(x => x.name),
    runner_sha256: evidence.blob(fs.readFileSync(fileURLToPath(import.meta.url))) };
  const historyPath = path.join(directory, 'invocations.json');
  const history = fs.existsSync(historyPath) ? JSON.parse(fs.readFileSync(historyPath)) : [];
  evidence.save('invocations.json', [...history, invocation]);
  if (mode !== 'resume-sites') evidence.save('environment.json', { started_at: new Date().toISOString(), node: process.version,
    chromium: execFileSync(process.env.BOT_DIAGNOSTICS_CHROMIUM || '/usr/bin/chromium', ['--version'], { encoding: 'utf8' }).trim(),
    lightpanda: execFileSync(path.join(deps, 'lightpanda'), ['version'], { encoding: 'utf8' }).trim(),
    lightpanda_sha256: sha(fs.readFileSync(path.join(deps, 'lightpanda'))),
    detector_source: JSON.parse(fs.readFileSync(path.join(deps, 'detector-source.json'))),
    package_lock_sha256: sha(fs.readFileSync(path.join(deps, 'package-lock.json'))),
    proxy: { protocol: new URL(proxy).protocol, host: new URL(proxy).hostname, port: new URL(proxy).port },
    runs_per_cell: 1, tls_fingerprint_at_origin: 'not measured',
    limits_note: 'All HTTP requests admitted by the runner are counted, including robots and errors. Local browser fixtures are offline tests. Byte caps cover retained/charged bodies; Chromium can receive more before the response is truncated for storage.' });
  let fixture;
  try {
    if (!['sites', 'resume-sites'].includes(mode)) {
      fixture = await startFixture();
      for (const name of selectedClients()) await detectorRun(evidence, name, fixture.origin);
    }
    if (mode !== 'detectors') {
      for (const name of selectedClients()) for (const target of selectedTargets()) {
        if (['wreq-js', 'impit'].includes(name)) await httpSite(evidence, name, target);
        else await browserSite(evidence, name, target);
        console.log(JSON.stringify(evidence.results.at(-1), (key, value) => ['blocked_requests', 'page_errors', 'visible_text_prefix'].includes(key) ? undefined : value));
      }
    }
  } finally { await fixture?.close(); evidence.flush(); }
  const verification = verify(directory);
  evidence.save('verification.json', verification);
  console.log(JSON.stringify({ directory, verification }));
  } finally { fs.closeSync(lock); fs.unlinkSync(lockPath); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(JSON.stringify(safeError(error))); process.exitCode = 1; });
}
