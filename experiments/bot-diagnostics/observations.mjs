/** Classify only observable response evidence; proprietary WAF scores remain unknown. */
export function classify(status, html, headers = {}) {
  const text = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  const title = (html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || '').trim();
  const patterns = [
    ['human_verification', /verify (?:that )?you (?:are|['’]re) human|verify that you(?:'re| are) not a robot|ロボットではないことを確認|私はロボットではありません|robot check|enter the characters you see|人間であること|just a moment|checking your browser|additional verification required/i],
    ['access_denied_message', /access denied|request (?:was )?blocked|automated access|sorry[^.]{0,80}robot|unusual traffic|不正なアクセス|アクセスが制限|アクセスを拒否|アクセスが集中/i],
  ];
  const signals = [];
  if (/AwsWafIntegration/.test(html) && /token\.awswaf\.com\//.test(html)) {
    signals.push({ name: 'aws_waf_challenge', evidence: 'AWS WAF token challenge script and AwsWafIntegration in response HTML' });
  }
  for (const [name, re] of patterns) {
    const match = text.match(re);
    if (match) signals.push({ name, evidence: text.slice(Math.max(0, match.index - 80), match.index + 180) });
  }
  if (headers['cf-mitigated'] === 'challenge') signals.push({ name: 'cf_mitigated', evidence: 'challenge' });
  let outcome = 'response_observed';
  if (signals.some(x => ['human_verification', 'cf_mitigated', 'aws_waf_challenge'].includes(x.name))) outcome = 'challenge_observed';
  else if (status === 403 || signals.some(x => x.name === 'access_denied_message')) outcome = 'access_denied_observed';
  else if (status === 429) outcome = 'rate_limited_observed';
  else if (status >= 500) outcome = 'server_error_observed';
  else if (status >= 300 && status < 400) outcome = 'redirect_observed';
  else if (status >= 200 && status < 300) outcome = 'content_observed';
  return { outcome, title, visible_text_prefix: text.slice(0, 1500), signals,
    site_block_reason: null, reason_certainty: 'response evidence only; site policy/logs unavailable' };
}

export function decodeBody(body, headers = {}) {
  const meta = body.subarray(0, 4096).toString('ascii').match(/charset\s*=\s*["'\s]*([\w-]+)/i);
  const charset = headers['content-type']?.match(/charset\s*=\s*["']?([\w-]+)/i)?.[1] || meta?.[1] || 'utf-8';
  try { return new TextDecoder(charset).decode(body); }
  catch { return body.toString('utf8'); }
}
