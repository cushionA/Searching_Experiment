import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {repo, state, proxy} from './runtime.mjs';

const deps = path.resolve(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS || path.join(repo, '.deps/camoufox'));
const browserDirectory = path.join(deps, 'browser');
const executable = path.join(browserDirectory, 'camoufox-bin');
let camoufoxDependenciesPromise;

function loadCamoufoxDependencies() {
  if (camoufoxDependenciesPromise) return camoufoxDependenciesPromise;
  // Resolve both packages from the private root so the shared diagnostics
  // Playwright installation is not affected by Camoufox's peer dependency.
  const previousInstallDir = process.env.CAMOUFOX_INSTALL_DIR;
  const hadInstallDir = Object.hasOwn(process.env, 'CAMOUFOX_INSTALL_DIR');
  process.env.CAMOUFOX_INSTALL_DIR = browserDirectory;
  camoufoxDependenciesPromise = (async()=>{
    try {
      const localRequire = createRequire(path.join(deps, 'package.json'));
      const playwrightPath = localRequire.resolve('playwright-core');
      const {firefox} = localRequire(playwrightPath);
      const camoufoxPath = localRequire.resolve('camoufox-js');
      const module = await import(pathToFileURL(camoufoxPath).href);
      return {firefox, launchOptions:module.launchOptions};
    } finally {
      // camoufox-js snapshots INSTALL_DIR during module evaluation. Restore the
      // caller's environment immediately; the library keeps the private path.
      if (hadInstallDir) process.env.CAMOUFOX_INSTALL_DIR = previousInstallDir;
      else delete process.env.CAMOUFOX_INSTALL_DIR;
    }
  })().catch(error=>{
    camoufoxDependenciesPromise = undefined;
    throw error;
  });
  return camoufoxDependenciesPromise;
}

function runCertutil(args, runCommand) {
  try {
    runCommand('certutil', args, {stdio:'ignore'});
  } catch (error) {
    if (error?.code === 'ENOENT') {
      throw new Error('camoufox_ca_setup_failed: certutil is required to trust the configured proxy CA');
    }
    throw new Error(`camoufox_ca_setup_failed: certutil could not update the temporary Firefox trust store (${error?.status ?? error?.code ?? 'unknown'})`);
  }
}

function configureFirefoxTrust(profile, caFile, runCommand) {
  if (!caFile) return false;
  if (!fs.existsSync(caFile) || !fs.statSync(caFile).isFile()) {
    throw new Error('camoufox_ca_setup_failed: configured proxy CA file is unavailable');
  }
  runCertutil(['-N', '--empty-password', '-d', `sql:${profile}`], runCommand);

  const caBytes = fs.readFileSync(caFile);
  const text = caBytes.toString('utf8');
  const certificates = [...text.matchAll(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g)]
    .map(match => match[0] + '\n');
  const files = [];
  try {
    if (certificates.length) {
      certificates.forEach((certificate, index) => {
        const filename = path.join(profile, `.proxy-ca-${index + 1}.pem`);
        fs.writeFileSync(filename, certificate, {mode:0o600, flag:'wx'});
        files.push(filename);
      });
    } else {
      // certutil also accepts a single DER certificate.
      const filename = path.join(profile, '.proxy-ca.der');
      fs.writeFileSync(filename, caBytes, {mode:0o600, flag:'wx'});
      files.push(filename);
    }
    files.forEach((filename, index) => runCertutil([
      '-A', '-n', `bot-diagnostics-proxy-ca-${index + 1}`, '-t', 'C,,',
      '-d', `sql:${profile}`, '-i', filename,
    ], runCommand));
  } finally {
    for (const filename of files) fs.rmSync(filename, {force:true});
  }
  return true;
}

function actualViewport(page) {
  return page.evaluate(() => ({width:innerWidth, height:innerHeight}));
}

