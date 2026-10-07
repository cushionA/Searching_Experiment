import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {extractJoshinProductPage} from './joshin-product-extraction.mjs';

const makeElement = ({text = '', innerText = text, attrs = {}, href, children = {}} = {}) => ({
  textContent:text,
  innerText,
  ...(href === undefined ? {} : {href}),
  value:attrs['data-value'] ?? attrs.value ?? '',
  getAttribute:name => attrs[name] ?? null,
  querySelector:selector => children[selector]?.[0] ?? null,
  querySelectorAll:selector => children[selector] ?? [],
});

function makeProduct(name, href, price = '1,280 円(税込)') {
  const anchor = makeElement({text:name,href,attrs:{href}});
  const priceNode = makeElement({text:price});
  return makeElement({children:{'.search_container_name a[href]':[anchor],'.search_container_price':[priceNode]}});
}

function makeResultPage({pageNo = 0, maxPage = 3, total = 5, start = 1, end = 2,
  products = [makeProduct('商品A','https://joshinweb.jp/item/1234567890123.html'),makeProduct('商品B','https://joshinweb.jp/item/9876543210987.html')],
  pageLinks = [{text:'2',href:'1'},{text:'->',href:'1'}], bodyText} = {}) {
  const input = name => makeElement({attrs:{'data-value':String(name === 'HIT_COUNT' ? total : name === 'MAX_PAGE' ? maxPage : pageNo)}});
  const pagerAnchors = pageLinks.map(link => makeElement({text:link.text,innerText:link.text,attrs:{href:link.href},href:link.href}));
  const pager = makeElement({children:{'a[href]':pagerAnchors}});
  const resultText = bodyText ?? `${total}件中 ${start}～${end}件を表示`;
  const document = {
    title:'Joshin result fixture',
    readyState:'complete',
    body:{innerText:resultText},
    querySelector:selector => {
      const match = selector.match(/^input\[data-field="(HIT_COUNT|MAX_PAGE|PAGE_NO)"\]$/);
      return match ? input(match[1]) : null;
    },
    querySelectorAll:selector => selector === 'div.search_container' ? products
      : selector === '.page_nation' ? [pager,makeElement({children:{'a[href]':pagerAnchors}})] : [],
  };
  return document;
}

function observe(document) {
  const context = vm.createContext({
    location:{href:'https://joshinweb.jp/srhzs.html?QK=example'},
    document,
  });
  return JSON.parse(JSON.stringify(vm.runInContext(`(${extractJoshinProductPage.toString()})('グローブ')`,context)));
}

test('denied document has no product count, total, or pagination state', () => {
  const result = observe({title:'Access Denied',readyState:'complete',body:{innerText:"You don't have permission to access joshinweb.jp on this server."},
    querySelector:() => null,querySelectorAll:() => []});
  assert.equal(result.count,null);
  assert.deepEqual(result.products,[]);
  assert.equal(result.next_available,false);
  assert.equal(result.displayed_total,null);
  assert.equal(result.current_page,null);
  assert.equal(result.extraction_error,'access_denied_document');
});

test('an unfamiliar page with plausible product and next links remains unvalidated', () => {
  const result = observe({title:'検索結果',readyState:'complete',body:{innerText:'グローブ 全100件'},
    querySelector:() => null,querySelectorAll:selector => selector === 'a[href]' ? [
      makeElement({text:'グローブ A',href:'https://joshinweb.jp/sports/123.html',attrs:{href:'https://joshinweb.jp/sports/123.html'}}),
      makeElement({text:'次へ',href:'https://joshinweb.jp/srhzs.html?page=2',attrs:{href:'https://joshinweb.jp/srhzs.html?page=2'}}),
    ] : []});
  assert.equal(result.count,null);
  assert.deepEqual(result.products,[]);
  assert.equal(result.displayed_total,null);
  assert.equal(result.next_anchor,null);
  assert.equal(result.next_available,false);
  assert.equal(result.extraction_error,'unknown_or_contradictory_result_layout');
});

