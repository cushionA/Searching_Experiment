exec(open('/workspace/radar-private/20261014/fetch_review.py').read().split('jobs=[]')[0])
jobs=[('https://arxiv.org/html/'+x['id'],f'{i:02d}-full') for i,x in enumerate(rows) if not x['id'].startswith('hf:')]
res=list(concurrent.futures.ThreadPoolExecutor(max_workers=6).map(lambda p:fetch(*p),jobs));(r/'fulltext-fetch-ledger.json').write_text(json.dumps(res,indent=2));print({'ok':sum('status'in x for x in res),'errors':[(x['url'],x.get('error')) for x in res if 'error'in x]})
