"""Read-only primary-source snapshot; no model downloads or target-site crawl."""
import concurrent.futures, datetime, hashlib, json, pathlib, subprocess, time, urllib.request
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/daily-tech-20261011'
REPOS = ['docling-project/docling', 'docling-project/docling.rs', 'apify/crawlee', 'unclecode/crawl4ai', 'lightpanda-io/browser', 'microsoft/playwright', 'adbar/trafilatura', 'webrecorder/browsertrix-crawler', 'webrecorder/warcio', 'CommonCrawl/cc-index-server', 'RapidAI/RapidOCR', 'PaddlePaddle/PaddleOCR', 'huggingface/transformers', 'onnxruntime/onnxruntime', 'urchade/GLiNER', 'fastino-ai/GLiNER2']
def fetch(url):
    start=time.perf_counter()
    rec={'url':url,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'Searching-Experiment-daily-tech/20261011'})
        with urllib.request.urlopen(req, timeout=40) as r:
            raw=r.read(); rec.update(status=r.status,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        rec['data']=json.loads(raw)
    except Exception as e: rec['error']=str(e)
    rec['seconds']=time.perf_counter()-start
    return rec
if __name__=='__main__':
    urls=[f'https://api.github.com/repos/{repo}{suffix}' for repo in REPOS for suffix in ['', '/releases?per_page=3']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool: result=list(pool.map(fetch,urls))
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'sources.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for repo,meta,rel in zip(REPOS,result[::2],result[1::2]):
        d=meta.get('data',{}); releases=rel.get('data',[])
        print(repo, d.get('stargazers_count'), (d.get('license') or {}).get('spdx_id'), d.get('pushed_at'))
        for r in releases if isinstance(releases,list) else []:
            print(r['tag_name'],r['published_at'],r.get('body','')[:6000])
        if 'error' in meta or 'error' in rel: print(meta.get('error'),rel.get('error'))