test('successful result extracts the observed page range, products, and arrow pager', () => {
  const result = observe(makeResultPage({pageLinks:[{text:'2',href:'1'},{text:'->',href:'1'}]}));
  assert.equal(result.count,2);
  assert.equal(result.card_count,2);
  assert.equal(result.displayed_total,5);
  assert.deepEqual(result.displayed_range,{start:1,end:2});
  assert.equal(result.page_no,0);
  assert.equal(result.current_page,1);
  assert.equal(result.max_page,3);
  assert.deepEqual(result.products.map(({name,price_text,product_code_candidate})=>({name,price_text,product_code_candidate})),[
    {name:'商品A',price_text:'1,280 円(税込)',product_code_candidate:'1234567890123'},
    {name:'商品B',price_text:'1,280 円(税込)',product_code_candidate:'9876543210987'},
  ]);
  assert.equal(result.products[0].url,'https://joshinweb.jp/item/1234567890123.html');
  assert.equal(result.products[0].jan,undefined);
  assert.deepEqual(result.next_anchor,{selector:'.page_nation',container_index:0,anchor_index:1,raw_href:'1',text:'->',target_page:2,kind:'arrow'});
  assert.equal(result.next_available,true);
  assert.equal(result.extraction_error,null);
});

test('three successful pages reach the observed final page without inferring it from the pager alone', () => {
  const pages=[
    observe(makeResultPage({pageNo:0,maxPage:3,total:5,start:1,end:2,pageLinks:[{text:'2',href:'1'},{text:'->',href:'1'}]})),
    observe(makeResultPage({pageNo:1,maxPage:3,total:5,start:3,end:4,
      products:[makeProduct('商品C','https://joshinweb.jp/item/1111111111111.html'),makeProduct('商品D','https://joshinweb.jp/item/2222222222222.html')],
      pageLinks:[{text:'3',href:'2'},{text:'->',href:'2'}]})),
    observe(makeResultPage({pageNo:2,maxPage:3,total:5,start:5,end:5,
      products:[makeProduct('商品E','https://joshinweb.jp/item/3333333333333.html')],pageLinks:[]})),
  ];
  assert.deepEqual(pages.map(page=>page.current_page),[1,2,3]);
  assert.deepEqual(pages.map(page=>page.count),[2,2,1]);
  assert.deepEqual(pages.map(page=>page.next_available),[true,true,false]);
  assert.equal(pages.at(-1).current_page,pages.at(-1).max_page);
  assert.equal(pages.every(page=>page.extraction_error===null),true);
});

test('a numeric pager link is retained as a raw href candidate for the next page', () => {
  const result = observe(makeResultPage({pageLinks:[{text:'2',href:'1'},{text:'3',href:'2'}]}));
  assert.deepEqual(result.next_anchor,{selector:'.page_nation',container_index:0,anchor_index:0,raw_href:'1',text:'2',target_page:2,kind:'number'});
});

test('an arrow with an external URL is not accepted as a next-page candidate', () => {
  const result = observe(makeResultPage({pageLinks:[{text:'2',href:'1'},{text:'->',href:'https://example.invalid/2'}]}));
  assert.equal(result.next_anchor,null);
  assert.equal(result.next_available,false);
});

test('a numeric next-page label with a mismatched raw href is rejected', () => {
  const result = observe(makeResultPage({pageLinks:[{text:'2',href:'9'}]}));
  assert.equal(result.next_anchor,null);
  assert.equal(result.next_available,false);
});

test('contradictory total, range, and card count are rejected as an unknown layout', () => {
  const result = observe(makeResultPage({total:5,start:1,end:3,products:[makeProduct('商品A','https://joshinweb.jp/item/1234567890123.html'),
    makeProduct('商品B','https://joshinweb.jp/item/9876543210987.html')]}));
  assert.equal(result.count,null);
  assert.deepEqual(result.products,[]);
  assert.equal(result.displayed_total,5);
  assert.deepEqual(result.displayed_range,{start:1,end:3});
  assert.equal(result.card_count,2);
  assert.equal(result.next_available,false);
  assert.equal(result.extraction_error,'unknown_or_contradictory_result_layout');
});

test('duplicate observed product URLs are preserved for driver-level de-duplication', () => {
  const duplicate='https://joshinweb.jp/item/1234567890123.html';
  const page1=observe(makeResultPage({pageNo:0,maxPage:2,total:3,start:1,end:2,
    products:[makeProduct('商品A',duplicate),makeProduct('商品B','https://joshinweb.jp/item/9876543210987.html')],
    pageLinks:[{text:'2',href:'1'},{text:'->',href:'1'}]}));
  const page2=observe(makeResultPage({pageNo:1,maxPage:2,total:3,start:3,end:3,
    products:[makeProduct('商品A（再掲）',duplicate)],pageLinks:[]}));
  const occurrences=[...page1.products,...page2.products];
  assert.equal(occurrences.length,3);
  assert.equal(occurrences.filter(product=>product.url===duplicate).length,2);
  assert.equal(new Set(occurrences.map(product=>product.url)).size,2);
});
