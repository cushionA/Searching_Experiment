"""Audit native outputs and preserve every unfinished discovered identity."""
import json,sys,re,hashlib,datetime,collections
from pathlib import Path
r=Path(__file__).parent;root=r.parents[3];s=Path('/workspace/radar-skills-20261020/research-repro-radar');p=Path('/workspace/radar-private-20261020');sys.path.insert(0,str(s/'scripts'));import native_judge
req=json.loads((p/'updates.request.json').read_text());response=json.loads((p/'updates.response.json').read_text());result=native_judge.validate_response(req,response);assert result['verdict']is not None
elapsed=(p/'updates.response.json').stat().st_mtime-(p/'updates.request.json').stat().st_mtime;assert elapsed<180
payload=json.loads(req['payload']);quote_checks=[]
for a,b in zip(response['verdict']['candidates'],payload):
 for e in a['evidence']:
  ok=any(e['source_url']==x['url'] and e['quote']in x['text'] for x in b['sources']);quote_checks.append({'id':a['candidate_id'],'url':e['source_url'],'exact_match':ok});assert ok,e
(r/'updates-review.json').write_text(json.dumps(response['verdict'],ensure_ascii=False,indent=2)+'\n')
ledger=json.loads((r/'native-host-ledger.json').read_text())
for x in ledger:
 if '/review' in x['task']:
  i=int(x['task'].split('review')[-1]);start=(p/f'batch-{i}.request.json').stat().st_mtime;end=(p/f'batch-{i}.fixed.response.json').stat().st_mtime
  x.update(started_at=datetime.datetime.fromtimestamp(start,datetime.timezone.utc).isoformat(),finished_at=datetime.datetime.fromtimestamp(end,datetime.timezone.utc).isoformat(),elapsed_seconds=end-start,deadline_verified=end-start<=180)
  x['note']='One caller-envelope repair; per-candidate schemas/verdicts unchanged. Conservative deadline starts at request creation. Late repaired output is not adopted.'
