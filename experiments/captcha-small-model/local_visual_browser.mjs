/** Browser connection for the local visual model; no remote AI service. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');

function failure(code, message = code) {
  const error = new Error(message);
  error.code = code;
  return error;
}

export class LocalVisualClient {
  constructor({python, modelRoot, imageRoot, device = 'cpu', threads = 2,
    clipThreshold = 0.01, confidenceThreshold = 0.5, coverageThreshold = 0.1}) {
    this.options = {python, modelRoot, imageRoot, device, threads,
      clipThreshold, confidenceThreshold, coverageThreshold};
    this.pending = new Map();
    this.sequence = 0;
    this.stderr = '';
    this.buffer = '';
  }

  async start(timeoutMs) {
    if (this.ready) return this.ready;
    const options = this.options;
    this.ready = new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
      this.readyTimer = setTimeout(() => this.fail(failure('inference_timeout')), timeoutMs);
    });
    this.child = spawn(options.python, ['-B', path.join(here, 'local_visual_worker.py'),
      '--model-root', path.resolve(options.modelRoot), '--image-root', path.resolve(options.imageRoot),
      '--device', options.device, '--threads', String(options.threads),
      '--clip-threshold', String(options.clipThreshold),
      '--confidence-threshold', String(options.confidenceThreshold),
      '--coverage-threshold', String(options.coverageThreshold)], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: {...process.env, HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1'},
    });
    this.child.on('error', error => this.fail(error));
    this.child.on('exit', code => {
      this.exited = true;
      if (!this.closing) this.fail(failure('inference_worker_exited', 'Local inference worker exited: ' + code));
    });
    this.child.stdin.on('error', error => this.fail(error));
    this.child.stderr.on('data', data => { this.stderr = (this.stderr + data).slice(-8000); });
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', data => {
      this.buffer += data;
      if (this.buffer.length > 16 * 1024 * 1024) return this.fail(failure('invalid_worker_protocol'));
      for (;;) {
        const newline = this.buffer.indexOf('\n');
        if (newline < 0) break;
        const line = this.buffer.slice(0, newline);
        this.buffer = this.buffer.slice(newline + 1);
        try {
          const message = JSON.parse(line);
          if (message.event === 'ready') {
            clearTimeout(this.readyTimer);
            this.metadata = message;
            this.resolveReady(message);
          } else if (message.event === 'fatal') {
            this.fail(failure('inference_worker_failed', message.error?.message || 'Inference initialization failed'));
          } else {
            const request = this.pending.get(message.id);
            if (!request) throw failure('invalid_worker_protocol');
            clearTimeout(request.timer);
            this.pending.delete(message.id);
            if (message.ok) request.resolve(message.prediction);
            else request.reject(failure('inference_failed', message.error?.message || 'Local inference failed'));
          }
        } catch (error) {
          this.fail(error);
        }
      }
    });
    return this.ready;
  }

  fail(error) {
    if (this.failed) return;
    this.failed = error;
    clearTimeout(this.readyTimer);
    this.rejectReady?.(error);
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    this.pending.clear();
    this.child?.kill('SIGTERM');
  }

  async predict(request, timeoutMs) {
    const started = Date.now();
    await this.start(timeoutMs);
    if (this.failed) throw this.failed;
    const remaining = timeoutMs - (Date.now() - started);
    if (remaining <= 0) throw failure('inference_timeout');
    const id = String(++this.sequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(failure('inference_timeout')), remaining);
      this.pending.set(id, {resolve, reject, timer});
      this.child.stdin.write(JSON.stringify({...request, id}) + '\n');
    });
  }

  async close() {
    this.closing = true;
    clearTimeout(this.readyTimer);
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(failure('inference_worker_closed'));
    }
    this.pending.clear();
    if (!this.child || this.exited) return;
    this.child.stdin.end();
    await Promise.race([new Promise(resolve => this.child.once('exit', resolve)), delay(1000)]);
    if (!this.exited) {
      this.child.kill('SIGTERM');
      await Promise.race([new Promise(resolve => this.child.once('exit', resolve)), delay(1000)]);
    }
    if (!this.exited) this.child.kill('SIGKILL');
  }
}

async function visibleFrames(page) {
  const frames = [];
  for (const frame of page.frames()) {
    try {
      if (frame === page.mainFrame()) { frames.push(frame); continue; }
      const handle = await frame.frameElement();
      try { if (await handle.isVisible()) frames.push(frame); }
      finally { await handle.dispose(); }
    } catch { /* A widget may replace its iframe during verification. */ }
  }
  return frames;
}

