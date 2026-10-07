/**
 * Browser-context extractor for Joshin product search pages.
 * Pass this function itself to page.evaluate(extractJoshinProductPage, query).
 * It intentionally has no module-scope dependencies so browser serialization works.
 */
export function extractJoshinProductPage(query = '') {
  const started = Date.now();

  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const snippet = node => clean(node?.textContent || '').slice(0, 240);
  const visible = node => {
    if (!node || !node.isConnected) return false;
    const style = getComputedStyle(node);
    return style.display !== 'none' && style.visibility !== 'hidden' && node.getClientRects().length > 0;
  };
  const docText = clean(document.body?.innerText || '');
  const title = document.title || '';
  const source_snippets = [];
  const selectors = [];
  const measure = selector => {
    let nodes = [];
    try { nodes = [...document.querySelectorAll(selector)]; } catch {}
    selectors.push({ selector, match_count: nodes.length, visible_count: nodes.filter(visible).length });
    for (const node of nodes.slice(0, 2)) {
      const text = snippet(node);
      if (text) source_snippets.push({ selector, text });
    }
    return nodes;
  };

  const denied = /access denied|don't have permission|アクセスが拒否/i.test(`${title} ${docText.slice(0, 800)}`);
  if (denied) {
    return { location: location.href, title, readyState: document.readyState, query, products: [], count: 0,
      displayed_total: null, current_page: null, perpage: null, next_anchor: null, next_available: null,
      selectors: [{ selector: 'document.body', match_count: document.body ? 1 : 0, visible_count: document.body ? 1 : 0 }],
      matchcounts: { 'document.body': document.body ? 1 : 0 }, source_snippets: [{ selector: 'document.body', text: docText.slice(0, 240) }], candidate_links: [],
      extraction_error: 'Joshin returned an access-denied document; no result DOM is available.', elapsed_ms: Date.now() - started };
  }

  // Until a successful Joshin result DOM is observed, these are diagnostics
  // only: never classify links as products or infer that pagination has ended.
  const observedScopes = ['main', '#main', '#contents', '#search_result', '.search_result', '.search-results', 'a[href]'];
  for (const selector of observedScopes) measure(selector);
  const candidate_links = [...document.querySelectorAll('a[href]')].filter(visible).slice(0, 80).map(anchor => ({
    selector: 'a[href]', text: snippet(anchor), href: anchor.href, rel: anchor.rel || null,
  }));
  for (const link of candidate_links.slice(0, 8)) if (link.text || link.href) source_snippets.push({ selector: link.selector, text: `${link.text} ${link.href}`.trim().slice(0, 240) });
  return { location: location.href, title, readyState: document.readyState, query, products: [], count: null,
    displayed_total: null, current_page: null, perpage: null, next_anchor: null, next_available: null,
    selectors, matchcounts: Object.fromEntries(selectors.map(x => [x.selector, x.match_count])), source_snippets,
    candidate_links, extraction_error: 'Unknown Joshin result layout: observed candidate links are diagnostic only; product cards and pagination are not validated.',
    elapsed_ms: Date.now() - started };
}
