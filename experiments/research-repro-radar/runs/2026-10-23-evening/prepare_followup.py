import json,copy,sys,re,html.parser
from pathlib import Path
r=Path(__file__).parent;s=Path('/workspace/evening-radar-20261023/.agents/skills/research-repro-radar');sys.path.insert(0,str(s/'scripts'));import native_judge
base=json.loads(Path('/workspace/radar-native-20261023/batch-5.request.json').read_text());schema=json.loads((s/'references/native-candidate.schema.json').read_text());ev=[]
for f in ['review-evidence.json','followup-evidence.json','model-details-evidence.json']:ev+=json.loads((r/f).read_text())
ids=['2610.31574','2610.31062','2610.26836','2610.28679','2610.19705','2610.30949'];rows={x['id']:x for x in json.loads((r/'review-targets.json').read_text())};payload=[]
for ident in ids:
 sources=[]
 for e in ev:
  if e['id']!=ident or 'text'not in e:continue
  t=e['text']
  if e['kind'].startswith('model') and t.startswith('{'):
   d=json.loads(t);t=json.dumps({k:d.get(k)for k in ['id','sha','lastModified','cardData','safetensors','siblings']},ensure_ascii=False)
  if e['kind']=='independent-signal':
   class T(html.parser.HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,d):self.parts.append(d)
   p=T();p.feed(t);t=' '.join(p.parts)
  sources.append({'url':e['url'],'kind':e['kind'],'text':re.sub(r'\s+',' ',t)[:26000]})
 payload.append({'candidate':rows[ident],'sources':sources})
sc={'type':'object','additionalProperties':False,'required':['candidates'],'properties':{'candidates':{'type':'array','minItems':len(ids),'maxItems':len(ids),'prefixItems':[],'items':False}}}
for ident in ids:
 z=copy.deepcopy(schema);z['properties']['candidate_id']['const']=ident;sc['properties']['candidates']['prefixItems'].append(z)
instruction=base['instruction']+'''\n追加一次資料で訂正する。CPUやDL枠を満たせないことはAの価値判定の否定ではなく実行保留。TTRは公式collectionに既成checkpointが実在し学習必須とは言えない。ただし345m名と実446631936パラメータ、893293472bytes重み、configのflash_attention_3を区別。1b=2247931312bytes、vision conv=9465033968bytesで上限超。license未確認。HF10upvoteと独立媒体AI Weeklyの当日解説の2信号を資料から吟味し、単なる転載が独立信号として弱ければその限界を明記。大きな効果は著者値だけでは禁止。
SCOPEはNLPの分類/抽出/判定経路。データPlurai/SCOPE MIT実在。neural predicatesのデータ/プログラム最適化を必ず重み学習だと断定しない。Same Simulationはツール出力の読み取り/構造化抽出/AI開発への用途であり、外部Web検索用条件を適用しない。Meta Expertも内部MoEアクセスであり検索サービスではない。VerifierBench/Gavelは著者の追加結果68.3vs62.1に対して攻撃誤受理18.2vs7.4というtradeoffあり、API必須や100GiB×2はfull再現条件、小さな静的点検とは区別。RouteWeaver4Bとcost版の実IDあり、routerだけ8822894520bytes。IQuest公式IDと649978660440bytes、独自license実在。後継や更に小さい版の存在は未確認。
引用はMarkdown装飾を除去して作り直さず、source textそのまま短い完全一致引用を使用。各source全体で25words以内。evidence URLは実際の引用元に一致させる。unconfirmedには確認できた具体項目と不明を分けて明記。'''
req=native_judge.prepare_request(instruction,json.dumps(payload,ensure_ascii=False),sc);Path('/workspace/radar-native-20261023/followup.request.json').write_text(json.dumps(req,ensure_ascii=False));print(req['request_id'],req['task_sha256'])
