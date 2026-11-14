import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const runDefault = path.resolve(repo, 'lab-runs/structure-routing-20261115');
const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const sha = text => crypto.createHash('sha256').update(text).digest('hex');
const documentKey = value => { const url = new URL(value); url.hash = ''; return url.href; };
const args = Object.fromEntries(process.argv.slice(2).reduce((out, item, i, all) => {
  if (!item.startsWith('--')) return out;
  const [key, inline] = item.slice(2).split('=', 2);
  out.push([key, inline ?? (all[i + 1] === undefined || all[i + 1].startsWith('--') ? 'true' : all[i + 1])]);
  return out;
}, []));

function readJSONL(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
}
function saveJSON(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}
function appendJSONL(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(value) + '\n');
}
function safeURL(value, base) {
  try {
    const u = new URL(value, base);
    return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : null;
  } catch { return null; }
}
function inHosts(value, hosts) {
  try { return hosts.has(new URL(value).hostname.toLowerCase()); } catch { return false; }
}
function stateFeatures(meta, task, rawText) {
  const spec = `${task.expected_state} ${(task.dom_checks || []).join(' ')}`.toLowerCase();
  const linkOkay = meta.valid_links > 0;
  const useful = meta.visible_text_length >= 20 && meta.element_count >= 3;
  const renderedDiff = !!meta.rendered_only_text;
  const itemIncrease = meta.item_count_after > meta.item_count_before;
  let category = 'generic';
  if (/javascript|java script|描画/.test(spec)) category = 'javascript';
  if (/scroll|スクロール/.test(spec)) category = 'scroll';
  if (/load more|追加読み込み|追加コンテンツ|ページ下部/.test(spec)) category = 'incremental';
  if (/ページネーション|ページ送り|次ページ|カーソル/.test(spec)) category = 'pagination';
  if (/ajax|ajax取得/i.test(spec)) category = 'ajax';
  if (/iframe|frame内|frameまたは/.test(spec)) category = 'frame';
  if (/shadow dom|shadow root/.test(spec)) category = 'shadow';
  if (/動的データ|繰返しデータ|dynamic data/.test(spec)) category = 'data';
  let passed = false, reason = 'state_requirements_not_met';
  if (category === 'javascript') { passed = useful && renderedDiff && (meta.valid_links > 0 || meta.item_count_after > 0); reason = passed ? 'rendered_dom_diff_with_content' : reason; }
  else if (category === 'scroll' || category === 'incremental') { passed = useful && itemIncrease && linkOkay; reason = passed ? 'scroll_or_incremental_content_increased' : reason; }
  else if (category === 'pagination') { passed = useful && (meta.pagination_control_count > 0 || meta.pagination_action_count > 0) && linkOkay; reason = passed ? 'pagination_control_with_content_and_link' : reason; }
  else if (category === 'ajax') { passed = useful && meta.item_count_after > 0 && meta.ajax_or_fetch_requests > 0; reason = passed ? 'ajax_data_rendered' : reason; }
  else if (category === 'frame') { passed = meta.frame_documents > 0 && meta.frame_nonempty_documents > 0; reason = passed ? 'frame_dom_saved' : reason; }
  else if (category === 'shadow') { passed = meta.shadow_item_count > 0 && meta.shadow_text_length > 0; reason = passed ? 'open_shadow_dom_saved' : reason; }
  else if (category === 'data') { passed = useful && meta.item_count_after > 0; reason = passed ? 'dynamic_items_with_text' : reason; }
  else { passed = useful && linkOkay && (renderedDiff || itemIncrease || meta.action_count > 0); reason = passed ? 'interactive_content_with_valid_link' : reason; }
  return {category, passed, reason, rendered_dom_diff: renderedDiff};
}

