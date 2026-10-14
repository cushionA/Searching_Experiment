exec(open('/workspace/radar-private/20261014/fetch_review.py').read().split('jobs=[]')[0])
allrows=json.loads((r/'candidates.json').read_text()); ids=['2502.11880','2610.08699','2606.23050','2609.18063'];extra=[x for ident in ids for x in allrows if x['id']==ident];(r/'extra-input.json').write_text(json.dumps(extra,ensure_ascii=False,indent=2));jobs=[]
for i,x in enumerate(extra,20):
 jobs.extend([(x['url'],f'{i:02d}-primary'),('https://arxiv.org/html/'+x['id'],f'{i:02d}-full')]);g=x.get('github_url')
 if g:
  repo=g.rstrip('/').split('github.com/')[-1];jobs.extend([(f'https://api.github.com/repos/{repo}',f'{i:02d}-github'),(f'https://api.github.com/repos/{repo}/readme',f'{i:02d}-readme')])
res=list(concurrent.futures.ThreadPoolExecutor(max_workers=6).map(lambda p:fetch(*p),jobs));(r/'extra-fetch-ledger.json').write_text(json.dumps(res,indent=2));print({'extra':[(x['id'],x['title'],x.get('github_url')) for x in extra],'ok':sum('status'in x for x in res),'fail':[x for x in res if 'error'in x]})
