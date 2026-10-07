// Passive, bounded comparison of the existing Joshin entry page and wait gates.
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {Evidence, verify, safeError} from './evidence.mjs';
import {ToolAdapter} from './adapters.mjs';
import {openBrowser} from './runner.mjs';
import {openCamoufox} from './camoufox-runtime.mjs';
import {openCamoufoxFourplay} from './camoufox-fourplay-runtime.mjs';
import {repo, snapshotSources, proxy, sha} from './runtime.mjs';

const [directory, mode = '--4play', seedDirectory, entryURL = 'https://joshinweb.jp/'] = process.argv.slice(2);
if (!directory || !['--4play', '--camoufox', '--hybrid'].includes(mode) || (mode === '--hybrid' && !seedDirectory))
  throw new Error('Usage: joshin-wait-diagnostics.mjs NEW_DIRECTORY --4play|--camoufox|--hybrid [CAMOUFOX_RUN_TO_SEED_HYBRID] [ENTRY_URL]');
if (!['https://joshinweb.jp/', 'https://joshinweb.jp/top.html'].includes(entryURL)) throw new Error('unsupported_joshin_entry_url');
const output = path.resolve(directory);
if (fs.existsSync(output)) throw new Error('output_directory_already_exists');
fs.mkdirSync(output, {recursive: true});
const write = (name, value) => fs.writeFileSync(path.join(output, name), JSON.stringify(value, null, 2) + '\n');
const tool = mode === '--4play' ? '4play' : mode === '--hybrid' ? 'camoufox-fourplay' : 'camoufox';
const arms = tool === '4play'
  ? [{id: 'load-default', wait: 'native-load-complete'},
    {id: 'load-selector-ready', wait: 'native-load-complete', readyCondition: {selector: '#suggest_input', timeoutMs: 5000}}]
  : tool === 'camoufox-fourplay' ? [{id: 'native-load', wait: 'native-load-complete'}]
    : [{id: 'domcontentloaded', wait: 'domcontentloaded'}, {id: 'load', wait: 'load'}];
const schedule = [...arms.map(arm => ({repeat: 1, arm})), ...[...arms].reverse().map(arm => ({repeat: 2, arm}))];
const rows = [];
write('conditions.json', {
  source_commit: process.env.BOT_DIAGNOSTICS_SOURCE_COMMIT || execFileSync('git', ['rev-parse', 'HEAD'], {cwd: repo, encoding: 'utf8'}).trim(),
  node: process.version, tool, schedule, observe_ms: 6000, url: entryURL,
  entry_url_override: entryURL !== 'https://joshinweb.jp/',
  source: 'experiments/bot-diagnostics/sites.json',
  authorization: 'User requested latest-main 4play blocking diagnostics focused on Joshin and the suspected wait difference.',
  policy: 'browser_observation; ordinary passive browser resource traffic; inherited managed proxy and TLS verification',
  navigation_limit: `${schedule.length} Joshin entry-page navigations; targets only after content and the observed search input are present; max 2 targets per cell`,
  no_automatic_retry: true,
  comparison_limits: ['4play bridge uses a fresh cookie container per cell in a shared browser; Camoufox variants use a fresh profile per cell', '4play uses Firefox ESR; Camoufox uses its own Firefox build',
    'same configured proxy does not establish the same egress IP', '4play response headers unavailable',
    '4play window.stop occurs after evidence capture; later snapshots do not test continued network activity'],
});

