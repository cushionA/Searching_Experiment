"""Refresh explicit public sources to a NEW destination without invoking their content.
Usage: python3 snapshot_urls.py /tmp/new-sources.json
No auth, model, crawl or inference. Errors are retained; no retry loop.
"""
import concurrent.futures,datetime,hashlib,json,pathlib,sys,time,urllib.request
urls=json.loads(pathlib.Path(__file__).with_name('source-urls.json').read_text())
def fetch(url):
 start=time.perf_counter();row={'url':url,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Searching-Experiment-daily-tech/20261011'}),timeout=40) as response:
   raw=response.read();row['status']=response.status
  row.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
  if 'api.github.com' in url or url.endswith('.json'):
   data=json.loads(raw)
   if '/commits/7146dd' in url:
    data={k:data[k] for k in ['sha','html_url','commit','stats']}
    row['projection']='commit metadata only; no patches or model answer bodies'
   row['data']=data
  else:row['text']=raw.decode()
 except Exception as exc:row['error']=str(exc)
 row['seconds']=time.perf_counter()-start;return row
if __name__=='__main__':
 with open(sys.argv[1],'x') as out:
  with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(fetch,urls))
  json.dump(rows,out,ensure_ascii=False,indent=2);out.write('\n')
