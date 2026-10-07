import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {Evidence, verify, safeError} from './evidence.mjs';
import {snapshotSources, sleep, proxy, sha, repo} from './runtime.mjs';
import {openFourplayNative} from './fourplay-native-runtime.mjs';
import {openBrowser} from './runner.mjs';
import {openCamoufox} from './camoufox-runtime.mjs';
import {openCamoufoxFourplay, buildCamoufoxLaunchOptions} from './camoufox-fourplay-runtime.mjs';
import {extractJoshinProductPage} from './joshin-product-extraction.mjs';

const [directory, mode, waitArgument = '6000'] = process.argv.slice(2);
const settleMs = Number(waitArgument);
if (!directory || !['standard', 'assembled', 'camoufox', 'assembled-ja', 'assembled-ja-fixture', 'camoufox-ja', 'camoufox-ja-fixture', 'patchright-ja', 'patchright-en', 'patchright-ja-fixture', 'patchright-en-fixture'].includes(mode) || !Number.isSafeInteger(settleMs) || settleMs < 0 || settleMs > 30000)
  throw new Error('Usage: joshin-product-search.mjs NEW_DIRECTORY standard|assembled|camoufox|assembled-ja|assembled-ja-fixture|camoufox-ja|camoufox-ja-fixture|patchright-ja|patchright-en|patchright-ja-fixture|patchright-en-fixture [HOME_SETTLE_MS=6000]');
