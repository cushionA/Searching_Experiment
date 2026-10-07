import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';

const [directoryArg, scenario] = process.argv.slice(2);
if (!directoryArg || !['complete', 'denied', 'no-progress', 'duplicate'].includes(scenario)) throw new Error('Invalid fixture scenario');
const directory = path.resolve(directoryArg);
if (fs.existsSync(directory)) throw new Error('Existing fixture output');
fs.mkdirSync(directory, {recursive:true});
const requests = [];
const query = 'グローブ';
const write = (name, value) => fs.writeFileSync(path.join(directory, name), JSON.stringify(value, null, 2) + '\n');
const form = page => `<form name="itemlistform" action="/srhzs.html" method="get" accept-charset="UTF-8"><input id="suggest_input" name="QK" value="${query}"><input type="hidden" name="PGN" value="${page}"><input type="hidden" name="QS" value=""><a href="javascript:changeSubmit()">検索</a></form>`;
const script = `<script>function changeSubmit(){document.forms.itemlistform.PGN.value='0';document.forms.itemlistform.requestSubmit()}document.addEventListener('click',function(event){const anchor=event.target.closest('.page_nation a[href]');if(!anchor)return;event.preventDefault();document.forms.itemlistform.PGN.value=anchor.getAttribute('href');document.forms.itemlistform.requestSubmit()})</script>`;
const pager = page => `<div class="page_nation">${[0,1,2].map(index => index === page ? `<span>${index+1}</span>` : `<a href="${index}">${index+1}</a>`).join(' ')}${page < 2 ? ` <a href="${page+1}">-&gt;</a>` : ''}</div>`;
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const cookiePresent = /(?:^|;\s*)joshin-pagination-session=fixture(?:;|$)/.test(request.headers.cookie || '');
  const requestedPage = Number(url.searchParams.get('PGN') || '0');
  const row = {at:new Date().toISOString(),path:url.pathname,page_no:requestedPage,
    query_matches:url.searchParams.get('QK') === query,session_cookie_present:cookiePresent};
  requests.push(row);
  write('requests.json', requests);
  response.setHeader('content-type', 'text/html; charset=utf-8');
  if (url.pathname === '/top.html') {
    row.status = 200;
    response.setHeader('set-cookie', 'joshin-pagination-session=fixture; SameSite=Lax; Path=/');
    response.end(`<!doctype html><html lang="ja"><meta charset="utf-8"><title>Joshin pagination fixture</title><body>${form(0)}${script}</body></html>`);
  } else if (url.pathname === '/srhzs.html') {
    if (!cookiePresent || !row.query_matches || !Number.isInteger(requestedPage) || requestedPage < 0 || requestedPage > 2) {
      row.status = 400;
      response.statusCode = 400;
      response.end('<title>Invalid fixture request</title>Missing session, query or page');
    } else if (scenario === 'denied' && requestedPage === 1) {
      row.status = 403;
      response.statusCode = 403;
      response.end('<title>Access Denied</title>You do not have permission to access this fixture.');
    } else {
      row.status = 200;
      const page = scenario === 'no-progress' && requestedPage === 1 ? 0 : requestedPage;
      const ids = scenario === 'duplicate' && page === 1 ? [2,4] : [page*2+1,page*2+2];
      const cards = ids.map(id => `<div class="search_container"><div class="search_container_name"><a href="/sports/fixture/${String(id).padStart(13,'0')}.html">グローブ fixture ${id}</a></div><div class="search_container_price"><span class="price"><span class="fsL">${(id*1000).toLocaleString('en-US')}</span><span class="fsS">円（税込）</span></span></div></div>`).join('');
      response.end(`<!doctype html><html lang="ja"><meta charset="utf-8"><title>グローブ | Joshin pagination fixture</title><body>${form(page)}<input data-field="HIT_COUNT" data-value="6"><input data-field="MAX_PAGE" data-value="3"><input data-field="PAGE_NO" data-value="${page}"><div class="title_bar"><span>6件中${page*2+1}～${page*2+2}件</span></div>${pager(page)}${cards}${pager(page)}${script}</body></html>`);
    }
  } else {
    row.status = 404;
    response.statusCode = 404;
    response.end('Not found');
  }
  write('requests.json', requests);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
write('ready.json', {scenario, homepage_url:`http://127.0.0.1:${server.address().port}/top.html`});
console.log(JSON.stringify({ready:true,scenario}));
for (const signal of ['SIGTERM','SIGINT']) process.on(signal, () => server.close(() => process.exit(0)));
