"""Shared decision contract, strong recent-evidence baseline and audit. stdlib only."""
import json,time,pathlib,statistics,resource,hashlib,math
P=pathlib.Path(__file__).parent

def adaptive(r):
 s=r['state'];h=s['history'];recent=h[-1].lower();allh=' '.join(h).lower()
 if s['stamina']<20 or (s['hp']<20 and s['stamina']<35):return 'conserve'
 if s['seconds_remaining']<=5 and s['hp']>s['enemy_hp']:return 'conserve'
 if 'recovering' in recent:return 'pressure'
 if 'ignored' in recent or 'ignores' in recent:return 'conserve' if s['stamina']<30 else 'hold'
 # Count only explicit observations, require >=3 supporting events, use latest evidence.
 repeated=any(x in recent for x in ['three','four','five','last 3','last 4'])
 if repeated and ('dodge' in recent):return 'catch'
 if repeated and ('block' in recent):return 'break'
 if repeated and ('swing' in recent):return 'bait'
 if 'another' in recent and ('four' in allh):
  if 'dodge' in recent:return 'catch'
  if 'block' in recent:return 'break'
 if s['stamina']<30:return 'conserve'
 return 'pressure' if s['personality']=='assertive' else 'hold'

def pack(choice,row,elapsed_ms):
 return dict(choice=choice,state_version=row['state_version'],observed_at_ms=row['observed_at_ms'],available_at_ms=row['observed_at_ms']+elapsed_ms)

def validate(output,current,deadline_ms=250):
 if output.get('choice') not in current['choices']:return 'invalid'
 if output.get('state_version')!=current['state_version']:return 'stale_version'
 age=output.get('available_at_ms',float('inf'))-current['observed_at_ms']
 if not math.isfinite(age) or age<0:return 'invalid'
 if output.get('observed_at_ms')!=current['observed_at_ms']:return 'stale_timestamp'
 if age>deadline_ms:return 'expired'
 return 'usable'

def stats(ts):
 return dict(n=len(ts),median_ms=statistics.median(ts),max_ms=max(ts),reference_p95_ms=sorted(ts)[math.ceil(.95*len(ts))-1])

def score(pred,rows,gold):
 assert len(pred)==len(rows) and len({x['id'] for x in pred})==len(rows)
 by={r['id']:r for r in rows};out={}
 for split in ['dev','heldout','all']:
  xs=[x for x in pred if split=='all' or by[x['id']]['split']==split]
  out[split]=dict(n=len(xs),acceptable=sum(x['choice'] in gold[x['id']]['acceptable'] for x in xs),clearly_bad=sum(x['choice'] in gold[x['id']]['clearly_bad'] for x in xs),usable_acceptable=sum(x['choice'] in gold[x['id']]['acceptable'] and x['validity']=='usable' for x in xs),deadline_exceeded=sum(x['ms']>250 for x in xs),invalid=sum(x['validity']=='invalid' for x in xs),stale_or_expired=sum(x['validity'].startswith('stale') or x['validity']=='expired' for x in xs),timing=stats([x['ms'] for x in xs]))
 out['by_series']={sid:dict(n=len(xs:=[x for x in pred if by[x['id']]['series']==sid]),acceptable=sum(x['choice'] in gold[x['id']]['acceptable'] for x in xs),clearly_bad=sum(x['choice'] in gold[x['id']]['clearly_bad'] for x in xs)) for sid in sorted({r['series'] for r in rows})}
 return out

if __name__=='__main__':
 import sys
 dest=pathlib.Path(sys.argv[1]);dest.mkdir(parents=True,exist_ok=True)
 rows=json.loads((P/'inputs.json').read_text());gold=json.loads((P/'gold.json').read_text())
 manifest=json.loads((P/'fixture-manifest.json').read_text())
 for n,h in manifest.items():assert hashlib.sha256((P/n).read_bytes()).hexdigest()==h
 results={}
 # Include every fixed tactic so no deliberately weak fixed comparator is selected after results.
 for name,fn in [('adaptive',adaptive)]+[(f'fixed_{k}',lambda r,k=k:k) for k in rows[0]['choices']]:
  pred=[]
  for row in rows:
   start=time.perf_counter();r=json.loads(json.dumps(row));choice=fn(r);elapsed=(time.perf_counter()-start)*1000;output=pack(choice,r,elapsed);validity=validate(output,r)
   pred.append(dict(id=r['id'],**output,ms=elapsed,validity=validity))
  results[name]=dict(predictions=pred,metrics=score(pred,rows,gold),first_ms=pred[0]['ms'],warm=stats([p['ms'] for p in pred[1:]]))
 (dest/'baseline.json').write_text(json.dumps(dict(results=results,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),indent=2)+'\n')
 print({k:v['metrics']['heldout'] for k,v in results.items()})
