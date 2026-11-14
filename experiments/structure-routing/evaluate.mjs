import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const runDir = path.join(root, 'lab-runs/structure-routing-20261115');
const argv = process.argv.slice(2);
const version = argv.find(arg => arg.startsWith('--version='))?.split('=')[1] || 'v1';
if (!['v1', 'v2'].includes(version)) throw new Error(`unknown version: ${version}`);
const goldDir = path.join(runDir, version === 'v2' ? 'gold-private-v2' : 'gold-private');
const resultsFile = path.join(runDir, version === 'v2' ? 'evaluator-results-v2.jsonl' : 'evaluator-results.jsonl');
const protocolBytes = await fs.readFile(path.join(runDir, 'protocol.json'));
const protocolHash = createHash('sha256').update(protocolBytes).digest('hex');
const evaluatorBytes = await fs.readFile(fileURLToPath(import.meta.url));
const evaluatorHash = createHash('sha256').update(evaluatorBytes).digest('hex');
const localModules = path.join(root, '.deps/bot-diagnostics/node_modules');
const localRequire = createRequire(path.join(localModules, 'package.json'));
const { chromium } = localRequire('playwright');
const robotsParser = localRequire('robots-parser');
const chromePath = 'C:/Program Files/Google/Chrome/Application/chrome.exe';

const targets = (await fs.readFile(path.join(runDir, 'targets.jsonl'), 'utf8'))
  .trim().split(/\r?\n/).map(line => JSON.parse(line));
const idArg = argv.find(arg => arg.startsWith('--ids='));
const selectedIds = idArg ? new Set(idArg.slice(6).split(',')) : null;
let selected = targets.filter(target => !selectedIds || selectedIds.has(target.id));
const jobsArg = Number(argv.find(arg => arg.startsWith('--jobs='))?.split('=')[1] ?? 4);
const maxParallel = Math.min(4, Math.max(1, Number.isInteger(jobsArg) ? jobsArg : 4));

function hostAllowed(url, hosts) {
  try { return hosts.includes(new URL(url).hostname.toLowerCase()); }
  catch { return false; }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function compactName(value) {
  return value.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 120) || 'document';
}

async function mkdir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function saveGold(record) {
  await fs.writeFile(path.join(goldDir, record.id, 'result.json'), `${JSON.stringify(record, null, 2)}\n`, 'utf8');
}