let camoufoxDependencies;
let hybridOptions;
if (tool === 'camoufox-fourplay') {
  const seed = path.resolve(seedDirectory);
  const fingerprint = JSON.parse(fs.readFileSync(path.join(seed, 'fingerprint.json')));
  const launch = JSON.parse(fs.readFileSync(path.join(seed, 'camoufox-launch.json')));
  if (sha(JSON.stringify(fingerprint)) !== launch.fingerprint_sha256) throw new Error('seed_fingerprint_hash_mismatch');
  const config = JSON.parse(Object.entries(fingerprint).sort(([a], [b]) => Number(a.slice(13)) - Number(b.slice(13))).map(([, value]) => value).join(''));
  const previousAddonPolicy = config.allowAddonNewtab ?? null;
  config.allowAddonNewtab = true;
  const nativeFingerprint = {CAMOU_CONFIG_1: JSON.stringify(config)};
  hybridOptions = {env: {...process.env, ...nativeFingerprint}, args: launch.args, firefoxUserPrefs: launch.firefox_user_prefs};
  write('fingerprint.json', nativeFingerprint);
  write('camoufox-launch.json', {seed, seed_fingerprint_sha256: launch.fingerprint_sha256,
    fingerprint_sha256: sha(JSON.stringify(nativeFingerprint)), args: launch.args, firefox_user_prefs: launch.firefox_user_prefs,
    fixed_within_run: true, overrides: {allowAddonNewtab: {previous: previousAddonPolicy, current: true}},
    comparison_limit: 'same saved fingerprint except required addon-tab permission; native 4play container and launch preferences also differ from Playwright'});
}
if (tool === 'camoufox') {
  const deps = path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS || path.join(repo, '.deps/camoufox'));
  const require = createRequire(path.join(deps, 'package.json'));
  const {firefox} = require('playwright-core');
  const had = Object.hasOwn(process.env, 'CAMOUFOX_INSTALL_DIR'), previous = process.env.CAMOUFOX_INSTALL_DIR;
  let launchOptions;
  try {
    process.env.CAMOUFOX_INSTALL_DIR = path.join(deps, 'browser');
    ({launchOptions} = await import(pathToFileURL(require.resolve('camoufox-js')).href));
  } finally {
    if (had) process.env.CAMOUFOX_INSTALL_DIR = previous;
    else delete process.env.CAMOUFOX_INSTALL_DIR;
  }
  let generated = await launchOptions({headless: false, os: 'linux', humanize: false, geoip: false,
    exclude_addons: ['UBO'], executable_path: path.join(deps, 'browser', 'camoufox-bin'), window: [1280, 720],
    ...(proxy ? {proxy: {server: proxy, bypass: '127.0.0.1,localhost'}} : {}),
    env: {...process.env, FONTCONFIG_PATH: path.join(deps, 'browser', 'fontconfig', 'linux')},
  });
  if (seedDirectory) {
    const seed = path.resolve(seedDirectory);
    const fingerprint = JSON.parse(fs.readFileSync(path.join(seed, 'fingerprint.json')));
    const launch = JSON.parse(fs.readFileSync(path.join(seed, 'camoufox-launch.json')));
    if (sha(JSON.stringify(fingerprint)) !== launch.fingerprint_sha256) throw new Error('seed_fingerprint_hash_mismatch');
    generated = {...generated, env: {...generated.env, ...fingerprint}, args: launch.args, firefoxUserPrefs: launch.firefox_user_prefs};
  }
  const fingerprint = Object.fromEntries(Object.entries(generated.env).filter(([key]) => /^CAMOU_CONFIG_\d+$/.test(key)));
  write('fingerprint.json', fingerprint);
  write('camoufox-launch.json', {fingerprint_sha256: sha(JSON.stringify(fingerprint)), args: generated.args,
    firefox_user_prefs: generated.firefoxUserPrefs, fixed_within_run: true, seed: seedDirectory ? path.resolve(seedDirectory) : null});
  camoufoxDependencies = {firefox, launchOptions: async () => structuredClone(generated)};
}