const evidence = new Evidence(path.resolve(directory), false, {
  policy: 'browser_observation', flush_interval_ms: 500,
  authorization: 'User requested Joshin グローブ browser search comparisons without a proxy and a lightweight Patchright run. The operator bounded the Patchright comparison to three Japanese pages and one English page.',
});
const query = 'グローブ';
const fixture = mode === 'assembled-ja-fixture' || mode === 'camoufox-ja-fixture' || mode === 'patchright-ja-fixture' || mode === 'patchright-en-fixture';
const patchrightMode=mode.startsWith('patchright-');
const patchrightLocale=mode.includes('-en')?'en-US':'ja-JP';
const playwrightBrowserMode=patchrightMode||mode==='camoufox-ja'||mode==='camoufox-ja-fixture';
const requestedPageLimit=patchrightMode?(patchrightLocale==='ja-JP'?3:1):null;
const camoufoxPlaywrightMode = mode === 'camoufox-ja' || mode === 'camoufox-ja-fixture';
const regionalMode = mode === 'assembled-ja' || mode === 'assembled-ja-fixture' || camoufoxPlaywrightMode;
const camoufoxDependencies = path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS || path.join(repo,'.deps/camoufox'));
const camoufoxRequire = createRequire(path.join(camoufoxDependencies,'package.json'));
if(patchrightMode&&proxy) throw new Error('patchright_joshin_run_requires_direct_transport');
const fixtureURL = process.env.BOT_DIAGNOSTICS_JOSHIN_FIXTURE_URL || null;
if (fixture && !fixtureURL) throw new Error('BOT_DIAGNOSTICS_JOSHIN_FIXTURE_URL_required');
const homepageURL = fixture ? fixtureURL : 'https://joshinweb.jp/top.html';
if (fixture) {
  const fixtureOrigin = new URL(homepageURL);
  if (fixtureOrigin.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(fixtureOrigin.hostname))
    throw new Error('joshin_fixture_must_use_loopback_http');
}
const key = `${mode}/joshin/glove`;
const expectedOrigin = new URL(homepageURL).origin;
let regionalConfig = null, regionalCondition = null;
if (regionalMode) {
  if (proxy) throw new Error('regional_joshin_run_requires_direct_transport');
  const fingerprintSource = process.env.BOT_DIAGNOSTICS_INPUT_FINGERPRINT_FROM;
  if (!fingerprintSource) throw new Error('BOT_DIAGNOSTICS_INPUT_FINGERPRINT_FROM_required');
  const baseConfig = await buildCamoufoxLaunchOptions();
  const saved = JSON.parse(fs.readFileSync(path.join(path.resolve(fingerprintSource), 'fingerprint.json'), 'utf8'));
  const chunks = Object.entries(saved).filter(([name]) => /^CAMOU_CONFIG_\d+$/.test(name))
    .sort(([a], [b]) => Number(a.slice(13)) - Number(b.slice(13)));
  if (!chunks.length) throw new Error('saved_camoufox_fingerprint_missing');
  const original = JSON.parse(chunks.map(([, value]) => value).join(''));
  const originalHash = sha(JSON.stringify(original));
  const fingerprint = {...original, timezone:'Asia/Tokyo', 'locale:language':'ja', 'locale:region':'JP'};
  const generatedEnv = {...baseConfig.generated.env};
  for (const name of Object.keys(generatedEnv)) if (/^CAMOU_CONFIG_\d+$/.test(name)) delete generatedEnv[name];
  generatedEnv.CAMOU_CONFIG_1 = JSON.stringify(fingerprint);
  const fonts = Array.isArray(fingerprint.fonts) ? fingerprint.fonts : [];
  regionalConfig = {...baseConfig, generated:{...baseConfig.generated, env:generatedEnv}, metadata:{...baseConfig.metadata,
    config_sha256:sha(JSON.stringify(fingerprint)), font_count:fonts.length, font_list_sha256:sha(JSON.stringify(fonts))}};
  regionalCondition = {original_config_sha256:originalHash, applied_keys:{timezone:'Asia/Tokyo','locale:language':'ja','locale:region':'JP'},
    fingerprint_reused_from:path.resolve(fingerprintSource), config_sha256:regionalConfig.metadata.config_sha256};
  evidence.save('fingerprint.json', {CAMOU_CONFIG_1:generatedEnv.CAMOU_CONFIG_1});
  evidence.save('camoufox-launch.json', regionalConfig.metadata);
}
evidence.save('sources.json', snapshotSources(evidence));
evidence.save('conditions.json', {
  source_commit: process.env.BOT_DIAGNOSTICS_SOURCE_COMMIT || null,
  started_at: new Date().toISOString(), mode, query, homepage_url: homepageURL, node: process.version,
  homepage_settle_ms: settleMs, browser_control: patchrightMode?'Patchright + Playwright':camoufoxPlaywrightMode || mode === 'camoufox' ? 'Camoufox + Playwright'
    : mode === 'assembled' || regionalMode ? 'Camoufox + native 4play WebExtension'
      : 'native Firefox + 4play WebExtension',
  profile: 'fresh per run; same cookie session and tab retained for search submission',
  fingerprint_sha256:regionalConfig?.metadata.config_sha256 ?? null, regional_condition:regionalCondition,
  patchright_condition:patchrightMode?{locale:patchrightLocale,timezone:'Asia/Tokyo',context_source:'Patchright browser.newContext options',service_workers:'allow',tls_verification:'enabled',camoufox_fingerprint_applied:false,requested_page_limit:requestedPageLimit}:null,
  submission: 'fill observed #suggest_input then DOM click on the observed changeSubmit search anchor',
  input_events_trusted: mode === 'camoufox' || playwrightBrowserMode ? null : false, pointer_events_used: false, wheel_used: false,
  submission_control:playwrightBrowserMode?'Playwright locator.fill plus observed anchor DOM click':'native 4play API with observed anchor DOM click',
  search_click_is_trusted:false,tab_container_observation:playwrightBrowserMode?'Playwright request metadata does not expose tab_id or container; values remain null':'native 4play request metadata',
  wait: 'actual HTTP main-document response plus complete changed document; 30s timeout',
  network: fixture ? 'loopback fixture; TLS verification enabled' : proxy ? 'inherited HTTPS proxy; TLS verification enabled' : 'direct; TLS verification enabled',
  fixture, fixture_route:fixture ? (playwrightBrowserMode ? 'Playwright context route allows same-origin loopback HTTP and aborts external requests' : 'loopback HTTP; adapter fixture isolation enabled') : null,
  proxy_configured: Boolean(proxy), tls_verification: 'enabled',
  proxy_environment_present: Object.fromEntries(['HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy','ALL_PROXY','all_proxy']
    .map(name => [name, Boolean(process.env[name])])),
  capture_scope: 'main-document request/response bodies, request header allowlist, DOM and extraction observations; subresources load normally but are not archived',
  limitations: [mode === 'camoufox' || playwrightBrowserMode ? 'Playwright exposes response headers; tab_id and container are not exposed by request metadata'
      : 'response headers unavailable from 4play',
    'native DOM fill/click does not establish equivalence to trusted physical user input',
    'Observed product cards, page fields, and pager anchors are recorded per document; page advancement requires a new document response, PAGE_NO increment, and displayed-range increment'],
});
const requestRecords = new WeakMap(), records = [], documents = [], pending = new Set();
const activeTopFrameRequests = new WeakSet();
let fixtureExternalAborts = 0;
let requestSequence = 0;
let browser, stage = 'homepage';
const productRows = [];
const measurement = {mode, query, homepage: null, search_pages: [], successful_result_pages: 0,
  pagination_clicks: 0, pagination_actions:[], pagination_complete:false, requested_page_limit:requestedPageLimit, extracted_products:null, total_product_occurrences:null,
  unique_product_urls:null, duplicate_product_urls:null, product_names:null, stop_reason:null};

