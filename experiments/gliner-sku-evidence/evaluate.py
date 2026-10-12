#!/usr/bin/env python3
"""Extraction diagnostics; machine citations are partial references, never gold."""
import collections, hashlib, json, re, statistics, time, zipfile
from pathlib import Path
from prepare import OUT, ROOT, RESULTS, BASE, leaf, dump

def merged_intervals(spans):
 out=[]
 for a,b in sorted((x['start'],x['end']) for x in spans):
  if out and a<=out[-1][1]:out[-1][1]=max(b,out[-1][1])
  else:out.append([a,b])
 return out

def measure(spans,refs):
 intervals=merged_intervals(spans);overlap=whole=0
 for r in refs:
  ov=full=False
  for hit in r['hits']:
   ov|=any(a<hit['end'] and b>hit['start'] for a,b in intervals)
   full|=any(a<=hit['start'] and b>=hit['end'] for a,b in intervals)
  overlap+=ov;whole+=full
 return {'partial_reference_quotes':len(refs),'any_character_overlap':overlap,'whole_quote_retained':whole,'retained_characters':sum(b-a for a,b in intervals)}

def expand(spans,doc):
 return [s for s in doc['segments'] if any(s['start']<x['end'] and s['end']>x['start'] for x in spans)]

def run():
 docs=json.loads((OUT/'input.json').read_text());refs=json.loads((OUT/'weak-references.json').read_text());z=zipfile.ZipFile(RESULTS/BASE)
 probes=json.loads((OUT/'source-audit-probes.json').read_text());probe_results=[]
 summary=[];details=[];baseline_time=0;times=[];invalid=0;source_invalid=0;anchor=0;reference_dups=len(refs)-len({(r['product_id'],r['evidence']['quote']) for r in refs})
 for doc in docs:
  pid=doc['product_id'];files=list(OUT.glob('*-'+pid+'.json'))
  if len(files)!=1:raise ValueError((pid,'expected exactly one run',files))
  model=json.loads(files[0].read_text());spans=model['entities'];times.extend(c['seconds'] for c in model['chunk_calls'])
  t=time.perf_counter();baseline=[];queries=[]
  for c in doc['unresolved_conditions']:
   if c['kind']!='rakuten':continue
   if c['status']=='residual':queries.extend(x['text'] for x in c['residual'] or [] if x['verify_with']=='au')
   else:queries.append(c['value'])
  for q in set(queries):
   for m in re.finditer(re.escape(q),doc['text']):baseline.append({'start':m.start(),'end':m.end(),'text':m.group(),'query':q})
  baseline_time+=time.perf_counter()-t
  rrefs=[r for r in refs if r['product_id']==pid]
  candidates=[]
  for s in spans:
   if doc['text'][s['start']:s['end']]!=s['text']:invalid+=1
   mapped=[]
   for seg in expand([s],doc):
    a=max(s['start'],seg['start']);b=min(s['end'],seg['end']);rs=seg['raw_start']+a-seg['start'];re_=rs+b-a
    raw=z.read(seg['raw_file']);container=leaf(json.loads(raw),seg['json_path']);quote=container[rs:re_]
    valid=hashlib.sha256(raw).hexdigest()==seg['raw_sha256'] and quote==doc['text'][a:b]
    source_invalid+=not valid
    mapped.append({'raw_file':seg['raw_file'],'sha256':seg['raw_sha256'],'json_path':seg['json_path'],'start':rs,'end':re_,'quote':quote,'inside_anchor':seg['inside_anchor'],'valid':valid})
   in_anchor=any(p['inside_anchor'] for p in mapped);anchor+=in_anchor
   candidates.append({**s,'raw_spans':mapped,'anchor_scope_risk':in_anchor,'row_applicability':'unassessed','condition_state':'unassessed'})
  methods={'literal_spans':baseline,'literal_lines':expand(baseline,doc),'gliner_spans':spans,'gliner_lines':expand(spans,doc),'all_text':[{'start':0,'end':len(doc['text'])}]}
  measures={name:measure(s,rrefs) for name,s in methods.items()}
  for probe in [r for r in probes if r['product_id']==pid]:
   probe_results.append({**probe,'measures':{name:measure(s,[probe]) for name,s in methods.items()}})
  summary.append({'product_id':pid,'text_characters':len(doc['text']),'model_span_count':len(spans),'model_fields':dict(collections.Counter(s['field'] for s in spans)),'model_seconds':model['seconds'],'measures':measures})
  details.append({'product_id':pid,'literal_queries':queries,'baseline':baseline,'model_candidates':candidates,'weak_references':rrefs})
 agg={m:{k:sum(s['measures'][m][k] for s in summary) for k in summary[0]['measures'][m]} for m in summary[0]['measures']}
 sec=sum(s['model_seconds'] for s in summary)
 result={'products':len(docs),'model_spans':sum(s['model_span_count'] for s in summary),'invalid_literal_spans':invalid,'invalid_raw_source_mappings':source_invalid,'spans_touching_html_anchor':anchor,'weak_reference_duplicate_rows':reference_dups,'aggregate':agg,'baseline_literal_scan_seconds':baseline_time,'inference_seconds':sec,'products_per_second':len(docs)/sec,'chunk_calls':len(times),'chunk_median_seconds':statistics.median(times),'chunk_p95_seconds':sorted(times)[max(0,int(len(times)*.95)-1)],'per_product':summary,'semantic_metrics':None,'reason_semantic_metrics_unavailable':'No independent field/span/condition/row-applicability gold; existing case labels and partial citations do not define it; no decision model was run.'}
 dump(OUT/'probe-retention.json',probe_results);dump(OUT/'evaluation.json',result);dump(OUT/'candidates.json',details);print(json.dumps({k:v for k,v in result.items() if k!='per_product'},ensure_ascii=False,indent=2))
if __name__=='__main__':run()