async function captureDocument(page, url, label, dir, counters, startedAt) {
  if (!hostAllowed(url, counters.allowed_hosts)) throw new Error('navigation_host_not_allowed');
  const browserStart = Date.now();
  const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  if (!response) throw new Error('navigation_without_response');
  const source = await response.body();
  const rendered = await page.content();
  const dom = await page.evaluate(() => {
    const text = (document.body?.innerText ?? '').replace(/\s+/g, ' ').trim();
    const selectors = ['main', 'article', 'ul li', 'ol li', 'table tr', '[class*="product"]', '[class*="card"]'];
    const counts = Object.fromEntries(selectors.map(selector => [selector, document.querySelectorAll(selector).length]));
    const links = [...document.querySelectorAll('a[href]')].map(a => {
      const forbidden = a.closest('header,nav,footer,aside,[role="navigation"],[role="banner"],[role="contentinfo"],.navbar,.footer');
      const main = a.closest('main,article,[role="main"]');
      const repeatItem = a.closest('li,tr,[class*="card" i],[class*="product" i],[class*="item" i]');
      const repeated = repeatItem && repeatItem.parentElement
        ? [...repeatItem.parentElement.children].filter(node => node.tagName === repeatItem.tagName && (node.className === repeatItem.className || !repeatItem.className)).length >= 2
        : false;
      const container = main || (repeated ? repeatItem : null);
      const heading = container?.querySelector('h1,h2,h3,h4,[itemprop="name"]')?.innerText || '';
      const imageAlt = a.querySelector('img[alt]')?.getAttribute('alt') || '';
      const label = (a.innerText || a.getAttribute('aria-label') || imageAlt || heading).replace(/\s+/g, ' ').trim().slice(0, 240);
      return {
        href: a.href,
        text: label,
        context: (container?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 360),
        semantic_area: Boolean(container) && !forbidden,
        container_type: container?.tagName.toLowerCase() || '',
        in_repeated_area: Boolean(repeated)
      };
    }).filter(link => (link.text || link.context) && link.semantic_area);
    const title = document.title || '';
    const headings = [...document.querySelectorAll('h1,h2,h3')].map(node => (node.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
    const mainText = [...document.querySelectorAll('main,article,[role="main"]')].map(node => (node.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
    const productMarkers = document.querySelectorAll('[itemprop="price"],[class*="price" i],[class*="product" i],[id*="product" i],meta[property="product:price:amount"]').length;
    const captchaFrame = [...document.querySelectorAll('iframe')].some(frame => /captcha|recaptcha|hcaptcha/i.test(`${frame.src} ${frame.title}`));
    const passwordForm = Boolean(document.querySelector('input[type="password"]'));
    return { url: location.href, title, headings, body_text: text.slice(0, 20000), body_text_length: text.length, main_text: mainText.slice(0, 4), selector_counts: counts, product_markers: productMarkers, captcha_frame: captchaFrame, password_form: passwordForm, links };
  });
  counters.browser_ms += Date.now() - browserStart;
  await fs.writeFile(path.join(dir, `${label}.source.html`), source);
  await fs.writeFile(path.join(dir, `${label}.rendered.html`), rendered, 'utf8');
  counters.documents.push({
    label,
    requested_url: url,
    final_url: dom.url,
    status: response.status(),
    title: dom.title,
    headings: dom.headings,
    main_text: dom.main_text,
    body_text_length: dom.body_text_length,
    selector_counts: dom.selector_counts,
    product_markers: dom.product_markers,
    source_bytes: source.byteLength
  });
  return { response, dom };
}

function chooseLinks(entry, taskText, allowedHosts, startUrl) {
  const taskTokens = taskText.toLocaleLowerCase('ja-JP').split(/[\s、。・/]+/).filter(token => token.length > 1);
  const start = new URL(startUrl);
  const startSegments = start.pathname.split('/').filter(Boolean);
  const sourcePrefix = start.pathname.endsWith('/') ? start.pathname : startSegments.length > 1 ? `/${startSegments.slice(0, -1).join('/')}/` : '';
  const utilityPath = /\/(api(?:[_-][^/]*)?|faq|privacy|terms|about(?:-us)?|contact|login|logout|account|cart|basket|checkout)(?:\/|$)/i;
  const taskVocabulary = /商品|製品|本/.test(taskText) ? ['product', 'item', 'book', 'price', 'catalogue', 'catalog']
    : /記事|論文|ニュース|書籍|出版物/.test(taskText) ? ['article', 'paper', 'news', 'publication', 'journal']
      : /文書|ガイド|章|節|トピック/.test(taskText) ? ['docs', 'guide', 'documentation', 'tutorial', 'chapter'] : [];
  const seen = new Set();
  return entry.links.map(link => {
    let candidate;
    try { candidate = new URL(link.href); } catch { return null; }
    if (!['http:', 'https:'].includes(candidate.protocol) || !allowedHosts.includes(candidate.hostname.toLowerCase())) return null;
    if (start.pathname !== '/' && candidate.pathname === '/') return null;
    if (utilityPath.test(candidate.pathname)) return null;
    const normalized = candidate.href;
    if (seen.has(normalized) || normalized === start.href) return null;
    seen.add(normalized);
    if (/\b(login|log in|sign in|cart|basket|checkout|account|register)\b|ログイン|カート|会員登録|チェックアウト/i.test(`${link.text} ${candidate.pathname}`)) return null;
    const searchText = `${link.text} ${link.context} ${candidate.pathname}`.toLocaleLowerCase('ja-JP');
    const taskHits = [...taskTokens, ...taskVocabulary].reduce((sum, token) => sum + Number(searchText.includes(token)), 0);
    const structuralScore = link.in_repeated_area ? 8 : 4;
    const prefixScore = sourcePrefix && candidate.pathname.startsWith(sourcePrefix) ? 8 : 0;
    const pathNovelty = candidate.pathname !== start.pathname ? 1 : 0;
    return { ...link, href: normalized, score: taskHits * 4 + structuralScore + prefixScore + pathNovelty };
  }).filter(Boolean).sort((a, b) => b.score - a.score || a.href.localeCompare(b.href)).slice(0, 2);
}

async function runTarget(target) {
  const dir = path.join(goldDir, target.id);
  await mkdir(dir);
  const startedAt = Date.now();
  let robots = null;
  const taskText = target.tasks.join(' ');
  const expectedType = /商品|製品/.test(taskText) ? 'product_detail' : /記事|論文|文書|章|出版物|トピック|書籍|作品|項目|レコード|調査|報告|資料|指標|施設|観光地|物件/.test(taskText) ? 'article_or_detail_record' : 'detail_record';
  const record = {
    id: target.id,
    domain: target.domain,
    started_at: new Date().toISOString(),
    protocol: 'lab-runs/structure-routing-20261115/protocol.json',
    protocol_sha256: protocolHash,
    evaluator: 'experiments/structure-routing/evaluate.mjs',
    evaluator_sha256: evaluatorHash,
    selection_evidence: [],
    documents: [],
    expected_type: expectedType,
    requests: { count: 0, bytes: 0, statuses: {}, document_attempts: 0, document_responses: 0, xhr_fetch: 0, external_frame_hosts: [] },
    network_events: [],
    browser_ms: 0,
    gold_status: 'unverified'
  };
  record.allowed_hosts = target.allowed_hosts;
  const context = await browser.newContext({
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
    viewport: { width: 1365, height: 900 },
    serviceWorkers: 'block',
    ignoreHTTPSErrors: false
  });
  const page = await context.newPage();
  const client = await context.newCDPSession(page);
  await client.send('Network.setCacheDisabled', { cacheDisabled: true });
  await client.send('Network.enable');
  await page.route('**/*', route => {
    const request = route.request();
    if (request.isNavigationRequest() && request.frame() === page.mainFrame() && !hostAllowed(request.url(), target.allowed_hosts)) return route.abort();
    return route.continue();
  });
  page.on('request', request => {
    record.requests.count += 1;
    const atMs = Date.now() - startedAt;
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
      record.requests.document_attempts += 1;
      record.network_events.push({ kind: 'document_attempt', at_ms: atMs, url: request.url() });
    }
    if (['xhr', 'fetch'].includes(request.resourceType())) {
      record.requests.xhr_fetch += 1;
      record.network_events.push({ kind: request.resourceType(), at_ms: atMs, url: request.url() });
    }
  });
  client.on('Network.loadingFinished', event => {
    record.requests.bytes += Math.max(0, Math.round(event.encodedDataLength || 0));
  });
  page.on('response', response => {
    const status = String(response.status());
    record.requests.statuses[status] = (record.requests.statuses[status] || 0) + 1;
    const request = response.request();
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
      record.requests.document_responses += 1;
      record.network_events.push({ kind: 'document_response', at_ms: Date.now() - startedAt, status: response.status(), url: response.url() });
    }
  });
  page.on('framenavigated', frame => {
    if (frame === page.mainFrame()) return;
    try {
      const host = new URL(frame.url()).hostname;
      if (!target.allowed_hosts.includes(host) && !record.requests.external_frame_hosts.includes(host)) record.requests.external_frame_hosts.push(host);
    } catch { }
  });
  try {
    const robotsUrl = new URL('/robots.txt', target.start_url).href;
    const robotsResponse = await context.request.get(robotsUrl, { timeout: 20000 }).catch(() => null);
    record.robots = { url: robotsUrl, status: robotsResponse?.status() ?? null, bytes: 0, allowed: true };
    if (robotsResponse) {
      const body = await robotsResponse.body();
      record.robots.bytes = body.byteLength;
      record.requests.count += 1;
      record.requests.bytes += body.byteLength;
      robots = robotsParser(robotsUrl, body.toString('utf8'));
      record.robots.allowed = robots.isAllowed(target.start_url, '*') !== false;
      if ([403, 429].includes(robotsResponse.status())) record.robots.allowed = false;
    }
    if (!record.robots.allowed) {
      record.gold_status = 'blocked_robots';
      record.block_reason = 'robots_disallows_entry';
      return record;
    }
    await sleep(target.practice ? 1000 : 5000);
    record.allowed_hosts = target.allowed_hosts;
    const entry = await captureDocument(page, target.start_url, 'entry', dir, record, startedAt);
    if ([429, 403].includes(entry.response.status()) || blockedPage(entry.dom)) {
      record.gold_status = 'blocked';
      record.block_reason = [429, 403].includes(entry.response.status()) ? `entry_http_${entry.response.status()}` : 'entry_challenge_page';
      return record;
    }
    await saveGold(record);
    const links = chooseLinks(entry.dom, taskText, target.allowed_hosts, target.start_url);
    record.selection_evidence = links.map((link, index) => ({ rank: index + 1, href: link.href, text: link.text, context: link.context, score: link.score, container_type: link.container_type, in_repeated_area: link.in_repeated_area }));
    await saveGold(record);
    for (let index = 0; index < links.length; index += 1) {
      const link = links[index];
      if (robots?.isAllowed(link.href, '*') === false) {
        record.documents.push({ label: `target-${index + 1}`, requested_url: link.href, error: 'robots_disallowed' });
        record.gold_status = 'blocked_robots';
        record.block_reason = 'robots_disallows_target';
        await saveGold(record);
        break;
      }
      await sleep(target.practice ? 1000 : 5000);
      try {
        const targetDoc = await captureDocument(page, link.href, `target-${index + 1}-${compactName(new URL(link.href).pathname)}`, dir, record, startedAt);
        const status = targetDoc.response.status();
        const selectorCounts = targetDoc.dom.selector_counts;
        const structureCount = ['main', 'article', 'ul li', 'ol li', 'table tr', '[class*="product"]', '[class*="card"]']
          .reduce((sum, selector) => sum + selectorCounts[selector], 0);
        const label = link.text.trim();
        const searchableText = normalizeText(`${targetDoc.dom.title} ${targetDoc.dom.body_text}`);
        const labelFound = normalizeText(label).length >= 3 && searchableText.includes(normalizeText(label));
        const meaningfulHeading = targetDoc.dom.headings.some(text => text.trim().length >= 4 && !/^(home|products|product|search|results|category|login|sign in|ログイン|商品一覧)$/i.test(text.trim()));
        const typeMarker = expectedType !== 'product_detail' || (targetDoc.dom.product_markers > 0 && selectorCounts['[class*="product"]'] <= 4);
        const valid = status === 200 && targetDoc.dom.title.trim().length > 0 && targetDoc.dom.body_text_length > 40 && structureCount > 0 && typeMarker && (labelFound || meaningfulHeading) && !blockedPage(targetDoc.dom);
        record.documents.at(-1).candidate_valid = valid;
        record.documents.at(-1).structure_count = structureCount;
        record.documents.at(-1).validation = { expected_type: expectedType, title: Boolean(targetDoc.dom.title.trim()), body_text_length: targetDoc.dom.body_text_length, display_label: label, label_found: labelFound, meaningful_heading: meaningfulHeading, structural_signature_count: structureCount, type_marker: typeMarker };
        record.documents.at(-1).selection = record.selection_evidence[index];
        if ([429, 403].includes(status) || blockedPage(targetDoc.dom)) {
          record.gold_status = 'blocked';
          record.block_reason = [429, 403].includes(status) ? `target_http_${status}` : 'target_challenge_page';
          break;
        }
        await saveGold(record);
      } catch (error) {
        record.documents.push({ label: `target-${index + 1}`, requested_url: link.href, error: String(error) });
        await saveGold(record);
      }
    }
    const validTargets = record.documents.filter(document => document.label.startsWith('target-') && document.candidate_valid);
    if (record.gold_status !== 'blocked' && validTargets.length) record.gold_status = 'verified';
    record.finished_at = new Date().toISOString();
    return record;
  } catch (error) {
    record.error = String(error);
    record.finished_at = new Date().toISOString();
    return record;
  } finally {
    await context.close();
    record.elapsed_ms = Date.now() - startedAt;
    record.finished_at = new Date().toISOString();
    record.completed = true;
    await saveGold(record);
  }
}

function blockedPage(dom) {
  const titleAndHeadings = [dom.title || '', ...(dom.headings || []).slice(0, 2)];
  const challengeHeading = titleAndHeadings.some(text => /^(access denied|forbidden|request blocked|unusual traffic|verify you are human|captcha verification|just a moment|maintenance|log in|login|sign in|ログイン|アクセス拒否|メンテナンス中)$/i.test(text.trim()));
  return challengeHeading || dom.captcha_frame || (dom.password_form && titleAndHeadings.some(text => /log in|login|sign in|ログイン/i.test(text)));
}

function normalizeText(value) {
  return value.normalize('NFKC').toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
}

const browser = await chromium.launch({ headless: true, ...(await fs.access(chromePath).then(() => ({ executablePath: chromePath })).catch(() => ({}))) });
try {
  const completedIds = new Set();
  for (const target of selected) {
    try {
      const prior = JSON.parse(await fs.readFile(path.join(goldDir, target.id, 'result.json'), 'utf8'));
      if (prior.completed) completedIds.add(target.id);
    } catch { }
  }
  selected = selected.filter(target => !completedIds.has(target.id));
  let existingSummaryIds = new Set();
  try {
    existingSummaryIds = new Set((await fs.readFile(resultsFile, 'utf8')).trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line).id));
  } catch { }
  for (let offset = 0; offset < selected.length; offset += maxParallel) {
    const batch = selected.slice(offset, offset + maxParallel);
    const records = await Promise.all(batch.map(runTarget));
    for (const record of records) {
      const summary = { id: record.id, gold_status: record.gold_status, block_reason: record.block_reason || null, captured_documents: record.documents.filter(document => Number.isInteger(document.status)).length, document_attempts: record.requests.document_attempts, document_responses: record.requests.document_responses, xhr_fetch: record.requests.xhr_fetch, requests: record.requests.count, bytes: record.requests.bytes, elapsed_ms: record.elapsed_ms, browser_ms: record.browser_ms, protocol_sha256: protocolHash, evaluator_sha256: evaluatorHash };
      if (!existingSummaryIds.has(record.id)) {
        await fs.appendFile(resultsFile, `${JSON.stringify(summary)}\n`, 'utf8');
        existingSummaryIds.add(record.id);
      }
      console.log(JSON.stringify(summary));
    }
  }
} finally {
  await browser.close();
}
