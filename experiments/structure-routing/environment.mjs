// Opt in to the Cloud environment's supplied proxy; never persist credentials.
export function environmentContextOptions() {
  if (process.env.STRUCTURE_USE_ENV_PROXY !== 'true') return {};
  const raw = process.env.HTTPS_PROXY || process.env.https_proxy || process.env.HTTP_PROXY || process.env.http_proxy;
  if (!raw) throw new Error('environment_proxy_requested_but_missing');
  const u = new URL(raw);
  const proxy = {server: `${u.protocol}//${u.hostname}:${u.port || (u.protocol === 'https:' ? '443' : '80')}`};
  if (u.username) proxy.username = decodeURIComponent(u.username);
  if (u.password) proxy.password = decodeURIComponent(u.password);
  const bypass = process.env.NO_PROXY || process.env.no_proxy;
  if (bypass) proxy.bypass = bypass;
  return {proxy};
}