function reserve(request) {
  const existing = requestRecords.get(request);
  if (existing) return existing;
  const record = evidence.reserve(key, request.url(), 'document');
  Object.assign(record, {stage, method: request.method(), resource_type: request.resourceType(),
    request_sequence: ++requestSequence, tab_id:playwrightBrowserMode?null:request.tabId,
    container:playwrightBrowserMode?null:request.container,
    ...(playwrightBrowserMode?{frame_is_active_top_level:activeTopFrameRequests.has(request)}:{})});
  const headers = Object.fromEntries(Object.entries(request.headers?.() || {}).map(([name,value]) => [name.toLowerCase(),value]));
  const allowed = new Set(['user-agent','accept','accept-language','accept-encoding','referer','origin',
    'sec-fetch-site','sec-fetch-mode','sec-fetch-dest','sec-fetch-user','upgrade-insecure-requests']);
  record.request_headers = Object.fromEntries(Object.entries(headers).filter(([name]) => allowed.has(name)));
  record.cookie_names = String(headers.cookie || '').split(';').map(part => part.trim().split('=')[0]).filter(Boolean);
  requestRecords.set(request, record);
  records.push(record);
  return record;
}

function isTopLevelNavigation(request) {
  if (!request.isNavigationRequest()) return false;
  const frame = request.frame?.();
  if (playwrightBrowserMode) {
    const activeTopLevel = Boolean(frame && browser?.page && frame === browser.page.mainFrame());
    if (activeTopLevel) activeTopFrameRequests.add(request);
    return activeTopLevel;
  }
  return !frame || typeof frame.parentFrame !== 'function' || frame.parentFrame() === null;
}

async function snapshot(name) {
  const observation = await browser.page.evaluate(includeEnvironment => ({url:location.href, title:document.title,
    readyState:document.readyState, ua:navigator.userAgent, webdriver:navigator.webdriver,
    cookie_names:document.cookie.split(';').map(part => part.trim().split('=')[0]).filter(Boolean),
    text_prefix:(document.body?.innerText || '').slice(0,1500),
    search_input_count:document.querySelectorAll('#suggest_input').length,
    ...(includeEnvironment ? {environment:{navigator_language:navigator.language,navigator_languages:[...navigator.languages],
      intl_locale:Intl.DateTimeFormat().resolvedOptions().locale,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,
      timezone_offset_minutes:{now:new Date().getTimezoneOffset(),jan_1_2026:new Date('2026-01-01T12:00:00').getTimezoneOffset(),
        jul_1_2026:new Date('2026-07-01T12:00:00').getTimezoneOffset()}},document_time_origin:performance.timeOrigin} : {})}),playwrightBrowserMode);
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
    } catch {}
    await sleep(150);
  }
  throw new Error('search_navigation_response_and_complete_document_timeout');
}

