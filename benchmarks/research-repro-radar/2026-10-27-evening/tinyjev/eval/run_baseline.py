import json,re,time,resource,sys
from pathlib import Path
items=json.loads(Path(sys.argv[1]).read_text());out=[];start=time.perf_counter()
keywords={'billing':['charg','invoice','fee','money','payment','refund'],'delivery':['parcel','tracking','courier','delivered','shipment'],'access':['password','sign in','login','verification','unlock','account'],'technical':['crash','freez','export','bug','empty file']}
for x in items:
 t=time.perf_counter();s=x['state']
 if x['domain']=='nlp':
  scores={k:sum(w in s.lower() for w in ws) for k,ws in keywords.items()};pred=max(scores,key=scores.get)
 else:
  if isinstance(s,dict):hp,enemy,ammo=s['health'],s['enemy_visible'],s['ammo']
  else:hp=int(re.search(r'Health is (\d+)',s)[1]);ammo=int(re.search(r'remaining: (\d+)',s)[1]);enemy='An enemy is visible.' in s
  pred='retreat' if hp<=25 else 'attack' if enemy and ammo>0 else 'reload' if ammo==0 else 'explore'
 out.append({'id':x['id'],'choice':pred,'wall_seconds':time.perf_counter()-t})
Path(sys.argv[2]).write_text(json.dumps({'predictions':out,'total_seconds':time.perf_counter()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},indent=2)+'\n')
