import { randomUUID } from 'node:crypto';

const MAX_COOKIE_COUNT = 64;
const MAX_COOKIE_BYTES = 32_768;

/** A small metadata-only session pool. It never owns or creates a browser. */
export class LightweightSessionPool {
  constructor({ maxPoolSize = 3, maxAgeMillis = 900_000, maxUsageCount = 20, now = () => Date.now() } = {}) {
    if (!Number.isInteger(maxPoolSize) || maxPoolSize < 1 || maxPoolSize > 5) {
      throw new RangeError('maxPoolSize must be an integer from 1 to 5');
    }
    if (!Number.isFinite(maxAgeMillis) || maxAgeMillis <= 0) {
      throw new RangeError('maxAgeMillis must be a positive finite number');
    }
    if (!Number.isInteger(maxUsageCount) || maxUsageCount < 1) {
      throw new RangeError('maxUsageCount must be a positive integer');
    }
    if (typeof now !== 'function') throw new TypeError('now must be a function');
    this.maxPoolSize = maxPoolSize;
    this.maxAgeMillis = maxAgeMillis;
    this.maxUsageCount = maxUsageCount;
    this.now = now;
    this.sessions = [];
    this.current = null;
  }

  /** Current session for controller integrations; null until first allocation. */
  get session() {
    return this.current;
  }

  #usable(session, at = this.now()) {
    return Boolean(session && !session.retired
      && at - session.createdAt < this.maxAgeMillis
      && session.uses < this.maxUsageCount);
  }

  #owns(session) {
    if (!this.sessions.includes(session)) throw new TypeError('session does not belong to this pool');
  }

  #pruneUnusable() {
    const at = this.now();
    const unusable = this.sessions
      .filter(session => !this.#usable(session, at))
      .sort((a, b) => a.createdAt - b.createdAt);
    for (const session of unusable) {
      if (this.sessions.length < this.maxPoolSize) break;
      this.retire(session);
      this.sessions.splice(this.sessions.indexOf(session), 1);
    }
  }

  getSession() {
    const at = this.now();
    if (this.#usable(this.current, at)) return this.current;
    this.current = null;

    const existing = this.sessions.find(session => this.#usable(session, at));
    if (existing) {
      this.current = existing;
      return existing;
    }

    this.#pruneUnusable();
    if (this.sessions.length >= this.maxPoolSize) {
      // This can only happen if a future change creates an allocation path
      // that bypasses the existing-session selection above.
      throw new Error('session_pool_capacity');
    }
    const session = {
      id: randomUUID(),
      createdAt: at,
      uses: 0,
      errorScore: 0,
      retired: false,
      cookies: [],
    };
    this.sessions.push(session);
    this.current = session;
    return session;
  }

  markGood(session) {
    this.#owns(session);
    session.uses += 1;
    session.errorScore = Math.max(0, session.errorScore - 0.5);
    return session;
  }

  markBad(session) {
    this.#owns(session);
    session.uses += 1;
    session.errorScore += 1;
    if (session.errorScore >= 2) this.retire(session);
    return session;
  }

  retire(session) {
    this.#owns(session);
    session.retired = true;
    session.cookies = [];
    if (this.current === session) this.current = null;
    return session;
  }

  snapshotCookies(session, cookies) {
    this.#owns(session);
    if (!Array.isArray(cookies)) throw new TypeError('cookies must be an array');
    let serialized;
    try {
      serialized = JSON.stringify(cookies);
    } catch {
      throw new TypeError('cookies must be JSON-safe');
    }
    if (serialized === undefined) throw new TypeError('cookies must be JSON-safe');
    const bytes = Buffer.byteLength(serialized, 'utf8');
    if (cookies.length > MAX_COOKIE_COUNT || bytes > MAX_COOKIE_BYTES) {
      const error = new RangeError('cookie_storage_limit');
      error.code = 'cookie_storage_limit';
      throw error;
    }
    const clone = JSON.parse(serialized);
    session.cookies = clone;
    return JSON.parse(serialized);
  }

  state() {
    return this.sessions.map(session => {
      const cookieBytes = Buffer.byteLength(JSON.stringify(session.cookies), 'utf8');
      return {
        id: session.id,
        createdAt: session.createdAt,
        uses: session.uses,
        errorScore: session.errorScore,
        retired: session.retired,
        usable: this.#usable(session),
        cookie_count: session.cookies.length,
        cookie_bytes: cookieBytes,
      };
    });
  }
}