async function waitForNewDocument(previousDocuments, previousTimeOrigin) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const state = await browser.page.evaluate(includeTimeOrigin => ({url:location.href, ready:document.readyState,
        timeOrigin:includeTimeOrigin?performance.timeOrigin:null}),playwrightBrowserMode);
      const response = documents.slice(previousDocuments).find(item => item.url === state.url);
      const newDocument=!playwrightBrowserMode||state.timeOrigin!==previousTimeOrigin;
      if (state.ready === 'complete' && newDocument && response) return response;
    } catch {}
    await sleep(150);
  }
  return null;
}

function challengeDocument(page) {
  return /captcha|challenge|access denied|don't have permission|アクセスが拒否|ロボットであることを確認/i
    .test(`${page.title} ${page.text_prefix || ''}`);
}

async function recordResultPage(attempt, expectedPage) {
  const page = await snapshot(`search-page-${attempt}`);
  const documentRecord=documents.filter(item=>item.url===page.url).at(-1);
  const row = {...page,document_observed_at:documentRecord?.observed_at??null,attempted_page:attempt,expected_page:expectedPage,
    navigation_scope_valid:false,extracted_count:null,extraction_validated:false,extraction_error:null,next_available:null};
  const actualURL=new URL(page.url);
  if (actualURL.origin!==expectedOrigin || actualURL.pathname!=='/srhzs.html') {
    row.extraction_error='navigation_scope_changed';
    measurement.search_pages.push(row);
    return {page,row,extraction:null,valid:false,scopeChanged:true};
  }
  row.navigation_scope_valid=true;
  if (page.http_status !== 200) {
    measurement.search_pages.push(row);
    return {page,row,extraction:null,valid:false};
  }
  if (challengeDocument(page)) {
    row.challenge_detected = true;
    row.extraction_error = 'challenge_document';
    measurement.search_pages.push(row);
    return {page,row,extraction:null,valid:false,challenge:true};
  }
  const extraction = await browser.page.evaluate(extractJoshinProductPage,query);
  evidence.save(`extraction-page-${attempt}.json`,extraction);
  const valid = extraction && Number.isSafeInteger(extraction.count) && extraction.count > 0
    && Array.isArray(extraction.products) && extraction.products.length === extraction.count
    && extraction.products.every(product=>typeof product?.name==='string'&&product.name.trim()
      &&typeof product.url==='string'&&product.url)
    && extraction.extraction_error === null
    && extraction.current_page === expectedPage
    && Number.isSafeInteger(extraction.max_page) && extraction.max_page >= extraction.current_page
    && Number.isSafeInteger(extraction.displayed_total) && extraction.displayed_total > 0
    && extraction.displayed_range && extraction.displayed_range.end-extraction.displayed_range.start+1===extraction.count;
  Object.assign(row,{page_no:extraction?.page_no??null,current_page:extraction?.current_page??null,
    max_page:extraction?.max_page??null,displayed_total:extraction?.displayed_total??null,
    displayed_range:extraction?.displayed_range??null,extracted_count:extraction?.count??null,
    extraction_validated:Boolean(valid),extraction_error:extraction?extraction.extraction_error:'extraction_failed',
    next_available:extraction?.next_available??false,next_anchor:extraction?.next_anchor??null});
  measurement.search_pages.push(row);
  if (valid) {
    measurement.successful_result_pages++;
    for (const product of extraction.products) productRows.push({page:extraction.current_page,product_name:product.name,
      product_url:product.url,price_text:product.price_text,product_code_candidate:product.product_code_candidate});
  }
  return {page,row,extraction,valid};
}

