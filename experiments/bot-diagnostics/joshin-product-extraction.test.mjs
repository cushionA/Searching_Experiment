import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {extractJoshinProductPage} from './joshin-product-extraction.mjs';

function observe(title, text, links = []) {
  const context = vm.createContext({
    location:{href:'https://joshinweb.jp/srhzs.html?QK=example'},
    document:{title,readyState:'complete',body:{innerText:text},
      querySelectorAll:selector => selector === 'a[href]' ? links : []},
    getComputedStyle:() => ({display:'block',visibility:'visible'}),
  });
  return JSON.parse(JSON.stringify(vm.runInContext(`(${extractJoshinProductPage.toString()})('グローブ')`,context)));
}

test('denied document records no extracted names without asserting a last page or catalog total', () => {
  const result = observe('Access Denied',"You don't have permission to access joshinweb.jp on this server.");
  assert.equal(result.count,0);
  assert.deepEqual(result.products,[]);
  assert.equal(result.next_available,null);
  assert.equal(result.displayed_total,null);
  assert.match(result.extraction_error,/access-denied/);
});

test('an unfamiliar page with plausible product and next links remains unvalidated', () => {
  const makeLink = (text,href,rel = '') => ({isConnected:true,textContent:text,href,rel,getClientRects:() => [{}]});
  const result = observe('検索結果','グローブ 全100件',[
    makeLink('グローブ A','https://joshinweb.jp/sports/123.html'),
    makeLink('次へ','https://joshinweb.jp/srhzs.html?page=2','next'),
  ]);
  assert.equal(result.candidate_links.length,2);
  assert.equal(result.count,null);
  assert.deepEqual(result.products,[]);
  assert.equal(result.displayed_total,null);
  assert.equal(result.next_anchor,null);
  assert.equal(result.next_available,null);
  assert.match(result.extraction_error,/Unknown Joshin result layout/);
});
