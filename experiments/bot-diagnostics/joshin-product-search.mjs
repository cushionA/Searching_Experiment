// Compare the real Joshin search submission with an unchanged native 4play controller.
// A successful but unfamiliar result layout is saved for inspection, never counted
// as zero results or the last page. Product/pager selectors need a real result DOM.
import fs from 'node:fs';
import path from 'node:path';
import {Evidence, verify, safeError} from './evidence.mjs';
import {snapshotSources, sleep} from './runtime.mjs';
import {openFourplayNative} from './fourplay-native-runtime.mjs';
import {openCamoufoxFourplay} from './camoufox-fourplay-runtime.mjs';
import {extractJoshinProductPage} from './joshin-product-extraction.mjs';

const [directory, mode, waitArgument = '6000'] = process.argv.slice(2);
const settleMs = Number(waitArgument);
if (!directory || !['standard', 'assembled'].includes(mode) || !Number.isSafeInteger(settleMs) || settleMs < 0 || settleMs > 30000)
  throw new Error('Usage: joshin-product-search.mjs NEW_DIRECTORY standard|assembled [HOME_SETTLE_MS=6000]');
const evidence = new Evidence(path.resolve(directory), false, {
  policy: 'browser_observation', flush_interval_ms: 500,
  authorization: 'User requested ordinary 4play and assembled Camoufox + 4play Joshin グローブ search, product-name extraction, and pagination until blocked or finished.',
});
const query = 'グローブ';
const homepageURL = 'https://joshinweb.jp/top.html';
const key = `${mode}/joshin/glove`;
evidence.save('sources.json', snapshotSources(evidence));
evidence.save('conditions.json', {
  source_commit: process.env.BOT_DIAGNOSTICS_SOURCE_COMMIT || null,
  started_at: new Date().toISOString(), mode, query, homepage_url: homepageURL, node: process.version,
  homepage_settle_ms: settleMs, browser_control: 'native 4play WebExtension for both arms',
  profile: 'fresh per run; same container and tab retained for search submission',
  submission: 'fill observed #suggest_input then DOM click on the observed changeSubmit search anchor',
  input_events_trusted: false, pointer_events_used: false, wheel_used: false,
  wait: 'actual HTTP main-document response plus complete changed document; 30s timeout',
  network: 'inherited managed Cloud proxy and CA; TLS verification enabled',
  capture_scope: 'main-document request/response bodies, request header allowlist, DOM and extraction observations; subresources load normally but are not archived',
  limitations: ['response headers unavailable from 4play', 'configured proxy does not establish equal egress IP',
    'native DOM fill/click does not establish equivalence to trusted physical user input',
    'product and pagination selectors remain unvalidated until a successful result DOM is available'],
});
const records = new Map(), documents = [], pending = new Set();
let browser, stage = 'homepage';
const measurement = {mode, query, homepage: null, search_pages: [], successful_result_pages: 0,
  pagination_clicks: 0, extracted_products: 0, unique_product_urls: 0, product_names: [], stop_reason: null};

function reserve(request) {
  const existing = records.get(request.sequence);
  if (existing) return existing;
  const record = evidence.reserve(key, request.url(), 'document');
  Object.assign(record, {stage, method: request.method(), resource_type: request.resourceType(),
    request_sequence: request.sequence, tab_id: request.tabId, container: request.container});
  const headers = Object.fromEntries(Object.entries(request.headers?.() || {}).map(([name,value]) => [name.toLowerCase(),value]));
  const allowed = new Set(['user-agent','accept','accept-language','accept-encoding','referer','origin',
    'sec-fetch-site','sec-fetch-mode','sec-fetch-dest','sec-fetch-user','upgrade-insecure-requests']);
  record.request_headers = Object.fromEntries(Object.entries(headers).filter(([name]) => allowed.has(name)));
  record.cookie_names = String(headers.cookie || '').split(';').map(part => part.trim().split('=')[0]).filter(Boolean);
  records.set(request.sequence, record);
  return record;
}

async function snapshot(name) {
  const observation = await browser.page.evaluate(() => ({url:location.href, title:document.title,
    readyState:document.readyState, ua:navigator.userAgent, webdriver:navigator.webdriver,
    cookie_names:document.cookie.split(';').map(part => part.trim().split('=')[0]).filter(Boolean),
    text_prefix:(document.body?.innerText || '').slice(0,1500),
    search_input_count:document.querySelectorAll('#suggest_input').length}));
  const dom = await browser.page.content();
  const row = {...observation, http_status:documents.filter(item => item.url === observation.url).at(-1)?.status ?? null,
    dom_sha256:evidence.blob(Buffer.from(dom)), documents_seen:documents.length};
  evidence.save(`${name}.json`, row);
  evidence.result({client:mode,target:'joshin-glove',stage:name,...row});
  return row;
}