try {
  browser = mode === 'standard' ? await openFourplayNative({profile:'joshin-glove'})
    : mode === 'camoufox' ? await openCamoufox({profile:'joshin-glove',headful:true})
      : patchrightMode ? await openBrowser('patchright',{fixture,headful:true,profile:'browser_observation',timezoneId:'Asia/Tokyo'})
        : camoufoxPlaywrightMode ? await openCamoufox({fixture,profile:'joshin-glove',headful:true,
        loadDependencies:async()=>({firefox:camoufoxRequire('playwright-core').firefox,
          launchOptions:async()=>structuredClone(regionalConfig.generated)})})
        : regionalMode ? await openCamoufoxFourplay({fixture,profile:'joshin-glove',headful:true,
          loadLaunchOptions:async()=>async()=>structuredClone(regionalConfig.generated)})
          : await openCamoufoxFourplay({profile:'joshin-glove',headful:true});
  if(patchrightMode) {
    await browser.context.close();
    browser.context=await browser.browser.newContext({userAgent:browser.ua,locale:patchrightLocale,timezoneId:'Asia/Tokyo',
      serviceWorkers:'allow',ignoreHTTPSErrors:false});
    browser.page=await browser.context.newPage();
    browser.runtime={...browser.runtime,locale:patchrightLocale,timezoneId:'Asia/Tokyo',service_workers:'allow',tls_verification:'enabled',camoufox_fingerprint_applied:false};
  }
  evidence.save('runtime.json', browser.runtime);
  if(fixture && playwrightBrowserMode) await browser.context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin===expectedOrigin&&url.protocol==='http:'&&['127.0.0.1','localhost'].includes(url.hostname)) await route.continue();
    else {fixtureExternalAborts++;await route.abort();}
  });
  browser.context.on('request', request => { if(isTopLevelNavigation(request)) reserve(request); });
  browser.context.on('response', response => {
    if(!isTopLevelNavigation(response.request())) return;
    documents.push({url:response.url(),status:response.status(),stage,observed_at:new Date().toISOString()});
    const record = reserve(response.request());
    const task = (async () => {
      try { evidence.finish(record,response.status(),response.headers(),await response.body()); }
      catch(error) { evidence.fail(record,error); }
    })();
    pending.add(task); task.finally(() => pending.delete(task));
  });
  browser.context.on('requestfailed', request => {
    if(playwrightBrowserMode ? isTopLevelNavigation(request) : request.isNavigationRequest())
      evidence.fail(reserve(request),new Error(request.failure()?.errorText || 'document_request_failed'));
  });
  await browser.page.goto(homepageURL,{timeout:30000});
  await sleep(settleMs);
  measurement.homepage = await snapshot('homepage');
  if(playwrightBrowserMode) {
    const environment=measurement.homepage.environment;
    const expectedLocale=patchrightMode?patchrightLocale:'ja-JP';
    measurement.regional_environment_valid=environment?.navigator_language===expectedLocale&&environment?.navigator_languages?.[0]===expectedLocale&&environment?.intl_locale===expectedLocale
      &&environment?.timezone==='Asia/Tokyo'&&environment?.timezone_offset_minutes?.now===-540
      &&environment?.timezone_offset_minutes?.jan_1_2026===-540&&environment?.timezone_offset_minutes?.jul_1_2026===-540;
  }
  if(measurement.homepage.http_status !== 200 || measurement.homepage.search_input_count !== 1) {
    measurement.stop_reason = 'homepage_unavailable';
  } else if(playwrightBrowserMode&&!measurement.regional_environment_valid) {
    measurement.stop_reason='regional_environment_mismatch';
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
    const observedAction = new URL(control.form_action, homepageURL);
    const actionValid = fixture
      ? observedAction.origin === new URL(homepageURL).origin && observedAction.pathname === '/srhzs.html'
      : observedAction.href === 'https://joshinweb.jp/srhzs.html';
    if(control.icon_count !== 1 || control.input_value !== query || !actionValid)
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
    let result = await recordResultPage(1,1);
    if (result.page.http_status === 403) measurement.stop_reason = 'http_403_on_first_search_page';
    else if (result.page.http_status === 429) measurement.stop_reason = 'http_429_on_first_search_page';
    else if (result.scopeChanged) measurement.stop_reason = 'navigation_scope_changed';
    else if (result.page.http_status !== 200) measurement.stop_reason = `search_http_${result.page.http_status ?? 'unknown'}`;
    else if (result.challenge) measurement.stop_reason = 'challenge_on_first_search_page';
    else if (!result.valid) measurement.stop_reason = 'result_layout_requires_inspection';
    else if (result.extraction.displayed_range.start !== 1) measurement.stop_reason='first_page_range_did_not_start_at_one';
    else {
      const reportedTotal = result.extraction.displayed_total;
      measurement.catalog_total_reported = reportedTotal;
      measurement.max_page = result.extraction.max_page;
      let current = result.extraction;
      while (true) {
        if (current.current_page === current.max_page) {
          if (productRows.length === reportedTotal) {
            measurement.pagination_complete = true;
            measurement.stop_reason = 'last_page_reached_and_reported_total_matched';
          } else measurement.stop_reason = 'last_page_reached_but_reported_total_mismatched';
          break;
        }
        if(patchrightMode&&current.current_page>=requestedPageLimit) {
          measurement.stop_reason='requested_page_limit_reached';
          break;
        }
        const next = current.next_anchor;
        if (!current.next_available || !next || next.target_page !== current.current_page + 1) {
          measurement.stop_reason = 'next_pager_anchor_unavailable_before_max_page';
          break;
        }
        const beforePage = documents.length;
        const clickRecord = {from_page:current.current_page,to_page:next.target_page,selector:next.selector,
          container_index:next.container_index,anchor_index:next.anchor_index,raw_href:next.raw_href,text:next.text,
          kind:next.kind,clicked_at:new Date().toISOString(),click_action:'observed_anchor_element.click'};
        measurement.pagination_actions.push(clickRecord);
        stage = `search-page-${next.target_page}`;
        let clicked = false;
        try {
          clicked = await browser.page.evaluate(({selector,containerIndex,anchorIndex,rawHref,text,expectedOrigin})=>{
            const clean=value=>(value||'').replace(/\s+/g,' ').trim();
            const forms=document.querySelectorAll('form[name="itemlistform"]');
            if(forms.length!==1) return {clicked:false,reason:'search_form_not_unique'};
            const form=forms[0],action=new URL(form.action,location.href);
            if(form.method.toUpperCase()!=='GET'||action.origin!==expectedOrigin||action.pathname!=='/srhzs.html'||action.username||action.password)
              return {clicked:false,reason:'search_form_scope_mismatch'};
            const container=document.querySelectorAll(selector)[containerIndex];
            const anchor=container?.querySelectorAll('a[href]')[anchorIndex];
            if(!anchor||anchor.getAttribute('href')!==rawHref||clean(anchor.innerText||anchor.textContent)!==text)
              return {clicked:false,reason:'pager_anchor_changed'};
            const destination=new URL(anchor.href);
            if(destination.origin!==expectedOrigin||destination.username||destination.password)
              return {clicked:false,reason:'pager_anchor_scope_mismatch'};
            anchor.click();
            return {clicked:true,reason:null};
          },{selector:next.selector,containerIndex:next.container_index,anchorIndex:next.anchor_index,rawHref:next.raw_href,text:next.text,expectedOrigin});
        } catch(error) { clickRecord.click_error=safeError(error); }
        const clickOutcome=clicked;
        clicked=Boolean(clickOutcome?.clicked);
        clickRecord.scope_check=clickOutcome?.reason??(clicked?'passed':'evaluation_failed');
        clickRecord.click_confirmed=clicked;
        if (!clicked) { measurement.stop_reason='pagination_scope_validation_failed'; break; }
        measurement.pagination_clicks++;
        const navigation=await waitForNewDocument(beforePage,result.page.document_time_origin);
        clickRecord.navigation_status=navigation?.status??null;
        clickRecord.navigation_url=navigation?.url??null;
        if (!navigation) {
          measurement.stop_reason = clicked ? 'pagination_document_not_observed' : 'pager_anchor_changed_before_click';
          break;
        }
        result = await recordResultPage(measurement.search_pages.length+1,next.target_page);
        if (result.page.http_status === 403) { measurement.stop_reason=`http_403_on_page_${next.target_page}`; break; }
        if (result.scopeChanged) { measurement.stop_reason='navigation_scope_changed'; break; }
        if (result.page.http_status === 429) { measurement.stop_reason=`http_429_on_page_${next.target_page}`; break; }
        if (result.page.http_status !== 200) { measurement.stop_reason=`search_http_${result.page.http_status??'unknown'}_on_page_${next.target_page}`; break; }
        if (result.challenge) { measurement.stop_reason=`challenge_on_page_${next.target_page}`; break; }
        if (result.extraction?.current_page === clickRecord.from_page) { measurement.stop_reason='page_number_did_not_advance'; break; }
        if (result.extraction?.current_page !== next.target_page) { measurement.stop_reason=`page_number_mismatch_on_page_${next.target_page}`; break; }
        if (!result.valid) { measurement.stop_reason=`result_layout_invalid_on_page_${next.target_page}`; break; }
        if (result.extraction.displayed_range.start !== current.displayed_range.end + 1) {
          measurement.stop_reason='displayed_range_did_not_advance';
          break;
        }
        if (result.extraction.displayed_total !== reportedTotal || result.extraction.max_page !== measurement.max_page) {
          measurement.stop_reason='pagination_metadata_changed';
          break;
        }
        current = result.extraction;
      }
    }
  }
} catch(error) {
  measurement.error = safeError(error);
  measurement.stop_reason ||= 'experiment_error';
} finally {
  await Promise.allSettled([...pending]);
  await browser?.close?.().catch(error => {measurement.close_error = safeError(error);});
  for(const record of records) if(record.status === 'interrupted') evidence.fail(record,new Error('document_request_unfinished_at_close'));
  measurement.documents = documents;
  if(fixture&&playwrightBrowserMode) evidence.save('fixture-network.json',{allowed_origin:expectedOrigin,allowed_protocol:'http:',allowed_hosts:['127.0.0.1','localhost'],external_requests_aborted:fixtureExternalAborts});
  if (measurement.successful_result_pages > 0) {
    const counts=new Map();for(const row of productRows)counts.set(row.product_url,(counts.get(row.product_url)||0)+1);
    measurement.extracted_products=productRows.length;
    measurement.total_product_occurrences=productRows.length;
    measurement.unique_product_urls=counts.size;
    measurement.duplicate_product_urls=[...counts].filter(([,count])=>count>1).map(([url,count])=>({url,count}));
    measurement.product_names=productRows.map(row=>row.product_name);
  }
  measurement.finished_at = new Date().toISOString();
  evidence.save('product-names.json',productRows);
  fs.writeFileSync(path.join(evidence.directory,'product-names.csv'),['page,product_name,product_url,price_text,product_code_candidate',...productRows.map(row =>
    [row.page,row.product_name,row.product_url,row.price_text,row.product_code_candidate].map(value => `"${String(value ?? '').replaceAll('"','""')}"`).join(','))].join('\n')+'\n');
  evidence.flush(true);
  measurement.verification = verify(evidence.directory);
  evidence.save('measurement.json',measurement);
  console.log(JSON.stringify({mode,homepage_status:measurement.homepage?.http_status,
    search_status:measurement.search_pages[0]?.http_status,successful_result_pages:measurement.successful_result_pages,
    total_product_occurrences:measurement.total_product_occurrences,unique_product_urls:measurement.unique_product_urls,
    pagination_complete:measurement.pagination_complete,stop_reason:measurement.stop_reason,verify:measurement.verification.ok,error:measurement.error}));
  if(measurement.error || measurement.close_error || !measurement.verification.ok) process.exitCode = 1;
}