async function anchorState(page) {
  let anchor = null;
  for (const frame of await visibleFrames(page)) {
    const candidate = frame.locator('#recaptcha-anchor');
    if (await candidate.count() && await candidate.first().isVisible()) {
      if (anchor) throw failure('unsupported_challenge', 'Multiple visible reCAPTCHA widgets');
      anchor = candidate.first();
    }
  }
  return {anchor, checked: anchor ? await anchor.getAttribute('aria-checked') === 'true' : false};
}

async function rejection(page) {
  for (const frame of await visibleFrames(page)) {
    for (const selector of ['.rc-doscaptcha-body-text', '.rc-anchor-error-msg-container']) {
      const notice = frame.locator(selector).first();
      if (await notice.count() && await notice.isVisible() && (await notice.textContent())?.trim()) return true;
    }
  }
  return false;
}

async function challengeState(page) {
  for (const frame of await visibleFrames(page)) {
    const board = frame.locator('.rc-imageselect-target').first();
    if (!await board.count() || !await board.isVisible()) continue;
    const state = await frame.evaluate(() => {
      const cells = [...document.querySelectorAll('.rc-imageselect-target .rc-imageselect-tile')];
      const instructions = document.querySelector('.rc-imageselect-instructions');
      const keyword = instructions?.querySelector('strong')?.textContent?.trim().replace(/\s+/g, ' ');
      const images = cells.map(cell => [...cell.querySelectorAll('img')].map(image => ({
        src: image.currentSrc || image.src, style: image.getAttribute('style'),
        width: image.naturalWidth, height: image.naturalHeight, complete: image.complete,
      })));
      return {
        keyword, count: cells.length,
        dynamic: /once there are none left/i.test(instructions?.textContent || ''),
        selected: cells.map(cell => cell.classList.contains('rc-imageselect-tileselected')),
        busy: cells.map(cell => cell.classList.contains('rc-imageselect-dynamic-selected')),
        images,
        loaded: images.every(list => list.length && list.every(image => image.complete && image.width > 0)),
      };
    });
    state.frame = frame;
    state.board = board;
    state.fingerprint = hash(JSON.stringify([state.keyword, state.count, state.dynamic, state.images]));
    state.identities = state.images.map(images => hash(JSON.stringify(images)));
    return state;
  }
  return null;
}

function validatePrediction(prediction, count) {
  if (!prediction || !Array.isArray(prediction.selection) || !Array.isArray(prediction.scores)
    || prediction.scores.length !== count || prediction.scores.some(value => !Number.isFinite(value))
    || prediction.selection.some(index => !Number.isInteger(index) || index < 0 || index >= count)
    || new Set(prediction.selection).size !== prediction.selection.length) {
    throw failure('failed', 'Local model returned invalid tile indices or scores');
  }
  return [...prediction.selection].sort((a, b) => a - b);
}