ledger.append({'task':'/root/review_updates','backend':'native','requested_model':'gpt-6-luna','runtime_model':'unknown','request_id':req['request_id'],'task_sha256':req['task_sha256'],'response_sha256':hashlib.sha256((p/'updates.response.json').read_bytes()).hexdigest(),'elapsed_seconds':elapsed,'deadline_verified':True,'schema_validation':result['diagnostics'],'quote_checks':quote_checks,'repairs':1})
(r/'native-host-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
# Final dispositions never treat ranking exclusion or digest publication as completion.
queue=json.loads((s/'state/queue.json').read_text());seen=json.loads((s/'state/seen.json').read_text())['reported'];rows=json.loads((r/'all-candidates.json').read_text());key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold();by={key(x):x for x in queue['candidates']};added=[]
for x in rows:
 k=key(x)
 if k not in by and k not in seen:
  y=dict(x);y.setdefault('carried_over_since','2026-10-20');y.setdefault('carry_count',1);y['pending_reason']='Not execution-complete or source-confirmed rejected; ranking exclusion alone is not completion.';by[k]=y;added.append(k)
queue['candidates']=list(by.values());(s/'state/queue.json').write_text(json.dumps(queue,ensure_ascii=False,indent=2)+'\n');(r/'all-unfinished-retention.json').write_text(json.dumps({'added_ids':added,'pending_before_record':len(by)},indent=2)+'\n')
allby={key(x):x for x in rows};targets=json.loads((r/'review-targets.json').read_text());allby.update({x['id']:x for x in targets})
# A indicates trial value, not achieved speed or unconditional execution permission.
digest_specs=[
('2610.23787','A','長いagent文脈のdraft KVを省く。HF trending37位、READMEでQwen3.5推論API対応に更新。','target+draftの合計DL/RAM、独立した第2話題信号、実行動作は未確認。'),
('hf:qwen/qwen2.5-coder-1.5b-instruct','B','1.5Bコード生成・修正でAI開発に適合。','BF16単体約3.09GBで2GB超。公式GGUF等の個別形式を未確認。Qwen3-Coder-Nextなど後継IDは存在確認済み。'),
('2601.22709','A','GRACEの関係蒸留・INT4 VLMは軽量画像理解に合う。','CPU適性と独立話題信号未確認。ZeroGPU利用は今回承認されたKaggle枠ではなく実行しない。'),
('2412.14219','A','MoE推論最適化の俯瞰資料としてCPU小型化の手法選別に役立つ。','性能比較未実施、独立2信号未確認。repo更新日時をcode push日時と混同しない。'),
('2503.06029','A','SmartBenchの中国語20タスクは要約・抽出評価の設計に使える。','データ公開状況・license・日本語適性・独立2信号未確認。'),
('2609.05334','A','現場病害画像を対象とした小型ViT。著者の6.01MB報告が関心に合う。','重み・商用license・汎化性能・独立2信号未確認。学習は実施しない。'),
('2602.09373','B','AfriNLLBの圧縮とCTranslate2配布は翻訳NLPの候補。','CC-BY-NC-4.0、対象言語の需要とCPU実測未確認。'),
('2207.01405','B','I-ViTの整数演算は軽量画像認識の効率化候補。','論文の速度測定はGPU。CPU経路は未確認。'),
('2211.11014','B','量子化encoderのattention蒸留は分類の小型化に関連。','QAT訓練中心で学習禁止。利用可能な推論重み未確認。'),
('2405.17890','B','SLMRecは逐次推薦モデルを小さくする設計。','推薦用途の需要とCPU推論経路未確認。SKUへ流用・再開しない。'),
('2607.18213','判定保留','SWE-Pruner Proはhidden stateからコード行の削減を決める後継。','SGLang/CUDA依存、license本文未確認。native修復が保守的期限を超え正式A/B/C未採用。'),
('2601.16746','判定保留','SWE-Prunerは0.6Bの目標別コード行削減でAI開発に接点。','Pro後継を確認。CPU実測・コスト未確認、native修復期限超過。'),
('2607.25180','判定保留','Bekkoは小型多言語encoderとONNX配布。','検索順位改善だけでは用途条件を満たさない。網羅性補完や前処理への具体的価値未確認。'),
('2610.22428','判定保留','凍結16MB語彙表は小型モデル容量削減の着想。','非商用研究license・配布形式確認が必要。BitNet保留を迂回せず実行しない。'),
('2401.17919','判定保留','LOCOSTは長文要約向けSSMでNLP別枠の候補。','CPU/RAM/公開重み条件未確認。古さだけで除外しない。')]
digest=[{'id':i,'title':allby[i]['title'],'url':allby[i]['url'],'assessment':a,'why_picked':why,'deferred_reason':hold}for i,a,why,hold in digest_specs]
ncm=response['verdict']['candidates'][0];recommended=[{'id':'2610.14162','title':'Next-Concept Modeling (NCM)','published_at':'2026-10-09','url':'https://arxiv.org/abs/2610.14162','category':'deep_dive_value','assessment':'A','verification_level':'reading','reason':ncm['reason'],'independent_signals':[{'kind':'platform-trending','url':'https://huggingface.co/papers/2610.14162','rank':15},{'kind':'third-party CPU report','url':'https://github.com/HazyResearch/ncm/issues/8','note':'Third-party Sudoku report, not our execution or XSum verification.'}],'next_check':'承認された隔離環境で英語XSum固定文書5件をCPU推論し、品質・時間・ピークRAMを測る。日本語は別途未確認。','unconfirmed':ncm['unconfirmed']}]
report={'run_id':'2026-10-20-evening-native','date':'2026-10-20','status':'completed_with_partial_judgment_failures','recommended':recommended,'digest':digest,'completed_ids':['2603.11531'],'counts':{'collected_unique':len(rows),'ranking_input_with_carry':json.loads((r/'ranking.json').read_text())['counts']['input'],'primary_read_review_pool':20,'primary_read_total':len(targets),'candidate_code_executions':0,'candidate_model_inferences':0,'deadline_accepted_initial_candidates':10,'deadline_accepted_updated_candidates':2,'pending_before_record':len(by)},'limitations':['OpenReview API 403 for five queries.','Native bare-array envelope rejected; repaired once with unchanged candidate schema. Conservative 180-second cutoff excludes batches2-5; pending preserved.','No credential-isolated candidate runtime was verified. No unknown candidate code, model weights or GPU jobs executed.','API result ceilings and bounded queries mean coverage is not exhaustive.'],'budget':{'money_jpy':0,'candidate_cpu_seconds':0,'candidate_weights_bytes':0,'gpu_seconds':0,'run_started_at':'2026-10-20T09:05:00Z','run_deadline':'2026-10-20T09:35:00Z'},'runtime_model':'unknown'}
(r/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report['counts']))