/** Open an isolated Camoufox Firefox session for passive browser diagnostics. */
export async function openCamoufox({
  extensions = [], fixture = false, headful = false, profile = 'diagnostic',
  timezoneId,
  loadDependencies = loadCamoufoxDependencies, runCommand = execFileSync,
} = {}) {
  if (extensions.length) throw new Error('unsupported_capability:extensions');
  if (headful && !(process.env.DISPLAY || process.env.WAYLAND_DISPLAY)) {
    throw new Error('headful_requires_display: set DISPLAY or WAYLAND_DISPLAY');
  }
  if (!fs.existsSync(executable) || !fs.statSync(executable).isFile()) {
    throw new Error(`camoufox_browser_missing: expected ${executable}; run the optional Camoufox setup`);
  }

  fs.mkdirSync(state, {recursive:true});
  const profileDirectory = fs.mkdtempSync(path.join(state, 'camoufox-profile-'));
  const cacheDirectory = path.join(profileDirectory, '.cache');
  fs.mkdirSync(cacheDirectory);
  // Playwright waits for Juggler's stdout readiness marker before it can set
  // Firefox user preferences over the protocol. Enable dump before startup.
  fs.writeFileSync(path.join(profileDirectory, 'user.js'), 'user_pref("browser.dom.window.dump.enabled", true);\n');
  let context;
  try {
    const caFile = process.env.BOT_DIAGNOSTICS_CA || (!fixture ? process.env.CODEX_PROXY_CERT : null) || null;
    const proxyCaTrusted = configureFirefoxTrust(profileDirectory, caFile, runCommand);
    const {firefox, launchOptions} = await loadDependencies();
    if (typeof launchOptions !== 'function' || !firefox?.launchPersistentContext) {
      throw new Error('camoufox_setup_invalid: camoufox-js launchOptions and isolated playwright-core Firefox are required');
    }

    const camoufoxOptions = await launchOptions({
      headless:!headful,
      os:'linux',
      humanize:false,
      geoip:false,
      exclude_addons:['UBO'],
      executable_path:executable,
      window:[1280, 720],
      ...(proxy ? {proxy:{server:proxy, bypass:'127.0.0.1,localhost'}} : {}),
      env:{...process.env, FONTCONFIG_PATH:path.join(browserDirectory, 'fontconfig', 'linux')},
    });
    context = await firefox.launchPersistentContext(profileDirectory, {
      ...camoufoxOptions,
      executablePath:executable,
      headless:!headful,
      env:{...process.env, ...(camoufoxOptions.env || {}), FONTCONFIG_PATH:path.join(browserDirectory, 'fontconfig', 'linux'),
        XDG_CACHE_HOME:cacheDirectory},
      ignoreHTTPSErrors:false,
      timeout:15000,
      viewport:null,
      screen:{width:1280,height:720},
      ...(timezoneId?{timezoneId}:{}),
      ...(proxy ? {proxy:{server:proxy, bypass:'127.0.0.1,localhost'}} : {}),
    });
    const page = context.pages()[0] || await context.newPage();
    const ua = await page.evaluate(() => navigator.userAgent);
    const viewport = await actualViewport(page);
    const browser = context.browser();
    let installMetadata = {};
    try { installMetadata = JSON.parse(fs.readFileSync(path.join(deps, 'browser-runtime.json'), 'utf8')); } catch { /* optional metadata */ }
    let browserMetadata = {};
    try { browserMetadata = JSON.parse(fs.readFileSync(path.join(browserDirectory, 'version.json'), 'utf8')); } catch { /* optional metadata */ }
    const version = installMetadata.browser_version || [browserMetadata.version, browserMetadata.release].filter(Boolean).join('-') || 'unknown';
    const camoufoxJsVersion = installMetadata.package?.match(/@([^@]+)$/)?.[1] || 'unknown';
    return {
      browser, context, page, ua, kind:'playwright',
      runtime:{engine:'firefox',version,camoufox_js:camoufoxJsVersion,
        playwright_core:installMetadata.playwright_core || 'unknown',executable,headless:!headful,
        display:headful?(process.env.DISPLAY||process.env.WAYLAND_DISPLAY):null,
        viewport,screenshots:true,budgeted_navigation:false,proxy_ca_trusted:proxyCaTrusted},
      close:async()=>{try { await context.close(); } finally { fs.rmSync(profileDirectory,{recursive:true,force:true}); }},
    };
  } catch (error) {
    try { await context?.close(); } finally { fs.rmSync(profileDirectory,{recursive:true,force:true}); }
    throw error;
  }
}
