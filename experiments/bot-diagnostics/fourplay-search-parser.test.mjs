import test from 'node:test';
import assert from 'node:assert/strict';
import {extractPage} from './fourplay-search-parser.mjs';

const base='https://www.google.com/search?q=Python+documentation';

test('observed Google opaque goto is retained while the visible Python result is recognized',()=>{
  const html='<a jsname="UWckNb" href="/goto?url=CAESUwHrOzAV" data-sb="/url?url=CAESUwHrOzAV"><h3>Python Docs</h3><cite>https://docs.python.org</cite></a>';
  const page=extractPage(html,base);
  assert.equal(page.links.length,1);
  assert.equal(page.officialDocs.length,0);
  assert.equal(page.officialDocsCitations.length,1);
  assert.equal(page.links[0].url,'https://www.google.com/goto?url=CAESUwHrOzAV');
  assert.equal(page.links[0].url_resolution,'google_opaque_redirect');
  assert.equal(page.links[0].display_url,'https://docs.python.org');
  assert.equal(page.links[0].raw_href,'/goto?url=CAESUwHrOzAV');
});

test('known Google URL wrappers decode absolute HTTP targets and preserve source URL',()=>{
  const page=extractPage('<a href="/url?q=https%3A%2F%2Fdocs.python.org%2F3%2F&amp;sa=U"><h3>Python Docs</h3></a>',base);
  assert.equal(page.links[0].url,'https://docs.python.org/3/');
  assert.equal(page.links[0].source_url,'https://www.google.com/url?q=https%3A%2F%2Fdocs.python.org%2F3%2F&sa=U');
  assert.equal(page.links[0].url_resolution,'google_wrapper_decoded');
  assert.equal(page.officialDocs.length,1);
});

test('unsafe schemes and wrappers without a decodable destination never become external targets',()=>{
  const page=extractPage('<a href="javascript:alert(1)">bad</a><a href="/url?q=javascript%3Aalert(1)">bad redirect</a>',base);
  assert.equal(page.links.length,0);
});

test('a misleading docs.python.org citation cannot override the resolved target host',()=>{
  const page=extractPage('<a href="https://example.org/python"><h3>Python Docs</h3><cite>https://docs.python.org</cite></a>',base);
  assert.equal(page.officialDocs.length,0);
  assert.equal(page.officialDocsCitations.length,1);
});

test('non-Google paths named like wrappers stay direct URLs',()=>{
  const page=extractPage('<a href="https://example.org/url?q=https%3A%2F%2Fdocs.python.org">External</a>',base);
  assert.equal(page.links[0].url,'https://example.org/url?q=https%3A%2F%2Fdocs.python.org');
  assert.equal(page.links[0].url_resolution,'direct');
  assert.equal(page.officialDocs.length,0);
});