async function collectSnapshot(page, rawBody, task, directory, sequence, counters) {
  const frames = [];
  let mainFrameData = null;
  for (const [index, frame] of page.frames().entries()) {
    const url = frame.url();
    let data;
    try {
      data = await frame.evaluate(() => {
        const deepClone = root => {
          const clone = root.cloneNode(true);
          const sourceEls = [root, ...root.querySelectorAll('*')];
          const cloneEls = [clone, ...clone.querySelectorAll('*')];
          sourceEls.forEach((source, i) => {
            if (!source.shadowRoot || !cloneEls[i]) return;
            const marker = document.createElement('template');
            marker.setAttribute('data-open-shadow-root', '');
            marker.innerHTML = [...source.shadowRoot.childNodes].map(node => node.nodeType === Node.ELEMENT_NODE ? node.outerHTML : node.textContent).join('');
            cloneEls[i].append(marker);
          });
          return clone.outerHTML;
        };
        const html = document.documentElement ? deepClone(document.documentElement) : '';
        const text = document.body?.innerText || '';
        const elements = [...document.querySelectorAll('body *')];
        const shadowHosts = elements.filter(el => el.shadowRoot);
        let shadowItems = 0, shadowText = '';
        for (const host of shadowHosts) {
          const nodes = host.shadowRoot.querySelectorAll('a[href], article, li, [itemscope], [role="listitem"]');
          shadowItems += nodes.length;
          shadowText += host.shadowRoot.textContent || '';
        }
        const itemSelector = 'article,li,tr,[itemscope],[itemtype],[role="listitem"],[class*="quote"],[class*="product"],[class*="item"],[class*="card"]';
        const items = [...document.querySelectorAll(itemSelector)].filter(el => (el.innerText || el.textContent || '').trim().length > 0);
        const groupCounts = new Map();
        for (const el of document.querySelectorAll('main *,article *,[role="main"] *')) {
          if (!el.parentElement) continue;
          const cls = typeof el.className === 'string' ? el.className.split(/\s+/).filter(Boolean).sort().join('.') : '';
          const key = `${el.parentElement.tagName}:${el.tagName}:${cls}`;
          groupCounts.set(key, (groupCounts.get(key) || 0) + 1);
        }
        const controls = [...document.querySelectorAll('button,a[rel],a[aria-label],a[href],input[type="button"],[role="button"]')]
          .filter(el => { const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0; });
        const pagination = controls.filter(el => /next|more|older|page|次|もっと|続き|読み込|前へ|後へ/i.test(`${el.innerText || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('rel') || ''} ${el.getAttribute('title') || ''}`));
        const links = [...document.querySelectorAll('a[href]')].map(a => ({href: a.href, text: (a.innerText || a.textContent || a.getAttribute('aria-label') || '').trim()}));
        const frames = [...document.querySelectorAll('iframe,frame')].map(el => el.src || el.getAttribute('src') || '');
        const pathSignature = elements.filter(el => el.parentElement).reduce((map, el) => {
          const parent = el.parentElement;
          const key = `${parent.tagName}:${[...parent.children].filter(x => x.tagName === el.tagName).length}`;
          map[key] = (map[key] || 0) + 1; return map;
        }, {});
        return {html, text, element_count: elements.length, visible_text_length: text.trim().length,
          item_count: Math.max(items.length, ...[...groupCounts.values()].filter(x => x >= 2)), nonempty_items: items.filter(x => (x.innerText || x.textContent || '').trim().length > 0).length,
          links, frames, pagination_control_count: pagination.length, action_count: controls.length,
          shadow_host_count: shadowHosts.length, shadow_item_count: shadowItems, shadow_text_length: shadowText.trim().length,
          path_signature: pathSignature};
      });
    } catch (error) { data = {html: '', text: '', element_count: 0, visible_text_length: 0, item_count: 0, nonempty_items: 0, links: [], frames: [], pagination_control_count: 0, action_count: 0,
      shadow_host_count: 0, shadow_item_count: 0, shadow_text_length: 0, capture_error: String(error.message).slice(0, 200)}; }
    if (index === 0) mainFrameData = data;
    if (index > 0) {
      let frameData = null;
      if (/^https?:/.test(url)) {
        try { frameData = await frame.evaluate(() => ({html: document.documentElement?.outerHTML || '', text: document.body?.innerText || '', links: [...document.querySelectorAll('a[href]')].map(a => a.href)})); } catch {}
      }
      frames.push({index, url, allowed_host: inHosts(url, counters.hosts), html: frameData?.html || null,
        text: frameData?.text || '', links: frameData?.links || [], nonempty: !!frameData?.text?.trim()});
    }
  }
  const main = await page.evaluate(rawHTML => {
    const html = document.documentElement?.outerHTML || '';
    const text = document.body?.innerText || '';
    const items = [...document.querySelectorAll('article,li,tr,[itemscope],[itemtype],[role="listitem"],[class*="quote"],[class*="product"],[class*="item"],[class*="card"]')]
      .filter(el => (el.innerText || el.textContent || '').trim().length > 0);
    const groupCounts = new Map();
    for (const el of document.querySelectorAll('main *,article *,[role="main"] *')) {
      if (!el.parentElement) continue;
      const cls = typeof el.className === 'string' ? el.className.split(/\s+/).filter(Boolean).sort().join('.') : '';
      const key = `${el.parentElement.tagName}:${el.tagName}:${cls}`;
      groupCounts.set(key, (groupCounts.get(key) || 0) + 1);
    }
    const rawDocumentText = (() => { try {
      const raw = new DOMParser().parseFromString(rawHTML, 'text/html');
      raw.querySelectorAll('script,style,noscript').forEach(el => el.remove());
      return raw.body?.textContent || '';
    } catch { return ''; } })().replace(/\s+/g, ' ');
    const renderedOnlyText = text.replace(/\s+/g, ' ').trim().split(/(?<=[.!?。！？])\s+|\s+/)
      .filter(word => word.length >= 4 && !rawDocumentText.includes(word)).slice(0, 20);
    const validLinks = [...document.querySelectorAll('a[href]')].filter(a => /^https?:$/.test(new URL(a.href, location.href).protocol));
    const itemCount = Math.max(items.length, ...[...groupCounts.values()].filter(x => x >= 2));
    return {html, text, item_count: itemCount, valid_links: validLinks.length, visible_text_length: text.trim().length,
      element_count: document.querySelectorAll('body *').length,
      pagination_control_count: [...document.querySelectorAll('a,button,[role="button"]')].filter(el => /next|more|older|page|次|もっと|続き|読み込|後へ/i.test(`${el.innerText || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('rel') || ''}`)).length,
      action_count: document.querySelectorAll('button,a[href],[role="button"]').length, rendered_only_text: renderedOnlyText.length > 0,
      links: [...document.querySelectorAll('a[href]')].map(a => ({href: a.href, text: (a.innerText || a.textContent || a.getAttribute('aria-label') || '').trim()}))};
  }, rawBody?.toString('utf8') || '');
  const domHash = sha(JSON.stringify({html: main.html || '', frames: frames.filter(x => x.index > 0).map(x => ({url: x.url, html: x.html || ''}))}));
  const rawText = rawBody?.toString('utf8') || '';
  const frameDir = path.join(directory, `frame-${sequence}`);
  fs.mkdirSync(frameDir, {recursive: true});
  for (const frame of frames) if (frame.index > 0 && frame.html) fs.writeFileSync(path.join(frameDir, `${frame.index}.html`), frame.html);
  const domPath = path.join(directory, `${sequence}.dom.html`);
  const rawPath = path.join(directory, `${sequence}.response.html`);
  fs.writeFileSync(domPath, main.html || '');
  if (rawText) fs.writeFileSync(rawPath, rawText);
  const snapshot = {url: page.url(), state_id: `sha256:${domHash}`, dom_hash: domHash,
    item_count: main.item_count, visible_text_length: main.visible_text_length, element_count: main.element_count,
    valid_links: main.valid_links, pagination_control_count: main.pagination_control_count,
    pagination_action_count: main.action_count, action_count: main.action_count,
    frame_documents: frames.filter(x => x.index > 0 && x.html).length,
    frame_nonempty_documents: frames.filter(x => x.index > 0 && x.nonempty).length,
    frame_urls: frames.filter(x => x.index > 0).map(x => ({url: x.url, allowed_host: x.allowed_host, saved: !!x.html,
      nonempty: x.nonempty, text_length: x.text.length, link_count: x.links.length, saved_file: x.html ? `frame-${sequence}/${x.index}.html` : null})),
    shadow_host_count: mainFrameData?.shadow_host_count || 0, shadow_item_count: mainFrameData?.shadow_item_count || 0,
    shadow_text_length: mainFrameData?.shadow_text_length || 0, rendered_only_text: main.rendered_only_text,
    raw_length: rawText.length, rendered_dom_length: (main.html || '').length,
    dom_file: path.basename(domPath), response_html_file: rawText ? path.basename(rawPath) : null,
    item_count_before: counters.initialItemCount ?? main.item_count,
    item_count_after: main.item_count,
    ajax_or_fetch_requests: counters.ajax,
    title: await page.title().catch(() => ''), status: counters.status.get(documentKey(page.url())) ?? null,
    links: main.links};
  return {snapshot, mainHTML: main.html, rawText};
}

