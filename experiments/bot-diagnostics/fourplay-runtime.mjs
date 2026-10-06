import fs from 'node:fs';
import path from 'node:path';
import {proxy as inheritedProxy, limits as L, repo} from './runtime.mjs';

const bridgeURL = () => {
  const value=(process.env.BOT_DIAGNOSTICS_FOURPLAY_URL || 'http://127.0.0.1:3004').replace(/\/$/, '');
  let parsed;
  try {parsed=new URL(value);} catch {throw new Error('invalid_fourplay_bridge_url');}
  if(parsed.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(parsed.hostname)||parsed.username||parsed.password)
    throw new Error('fourplay_bridge_must_be_loopback_http');
  return parsed.href.replace(/\/$/,'');
};

function bearerToken() {
  const direct = process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD;
  if (direct) return direct.trim();
  const filename = process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD_FILE
    || path.join(repo,'experiments/fourget-selfhost/.runtime/fourplay-password.txt');
  try { return fs.readFileSync(filename, 'utf8').trim(); }
  catch { throw new Error('fourplay_password_unavailable'); }
}

async function request(path, {method = 'GET', body, token, fetchImpl = fetch} = {}) {
  const response = await fetchImpl(`${bridgeURL()}${path}`, {
    method, headers: { ...(token ? {authorization: `Bearer ${token}`} : {}), ...(body ? {'content-type':'application/json'} : {}) },
    ...(body ? {body:JSON.stringify(body)} : {}), signal:AbortSignal.timeout(L.navigation_timeout_ms)
  });
  if (!response.ok) throw new Error(`fourplay_bridge_http_${response.status}`);
  return response.json();
}

export async function openFourplay({fixture = false, profile = 'browser_observation', headful = false, fetchImpl = fetch} = {}) {
  if (profile === 'grounding') throw new Error('unsupported_capability:fourplay_grounding');
  if (!headful) throw new Error('unsupported_capability:4play_requires_headful');
  const token = bearerToken();
  const health = await request('/health', {fetchImpl});
  if (health.browser_connected !== true || typeof health.ua !== 'string' || !health.runtime || typeof health.runtime !== 'object')
    throw new Error('fourplay_browser_unavailable');
  const proxy = fixture ? null : inheritedProxy;
  if (!fixture && !proxy) throw new Error('environment_proxy_required');
  const session = await request('/diagnostics/session', {method:'POST',token,fetchImpl,
    body:{proxy,fixture}});
  if (typeof session.session_id !== 'string' || typeof session.ua !== 'string' || !session.runtime)
    throw new Error('fourplay_invalid_session_response');
  let closed = false;
  return {kind:'fourplay', ua:session.ua, runtime:{...session.runtime,transport:'4play',
      ...(session.runtime.headless===undefined?{headless:false}:{}),
      ...(session.runtime.display===undefined?{display:process.env.DISPLAY||process.env.WAYLAND_DISPLAY||null}:{}),
      instrumentation:'bridge responses and captured DOM; no Playwright events or CDP'},
    sessionId:session.session_id, token, fixture, fetchImpl,
    navigate: (url, observeMs) => request('/diagnostics/navigate',{method:'POST',token,fetchImpl,
      body:{session_id:session.session_id,url,observe_ms:observeMs??L.observe_ms}}),
    close:async()=>{if(closed)return;closed=true;await request('/diagnostics/close',{method:'POST',token,fetchImpl,body:{session_id:session.session_id}});}};
}
