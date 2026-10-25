import json,re,sys,copy,html.parser
from pathlib import Path
r=Path('/workspace/evening-radar-20261025/experiments/research-repro-radar/runs/2026-10-25-evening');s=r.parents[3]/'.agents/skills/research-repro-radar';p=Path('/tmp/radar-native-20261025');sys.path.insert(0,str(s/'scripts'));import native_judge
class Text(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.p=[]
 def handle_data(self,d):self.p.append(d)
rows={x['id']:x for x in json.loads((r/'review-targets.json').read_text())};ev=[]
for n in ['review-evidence.json','followup-evidence.json','model-details-evidence.json','license-evidence.json','project-evidence.json']:ev+=json.loads((r/n).read_text())
sc=json.loads((s/'references/native-candidate.schema.json').read_text());instruction=json.loads((p/'batch-4.request.json').read_text())['instruction']
instruction+='\n追加確認による再判定。以前の判定は引き継がず今回証拠だけ評価。RAW-RAGが昨日詳細済みという先の自己申告は、この要求に既報根拠がないため使わない。OpenDSは評価基盤だけで自動Cにしない。archive-free新サイト発見/欠落ページ回復を測る評価設計への具体的用途も価値に含めるが、その改善実測とは呼ばない。IQuest公式のモデル配布と量子化版を実在確認済み。F2Gの必須RADIOライセンスを必ず読む。model family一覧にある別系統を後継と決めつけない。全件readingで実測0。独立2信号は同一著者資料2個を数えず、引用1つは25語以内、URL一致・短い完全一致の引用のみ。実行停止は保持する。'
for bi,ids in enumerate([['2610.31623','2610.30949','2610.31305'],['2610.30181','2610.30755']]):
 payload=[]
 for ident in ids:
  src=[]
  for e in ev:
   if e['id']!=ident or 'error'in e or 'text'not in e:continue
   t=e['text'];kind=e['kind']
   if kind in ['fulltext','project']:
    parser=Text();parser.feed(t);t=' '.join(parser.p)
   elif kind=='repo':
    d=json.loads(t);t=json.dumps({k:d.get(k)for k in ['full_name','created_at','pushed_at','stargazers_count','forks_count','license']})
   elif kind in ['model','radio-model','quantized']:
    d=json.loads(t);t=json.dumps({k:d.get(k)for k in ['id','lastModified','sha','cardData','safetensors','downloads','likes']} | {'siblings':[x for x in d.get('siblings',[])if x['rfilename'].endswith(('.safetensors','.pt','.gguf','.pth.tar'))]},ensure_ascii=False)
   t=re.sub(r'\s+',' ',t)
   src.append({'url':e['url'],'kind':kind,'fetched_at':e['at'],'text':t[:22000]})
  payload.append({'candidate':rows[ident],'sources':src})
 arr={'type':'array','minItems':len(ids),'maxItems':len(ids),'prefixItems':[],'items':False}
 for ident in ids:
  cs=copy.deepcopy(sc);cs['properties']['candidate_id']['const']=ident;arr['prefixItems'].append(cs)
 schema={'type':'object','required':['candidates'],'additionalProperties':False,'properties':{'candidates':arr}}
 req=native_judge.prepare_request(instruction,json.dumps(payload,ensure_ascii=False),schema);(p/f'followup-{bi}.request.json').write_text(json.dumps(req,ensure_ascii=False));print(bi,ids)
