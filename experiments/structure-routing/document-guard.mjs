/** Strict browser document request cap using Chromium CDP Fetch interception. */
export async function installDocumentGuard(page, {limit, beforeRequest, onReserved} = {}) {
  if (!Number.isSafeInteger(limit) || limit < 0) throw new TypeError('limit must be a non-negative safe integer');
  const context = page.context();
  const states = new Map();
  const chains = new Map();
  const counters = {reserved: 0, denied: 0, continued: 0, failed: 0, callbackErrors: 0, drainTimeouts: 0};
  let mainFrameId = null;
  let nextMessageId = 1;
  let closed = false;

  const pageSession = await context.newCDPSession(page);
  const root = {id: null, parent: null, session: pageSession, children: new Map(), pending: new Map(), targetInfo: {type: 'page'}, attached: true};

  const send = (state, method, params = {}) => {
    if (state.id === null) return state.session.send(method, params);
    return new Promise((resolve, reject) => {
      const id = nextMessageId++;
      state.pending.set(id, {resolve, reject});
      const message = JSON.stringify({id, method, params});
      send(state.parent, 'Target.sendMessageToTarget', {sessionId: state.id, message})
        .catch(error => { state.pending.delete(id); reject(error); });
    });
  };

  const failPending = (state, error) => {
    for (const entry of state.pending.values()) entry.reject(error);
    state.pending.clear();
  };

  const handlePaused = (state, event) => {
    const key = state.id ?? pageSession;
    const previous = chains.get(key) || Promise.resolve();
    const next = previous.then(async () => {
      const respond = (method, params) => send(state, method, params);
      if (closed) { await respond('Fetch.failRequest', {requestId: event.requestId, errorReason: 'Aborted'}).catch(() => {}); return; }
      const info = {url: event.request.url, frameId: event.frameId, mainFrame: event.frameId === mainFrameId,
        method: event.request.method, resourceType: event.resourceType, redirect: Boolean(event.redirectedRequestId)};
      if (counters.reserved >= limit) {
        counters.denied++; counters.failed++;
        await respond('Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'});
        return;
      }
      let accepted = true;
      if (beforeRequest) {
        try { accepted = (await beforeRequest(info, counters)) !== false; }
        catch { counters.callbackErrors++; accepted = false; }
      }
      // Recheck after the potentially asynchronous policy callback. Different
      // CDP targets have independent queues and can finish callbacks together.
      if (closed || !accepted || counters.reserved >= limit) {
        counters.denied++; counters.failed++;
        await respond('Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'});
        return;
      }
      counters.reserved++;
      if (onReserved) {
        try { await onReserved(info, counters); }
        catch {
          counters.callbackErrors++; counters.denied++; counters.failed++;
          await respond('Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'});
          return;
        }
      }
      if (closed) {
        counters.denied++; counters.failed++;
        await respond('Fetch.failRequest', {requestId: event.requestId, errorReason: 'Aborted'}).catch(() => {});
        return;
      }
      await respond('Fetch.continueRequest', {requestId: event.requestId});
      counters.continued++;
    }).catch(async () => {
      counters.denied++; counters.failed++; counters.callbackErrors++;
      await send(state, 'Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'}).catch(() => {});
    });
    chains.set(key, next);
  };

  const targetAttached = (parent, event) => {
    const {sessionId, targetInfo} = event;
    const child = {id: sessionId, parent, children: new Map(), pending: new Map(), targetInfo, attached: true};
    parent.children.set(sessionId, child);
    states.set(sessionId, child);
    void (async () => {
      try {
        if (targetInfo.type === 'iframe') {
          await send(child, 'Network.enable');
          await send(child, 'Network.setCacheDisabled', {cacheDisabled: true});
          await send(child, 'Fetch.enable', {patterns: [{urlPattern: '*', resourceType: 'Document', requestStage: 'Request'}]});
          // Auto-attach is scoped to direct children; relay it for nested OOPIFs.
          await send(child, 'Target.setAutoAttach', {autoAttach: true, waitForDebuggerOnStart: true, flatten: false});
        }
        await send(child, 'Runtime.runIfWaitingForDebugger');
      } catch {
        counters.callbackErrors++;
        // Leave failed iframe setup paused: no request can escape the cap.
      }
    })();
  };

  const handleNestedMessage = (parent, message) => {
    let payload;
    try { payload = JSON.parse(message.message); } catch { return; }
    const child = parent.children.get(message.sessionId);
    if (!child) return;
    if (payload.id != null) {
      const waiter = child.pending.get(payload.id);
      if (!waiter) return;
      child.pending.delete(payload.id);
      if (payload.error) waiter.reject(new Error(payload.error.message || 'Nested CDP command failed'));
      else waiter.resolve(payload.result || {});
    } else if (payload.method === 'Fetch.requestPaused') handlePaused(child, payload.params);
    else if (payload.method === 'Target.attachedToTarget') targetAttached(child, payload.params);
    else if (payload.method === 'Target.receivedMessageFromTarget') handleNestedMessage(child, payload.params);
    else if (payload.method === 'Target.detachedFromTarget') {
      const detached = child.children.get(payload.params.sessionId);
      if (detached) { detached.attached = false; failPending(detached, new Error('CDP target detached')); child.children.delete(detached.id); states.delete(detached.id); }
    }
  };

  const onAttached = event => targetAttached(root, event);
  const onMessage = message => handleNestedMessage(root, message);
  const onDetached = event => {
    const child = root.children.get(event.sessionId);
    if (child) { child.attached = false; failPending(child, new Error('CDP target detached')); root.children.delete(child.id); states.delete(child.id); }
  };
  const onPaused = event => handlePaused(root, event);
  pageSession.on('Target.attachedToTarget', onAttached);
  pageSession.on('Target.receivedMessageFromTarget', onMessage);
  pageSession.on('Target.detachedFromTarget', onDetached);
  pageSession.on('Fetch.requestPaused', onPaused);
  try { mainFrameId = (await pageSession.send('Page.getFrameTree')).frameTree.frame.id; } catch {}
  await pageSession.send('Network.enable');
  await pageSession.send('Network.setCacheDisabled', {cacheDisabled: true});
  await pageSession.send('Target.setAutoAttach', {autoAttach: true, waitForDebuggerOnStart: true, flatten: false});
  await pageSession.send('Fetch.enable', {patterns: [{urlPattern: '*', resourceType: 'Document', requestStage: 'Request'}]});

  let sealPromise;
  const seal = () => {
    if (sealPromise) return sealPromise;
    closed = true;
    sealPromise = (async () => {
      // Take the context offline before browser teardown can release Fetch-paused
      // requests. Drain callbacks for at most five seconds; offline remains on.
      await context.setOffline(true).catch(() => {});
      let timer;
      const drained = await Promise.race([
        Promise.all([...chains.values()].map(chain => chain.catch(() => {}))).then(() => true),
        new Promise(resolve => { timer = setTimeout(() => resolve(false), 5000); }),
      ]);
      clearTimeout(timer);
      if (!drained) counters.drainTimeouts++;
    })();
    return sealPromise;
  };

  return {
    counters,
    seal,
    async cleanup() {
      await seal();
      // Context is offline and closed to new guarded sends. Reject outstanding
      // nested command waiters before detaching only the root CDP session.
      for (const state of states.values()) failPending(state, new Error('Document guard cleaned up'));
      failPending(root, new Error('Document guard cleaned up'));
      pageSession.off('Fetch.requestPaused', onPaused);
      pageSession.off('Target.attachedToTarget', onAttached);
      pageSession.off('Target.receivedMessageFromTarget', onMessage);
      pageSession.off('Target.detachedFromTarget', onDetached);
      await pageSession.detach().catch(() => {});
      states.clear();
    },
  };
}