function controlScore(entry, method) {
  const text = `${entry.text} ${entry.aria} ${entry.rel} ${entry.title} ${entry.value}`.trim();
  if (!/next|more|older|page|次|もっと|続き|読み込|追加|前へ|後へ/i.test(text)) return -100;
  if (/buy|purchase|cart|checkout|login|sign.?up|submit|delete|remove|購入|注文|カート|ログイン|登録|送信|削除/i.test(text)) return -100;
  const semantic = Number(!!entry.rel) + Number(!!entry.aria) + Number(!!entry.title) + Number(!!entry.dataAction);
  const paging = /next|次|後へ|older/i.test(text) ? 2 : 0;
  const more = /more|追加|読み込|もっと|続き/i.test(text) ? 2 : 0;
  return method === 'C1' ? semantic * 3 + paging + more : paging + more;
}

async function runTask(task, target, method, runDir, mainRows, priorChallengeRows) {
  const started = Date.now();
  const practice = !!target.practice;
  const methodRows = mainRows.filter(row => row.id === task.site_id && row.method === method);
  const challengeRows = priorChallengeRows.filter(row => row.site_id === task.site_id && row.method === method);
  const mainDocs = methodRows.reduce((sum, row) => sum + (row.document_requests || 0), 0);
  const mainOps = methodRows.reduce((sum, row) => sum + (row.operations || 0), 0);
  const mainTime = methodRows.reduce((sum, row) => sum + (row.elapsed_ms || 0), 0);
  const challengeDocs = challengeRows.reduce((sum, row) => sum + (row.document_requests || 0), 0);
  const challengeOps = challengeRows.reduce((sum, row) => sum + (row.operations || 0), 0);
  const challengeTime = challengeRows.reduce((sum, row) => sum + (row.elapsed_ms || 0), 0);
  const documentCap = practice ? 40 : 15, operationCap = practice ? 100 : 30;
  let interval = practice ? 1000 : 5000;
  const remainingDocs = Math.max(0, documentCap - mainDocs - challengeDocs);
  const remainingOps = Math.max(0, operationCap - mainOps - challengeOps);
  const priorLimit = Math.max(0, 600000 - mainTime - challengeTime);
  const dir = path.join(runDir, 'artifacts', task.site_id, method, 'challenges', task.id);
  fs.mkdirSync(dir, {recursive: true});
  const base = {execution_origin: 'candidate_challenge_measurement', site_id: task.site_id, task_id: task.id, method, start_url: task.start_url,
    expected_state: task.expected_state, status: 'not_run', task_success: false,
    prior_main_documents: mainDocs, prior_challenge_documents: challengeDocs,
    document_cap: documentCap, remaining_document_budget: remainingDocs,
    prior_main_operations: mainOps, prior_challenge_operations: challengeOps,
    operation_cap: operationCap, remaining_operation_budget: remainingOps,
    elapsed_ms: 0, browser_seconds: 0, requests: 0, document_requests: 0, document_responses: 0,
    xhr_fetch_requests: 0, transferred_bytes: 0, operations: 0, state_changes: 0,
    external_llm_calls: 0, local_inference: {calls: 0, runtime_ms: 0}, pages: [], stop_reason: null, failures: []};
  if (challengeRows.some(row => row.task_id === task.id)) return {...base, status: 'already_recorded', stop_reason: 'resume'};
  const accessStop = mainRows.find(row => row.id === task.site_id && ['blocked', 'login_page', 'maintenance', 'robots_disallowed'].includes(row.status)) ||
    priorChallengeRows.find(row => row.site_id === task.site_id && ['blocked', 'login_page', 'maintenance', 'robots_disallowed'].includes(row.status));
  if (accessStop) return {...base, status: 'skipped_due_access_condition', stop_reason: 'main_stage_access_stop', trigger: {method: accessStop.method, status: accessStop.status}};
  if (remainingDocs < 1 || remainingOps < 1 || priorLimit < 500) return {...base, status: 'budget_reached', stop_reason: 'insufficient_remaining_budget'};

  const allowedHosts = new Set([new URL(task.start_url).hostname.toLowerCase(), ...(target.allowed_hosts || [])].map(x => x.toLowerCase()));
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  const context = await browser.newContext({serviceWorkers: 'block', ignoreHTTPSErrors: false,
    locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}});
  const page = await context.newPage();
  await (await context.newCDPSession(page)).send('Network.setCacheDisabled', {cacheDisabled: true});
  let requests = 0, documentRequests = 0, documentReservations = 0, documentResponses = 0, xhr = 0, bytes = 0, operations = 0, lastNavigation = 0;
  const statuses = new Map(), bodies = new Map(), network = [], snapshots = [], failures = [];
  const robotsByOrigin = new Map();
  let robotsDenied = false;
  const robotsFor = async url => {
    const origin = new URL(url).origin;
    if (robotsByOrigin.has(origin)) {
      const cached = robotsByOrigin.get(origin);
      return {...cached.metadata, allowed: cached.parser ? cached.parser.isAllowed(url, '*') !== false : true};
    }
    try {
      const response = await context.request.get(new URL('/robots.txt', origin).href, {timeout: 10000});
      requests++;
      const body = await response.text(); bytes += Buffer.byteLength(body);
      if (response.status() >= 400) {
        const metadata = {status: response.status(), source: 'unavailable'};
        robotsByOrigin.set(origin, {metadata, parser: null}); return {...metadata, allowed: true};
      }
      const parser = require('robots-parser')(new URL('/robots.txt', origin).href, body);
      const metadata = {status: response.status(), source: 'robots-parser', crawl_delay_ms: Math.ceil((parser.getCrawlDelay('*') || 0) * 1000)};
      robotsByOrigin.set(origin, {metadata, parser});
      return {...metadata, allowed: parser.isAllowed(url, '*') !== false};
    } catch (error) { return {allowed: true, source: 'unavailable', error: String(error.message).slice(0, 160)}; }
  };
  page.on('request', request => {
    requests++;
    if (['xhr', 'fetch'].includes(request.resourceType())) xhr++;
    if (request.isNavigationRequest()) documentRequests++;
    network.push({url: request.url(), method: request.method(), type: request.resourceType()});
  });
  page.on('response', response => {
    const contentLength = Number(response.headers()['content-length']);
    if (Number.isFinite(contentLength) && contentLength > 0) bytes += contentLength;
    const req = response.request();
    if (req.resourceType() === 'document') {
      documentResponses++;
      const u = new URL(response.url()); u.hash = '';
      statuses.set(u.href, response.status());
      response.body().then(body => { bodies.set(u.href, body); if (!(contentLength > 0)) bytes += body.length; }).catch(() => {});
    }
  });
  await page.route('**/*', async route => {
    const request = route.request();
    const url = request.url();
    if (request.isNavigationRequest()) {
      documentReservations++;
      if (documentReservations > remainingDocs || documentRequests > remainingDocs) return route.abort('blockedbyclient');
      const mainFrame = request.frame()?.parentFrame() === null;
      if (mainFrame) {
        if (!/^https?:$/.test(new URL(url).protocol) || !allowedHosts.has(new URL(url).hostname.toLowerCase())) return route.abort('blockedbyclient');
        const robotsCheck = await robotsFor(url);
        if (!robotsCheck.allowed) { robotsDenied = true; return route.abort('blockedbyclient'); }
        const delay = Math.max(0, interval - (Date.now() - lastNavigation));
        if (delay) await wait(delay);
        lastNavigation = Date.now();
      }
    }
    return route.continue();
  });
  let robots = {allowed: true, status: null, source: 'unavailable'};
  let status = 'completed', stopReason = 'content_unchanged';
  try {
    robots = await robotsFor(task.start_url);
      if (!robots.allowed) { status = 'robots_disallowed'; stopReason = 'blocked'; }
    else {
      interval = Math.max(interval, robots.crawl_delay_ms || 0);
      await page.goto(task.start_url, {waitUntil: 'domcontentloaded', timeout: Math.min(30000, priorLimit)});
      await wait(Math.min(1500, interval));
      let navStatus = statuses.get(new URL(page.url()).href) ?? null;
      if (navStatus === 429 || navStatus === 403) { status = 'blocked'; stopReason = 'blocked'; }
      let raw = bodies.get(documentKey(page.url()));
      const initialCapture = await collectSnapshot(page, raw, task, dir, 0, {hosts: allowedHosts, status: statuses, ajax: xhr, initialItemCount: 0});
      const initialItemCount = initialCapture.snapshot.item_count;
      snapshots.push({...initialCapture.snapshot, stage: 'initial', item_count_before: initialItemCount, item_count_after: initialItemCount});
      const initialHash = initialCapture.snapshot.dom_hash;
      const actionCountsBefore = {item: initialItemCount, html: initialCapture.mainHTML, url: page.url()};
      let stateChanged = false;
      let scrollStep = 0;
      const initialStatus = initialCapture.snapshot.status;
      const initialText = initialCapture.mainHTML || '';
      const visibleText = await page.locator('body').innerText().catch(() => '');
      const blocked = initialStatus === 403 || initialStatus === 429 || /access denied|verify you are human|request blocked|unusual traffic|robot check/i.test(`${initialText.slice(0, 6000)} ${visibleText.slice(0, 1000)}`);
      if (blocked) { status = 'blocked'; stopReason = 'blocked'; }
      for (; !blocked && scrollStep < 8 && operations < remainingOps && Date.now() - started < priorLimit; scrollStep++) {
        const beforeHash = snapshots.at(-1)?.dom_hash;
        await page.evaluate(() => window.scrollTo({top: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight), behavior: 'instant'}));
        operations++;
        await wait(Math.max(400, Math.min(1200, interval)));
        const cap = await collectSnapshot(page, bodies.get(documentKey(page.url())), task, dir, snapshots.length, {hosts: allowedHosts, status: statuses, ajax: xhr, initialItemCount});
        const sig = cap.snapshot.dom_hash;
        snapshots.push({...cap.snapshot, stage: 'scroll', scroll_step: scrollStep + 1, item_count_before: snapshots[0].item_count_after,
          item_count_after: cap.snapshot.item_count});
        if (sig !== beforeHash) stateChanged = true;
        if (sig === beforeHash && scrollStep >= 1) break;
      }
      const initialMeta = snapshots[0];
      const maxClicks = Math.min(6, remainingOps - operations);
      for (let click = 0; !blocked && click < maxClicks && Date.now() - started < priorLimit; click++) {
        const controls = await page.evaluate(() => [...document.querySelectorAll('button,a[rel],a[aria-label],a[href],input[type="button"],[role="button"]')]
          .filter(el => { const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0; })
          .map((el, index) => ({index, text: (el.innerText || el.textContent || '').trim(), aria: el.getAttribute('aria-label') || '',
            rel: el.getAttribute('rel') || '', title: el.getAttribute('title') || '', value: el.value || '', dataAction: el.getAttribute('data-action') || '',
            tag: el.tagName.toLowerCase(), href: el.href || '', disabled: !!el.disabled, type: el.type || ''})));
        const candidate = controls.map(c => ({...c, score: controlScore(c, method)})).filter(c => c.score >= 0 && !c.disabled && c.type !== 'submit')
          .sort((a, b) => b.score - a.score || a.index - b.index)[0];
        if (!candidate) break;
        if (candidate.href && !inHosts(candidate.href, allowedHosts)) { failures.push({kind: 'out_of_scope_control', text: candidate.text}); break; }
        if (candidate.href && /(?:cart|checkout|order|account|login|register|signup|contact|submit|purchase|buy)/i.test(new URL(candidate.href).pathname)) break;
        const before = snapshots.at(-1).dom_hash;
        try {
          await page.locator('button,a[rel],a[aria-label],a[href],input[type="button"],[role="button"]').nth(candidate.index).click({timeout: 8000});
        } catch (error) {
          failures.push({kind: 'click_error', message: String(error.message).slice(0, 160)});
          break;
        }
        operations++;
        await page.waitForLoadState('domcontentloaded', {timeout: 10000}).catch(() => {});
        await wait(Math.max(500, Math.min(1500, interval)));
        const next = await collectSnapshot(page, bodies.get(documentKey(page.url())), task, dir, snapshots.length,
          {hosts: allowedHosts, status: statuses, ajax: xhr, initialItemCount});
        snapshots.push({...next.snapshot, stage: 'control', control: {text: candidate.text, tag: candidate.tag, href: candidate.href || null},
          item_count_before: initialItemCount, item_count_after: next.snapshot.item_count});
        if (next.snapshot.dom_hash !== before || next.snapshot.item_count > initialItemCount || page.url() !== actionCountsBefore.url) stateChanged = true;
        const spec = `${task.expected_state} ${(task.dom_checks || []).join(' ')}`.toLowerCase();
        if (/load more|追加読み込み|ページ下部|スクロール/.test(spec) && next.snapshot.item_count > initialItemCount) break;
      }
      const last = snapshots.at(-1) || initialMeta;
      const diff = last.dom_hash !== initialHash;
      const features = stateFeatures({...last, dom_hash: last.dom_hash, item_count_before: initialItemCount,
        item_count_after: last.item_count_after, ajax_or_fetch_requests: xhr, action_count: operations}, task, initialCapture.rawText);
      const strictFrame = features.category === 'frame' && last.frame_nonempty_documents > 0;
      const strictShadow = features.category === 'shadow' && last.shadow_item_count > 0 && last.shadow_text_length > 0;
      const strictAjax = features.category === 'ajax' && xhr > 0 && last.item_count_after > 0;
      const actual = !blocked && !robotsDenied && (features.passed || strictFrame || strictShadow || strictAjax);
      if (robotsDenied) { status = 'robots_disallowed'; stopReason = 'blocked'; }
      else if (['block', 'login'].includes(features.reason)) { status = features.reason; stopReason = 'blocked'; }
      else if (blocked) { status = 'blocked'; stopReason = 'blocked'; }
      else if (actual) {
        status = 'completed';
        stopReason = documentRequests >= remainingDocs || operations >= remainingOps || Date.now() - started >= priorLimit ? 'budget' : 'content_changed';
      }
      else if (snapshots.length && !stateChanged && !diff) stopReason = 'content_unchanged';
      else if (documentRequests >= remainingDocs || operations >= remainingOps || Date.now() - started >= priorLimit) stopReason = 'budget';
      else stopReason = 'content_unchanged';
      const output = {...base, status, task_success: actual, success_reason: features.reason, success_category: features.category,
        initial_state_id: `sha256:${initialHash}`, final_state_id: `sha256:${last.dom_hash}`, dom_changed: diff,
        item_count_before: initialItemCount, item_count_after: last.item_count_after,
        response_html_diff: initialCapture.rawText ? initialCapture.snapshot.rendered_only_text : null,
        frame_documents: last.frame_documents, frame_nonempty_documents: last.frame_nonempty_documents,
        shadow_hosts: last.shadow_host_count, shadow_items: last.shadow_item_count,
        elapsed_ms: Date.now() - started, browser_seconds: (Date.now() - started) / 1000,
        requests, document_requests: Math.max(documentRequests, documentReservations), document_reservations: documentReservations,
        document_responses: documentResponses, xhr_fetch_requests: xhr,
        transferred_bytes: bytes, operations, state_changes: snapshots.filter((x, i) => i > 0 && x.dom_hash !== snapshots[i - 1].dom_hash).length,
        robots, network, pages: snapshots, failures, stop_reason: stopReason,
      cumulative_document_requests: mainDocs + challengeDocs + Math.max(documentRequests, documentReservations),
        cumulative_operations: mainOps + challengeOps + operations,
        cumulative_elapsed_ms: mainTime + challengeTime + (Date.now() - started)};
      return output;
    }
  } catch (error) {
    status = robotsDenied ? 'robots_disallowed' : 'error'; stopReason = robotsDenied ? 'blocked' : 'error'; failures.push({kind: error.name || 'Error', message: String(error.message).slice(0, 240)});
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  return {...base, status, task_success: false, stop_reason: stopReason, robots, requests,
    document_requests: Math.max(documentRequests, documentReservations), document_reservations: documentReservations,
    document_responses: documentResponses, xhr_fetch_requests: xhr, transferred_bytes: bytes, operations,
    elapsed_ms: Date.now() - started, browser_seconds: (Date.now() - started) / 1000, failures,
    cumulative_document_requests: mainDocs + challengeDocs + Math.max(documentRequests, documentReservations),
    cumulative_operations: mainOps + challengeOps + operations,
    cumulative_elapsed_ms: mainTime + challengeTime + (Date.now() - started)};
}