async function waitForSearch(previousURL, previousDocuments) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const state = await browser.page.evaluate(() => ({url:location.href, ready:document.readyState}));
      if (state.url !== previousURL && state.ready === 'complete'
          && documents.slice(previousDocuments).some(item => item.url === state.url)) return;
    } catch {} // The old document may unload during the click injection.
    await sleep(150);
  }
  throw new Error('search_navigation_response_and_complete_document_timeout');
}

try {
  browser = mode === 'assembled' ? await openCamoufoxFourplay({profile:'joshin-glove'})
    : await openFourplayNative({profile:'joshin-glove'});
  evidence.save('runtime.json', browser.runtime);
  browser.context.on('request', request => { if(request.isNavigationRequest()) reserve(request); });
  browser.context.on('response', response => {
    if(!response.request().isNavigationRequest()) return;
    documents.push({url:response.url(),status:response.status(),stage,observed_at:new Date().toISOString()});
    const record = reserve(response.request());
    const task = (async () => {
      try { evidence.finish(record,response.status(),response.headers(),await response.body()); }
      catch(error) { evidence.fail(record,error); }
    })();
    pending.add(task); task.finally(() => pending.delete(task));
  });
  browser.context.on('requestfailed', request => {
    if(request.isNavigationRequest()) evidence.fail(reserve(request),new Error(request.failure()?.errorText || 'document_request_failed'));
  });
  await browser.page.goto(homepageURL,{timeout:30000});
  await sleep(settleMs);
  measurement.homepage = await snapshot('homepage');
  if(measurement.homepage.http_status !== 200 || measurement.homepage.search_input_count !== 1) {
    measurement.stop_reason = 'homepage_unavailable';
  } else {
    await browser.page.locator('#suggest_input').fill(query);
    const control = await browser.page.evaluate(() => {
      const input = document.querySelector('#suggest_input'), form = input.form;
      const anchors = [...form.querySelectorAll('a[href]')].filter(a => a.getAttribute('href').includes('changeSubmit'));
      return {input_value:input.value,input_name:input.name,form_action:form.action,form_method:form.method,
        accept_charset:form.acceptCharset,document_charset:document.characterSet,icon_count:anchors.length,
        icon_href:anchors[0]?.getAttribute('href') ?? null};
    });
    evidence.save('submission.json',control);
    if(control.icon_count !== 1 || control.input_value !== query || control.form_action !== 'https://joshinweb.jp/srhzs.html')
      throw new Error('search_control_not_uniquely_confirmed');
    stage = 'search-page-1';
    const before = documents.length;
    try {
      await browser.page.evaluate(() => {
        const form = document.querySelector('#suggest_input').form;
        [...form.querySelectorAll('a[href]')].find(a => a.getAttribute('href').includes('changeSubmit')).click();
        return true;
      });
    } catch(error) { evidence.save('submission-injection-error.json',safeError(error)); }
    await waitForSearch(measurement.homepage.url,before);
    const page = await snapshot('search-page-1');
    const extraction = await browser.page.evaluate(extractJoshinProductPage,query);
    evidence.save('extraction-page-1.json',extraction);
    measurement.search_pages.push({...page,attempted_page:1,extracted_count:extraction.count,
      extraction_error:extraction.extraction_error,next_available:extraction.next_available});
    if(page.http_status === 403) measurement.stop_reason = 'http_403_on_first_search_page';
    else if(page.http_status !== 200) measurement.stop_reason = `search_http_${page.http_status ?? 'unknown'}`;
    else {
      measurement.stop_reason = 'result_layout_requires_inspection';
      measurement.successful_result_pages = null;
      measurement.extracted_products = null;
      measurement.unique_product_urls = null;
    }
    // No next page is fabricated from an unavailable or unrecognized result DOM.
  }
} catch(error) {
  measurement.error = safeError(error);
  measurement.stop_reason ||= 'experiment_error';
} finally {
  await Promise.allSettled([...pending]);
  await browser?.close?.().catch(error => {measurement.close_error = safeError(error);});
  for(const record of records.values()) if(record.status === 'interrupted') evidence.fail(record,new Error('document_request_unfinished_at_close'));
  measurement.documents = documents;
  measurement.finished_at = new Date().toISOString();
  evidence.save('product-names.json',[]);
  fs.writeFileSync(path.join(evidence.directory,'product-names.csv'),'page,product_name,product_url\n');
  evidence.flush(true);
  measurement.verification = verify(evidence.directory);
  evidence.save('measurement.json',measurement);
  console.log(JSON.stringify({mode,homepage_status:measurement.homepage?.http_status,
    search_status:measurement.search_pages[0]?.http_status,successful_result_pages:measurement.successful_result_pages,
    product_names:measurement.extracted_products,stop_reason:measurement.stop_reason,verify:measurement.verification.ok,error:measurement.error}));
  if(measurement.error || measurement.close_error || !measurement.verification.ok) process.exitCode = 1;
}
