import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

/** Shared paths and configuration. Importing this module does not start a client. */
export const here = path.dirname(fileURLToPath(import.meta.url));
export const repo = path.resolve(here, '../..');
export const deps = path.resolve(process.env.BOT_DIAGNOSTICS_DEPS || path.join(repo, '.deps/bot-diagnostics'));
export const state = path.resolve(process.env.BOT_DIAGNOSTICS_STATE || path.join(os.tmpdir(), 'bot-diagnostics-state'));
export const require = createRequire(path.join(deps, 'package.json'));
export const config = JSON.parse(fs.readFileSync(path.join(here, 'config.json')));
export const limits = config.limits;
export const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
export const TOOL_NAMES = ['wreq-js', 'impit', 'patchright', 'rebrowser-lightpanda', 'playwright-baseline',
  'obscura', 'obscura-stealth', 'obscura-no-render', 'obscura-patched', 'camoufox', '4play', 'camoufox-fourplay'];
export const HTTP_USER_AGENT = `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 ${config.identification}`;
export const local = url => ['127.0.0.1', 'localhost'].includes(new URL(url).hostname);
export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export const sha = value => crypto.createHash('sha256').update(value).digest('hex');

/** Include extracted implementation modules automatically in new run provenance. */
export function snapshotSources(evidence, roleSource = null) {
  const files = fs.readdirSync(here, {withFileTypes: true})
    .filter(entry => entry.isFile() && /\.(mjs|cjs|json|py)$/.test(entry.name) && !entry.name.endsWith('.test.mjs'))
    .map(entry => entry.name).sort();
  const sources = Object.fromEntries(files.map(name => [name, evidence.blob(fs.readFileSync(path.join(here, name)))]));
  for(const name of ['tab-navigation.cjs','navigation-gate.cjs']) {
    sources[`../fourget-selfhost/fourplay/${name}`]=evidence.blob(fs.readFileSync(path.join(here,'../fourget-selfhost/fourplay',name)));
  }
  const patchDirectory=path.join(here,'patches');
  if(fs.existsSync(patchDirectory)) for(const entry of fs.readdirSync(patchDirectory,{withFileTypes:true})) {
    if(entry.isFile() && /\.(patch|txt|md)$/.test(entry.name)) {
      sources[`patches/${entry.name}`]=evidence.blob(fs.readFileSync(path.join(patchDirectory,entry.name)));
    }
  }
  if (roleSource) sources.custom_roles = evidence.blob(fs.readFileSync(roleSource));
  return sources;
}