async function runAll({runDir, taskFile, mainResults, taskIds, siteIds, methods}) {
  const registry = JSON.parse(fs.readFileSync(taskFile, 'utf8'));
  const targets = new Map(readJSONL(path.join(runDir, 'targets.jsonl')).map(target => [String(target.id), target]));
  const selectedTasks = registry.tasks.filter(task => (!taskIds.size || taskIds.has(task.id)) && (!siteIds.size || siteIds.has(String(task.site_id))));
  const resultPath = path.join(runDir, 'challenge-results.jsonl');
  let results = readJSONL(resultPath).filter(row => row.execution_origin !== 'accidental_preflight_flag_parse');
  const mainRows = readJSONL(mainResults);
  for (const task of selectedTasks) {
    const target = targets.get(String(task.site_id));
    if (!target) {
      for (const method of methods) {
        const result = {site_id: task.site_id, task_id: task.id, method, start_url: task.start_url, status: 'unavailable', task_success: false,
          stop_reason: 'target_not_registered', elapsed_ms: 0, requests: 0, document_requests: 0, operations: 0, pages: []};
        appendJSONL(resultPath, result); results.push(result);
      }
      continue;
    }
    for (const method of methods) {
      const result = await runTask(task, target, method, runDir, mainRows, results);
      if (result.status === 'already_recorded') continue;
      appendJSONL(resultPath, result); results.push(result);
    }
  }
  return results;
}

