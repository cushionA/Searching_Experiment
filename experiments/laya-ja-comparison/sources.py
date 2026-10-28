"""Retrieve primary metadata only; never import candidate source."""
import urllib.request,json,pathlib,concurrent.futures,time,hashlib
p=pathlib.Path('benchmarks/daily-tech-20261029');out=p/'sources';out.mkdir(exist_ok=True)
urls={
'laya-repo':'https://api.github.com/repos/nandhakishorm/laya',
'laya-releases':'https://api.github.com/repos/nandhakishorm/laya/releases?per_page=3',
'laya-contributors':'https://api.github.com/repos/nandhakishorm/laya/contributors?per_page=100',
'laya-models':'https://huggingface.co/api/models?author=convaiinnovations&limit=100&sort=lastModified&direction=-1',
'jev-ja-models':'https://huggingface.co/api/models?author=argos1111&limit=100&sort=lastModified&direction=-1',
'laya-card':'https://huggingface.co/convaiinnovations/laya-multilingual/raw/1720e3e3357cfe1e281542e223f8273b0890ca34/README.md',
}
for name,repo in [('playwright','microsoft/playwright'),('crawlee','apify/crawlee'),('crawl4ai','unclecode/crawl4ai'),('trafilatura','adbar/trafilatura'),('paddleocr','PaddlePaddle/PaddleOCR'),('browsertrix','webrecorder/browsertrix'),('pagesift','YOLOW92/pagesift'),('ollaya','ollaya-dev/ollaya')]:urls[name]='https://api.github.com/repos/'+repo+'/releases?per_page=2'
def get(item):
 name,url=item;t=time.monotonic()
 try:
  b=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'research-evidence'}),timeout=30).read();(out/(name+'.txt')).write_bytes(b)
  return dict(name=name,url=url,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),seconds=time.monotonic()-t,status='ok')
 except Exception as e:return dict(name=name,url=url,status='error',error=str(e))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:r=list(ex.map(get,urls.items()))
(p/'source-manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
