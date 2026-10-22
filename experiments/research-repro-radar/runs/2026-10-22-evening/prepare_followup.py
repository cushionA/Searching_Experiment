"""Bind follow-up judgments to primary evidence and independent discussion signals."""
import copy,html.parser,json,re,sys
from pathlib import Path
R=Path(__file__).parent;ROOT=R.parents[3];S=ROOT/'.agents/skills/research-repro-radar';P=Path('/workspace/radar-native-20261022');sys.path.insert(0,str(S/'scripts'));import native_judge
base=json.loads((P/'batch-4.request.json').read_text())['instruction']
base+='''\n追加読解。必ず日本語。Aは価値、Bは条件付き、Cは不要。GPU/RAM/学習制約だけでrejectにしない。PTQ4SAMの前回rejectはそのため不適切、deferとして再評価。費用不足や未確認も保留。既報は詳細推薦しない。独立話題信号はHFの第三者投票・trendingと著者に属さない公開の技術議論を区別して数える。著者論文＋著者repoを2信号とはしない。TokenEconomizerはHF trending 41位＋Alena投稿と独立コメントの議論。AKVはHF Paper of day 1位/67upvote表示＋DAIR.AIの独立解説と第三者コメント。LinkedInはその議論が存在する一次証拠だけ。技術の正しさ/性能の根拠は論文と公式codeから取り、投稿内の誤り（TokenEconomizerがデフォルト承認制等）は公式README優先。第三者による再現成功とは認定しない。著者API換算コストと実際のserving費用を区別。各deep_dive_valueなら独立信号をreasonで説明し、商用license/導入/CPU GPU RAM/制約/次の短い検証仮説を明記。今回外部コード実行は0、large_effect禁止。GoiBERTはbackbone配布のみ・task head無し、GPU throughputとCPU値を混同しない。公開重みのサイズはAPI実ファイルを優先。PrismaCoder35B RLはID実在と重み未配布を区別。VX-OneはモデルIDがあっても重みなし。Ask-to-Distillは学習なしで配布済みstudent推論の価値を別評価、DL上限は保留理由。引用は正しいURLから短い完全一致、25語以内。'''
rows={x['id']:x for x in json.loads((R/'review-targets.json').read_text())};ev=json.loads((R/'review-evidence.json').read_text())+json.loads((R/'followup-evidence.json').read_text());detail=json.loads((R/'model-signal-evidence.json').read_text())
mapping={'2610.22732':['token-hf','token-community'],'2610.28577':['akv-hf','akv-dair','akv-system','akv-agent','akv-license','akv-commits'],'2610.30067':['2HeartsVN/GoiBERT-S','2HeartsVN/GoiBERT-M'],'2610.20538':['QiushiSun/PrismaCoder-9B','QiushiSun/PrismaCoder-35B-A3B','QiushiSun/PrismaCoder-35B-A3B-SFT'],'2610.27450':['gasolsun/Math_DeepSeek-R1-Distill-Qwen-1.5B_JustRL-DeepSeek-1.5B']}
class Text(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.skip=0
 def handle_starttag(self,t,a):
  if t in ['script','style']:self.skip+=1
 def handle_endtag(self,t):
  if t in ['script','style']:self.skip=max(0,self.skip-1)
 def handle_data(self,d):
  if not self.skip:self.parts.append(d)
def source(e):
 t=e.get('text','')
 if '<html' in t.lower()or '<!doctype html' in t.lower():
  h=Text();h.feed(t);t=' '.join(h.parts)
 t=re.sub(r'\s+',' ',t)
 if len(t)>26000:
  bits=[t[:14000]]
  for term in ['Limitations','Conclusion','Experiment','cost','commercial','License']:
   m=list(re.finditer(term,t,re.I))
   if m:bits.append(t[m[-1].start():m[-1].start()+1800])
  t=' [...] '.join(bits)
 return {'url':e['url'],'kind':e.get('kind',e.get('label')),'fetched_at':e['at'],'text':t}
schema=json.loads((S/'references/native-candidate.schema.json').read_text())
for bi,ids in enumerate([['2610.22732','2610.28577','2610.30067','2610.11942'],['2610.20538','2610.30090','2610.27450','2405.03144']]):
 payload=[];bs={'type':'array','minItems':len(ids),'maxItems':len(ids),'prefixItems':[],'items':False}
 for ident in ids:
  src=[source(e)for e in ev if e['id']==ident and 'error'not in e];src += [source(e)for e in detail if e['label']in mapping.get(ident,[])and 'error'not in e]
  payload.append({'candidate':rows[ident],'sources':src});sc=copy.deepcopy(schema);sc['properties']['candidate_id']['const']=ident;bs['prefixItems'].append(sc)
 bs={'type':'object','additionalProperties':False,'required':['candidates'],'properties':{'candidates':bs}}
 req=native_judge.prepare_request(base,json.dumps(payload,ensure_ascii=False),bs);(P/f'followup-{bi}.request.json').write_text(json.dumps(req,ensure_ascii=False));print(bi,ids)
