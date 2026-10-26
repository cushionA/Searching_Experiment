import http from 'node:http';

export async function startFixtureServer() {
  const server = http.createServer((req, res) => {
    if (req.url === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(`<!doctype html><meta charset="utf-8"><title>Local visual fixture</title>
<style>iframe{border:0}#challenge{display:none}.rc-imageselect-tile{display:inline-block;width:105px;height:85px;margin:2px;border:1px solid #777}.rc-imageselect-tile img{width:100%;height:100%}</style>
<div id="widget"></div><script>
const mode=new URLSearchParams(location.search).get('mode')||'static'; window.record={clicks:[],submits:0,refreshes:0};
if(mode!=='none'){
 const a=document.createElement('iframe');a.id='anchor';a.title='reCAPTCHA';a.style.width='300px';a.style.height='70px';document.body.append(a);a.contentDocument.open();a.contentDocument.write('<div id="recaptcha-anchor" role="checkbox" aria-checked="false">I am human</div>');a.contentDocument.close();
 if(mode==='checkbox') {a.contentDocument.getElementById('recaptcha-anchor').onclick=()=>a.contentDocument.getElementById('recaptcha-anchor').setAttribute('aria-checked','true');}
 else {
  const c=document.createElement('iframe');c.id='challenge';c.title='recaptcha challenge';c.style.display='block';c.style.width='420px';c.style.height='520px';document.body.append(c);
  const instructions=mode==='dynamic'||mode==='no-refresh'?'Select all images with <strong>bicycles</strong>. Click verify once there are none left':'Select all images with <strong>bicycles</strong>';
  c.contentDocument.open();c.contentDocument.write('<style>body{font:16px sans-serif;margin:8px}.rc-imageselect-instructions{margin:0 0 8px}.rc-imageselect-target{display:grid;grid-template-columns:repeat(3,110px);gap:4px;width:max-content}.rc-imageselect-tile{display:block;width:108px;height:88px;box-sizing:border-box;border:2px solid #777;cursor:pointer}.rc-imageselect-tile img{display:block;width:100%;height:100%}.rc-imageselect-tileselected{outline:4px solid #1769ff;outline-offset:-4px}.rc-imageselect-error-select-more{display:none;color:#a00}</style><div class="rc-imageselect-instructions">'+instructions+'</div><div id="rc-imageselect-target" class="rc-imageselect-target"></div><button id="recaptcha-verify-button">Verify</button><div class="rc-imageselect-error-select-more">Try again</div><div class="rc-doscaptcha-body-text"></div>');c.contentDocument.close();
  const d=c.contentDocument, grid=d.querySelector('#rc-imageselect-target'), count=['B','empty-B'].includes(mode)?16:9, answer=mode==='empty-B'?[]:mode==='B'?[0,3,7,12]:mode==='dynamic'||mode==='no-refresh'?[1]:[1,5], resolved=new Set();
  if(['B','empty-B'].includes(mode)) { grid.style.gridTemplateColumns='repeat(4,110px)'; c.style.width='480px'; }
  if(mode==='error') d.querySelector('.rc-doscaptcha-body-text').textContent='Service rejected';
  const makeImage=(n,active=false)=>'data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect width="100%" height="100%" fill="'+(active?'#e65b39':['#eee','#cce','#cec','#ecc'][n%4])+'"/><text x="40" y="48" font-size="24">'+n+'</text></svg>');
  for(let i=0;i<count;i++){const td=d.createElement('td');td.className='rc-imageselect-tile';td.dataset.index=i;const im=d.createElement('img');im.src=makeImage(i);td.append(im);td.onclick=()=>{parent.record.clicks.push(i);td.classList.toggle('rc-imageselect-tileselected');if((mode==='dynamic'||mode==='no-refresh')&&answer.includes(i)){td.classList.add('rc-imageselect-dynamic-selected');if(mode==='dynamic')setTimeout(()=>{im.src=makeImage(i+30,true);td.classList.remove('rc-imageselect-dynamic-selected','rc-imageselect-tileselected');resolved.add(i);parent.record.refreshes++},120)}};grid.append(td)}
  d.querySelector('#recaptcha-verify-button').onclick=()=>{parent.record.submits++;const selected=[...d.querySelectorAll('.rc-imageselect-tileselected')].map(t=>Number(t.dataset.index));const passed=mode==='dynamic'?resolved.size===answer.length&&selected.length===0:answer.every(i=>selected.includes(i))&&selected.length===answer.length;if(passed)a.contentDocument.getElementById('recaptcha-anchor').setAttribute('aria-checked','true');else d.querySelector('.rc-imageselect-error-select-more').style.display='block';};
 }
}
</script>`);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return { origin: `http://127.0.0.1:${port}`, close: () => new Promise((resolve, reject) => server.close((e) => e ? reject(e) : resolve())) };
}
