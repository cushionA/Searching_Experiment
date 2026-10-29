import json,pathlib,urllib.request,concurrent.futures,hashlib,datetime
P=pathlib.Path('benchmarks/tactical-intent-20261030/sources')
repos={'browser-use':'browser-use/browser-use','crawl4ai':'unclecode/crawl4ai','paddleocr':'PaddlePaddle/PaddleOCR','archivebox':'ArchiveBox/ArchiveBox','rerankers':'AnswerDotAI/rerankers','opendecider':'manjunathshiva/opendecider'}
urls={}
for n,r in repos.items():
 urls[n+'-repo']='https://api.github.com/repos/'+r
 urls[n+'-releases']='https://api.github.com/repos/'+r+'/releases?per_page=2'
 urls[n+'-readme']='https://raw.githubusercontent.com/'+r+'/HEAD/README.md'
def get(it):
 n,u=it
 try:
  b=urllib.request.urlopen(u,timeout=30).read();(P/(n+'.txt')).write_bytes(b)
  return dict(name=n,url=u,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
 except Exception as e:return dict(name=n,url=u,error=str(e))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as e:rows=list(e.map(get,urls.items()))
(P.parent/'source-manifest.json').write_text(json.dumps(rows,indent=2));print(rows)