export async function runVisualChallenge({page, predictor, directory, maxAttempts = 20,
  maxRounds = 20, timeoutMs = 120000, refreshTimeoutMs = 10000,
  initialWaitMs = 10000, pollMs = 100}) {
  for (const [name, value] of Object.entries({maxAttempts, maxRounds, timeoutMs, refreshTimeoutMs, initialWaitMs, pollMs})) {
    if (!Number.isInteger(value) || value <= 0) throw new Error(name + ' must be a positive integer');
  }
  directory = path.resolve(directory);
  fs.mkdirSync(directory, {recursive: true});
  const eventsFile = path.join(directory, 'events.jsonl');
  const deadline = Date.now() + timeoutMs;
  const started = Date.now();
  let attempts = 0, classifications = 0, captures = 0, clickedTiles = 0, rounds = 0, sawImages = false;
  let clickedAnchor = false;
  const seen = new Set();
  const event = (kind, fields = {}) => fs.appendFileSync(eventsFile,
    JSON.stringify({elapsed_ms: Date.now() - started, kind, ...fields}) + '\n');
  const remaining = (cap = timeoutMs) => {
    const value = Math.min(cap, deadline - Date.now());
    if (value <= 0) throw failure('deadline_exceeded');
    return value;
  };
  const poll = async () => { await delay(Math.min(pollMs, remaining())); };
  const finish = (outcome, details = {}) => {
    const result = {outcome, attempts, classifications, clicked_tiles: clickedTiles,
      elapsed_ms: Date.now() - started, events_file: 'events.jsonl', ...details};
    event('terminal', result);
    fs.writeFileSync(path.join(directory, 'recovery.json'), JSON.stringify(result, null, 2) + '\n');
    return result;
  };

  try {
    const initialDeadline = Math.min(deadline, Date.now() + initialWaitMs);
    let state = null;
    while (Date.now() < initialDeadline) {
      if (await rejection(page)) return finish('service_rejected');
      const anchor = await anchorState(page);
      if (anchor.checked) return finish('passed_without_image_challenge');
      state = await challengeState(page);
      if (state) break;
      if (anchor.anchor && !clickedAnchor) {
        await anchor.anchor.click({timeout: remaining(5000)});
        clickedAnchor = true;
        event('checkbox_clicked');
      }
      await poll();
    }
    if (!state) return finish(Date.now() >= deadline ? 'deadline_exceeded' : 'captcha_not_observed');

    while (attempts < maxAttempts) {
      remaining();
      if ((await anchorState(page)).checked) return finish(sawImages ? 'passed_after_image_challenge' : 'passed_without_image_challenge');
      if (await rejection(page)) return finish('service_rejected');
      state = await challengeState(page);
      if (!state || !state.loaded || state.busy.some(Boolean)) { await poll(); continue; }
      if (![9, 16].includes(state.count) || !state.keyword) return finish('unsupported_challenge');
      if (++rounds > maxRounds) return finish('attempt_limit', {reason: 'image_round_limit'});
      sawImages = true;
      const kind = state.count === 9 ? 'A' : 'B';
      const prefix = String(++captures).padStart(4, '0');
      const images = [];
      if (kind === 'A') {
        const cells = state.frame.locator('.rc-imageselect-target .rc-imageselect-tile');
        for (let index = 0; index < 9; index++) {
          const filename = path.join(directory, prefix + '-tile-' + index + '.png');
          await cells.nth(index).screenshot({path: filename, animations: 'disabled', timeout: remaining(5000)});
          images.push(filename);
        }
      } else {
        const filename = path.join(directory, prefix + '-board.png');
        await state.board.screenshot({path: filename, animations: 'disabled', timeout: remaining(5000)});
        images.push(filename);
      }
      const captured = await challengeState(page);
      if (!captured || captured.fingerprint !== state.fingerprint || captured.busy.some(Boolean)) {
        event('stale_capture');
        continue;
      }
      let inferenceTimer;
      let prediction;
      try {
        const budget = remaining();
        prediction = await Promise.race([
          predictor.predict({type: kind, target: state.keyword, images, rows: 4, columns: 4}, budget),
          new Promise((_, reject) => {
            inferenceTimer = setTimeout(() => reject(failure('inference_timeout')), budget);
          }),
        ]);
      } finally { clearTimeout(inferenceTimer); }
      classifications++;
      remaining();
      const selection = validatePrediction(prediction, state.count);
      const predictionName = prefix + '-prediction.json';
      fs.writeFileSync(path.join(directory, predictionName), JSON.stringify({
        ...prediction, type: kind, target: state.keyword, dynamic: state.dynamic,
        input_images: images.map(filename => ({path: path.basename(filename), sha256: hash(fs.readFileSync(filename))})),
      }, null, 2) + '\n');
      event('prediction', {type: kind, target: state.keyword, selection, prediction_file: predictionName});
      let current = await challengeState(page);
      if (!current || current.fingerprint !== state.fingerprint || current.busy.some(Boolean)) {
        event('stale_prediction');
        continue;
      }
      const attemptKey = state.fingerprint + '/' + selection.join(',');
      if (seen.has(attemptKey)) return finish('no_progress');
      seen.add(attemptKey);

      if (state.dynamic && selection.length) {
        for (const index of selection) {
          current = await challengeState(page);
          if (!current || current.keyword !== state.keyword || current.count !== state.count
            || current.identities[index] !== state.identities[index] || current.busy[index]) throw failure('stale_challenge');
          if (current.selected[index]) continue;
          await current.frame.locator('.rc-imageselect-target .rc-imageselect-tile').nth(index).click({timeout: remaining(5000)});
          clickedTiles++;
          event('tile_clicked', {index, dynamic: true});
        }
        const refreshDeadline = Math.min(deadline, Date.now() + refreshTimeoutMs);
        let refreshed = false, stableFingerprint = null;
        while (Date.now() < refreshDeadline) {
          current = await challengeState(page);
          if (current && current.keyword === state.keyword && current.loaded
            && selection.every(index => current.identities[index] !== state.identities[index])
            && !current.busy.some(Boolean)) {
            if (stableFingerprint === current.fingerprint) { refreshed = true; break; }
            stableFingerprint = current.fingerprint;
          } else stableFingerprint = null;
          await poll();
        }
        if (!refreshed) return finish(Date.now() >= deadline ? 'deadline_exceeded' : 'refresh_timeout');
        event('tiles_refreshed', {indices: selection});
        continue;
      }

      // Reconcile selection, including deselection after a previous failed submit.
      const desired = new Set(selection);
      for (let index = 0; index < state.count; index++) {
        current = await challengeState(page);
        if (!current || current.fingerprint !== state.fingerprint) throw failure('stale_challenge');
        if (current.selected[index] === desired.has(index)) continue;
        await current.frame.locator('.rc-imageselect-target .rc-imageselect-tile').nth(index).click({timeout: remaining(5000)});
        clickedTiles++;
        event('tile_clicked', {index, dynamic: false, selected: desired.has(index)});
      }
      current = await challengeState(page);
      if (!current || current.fingerprint !== state.fingerprint) throw failure('stale_challenge');
      await current.frame.locator('#recaptcha-verify-button').click({timeout: remaining(5000)});
      attempts++;
      rounds = 0;
      event('submitted', {attempt: attempts, type: kind});
      const responseDeadline = Math.min(deadline, Date.now() + 15000);
      let responded = false;
      while (Date.now() < responseDeadline) {
        if ((await anchorState(page)).checked) return finish('passed_after_image_challenge');
        if (await rejection(page)) return finish('service_rejected');
        current = await challengeState(page);
        if (current?.loaded && !current.busy.some(Boolean)) {
          const errors = current.frame.locator('.rc-imageselect-error-select-more, .rc-imageselect-error-dynamic-more, .rc-imageselect-incorrect-response');
          let errorVisible = false;
          for (let index = 0; index < await errors.count(); index++) {
            if (await errors.nth(index).isVisible()) errorVisible = true;
          }
          if (current.fingerprint !== state.fingerprint || errorVisible) { responded = true; break; }
        }
        await poll();
      }
      if (!responded) return finish(Date.now() >= deadline ? 'deadline_exceeded' : 'response_timeout');
    }
    return finish('attempt_limit');
  } catch (error) {
    const outcome = ['deadline_exceeded', 'inference_timeout'].includes(error.code)
      || Date.now() >= deadline ? 'deadline_exceeded'
      : ['refresh_timeout', 'unsupported_challenge'].includes(error.code) ? error.code : 'failed';
    return finish(outcome, {error: {code: error.code || error.name, message: error.message}});
  }
}
