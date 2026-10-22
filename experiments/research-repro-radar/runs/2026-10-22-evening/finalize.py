"""Persist evidence-reviewed evening results; never execute candidate code."""
import datetime as dt,hashlib,json,re
from pathlib import Path
R=Path(__file__).parent;ROOT=R.parents[3];S=ROOT/'.agents/skills/research-repro-radar';STATE=S/'state'
def read(p):return json.loads(p.read_text())
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
rows=read(R/'all-candidates.json');targets={x['id']:x for x in read(R/'review-targets.json')};initial=read(R/'reviews.json');updates=read(R/'updated-reviews.json');by={x['candidate_id']:x for x in initial+updates};ledger=read(R/'native-host-ledger.json')
# Exact-source matches are mandatory for adopted detailed citations.
for ident in ['2610.22732','2610.28577']:
 assert by[ident]['recommended_category']=='deep_dive_value'
 assert all(q['exact']and q['words']<=25 for q in ledger[-2]['quotes']if q['candidate']==ident)
recommended=[]
for ident,title,date,signals in [
 ('2610.22732','TokenEconomizer','2026-10-15',[{'url':'https://huggingface.co/papers/2610.22732','kind':'HF trending','rank':41},{'url':'https://www.linkedin.com/posts/apanshina_uc-berkeley-and-microsoft-just-published-activity-7518598074869694465-VQJ5','kind':'Independent public discussion; not performance reproduction'}]),
 ('2610.28577','Cache Engineering for Agents / AKV','2026-10-20',[{'url':'https://huggingface.co/papers/2610.28577','kind':'HF public interest','upvotes':67},{'url':'https://www.linkedin.com/posts/dair-ai_we-love-papers-that-go-deeper-down-the-stack-activity-7518733866879336448-FcTh','kind':'Independent DAIR.AI discussion; not performance reproduction'}])]:
 v=by[ident];recommended.append({'id':ident,'title':title,'url':'https://arxiv.org/abs/'+ident,'published_at':date,'category':'deep_dive_value','assessment':'A','verification_level':'reading','reason':v['reason'],'next_check':v['next_check'],'independent_signals':signals,'candidate_execution':False})
specs=[
 ('2610.30067','GoiBERT','B','ベトナム語raw text向け26.4M/49.4M encoder。語分割を省く分類・NERの候補。','S/M実重み105.6/197.5MBを確認。配布はtask head無し、商用license未確認。著者の53/99MB表はbf16条件で実配布量と異なる。'),
 ('2610.11942','LGWM','B','画面だけでなく行動後の遷移を潜在表現で検証する軽量GUI認識。','CPU指定あり、推論173M・重み約691.5MB。17msはA100著者値。CPU時間/RAM・独立話題2信号は未確認。'),
 ('2610.20538','PrismaCoder','B','コードと描画結果を分離したルーブリックで視覚コード生成を評価。','9B RL重み計18.82GBで2GB超。35B-A3B RLはID実在でも重み未掲載、SFTとは別。重みMIT表示、repoの商用条件未確認。'),
 ('2610.17860','Socratic Skill Learning','A','実践と教師の問いを交互に行い、Skillが未見課題へ転移するか測るAI開発手法。','Node22.19+/Docker/model endpointが必要。コードMIT、教材は個別条件。現金0での経路・独立話題2信号未確認。'),
 ('2610.29545','PACE','B','線形attentionの再帰stateを毎tokenでなく周期的に4bit化し誤差蓄積を抑える。','著者評価は4B–35B hybridモデル。公式実装/配布物/license/CPU適合未確認。state圧縮と重み圧縮は別。'),
 ('2610.29680','TraceForge','B','教師の答えからstudent系統に合う推論traceを生成する蒸留手法。','生成器・criticの学習が必要で今回対象外。公式配布・商用条件未確認。真正性scoreを正答率と解釈しない。'),
 ('2610.27450','Ask-to-Distill','B','正解ラベルなしの質問選別で蒸留効率を高め、既成studentも公開。','1.5B student実在・MIT表示、単一重み3,554,214,752 bytesで上限超。学習は行わず、小容量形式未確認。'),
 ('2610.30090','VX-One','B','unknownを持つ型付き判定headの小型分類。CPU低遅延と校正崩れを併せて報告。','ネットワーク侵入検知用でNLP分類器ではない。HF IDはコード配布のみ、重み未掲載。学習/資源・用途適合未確認。'),
 ('2609.05334','農作物病害の軽量ViT圧縮','A','村・端末を跨ぐ評価で圧縮パイプラインと同サイズstudent直接学習を比較。','直接学習studentでも近い精度という著者結果が重要。既成重み/商用条件/CPU実測・独立話題2信号未確認。'),
 ('2606.26488','Recursive Reasonerのエッジ圧縮','A','再帰過程の量子化誤差を追い、再学習なし校正で回復する小型化の仮説。','3.3MBは著者のbackbone値。入力埋込みを含む総資源・公開artifact・実測・独立話題2信号未確認。'),
 ('2610.25519','ATEST','B','GPU混雑時の電力費用をオンライン学習し、追加サンプリングの停止を選ぶ。','A100/L40S評価。低負荷短タスクではtail latency/throughputが悪化。実装とCPU適合未確認。')]
