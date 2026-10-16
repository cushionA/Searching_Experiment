import concurrent.futures as cf,datetime as dt,hashlib,json,re,urllib.request
from pathlib import Path
from html.parser import HTMLParser
R=Path(__file__).parent
class Parser(HTMLParser):
 def __init__(self):super().__init__();self.hide=0;self.parts=[]
 def handle_starttag(self,t,a):
  if t in ['script','style']:self.hide+=1
 def handle_endtag(self,t):
  if t in ['script','style']:self.hide=max(0,self.hide-1)
 def handle_data(self,d):
  if not self.hide:self.parts.append(d)
def fetch(job):
 name,url=job;o={'name':name,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar public evidence'}),timeout=25) as r:b=r.read(4000001);o['status']=r.status
  if len(b)>4000000:raise ValueError('cap')
  path=R/'primary'/name;path.write_bytes(b);o.update(file=str(path.relative_to(R)),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
  if name.endswith('.html'):
   p=Parser();p.feed(b.decode('utf-8','replace'));(path.with_suffix('.txt')).write_text(re.sub(r'\s+',' ',' '.join(p.parts)))
 except Exception as e:o['error']=type(e).__name__+': '+str(e)
 return o
jobs=[('oprd-full.html','https://arxiv.org/html/2610.21496v1'),('hyco-full.html','https://arxiv.org/html/2610.21436v1'),('yolo27.html','https://docs.ultralytics.com/models/yolo27/'),('mdpi-full.html','https://www.mdpi.com/1996-1073/19/20/4893'),('Qwen3.5-4B.json','https://huggingface.co/api/models/Qwen/Qwen3.5-4B'),('Unlimited-OCR.json','https://huggingface.co/api/models/baidu/Unlimited-OCR'),('Edge0-8B.json','https://huggingface.co/api/models/Edge0/Edge0-8B-A1B-preview'),('Remory-Qwen.json','https://huggingface.co/api/models/mocoV3/Qwen3.8-27B-REMORY-1.9B')]
with cf.ThreadPoolExecutor(max_workers=4) as ex:res=list(ex.map(fetch,jobs))
(R/'detail-ledger.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n');print(res)
