import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {deps, state, require, config, limits as L, proxy, sleep, HTTP_USER_AGENT} from './runtime.mjs';
import {Evidence, verify, safeError, keptHeaders} from './evidence.mjs';
import {classify, decodeBody} from './observations.mjs';
import {openLightpanda} from './lightpanda-runtime.mjs';
import {openObscura} from './obscura-runtime.mjs';
import {assertChromiumTrustWritable} from './chromium-trust.mjs';

// Preserve the existing runner API for scenarios and historical verification commands.
export {Evidence, verify, safeError, classify, decodeBody};

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

export async function startFixture() {
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

export async function openBrowser(name, {extensions = [], lightpanda = null, fixture = false} = {}) {
  if(lightpanda && name !== 'rebrowser-lightpanda') throw new Error('lightpanda_profile_requires_lightpanda');
  fs.mkdirSync(state, {recursive:true});
  if (name.startsWith('obscura')) {
    if(extensions.length) throw new Error('unsupported_capability:extensions');
    return openObscura(name,{fixture});
  }
  if (name === 'rebrowser-lightpanda' && extensions.length) throw new Error('unsupported_capability:extensions');
  if (name !== 'rebrowser-lightpanda') {
    const dataDirectory=path.join(state,'chromium-data');
    const trust=process.platform==='linux' && proxy && !fixture
      ? await assertChromiumTrustWritable({dataDirectory}) : null;
    const { chromium } = require(name === 'patchright' ? 'patchright' : 'playwright');
    const installed=path.join(deps,'chromium');
    const launch = { executablePath: process.env.BOT_DIAGNOSTICS_CHROMIUM || (fs.existsSync(installed)?installed:'/usr/bin/chromium'),
      headless: true, chromiumSandbox: false,
      ...(proxy ? {proxy:{server:proxy,bypass:'127.0.0.1,localhost'}} : {}),
      env: { ...process.env, XDG_DATA_HOME: dataDirectory } };
    const browser = await chromium.launch(launch);
    const probe = await browser.newContext();
    const page = await probe.newPage();
    const ua = await page.evaluate(() => navigator.userAgent) + ' ' + config.identification;
    await probe.close();
    if (extensions.length) {
      await browser.close();
      const profile = fs.mkdtempSync(path.join(state, 'extension-profile-'));
      let context;
      try {
        context = await chromium.launchPersistentContext(profile, {...launch, userAgent:ua,
          serviceWorkers:'block', ignoreHTTPSErrors:false, ignoreDefaultArgs:['--disable-extensions'],
          args:['--enable-unsafe-extension-debugging']});
        // Modern Chromium removed --load-extension; use its native extension-loading CDP API.
        const extensionSession=await context.browser().newBrowserCDPSession();
        const loaded=[];
        try {for(const extension of extensions) loaded.push(await extensionSession.send('Extensions.loadUnpacked',{path:extension}));}
        finally {await extensionSession.detach();}
        return {browser:context.browser(), context, page:context.pages()[0] || await context.newPage(), ua,
          kind:'playwright', extensions, loaded_extensions:loaded,
          runtime:{executable:launch.executablePath,version:context.browser().version(),...(trust?{nss_trust:trust}:{})},
          close:async()=>{try{await context.close();}finally{fs.rmSync(profile,{recursive:true,force:true});}}};
      } catch(error) {await context?.close();fs.rmSync(profile,{recursive:true,force:true});throw error;}
    }
    const context = await browser.newContext({ userAgent: ua, serviceWorkers: 'block', ignoreHTTPSErrors: false });
    const handle={ browser, context, page: await context.newPage(), ua, kind: 'playwright',
      runtime:{executable:launch.executablePath,version:browser.version(),...(trust?{nss_trust:trust}:{})},close: () => browser.close() };
    handle.replaceContext=async(cookies=[])=>{
      await handle.context.close();
      handle.context=await browser.newContext({userAgent:ua,serviceWorkers:'block',ignoreHTTPSErrors:false});
      if(cookies.length) await handle.context.addCookies(cookies);
      handle.page=await handle.context.newPage();
    };
    return handle;
  }
  return openLightpanda(lightpanda);
}

export async function detectorRun(evidence, name, origin, browserOptions = {}) {
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
    b = await openBrowser(name, {...browserOptions,fixture:true});
    for (const detector of ['rebrowser', 'botd']) {
      const result = { client: name, detector, user_agent: b.ua, runtime:b.runtime, trigger_errors: [], page_errors: [] };
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
        if(b.runtime?.screenshots !== false) {
          try {await page.screenshot({ path: path.join(evidence.directory, `${name}-${detector}.png`), fullPage: true });}
          catch(error) {result.screenshot_error=safeError(error);}
        }
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
  const ua = HTTP_USER_AGENT;
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
    if(b.runtime?.budgeted_navigation === false) {
      throw new Error('unsupported_capability:budgeted_navigation; redirect interception unavailable');
    }
    result.user_agent = b.ua;
    robotsClient = sharedSession?.robots || await httpClient(name, b.ua);
    const rules = await robots(evidence, key, robotsClient, target, b.ua);
    if (!rules.allowed) { evidence.result({ ...result, ...rules }); return; }
    const origin = new URL(target.url).origin;
    const records = new Map(), tasks = [], documentResponses = [], intercepted = [];
    let redirects = 0, documentAttempts = 0, stopped = false;
    const watch = (event, listener) => { b.page.on(event, listener); listeners.push([event, listener]); };
    watch('pageerror', error => result.page_errors.push(safeError(error)));
    const admit = async request => {
      const url = request.url();
      const reason = stopped ? 'observation_end' : request.method() !== 'GET' ? 'non_get' : new URL(url).origin !== origin ? 'outside_scope'
        : ['image', 'media', 'font'].includes(request.resourceType()) || new URL(url).pathname === '/favicon.ico' ? 'resource_policy'
        : rules.parser.isAllowed(url, b.ua) === false ? 'robots_denied'
        : request.isNavigationRequest() && request.redirectChain?.().length > L.max_redirects ? 'redirect_limit' : null;
      if (reason) { result.blocked_requests.push({ url, reason }); return null; }
      // Obscura emits a new CDP request ID without redirectResponse for every
      // hop, so Puppeteer's redirectChain() is empty. Conservatively share the
      // initial + redirect allowance across all documents in this observation,
      // including script navigations and frames, until native chains exist.
      if (b.runtime?.redirect_chain_reporting === false && request.isNavigationRequest()
        && documentAttempts++ > L.max_redirects) {
        result.blocked_requests.push({ url, reason: 'redirect_limit' }); return null;
      }
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
    if (!target.operation) {
      try { await b.page.goto(target.url, { waitUntil: 'domcontentloaded', timeout: L.navigation_timeout_ms, ...(target.referer ? {referer: target.referer} : {}) }); }
      catch (error) { result.navigation_error = safeError(error); }
    } else {
      try { result.operation = await target.operation(b); }
      catch (error) { result.operation = {outcome:'operation_error',error:safeError(error)}; }
    }
    await sleep(target.observe_ms ?? L.observe_ms);
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
    if (b.kind === 'playwright' && target.capture_screenshot !== false && b.runtime?.screenshots !== false) {
      result.screenshot = `${name}-${target.artifact_tag || target.name}.png`;
      await b.page.screenshot({ path: path.join(evidence.directory, result.screenshot), fullPage: false });
    }
    if (final) Object.assign(result, classify(final.record.status, dom.toString('utf8'), final.record.headers),
      { http_status: final.record.status, main_record: final.record.index });
    else if (target.operation && target.previousObservation && result.final_url === target.previousObservation.final_url) Object.assign(result,
      classify(target.previousObservation.http_status, dom.toString('utf8'), target.previousObservation.headers),
      {http_status:target.previousObservation.http_status, main_record:target.previousObservation.main_record,
        status_source:'previous_document_response; no new document response during operation'});
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

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  import('./runner-cli.mjs').then(({main}) => main()).catch(error => {
    console.error(JSON.stringify(safeError(error)));
    process.exitCode = 1;
  });
}
