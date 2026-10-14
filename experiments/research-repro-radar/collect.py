"""Read-only HF discovery. Store source responses and honest conservative estimates."""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'runs/2026-10-12-evening'
RAW = RUN / 'sources'
QUERIES = ['text classification efficient', 'information extraction', 'small language model cpu inference', 'lightweight vision', 'coding agent', 'text summarization', 'structured extraction noisy text', 'quantization int4 inference', 'distillation encoder', 'cpu inference throughput']
URLS = [('trending', 'https://huggingface.co/api/daily_papers?sort=trending'), ('new', 'https://huggingface.co/api/daily_papers?sort=publishedAt')]
URLS += [('query-'+str(i), 'https://huggingface.co/api/papers/search?q='+urllib.parse.quote(q)) for i,q in enumerate(QUERIES)]

def fetch(pair):
    label,url=pair
    start=time.monotonic()
    entry={'label':label,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar/1.0 read-only literature research'}),timeout=30) as response:
            data=response.read(8_000_001)
            if len(data)>8_000_000: raise ValueError('response cap exceeded')
            entry.update(status=response.status,link=response.headers.get('Link'),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        parsed=json.loads(data)
        if not isinstance(parsed,list): raise ValueError('expected list')
        (RAW/(label+'.json')).write_bytes(data)
        entry['count']=len(parsed)
        return entry,parsed
    except Exception as e:
        entry['error']=type(e).__name__+': '+str(e)
        return entry,[]
    finally:
        entry['seconds']=time.monotonic()-start

def main():
    global RUN, RAW
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True,help='New evidence directory; refuses overwrite')
    args=parser.parse_args()
    from judge_preflight import require_judge
    require_judge()
    RUN=args.run
    RAW=RUN/'sources'
    if RUN.exists(): raise SystemExit('Run directory already exists; preserve previous evidence')
    RAW.mkdir(parents=True)
    results=list(cf.ThreadPoolExecutor(max_workers=4).map(fetch,URLS))
    records={}
    for entry,rows in results:
        for position,row in enumerate(rows,1):
            p=row.get('paper',row)
            key=p.get('id')
            if not key or not p.get('title'): continue
            if key in records:
                records[key]['discovered_via'].append(entry['label'])
                continue
            # No guessed model requirements: 20 minutes is a bounded inspection plan,
            # not a claim that inference can finish. Unknown resources gate execution.
            r={'id':key,'arxiv_id':key,'title':p['title'],'source':'huggingface',
               'url':'https://arxiv.org/abs/'+key,'abstract':p.get('summary',''),
               'published_at':p.get('publishedAt'),'has_code':bool(p.get('githubRepo')),
               'has_model':False,'requires_gpu':False,'estimated_cpu_minutes':20,
               'resource_status':'unverified; execution prohibited until inspection',
               'estimate_basis':'20 minute maximum bounded check allocation; not measured runtime',
               'github_url':p.get('githubRepo'),'discovered_via':[entry['label']],
               'evidence_file':'sources/'+entry['label']+'.json','evidence_position':position}
            if entry['label']=='trending': r['trend_rank']=position
            records[key]=r
    (RUN/'discovery-ledger.json').write_text(json.dumps({'queries':QUERIES,'requests':[x[0] for x in results],
        'unique_candidates':len(records),'pagination':'API returned lists without next link; complete responses retained. Search backend result ceiling is not exhaustive coverage.',
        'resource_policy':'requires_gpu=false is an unverified schema default, NOT evidence of CPU feasibility. External execution blocked separately.'},ensure_ascii=False,indent=2)+'\n')
    (RUN/'candidates.json').write_text(json.dumps(list(records.values()),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'unique':len(records),'requests':[{'label':a['label'],'count':a.get('count'),'error':a.get('error')} for a,b in results]}))

if __name__=='__main__': main()
