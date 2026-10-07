export function extractJoshinProductPage(query = '') {
  const started = Date.now();
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const title = document.title || '';
  const docText = clean(document.body?.innerText || '');
  const denied = /access denied|don't have permission|アクセスが拒否/i.test(`${title} ${docText.slice(0, 800)}`);
  if (denied) {
    return {location:location.href,title,readyState:document.readyState,query,products:[],count:null,
      displayed_total:null,displayed_range:null,page_no:null,current_page:null,max_page:null,next_anchor:null,
      next_available:false,extraction_error:'access_denied_document',elapsed_ms:Date.now()-started};
  }
  const readNumber = name => {
    const node = document.querySelector(`input[data-field="${name}"]`);
    const raw = node?.getAttribute('data-value') ?? node?.value ?? node?.getAttribute('value') ?? '';
    if (!String(raw).trim()) return null;
    const value = Number(String(raw).replace(/,/g, ''));
    return Number.isSafeInteger(value) && value >= 0 ? value : null;
  };
  const displayedTotal = readNumber('HIT_COUNT');
  const maxPage = readNumber('MAX_PAGE');
  const pageNo = readNumber('PAGE_NO');
  const currentPage = pageNo === null ? null : pageNo + 1;
  const range = docText.match(/([\d,]+)件中\s*([\d,]+)～([\d,]+)件/);
  const displayedRange = range ? {start:Number(range[2].replace(/,/g,'')),end:Number(range[3].replace(/,/g,''))} : null;
  const cards = [...document.querySelectorAll('div.search_container')];
  const products = cards.map(card => {
    const anchor = card.querySelector('.search_container_name a[href]');
    const priceNode = card.querySelector('.search_container_price');
    const url = anchor?.href || null;
    const codeMatch = url?.match(/(?:^|\D)(\d{13})(?=\.html(?:[?#]|$)|[/?#]|$)/);
    return {name:clean(anchor?.textContent || ''),url,price_text:priceNode ? clean(priceNode.innerText || priceNode.textContent) : null,
      product_code_candidate:codeMatch?.[1] || null};
  });
  const container = document.querySelectorAll('.page_nation')[0] || null;
  const anchors = container ? [...container.querySelectorAll('a[href]')] : [];
  const anchorRecords = anchors.map((anchor,anchorIndex)=>({selector:'.page_nation',container_index:0,anchor_index:anchorIndex,
    raw_href:anchor.getAttribute('href'),text:clean(anchor.innerText || anchor.textContent)}));
  const arrow = anchorRecords.find(anchor=>anchor.text==='->');
  const numbered = anchorRecords.find(anchor=>anchor.text===String((currentPage ?? 0)+1));
  const chosen = arrow || numbered || null;
  const expectedRawHref = pageNo === null ? null : String(pageNo + 1);
  const nextAnchor = chosen && chosen.raw_href === expectedRawHref
    ? {...chosen,target_page:(currentPage ?? 0)+1,kind:arrow?'arrow':'number'} : null;
  const valid = displayedTotal !== null && maxPage !== null && pageNo !== null && currentPage >= 1
    && currentPage <= maxPage && displayedRange && displayedRange.start >= 1 && displayedRange.end >= displayedRange.start
    && displayedRange.end <= displayedTotal && cards.length > 0 && cards.length === displayedRange.end-displayedRange.start+1
    && displayedTotal === Number(range?.[1]?.replace(/,/g,'')) && products.length === cards.length
    && products.every(product=>product.name && product.url);
  if (!valid) {
    return {location:location.href,title,readyState:document.readyState,query,products:[],count:null,displayed_total:displayedTotal,
      displayed_range:displayedRange,page_no:pageNo,current_page:currentPage,max_page:maxPage,next_anchor:null,
      next_available:false,card_count:cards.length,extraction_error:'unknown_or_contradictory_result_layout',
      diagnostic:{range_text:range?.[0]||null,product_card_count:cards.length},elapsed_ms:Date.now()-started};
  }
  return {location:location.href,title,readyState:document.readyState,query,products,count:products.length,displayed_total:displayedTotal,
    displayed_range:displayedRange,page_no:pageNo,current_page:currentPage,max_page:maxPage,next_anchor:nextAnchor,
    next_available:Boolean(nextAnchor),card_count:cards.length,extraction_error:null,elapsed_ms:Date.now()-started};
}