digest=[{'id':ident,'title':title,'url':targets[ident]['url'],'assessment':ass,'why_picked':why,'deferred_reason':why_not,'verification_level':'reading','measured':False}for ident,title,ass,why,why_not in specs]
previous=read(ROOT/'experiments/research-repro-radar/evening-state/queue.json');current=read(STATE/'queue.json');seen=read(STATE/'seen.json')['reported'];historical=set()
for p in R.parent.glob('*/report.json'):
 if p.parent!=R:historical.update(read(p).get('completed_ids',[]))
pending={key(x):x for x in previous['candidates']}
for x in current['candidates']+rows:
 ident=key(x)
 if ident in pending:continue
 if ident in historical or ident in seen:continue
 y=dict(x);y.setdefault('carried_over_since','2026-10-22');y.setdefault('carry_count',1);y['pending_reason']='Not executed or evidence-confirmed rejected in this run.';pending[ident]=y
current['candidates']=list(pending.values());write(STATE/'queue.json',current);write(R/'unfinished-before-record.json',current)
ranking=read(R/'ranking.json')
report={'run_id':'2026-10-22-evening-native','date':'2026-10-22','status':'reading_completed_no_candidate_execution','recommended':recommended,'digest':digest,'completed_ids':[],'counts':{'collected_unique':len(rows),'ranking_input_with_carry':ranking['counts']['input'],'ranked':ranking['counts']['ranked'],'primary_read_review_pool':20,'primary_read_total':len(targets),'native_initial_candidates':len(initial),'native_followup_candidates':len(updates),'candidate_code_executions':0,'candidate_model_inferences':0,'pending_after_record_expected':len(pending)},'budget':{'money_jpy':0,'candidate_cpu_seconds':0,'candidate_weights_bytes':0,'gpu_seconds':0,'candidate_repairs':0,'requested_start':'2026-10-22T09:04:00Z','deadline':'2026-10-22T09:34:00Z'},'host_requested_model':'gpt-6-luna','service_runtime_model_id':'unknown','limitations':['Metadata feeds and API response ceilings are not exhaustive coverage. All collected canonical candidate records were scored; no 40-item stopping rule.','OpenReview search returned HTTP403 for five queries. ArXiv metadata search succeeded for all five queries.','Initial native judgments contain eight quote mismatches; follow-up contains two. Mismatched quotes are not adopted. Metadata and feasibility errors are corrected only from saved primary evidence in review-validation.json. Raw native drafts remain audit artifacts.','No credential-isolated external-candidate execution environment confirmed. Existing pause maintained; no duplicate permission question, GPU fallback, training, new credentials, CLI fallback, email or schedule changes.','Morning task result was not accessed or rerun. No October22 report found under normally readable origin/main reports; duplicate check uses evening state and authorized history.','Detailed recommendations are reading-only and remain in unfinished queue. Historical source-confirmed rejects are not resurrected merely by rediscovery.','API-equivalent author costs are not measured cloud expenses; current pricing and practical throughput are unverified.']}
write(R/'report.json',report)
validation={'native_schema_valid_candidates':len(initial)+len(updates),'initial_quote_mismatches':[q for e in ledger[:7]for q in e['quotes']if not q['exact']],'followup_quote_mismatches':[q for e in ledger[7:]for q in e['quotes']if not q['exact']],'adopted_detailed_quotes_exact':True,'metadata_corrections':{'2610.11942':'README explicitly supports --device cpu; inference 173M, A100 17ms, model.safetensors 691486920 bytes. Do not adopt native claim that only GPU testing is possible or DL size unconfirmed.','2610.20538':'API confirms 9B weight shards totaling 18819720984 bytes, MIT weights; 35B RL has no weight files. Do not adopt native unconfirmed-size/ID claims.','2610.27450':'HF API confirms released 1.5B student weight 3554214752 bytes; no training needed just to infer this checkpoint, but download exceeds budget.','2610.30067':'README no task head; GPU throughput and CPU latency separately listed. API actual fp32 file sizes differ from bf16 efficiency table.','2610.30090':'HF repository has source code but no checkpoint files; no generic text-classification claim.','2405.03144':'Resource-only initial reject invalid; followup defer adopted; completed_ids stays empty.'},'historical_completed_not_resurrected':sorted(historical),'adopted_decisions':'Native A/B decisions retained for detail/digest; factual corrections grounded in primary snapshots, not fabricated verdicts.'}
write(R/'review-validation.json',validation)
write(ROOT/'benchmarks/research-repro-radar/2026-10-22-evening.json',{'run_id':report['run_id'],'measurement_level':'reading_only','candidate_model_inferences':0,'candidate_code_executions':0,'cpu_seconds':None,'peak_ram_bytes':None,'quality':None,'throughput_items_per_minute':None,'estimated_10000_items_cpu_seconds':None,'money_spent_jpy':0,'weights_downloaded_bytes':0,'gpu_seconds':0,'error':'External candidate credential-isolation pause remains unresolved; no execution attempted.','expected_difference':'Native JSON judge validation is successful, but it is not candidate inference. Author-reported numerical gains are not measured gains.','author_claims_only':[{'id':'2610.22732','mean_token_cost_reduction_pct':'18.4–30.9','source':'https://arxiv.org/abs/2610.22732'},{'id':'2610.28577','api_equivalent_cost_reduction_pct':27.7,'throughput_up_to':1.2,'p95_speedup_up_to':1.5,'source':'https://arxiv.org/html/2610.28577'}]})
print(json.dumps(report['counts']))