async function snapshot(adapter) {
  const expression = `(()=>({url:location.href,title:document.title,readyState:document.readyState,
    webdriver:navigator.webdriver,ua:navigator.userAgent,language:navigator.language,languages:Array.from(navigator.languages),
    timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,
    viewport:{width:innerWidth,height:innerHeight},screen:{width:screen.width,height:screen.height},
    cookie_names:document.cookie.split(';').map(x=>x.trim().split('=')[0]).filter(Boolean),
    search_input_count:document.querySelectorAll('#suggest_input').length,
    text_prefix:(document.body?.innerText||'').slice(0,1500),
    scripts:Array.from(document.scripts,s=>s.src).filter(Boolean),
    navigation:Array.from(performance.getEntriesByType('navigation'),x=>x.toJSON()),
    resource_count:performance.getEntriesByType('resource').length}))()`;
  if (adapter.tool !== '4play') return adapter.browser.page.evaluate(() => ({
    url: location.href, title: document.title, readyState: document.readyState,
    webdriver: navigator.webdriver, ua: navigator.userAgent, language: navigator.language, languages: Array.from(navigator.languages),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    viewport: {width: innerWidth, height: innerHeight}, screen: {width: screen.width, height: screen.height},
    cookie_names: document.cookie.split(';').map(x => x.trim().split('=')[0]).filter(Boolean),
    search_input_count: document.querySelectorAll('#suggest_input').length,
    text_prefix: (document.body?.innerText || '').slice(0, 1500),
    scripts: Array.from(document.scripts, s => s.src).filter(Boolean),
    navigation: Array.from(performance.getEntriesByType('navigation'), x => x.toJSON()),
    resource_count: performance.getEntriesByType('resource').length,
  }));
  const response = await fetch('http://127.0.0.1:3004/diagnostics/evaluate', {
    method: 'POST', headers: {'content-type': 'application/json', authorization: `Bearer ${adapter.browser.token}`},
    body: JSON.stringify({session_id: adapter.browser.sessionId, expression}), signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error('fourplay_snapshot_failed');
  return (await response.json()).result;
}

const originalSite = JSON.parse(fs.readFileSync(path.join(repo, 'experiments/bot-diagnostics/sites.json'))).sites.find(x => x.id === 'joshin');
for (const {repeat, arm} of schedule) {
  const evidence = new Evidence(path.join(output, `${repeat}-${arm.id}`), false, {
    policy: 'browser_observation', authorization: 'User-authorized Joshin wait-gate comparison; fresh independent cells, no challenge actions.',
  });
  evidence.save('sources.json', snapshotSources(evidence));
  evidence.save('scenario.json', {tool, site: originalSite, entry_url: entryURL, repeat, arm, observe_ms: 6000});
  const adapter = new ToolAdapter({tool, site: originalSite, evidence, profile: {id: arm.id, extensions: []},
    options: {observeMs: 6000, captureScreenshots: false, ...(arm.readyCondition ? {readyCondition: arm.readyCondition} : {})},
    browserOpener: async (name, options) => {
      const browser = name === 'camoufox-fourplay'
        ? await openCamoufoxFourplay({...options, loadLaunchOptions: async () => async () => structuredClone(hybridOptions)})
        : name === 'camoufox'
        ? await openCamoufox({...options, loadDependencies: async () => camoufoxDependencies})
        : await openBrowser(name, options);
      browser.context?.on('request', request => {
        if (!request.isNavigationRequest?.()) return;
        const headers = Object.fromEntries(Object.entries(request.headers?.() || {}).map(([key, value]) => [key.toLowerCase(), value]));
        const allowed = new Set(['user-agent', 'accept', 'accept-encoding', 'accept-language', 'sec-fetch-site', 'sec-fetch-mode',
          'sec-fetch-dest', 'sec-fetch-user', 'upgrade-insecure-requests', 'cache-control', 'pragma', 'connection', 'te', 'priority', 'referer', 'origin']);
        row.request_observations.push({url: request.url(), method: request.method(),
          headers: Object.fromEntries(Object.entries(headers).filter(([key]) => allowed.has(key))),
          cookie_names: String(headers.cookie || '').split(';').map(x => x.trim().split('=')[0]).filter(Boolean)});
      });
      if (name === 'camoufox') {
        const goto = browser.page.goto.bind(browser.page);
        browser.page.goto = (url, settings) => goto(url, {...settings, waitUntil: arm.wait});
      }
      return browser;
    }, clientOpener: async () => ({close: async () => {}}),
  });
  const row = {tool, repeat, arm: arm.id, started_at: new Date().toISOString(), targets: [], request_observations: []};
  try {
    const start = performance.now();
    await adapter.open();
    row.open_ms = performance.now() - start;
    row.runtime = adapter.browser.runtime;
    const navigationStart = performance.now();
    row.homepage = await adapter.homepage(entryURL);
    row.observation_ms = performance.now() - navigationStart;
    row.snapshot = await snapshot(adapter);
    if (row.homepage.outcome === 'content_observed' && row.snapshot.search_input_count > 0)
      for (const url of originalSite.links.targets) {
        const result = await adapter.followLink(url);
        row.targets.push(result);
        if (result.outcome !== 'content_observed') break;
      }
  } catch (error) {row.error = safeError(error);}
  finally {await adapter.close().catch(error => {row.close_error = safeError(error);});}
  evidence.flush(true);
  row.verification = verify(evidence.directory);
  evidence.save('measurement.json', row);
  rows.push(row);
  write('results.json', rows);
  console.log(JSON.stringify({tool, repeat, arm: arm.id, status: row.homepage?.http_status,
    outcome: row.homepage?.outcome, gate: row.homepage?.bridge_navigation,
    readiness: row.homepage?.ready_condition, search_inputs: row.snapshot?.search_input_count,
    verify: row.verification.ok, error: row.error}));
}
if (rows.some(row => row.error || row.close_error || !row.verification.ok)) process.exitCode = 1;
