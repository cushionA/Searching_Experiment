import json,time,resource,sys
start=time.perf_counter();out=[]
for r in json.load(open(sys.argv[1])):
 t=time.perf_counter();s=r['state']
 if r['group']=='nlp':
  pred='cancelled' if '中止' in s or '取りやめ' in s else 'unknown' if any(w in s for w in ['未定','再検討','決まっていません','発表していません']) else 'held'
 else:
  s=s.split('現在の状態: ',1)[1]
  if s.startswith('{'):v=json.loads(s);door=v['扉が開いている'];key=v['鍵を持っている']
  else:door='扉は開いています' in s;key='鍵は持っています' in s
  pred='advance' if door else 'unlock' if key else 'wait'
 out.append({'id':r['id'],'prediction':pred,'seconds':time.perf_counter()-t})
print(json.dumps({'predictions':out,'wall_seconds':time.perf_counter()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