async function selfTest() {
  let externalFrameURL = '';
  const frameServer = http.createServer((req, res) => {
    if (req.url === '/frame-start') { res.writeHead(302, {location: '/frame'}); return res.end(); }
    res.setHeader('content-type', 'text/html; charset=utf-8');
    res.end('<html><body><main><h1>External frame content</h1><a href="/detail/frame">Frame link</a></main></body></html>');
  });
  await new Promise(resolve => frameServer.listen(0, '127.0.0.1', resolve));
  externalFrameURL = `http://localhost:${frameServer.address().port}/frame-start`;
  const server = http.createServer((req, res) => {
    if (req.url === '/robots.txt') { res.setHeader('content-type', 'text/plain'); return res.end('User-agent: *\nAllow: /'); }
    if (req.url === '/redirect-data') { res.writeHead(302, {location: '/data'}); return res.end(); }
    if (req.url === '/data.json') { res.setHeader('content-type', 'application/json'); return res.end(JSON.stringify({label: 'Fetched dynamic record'})); }
    if (req.url === '/data') return res.end(`<html><body><main><ul id="items"><li><a href="/detail/1">First item</a></li></ul><button type="button" id="more">Load more</button><div id="host"></div><script>document.querySelector("#more").addEventListener("click",()=>document.querySelector("#items").insertAdjacentHTML("beforeend","<li><a href=/detail/2>Second item</a></li>"));const h=document.querySelector("#host");const s=h.attachShadow({mode:"open"});s.innerHTML="<article><a href=/detail/3>Shadow item</a></article>";fetch('/data.json').then(r=>r.json()).then(d=>document.querySelector("#items").insertAdjacentHTML("beforeend",'<li><a href=/detail/5>'+d.label+'</a></li>'));</script></main><iframe src="${externalFrameURL}"></iframe></body></html>`);
    if (req.url === '/scroll') return res.end('<html><body><main><ul id="items"><li><a href="/detail/1">First item</a></li></ul><div style="height:2000px"></div><script>let done=false;window.addEventListener("scroll",()=>{if(!done&&window.scrollY>0){done=true;document.querySelector("#items").insertAdjacentHTML("beforeend","<li><a href=/detail/6>Scrolled record</a></li>")}})</script></main></body></html>');
    if (req.url.startsWith('/detail/')) return res.end(`<html><body><main><h1>Detail</h1><p>${'Content '.repeat(20)}</p></main></body></html>`);
    res.statusCode = 404; return res.end('<html><body><h1>not found</h1></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const runDir = fs.mkdtempSync(path.join(os.tmpdir(), 'structure-challenge-selftest-'));
  const port = server.address().port;
  fs.writeFileSync(path.join(runDir, 'targets.jsonl'), JSON.stringify({id: 'fixture', domain: 'localhost', start_url: `http://127.0.0.1:${port}/data`, allowed_hosts: ['127.0.0.1'], practice: true, tasks: []}) + '\n');
  try {
    for (const method of ['B1', 'C1']) {
      const target = {id: 'fixture', domain: 'localhost', allowed_hosts: ['127.0.0.1'], practice: true};
      const fixtureRows = [];
      const taskDefs = [
        {id: 'fixture-incremental', url: '/redirect-data', expected_state: 'load more reveals additional items', dom_checks: ['追加読み込み後に項目数が増加'], kind: 'incremental'},
        {id: 'fixture-ajax', url: '/data', expected_state: 'Ajax results loaded', dom_checks: ['Ajax取得後の表の行がDOMに描画される'], kind: 'ajax'},
        {id: 'fixture-scroll', url: '/scroll', expected_state: 'scroll loads additional items', dom_checks: ['スクロール後に追加項目数が増加'], kind: 'scroll'},
      ];
      for (const definition of taskDefs) {
        const task = {...definition, site_id: 'fixture', start_url: `http://127.0.0.1:${port}${definition.url}`};
        const result = await runTask(task, target, method, runDir, [], fixtureRows);
        fixtureRows.push(result);
        if (!result.task_success || result.cumulative_document_requests > 40 || result.cumulative_operations > 100)
          throw new Error(`self_test_failed:${method}:${definition.kind}:${result.status}:${result.stop_reason}:${result.success_reason}:${result.item_count_before}->${result.item_count_after}:ops=${result.operations}`);
        if (definition.kind === 'ajax' && result.xhr_fetch_requests < 1) throw new Error(`self_test_ajax_not_counted:${method}`);
        if (definition.kind === 'scroll' && result.item_count_after <= result.item_count_before) throw new Error(`self_test_scroll_no_growth:${method}`);
        if (definition.kind === 'incremental') {
          const frame = result.pages.flatMap(x => x.frame_urls || []).find(x => new URL(x.url).port === String(frameServer.address().port));
          if (result.shadow_items < 1 || result.frame_nonempty_documents < 1 || !frame?.saved || !result.pages[0].rendered_only_text)
            throw new Error(`self_test_rendered_frame_shadow:${method}:${result.shadow_items}:${result.frame_nonempty_documents}:${frame?.saved}:${result.pages[0].rendered_only_text}`);
        }
      }
    }
    return {status: 'pass', methods: ['B1', 'C1'], assertions: ['redirect_accounting', 'load_more_increment', 'scroll_growth', 'ajax_rows',
      'rendered_dom_diff', 'open_shadow_dom', 'external_origin_frame_dom', 'cumulative_budget']};
  } finally {
    await new Promise(resolve => server.close(resolve));
    await new Promise(resolve => frameServer.close(resolve));
  }
}

async function main() {
  if (args['self-test'] === 'true') { process.stdout.write(JSON.stringify(await selfTest()) + '\n'); return; }
  if (args.execute !== 'true') throw new Error('public_execution_requires_--execute');
  if (!args.run) throw new Error('public_execution_requires_--run');
  const runDir = path.resolve(args.run || runDefault);
  const taskFile = path.resolve(args['task-file'] || path.join(runDir, 'challenge_tasks.json'));
  const mainResults = path.resolve(args['main-results'] || path.join(runDir, 'results.jsonl'));
  const taskIds = new Set(String(args.tasks || '').split(',').filter(Boolean));
  const siteIds = new Set(String(args.sites || '').split(',').filter(Boolean));
  const methods = String(args.methods || 'B1,C1').split(',').filter(Boolean);
  if (methods.some(method => !['B1', 'C1'].includes(method))) throw new Error('methods_must_be_B1_C1');
  if (!fs.existsSync(mainResults) || !readJSONL(mainResults).length) throw new Error('main_results_required_before_challenge_execution');
  const results = await runAll({runDir, taskFile, mainResults, taskIds, siteIds, methods});
  const selected = results.filter(row => (!taskIds.size || taskIds.has(row.task_id)) && (!siteIds.size || siteIds.has(String(row.site_id))));
  process.stdout.write(JSON.stringify({result_file: path.join(runDir, 'challenge-results.jsonl'), selected: selected.length,
    successes: selected.filter(row => row.task_success).length, incomplete: selected.filter(row => !row.task_success).length}) + '\n');
}

export {collectSnapshot, controlScore, runTask, stateFeatures};
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
