#!/usr/bin/env python3
"""Audit current shared v2/v3 input/source/split integrity without running models."""
import collections,json,zipfile
from prepare import RESULTS,OUT,sha,lines,walk,leaf,dump

def main():
 names=['20261011-sku-independent-real-eval-v2.zip','20261012-sku-independent-real-eval-v3.zip','20261012-sku-independent-real-eval-v2-v3-token-inputs.zip']
 zs=[zipfile.ZipFile(RESULTS/n) for n in names];idx={n:z for z in zs for n in z.namelist()};cache={};decoded={};report={};issues=[]
 def raw(n):
  n=n.replace('\\','/')
  if n.startswith('/workspace/Searching_Experiment/'):n=n[len('/workspace/Searching_Experiment/'):]
  if n not in cache:cache[n]=idx[n].read(n)
  return cache[n]
 def obj(n):
  if n not in decoded:decoded[n]=json.loads(raw(n))
  return decoded[n]
 for v in ['v2','v3']:
  pref=f'.lab-output/sku-independent-real-20261011/eval-{v}/'; ps=lines(raw(pref+'run-token/inputs/products.jsonl'));cs=lines(raw(pref+'run-token/inputs/cases.jsonl'));gs=lines(raw(pref+'groups-token/groups.jsonl'))
  spans={json.dumps(s,sort_keys=True):s for x in ps+cs for s in walk(x)};checks=collections.Counter()
  for s in spans.values():
   try:
    b=raw(s['raw_file']);assert sha(b)==s['sha256'],'sha_mismatch'
    loc=s['locator']
    if loc['kind']=='html_text':
     k=(s['raw_file'],loc['encoding'])
     if k not in decoded:decoded[k]=b.decode(loc['encoding'])
     val=decoded[k]
    elif loc['kind']=='json_leaf':val=leaf(obj(s['raw_file']),loc['json_path'])
    elif loc['kind']=='jsonl_leaf':val=leaf(json.loads(b.splitlines()[loc['line']-1]),loc['json_path'])
    else:raise ValueError(loc['kind'])
    assert 0<=s['start']<s['end']<=len(val),'invalid_offset'
    assert val[s['start']:s['end']]==s['quote'],'quote_mismatch'
    checks['valid']+=1
   except Exception as e:
    msg=str(e) if isinstance(e,AssertionError) else type(e).__name__;checks[msg]+=1;issues.append({'version':v,'error':msg,'span':s})
  gm={g['dossier_id']:g for g in gs}
  assert all(c['dossier_id'] in gm for c in cs)
  split={name:[c for c in cs if gm[c['dossier_id']]['split']==name] for name in ['dev','test']}
  assert sum(map(len,split.values()))==len(cs)
  def overlap(key):return len({gm[c['dossier_id']]['group_id'] if key=='group_id' else c[key] for c in split['dev']} & {gm[c['dossier_id']]['group_id'] if key=='group_id' else c[key] for c in split['test']})
  def urls(name):return {c['rakuten_selected'].get('url') for c in split[name]}-{None}
  report[v]={'source_path_resolution':'Windows separators normalized in read-only lookup; no source edit','split_resolution':'dossier_id joined to authoritative groups-token/groups.jsonl; case split fields are null','products':len(ps),'cases':len(cs),'groups':len({g['group_id'] for g in gs}),'split_cases':{k:len(x) for k,x in split.items()},'duplicates_case_id':len(cs)-len({c['case_id'] for c in cs}),'duplicate_pair_ref':len(ps)-len({p['pair_ref'] for p in ps}),'dev_test_overlap':{k:overlap(k) for k in ['case_id','dossier_id','group_id','au_product_id']},'rakuten_url_overlap':len(urls('dev') & urls('test')),'pair_status':dict(collections.Counter(c['pair_status'] for c in cs)),'source_spans':dict(checks),'source_span_count':len(spans),'au_rows_total':sum(len(p['au_rows']) for p in ps),'source_sha256':{k:sha(raw(pref+'run-token/inputs/'+k+'.jsonl')) for k in ['products','cases']}}
 dump(OUT/'latest-data-audit.json',report);dump(OUT/'latest-source-issues.json',issues);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
