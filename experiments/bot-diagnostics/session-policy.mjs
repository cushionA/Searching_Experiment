const DEFAULTS = Object.freeze({
  max_pool_size: 3,
  max_session_rotations: 2,
  max_retries_per_url: 1,
  max_retries_per_site: 2,
  max_session_uses: 20,
  max_session_age_seconds: 900,
  base_delay_ms: 1000,
  max_backoff_ms: 30000,
  max_auto_wait_ms: 30000,
});

const RANGES = Object.freeze({
  max_pool_size: [1, 5],
  max_session_rotations: [0, 4],
  max_retries_per_url: [0, 1],
  max_retries_per_site: [0, 4],
  max_session_uses: [1, 100],
  max_session_age_seconds: [1, 3600],
  base_delay_ms: [1, 30000],
  max_backoff_ms: [1, 60000],
  max_auto_wait_ms: [0, 60000],
});

/** Normalize and validate a session-policy setting. */
export function normalizeSessionPolicy(value) {
  if (value === false || value == null) return null;
  if (value === true) return { ...DEFAULTS };
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('session policy must be a boolean, null, or object');
  }

  const policy = { ...DEFAULTS };
  for (const [key, candidate] of Object.entries(value)) {
    if (!(key in RANGES)) throw new RangeError(`unknown session policy key: ${key}`);
    const [min, max] = RANGES[key];
    if (!Number.isInteger(candidate) || candidate < min || candidate > max) {
      throw new RangeError(`${key} must be an integer from ${min} to ${max}`);
    }
    policy[key] = candidate;
  }
  if (policy.max_backoff_ms < policy.base_delay_ms) {
    throw new RangeError('max_backoff_ms must be greater than or equal to base_delay_ms');
  }
  return policy;
}

/** Parse an HTTP Retry-After value into a nonnegative delay in milliseconds. */
export function retryAfterMs(value, now = Date.now()) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (/^\d+$/.test(text)) {
    const seconds = Number(text);
    const ms = seconds * 1000;
    return Math.min(ms,8.64e15);
  }
  // HTTP-date (IMF-fixdate, obsolete RFC 850, or asctime form). Date.parse
  // alone accepts natural-language strings such as "tomorrow", so constrain
  // the grammar before asking it to validate the calendar fields.
  const weekday = '(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)';
  const longWeekday = '(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)';
  const month = '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)';
  const httpDate = new RegExp(`^(?:${weekday}, \\d{2} ${month} \\d{4} \\d{2}:\\d{2}:\\d{2} GMT|${longWeekday}, \\d{2}-${month}-\\d{2} \\d{2}:\\d{2}:\\d{2} GMT|${weekday} ${month} [ \\d]\\d \\d{2}:\\d{2}:\\d{2} \\d{4})$`);
  if (!httpDate.test(text)) return null;
  const target = Date.parse(text);
  if (!Number.isFinite(target)) return null;
  return Math.max(0, target - now);
}

const STOP_REASONS = new Set([
  'budget_limited', 'robots', 'navigation_error', 'operation_error',
  'execution_error', 'navigation_unverified', 'redirect_outside_scope',
]);

/** Make a pure disposition decision for one completed session operation. */
export function sessionResponsePolicy(result, headers, { canRecoverChallenge = false } = {}) {
  const item = result && typeof result === 'object' ? result : {};
  const reasonTexts = [item.reason, item.outcome, item.status_reason, item.operation?.outcome]
    .filter(value => typeof value === 'string').map(value => value.toLowerCase());
  // These result fields describe failures that cannot be retried safely, even
  // if the same record also contains an HTTP status or response classification.
  if (item.budget_limited === true) return { action: 'stop', reason: 'budget_limited' };
  if (item.navigation_error && typeof item.navigation_error === 'object') {
    return { action: 'stop', reason: 'navigation_error' };
  }
  const hardStop = reasonTexts.find(value => STOP_REASONS.has(value) || value === 'robots' || value.startsWith('robots_'));
  if (hardStop) return { action: 'stop', reason: hardStop };
  const status = Number(item.http_status ?? item.status_code ?? item.status);

  // Status takes precedence over body classification: a challenge body on a 429
  // still requires backoff, and a 503 remains a server backoff signal.
  if (status === 429) return { action: 'backoff', reason: 'http_429' };
  if (status === 503) return { action: 'backoff', reason: 'http_503' };

  const reasonText = reasonTexts[0] ?? '';
  if (reasonTexts.some(value => value.includes('challenge')) && canRecoverChallenge) {
    return { action: 'stop', reason: 'configured_challenge_recovery' };
  }
  if (reasonTexts.some(value => value.includes('challenge') || value.includes('access_denied')) || status === 403) {
    return { action: 'retire', reason: reasonText || 'http_403' };
  }
  if (reasonTexts.includes('content_observed')) return { action: 'good', reason: 'content_observed' };
  return { action: 'stop', reason: reasonText || 'unclassified_response' };
}
