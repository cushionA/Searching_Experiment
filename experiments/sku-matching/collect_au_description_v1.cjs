'use strict';
// Executable JavaScript counterpart of the user's supplied TypeScript.
// The extraction algorithm is unchanged; types are omitted and the CLI added.
const path = require('node:path');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const dependencyRequire = createRequire(path.resolve(__dirname, '../../.deps/sku-nextdata-cheerio/package.json'));
const { load } = dependencyRequire('cheerio');

const ITEM_INFO_PATH = '$.props.pageProps.initialState.itemDetails.itemPageInfo.itemInfo';
const FIELDS = ['extraItemComment', 'extraItemCommentWithVideo', 'itemComment', 'detailComment'];
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function descriptionText(html) {
  const $ = load(html, {}, false);
  $('script,style,noscript,img,picture,iframe,svg,video,audio,object,embed,canvas').remove();
  function normalize(nodes) {
    for (const node of nodes) {
      if (node.type === 'text') node.data = node.data.replace(/[\t\r\n\f ]+/g, ' ');
      else if ('children' in node) normalize(node.children);
    }
  }
  normalize($.root().contents().toArray());
  $('br,hr').replaceWith('\n');
  $('th,td').append('\t');
  $('tr,p,div,li,dt,dd,h1,h2,h3,h4,h5,h6').append('\n');
  return $.root().text().replace(/\u00a0/g, ' ').split('\n')
    .map(line => line.replace(/[ \f\v]+/g, ' ').trim()).join('\n')
    .replace(/\n{3,}/g, '\n\n').trim();
}

function collectAuDescription(html, expectedProductId) {
  const fail = (reason) => ({ productId: expectedProductId,
    status: 'failure', source: '__NEXT_DATA__', sections: [], missingJsonPaths: [], reason });
  const $ = load(html);
  const scripts = $('script#__NEXT_DATA__');
  if (!scripts.length) return fail('NO_NEXT_DATA');
  if (scripts.length !== 1) return fail('AMBIGUOUS_NEXT_DATA');
  let data;
  try { data = JSON.parse(scripts.text()); } catch { return fail('INVALID_NEXT_DATA_JSON'); }
  let info = data;
  for (const key of ITEM_INFO_PATH.slice(2).split('.')) info = isObject(info) ? info[key] : undefined;
  if (!isObject(info)) return fail('ITEM_INFO_PATH_MISSING');
  const productId = info.lotNumber == null ? expectedProductId : String(info.lotNumber);
  if (expectedProductId && productId !== expectedProductId) return fail('PRODUCT_ID_MISMATCH');
  const sections = [];
  const missingJsonPaths = [];
  const seenHtml = new Map();
  for (const field of FIELDS) {
    const jsonPath = `${ITEM_INFO_PATH}.${field}`;
    const value = info[field];
    if (typeof value !== 'string') { missingJsonPaths.push(jsonPath); continue; }
    const existing = seenHtml.get(value);
    if (existing) { existing.jsonPaths.push(jsonPath); continue; }
    const f = load(value, {}, false);
    const text = descriptionText(value);
    const images = f('img').toArray().map(image => ({ src: f(image).attr('src'), alt: f(image).attr('alt') ?? '' }));
    const section = { jsonPaths: [jsonPath], text, tableCount: f('table').length,
      images, mediaOnly: !text && !!f('img,iframe,video,picture').length };
    seenHtml.set(value, section);
    if (text || images.length || section.mediaOnly) sections.push(section);
  }
  return { productId, productTitle: typeof info.itemTitle === 'string' ? info.itemTitle : undefined,
    status: sections.some(section => section.text) ? 'collected' : 'failure', source: '__NEXT_DATA__',
    sections, missingJsonPaths,
    ...(!sections.some(section => section.text) ? { reason: 'NO_DESCRIPTION_TEXT' } : {}) };
}

module.exports = { ITEM_INFO_PATH, FIELDS, descriptionText, collectAuDescription };
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const execute = (item) => input.mode === 'text' ? descriptionText(item.html) :
    collectAuDescription(item.html, item.expectedProductId);
  const output = Array.isArray(input.items) ? input.items.map(execute) : execute(input);
  process.stdout.write(JSON.stringify(output) + '\n');
}
