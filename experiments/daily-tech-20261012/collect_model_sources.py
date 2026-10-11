#!/usr/bin/env python3
"""Snapshot public HF family search and selected model metadata, without downloading weights."""
import json, urllib.request, urllib.parse
from datetime import datetime, timezone
BASE='https://huggingface.co/api/models'
def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Searching-Experiment-daily-tech/2026-10-12'})
    with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)
IDS=['Qwen/Qwen3.8-27B','Qwen/Qwen3.8-2.4T-A95B','Qwen/Qwen3.8-Flash-Next','Qwen/Qwen3.8-Flash-Next-FP8','togethercomputer/Tev1-0.8B-experimental','togethercomputer/Tev1-4B-experimental','jaredpalmer/kev-0.8b','fastino/GLiNER2.5-small-v1','fastino/gliner2.5-base-v1','fastino/gliner2.5-multi-v1','fastino/GLiNER2.5-Decide']
out={'collected_at_utc':datetime.now(timezone.utc).isoformat(),'source':'Hugging Face public model API; metadata only, no weights downloaded','models':{},'family_queries':{},'errors':[]}
for mid in IDS:
 try:
  d=get(BASE+'/'+mid)
  out['models'][mid]={k:d.get(k) for k in ['id','sha','lastModified','downloads','likes','pipeline_tag','tags','cardData','safetensors','siblings']}
 except Exception as e: out['errors'].append({'id':mid,'error':type(e).__name__+': '+str(e)})
for label,params in [('qwen38_author_search',{'author':'Qwen','search':'Qwen3.8','limit':100,'sort':'lastModified','direction':-1}),('gliner25_family_search',{'search':'GLiNER2.5','limit':100,'sort':'lastModified','direction':-1})]:
 try:
  url=BASE+'?'+urllib.parse.urlencode(params)
  data=get(url)
  out['family_queries'][label]={'url':url,'params':params,'results':[{'id':x.get('id'),'sha':x.get('sha'),'lastModified':x.get('lastModified'),'downloads':x.get('downloads'),'pipeline_tag':x.get('pipeline_tag')} for x in data]}
 except Exception as e: out['errors'].append({'query':label,'error':type(e).__name__+': '+str(e)})
with open('benchmarks/daily-tech-20261012/model-sources.json','w') as f: json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps({'models':len(out['models']),'queries':{k:len(v.get('results',[])) for k,v in out['family_queries'].items()},'errors':out['errors'],'collected_at_utc':out['collected_at_utc']}))
