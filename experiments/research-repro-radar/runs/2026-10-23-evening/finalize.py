"""Record evidence-only conclusions and preserve every unfinished candidate."""
import json,hashlib,datetime as dt,re
from pathlib import Path
R=Path(__file__).parent;ROOT=R.parents[3];S=ROOT/'.agents/skills/research-repro-radar';STATE=S/'state'
def read(p):return json.loads(p.read_text())
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
rows=read(R/'all-candidates.json');targets={x['id']:x for x in read(R/'review-targets.json')};initial=read(R/'reviews.json');updates=read(R/'updated-reviews.json');by={x['candidate_id']:x for x in initial+updates}
# Validate host envelopes and quoted sources without publishing private handoff payloads.
import sys
sys.path.insert(0,str(S/'scripts'));import native_judge
all_ev=[]
for f in ['review-evidence.json','followup-evidence.json','model-details-evidence.json']:all_ev+=read(R/f)
ledger=[];quotechecks=[]
for name in [f'batch-{i}'for i in range(6)]+['followup']:
 p=Path('/workspace/radar-native-20261023')/(name+'.request.json');q=p.with_name(name+'.response.json');req=read(p);res=read(q);v=native_judge.validate_response(req,res);assert v['verdict']is not None
 elapsed=q.stat().st_mtime-p.stat().st_mtime;assert 0<=elapsed<=180,(name,elapsed)
 ledger.append({'name':name,'agent':'/root/'+('review_followup'if name=='followup'else 'review'+name[-1]),'host_model':'gpt-6-luna','serving_model':'unknown','request_id':req['request_id'],'task_sha256':req['task_sha256'],'request_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'response_sha256':hashlib.sha256(q.read_bytes()).hexdigest(),'request_created_at':dt.datetime.fromtimestamp(p.stat().st_mtime,dt.timezone.utc).isoformat(),'response_saved_at':dt.datetime.fromtimestamp(q.stat().st_mtime,dt.timezone.utc).isoformat(),'upper_bound_elapsed_seconds':elapsed,'schema_valid':True,'host_completion_observed':True})
 for x in v['verdict']['candidates']:
  for e in x['evidence']:
   matches=[z for z in all_ev if z['id']==x['candidate_id']and z['url']==e['source_url']and'text'in z];exact=any(' '.join(e['quote'].split())in' '.join(z['text'].split())for z in matches)
   quotechecks.append({'batch':name,'id':x['candidate_id'],'url':e['source_url'],'exact':exact,'words':len(e['quote'].split())})
assert all(x['exact']and x['words']<=25 for x in quotechecks if x['batch']=='followup')
write(R/'native-host-ledger.json',ledger)
specs=[
('2610.31574','TTR-Compressor','A','入力token群の相関を回帰係数へ圧縮し、長文・視覚token削減を狙う。','実在sihany/ttr_345mは446.6M・重み893MB、configはflash_attention_3。CPU動作/商用license未確認。HF10票とニュース要約の独立性は弱く、深掘り2信号未達。'),
('2610.31062','SCOPE','A','複合ポリシー判定を小型の述語と決定的抽出・論理へ分ける。NLP分類/抽出に直接関係。','Plurai/SCOPEデータは実在・MIT。実装/配布プログラム・実行資源未確認。プログラム最適化を重み学習必須とは断定しない。'),
('2610.28679','VerifierBench / Gavel','A','生成検証器が正しい別解まで拒む問題を、正負両方のケースで評価する。','コードApache-2.0、データは個別条件。追加著者結果は攻撃誤受理とのtradeoffあり。全再現はAPIとworkspace/Docker各100GiBを要し、今回未実行。'),
('2610.26836','Same Simulation, Different Evidence','A','計算が正しくてもツール出力の読み替えで回答が崩れる現象。根拠の明示と抽出の設計に価値。','コードMIT・データ別条件。科学シミュレーションに限定された著者結果で、汎用NLPへの転移・無料評価経路は未確認。'),
('2610.30755','Raw-RAG','A','会話履歴を変形せず保存し、必要な関係・依存を根拠として残す単純な比較基準。','外部サイトのrerank追加とは別用途。license・API費用・CPU資源未確認。独立2信号未達で再現なし。'),
('2610.19705','RouteWeaver','B','モデルだけでなく単発/複数段/agent実行の方式も選ぶrouter。','e2rea1/RouteWeaver-4Bとcost版が実在。routerだけ8.82GBでDL上限超。公式評価は80GB GPU1枚＋worker群。'),
('2610.30949','IQuest-Q1','B','CLIエージェント向けのMoE基盤モデルを新着から捕捉。','公式IQuestLab/IQuest-Q1はBF16約650GB・独自license。15B活性化は総320Bの保存量を減らさない。小さい公式後継未確認。'),
('2610.31305','Cola2','B','連続意味圧縮と離散生成を協調させる階層言語モデル。','原論文のみ確認。実装・推論checkpoint・license・CPU資源未確認。arXiv HTMLは404。'),
('2610.28889','Policy-Mask','B','拡散特徴の時刻/層/チャネルを予算に合わせ選ぶ画像認識手法。','学習済みの利用可能policy/重み・商用条件・CPU資源未確認。新規学習は今回範囲外。'),
('2610.27598','Meta Expert','B','MoEの層間でexpertを共有し、パラメータ冗長性を減らす設計。','内部expertアクセスの研究で、Web検索条件を適用しない。公開実装/重み/導入条件未確認。')]
digest=[{'id':i,'title':t,'url':targets[i]['url'],'assessment':a,'why_picked':w,'deferred_reason':d,'verification_level':'reading','measured':False}for i,t,a,w,d in specs]
# Preserve prior pending items plus new items not actually reviewed to completion.
previous=read(R/'queue-before.json');current=read(STATE/'queue.json');seen=read(STATE/'seen.json')['reported'];historical=set()
for p in R.parent.glob('*/report.json'):
 if p.parent!=R:historical.update(read(p).get('completed_ids',[]))
pending={key(x):x for x in previous['candidates']}
for x in current['candidates']+rows:
 ident=key(x)
 if ident in pending or ident in historical or ident in seen:continue
 y=dict(x);y.setdefault('carried_over_since','2026-10-23');y.setdefault('carry_count',1);y['pending_reason']='Not executed or source-confirmed rejected in this run.';pending[ident]=y
current['candidates']=list(pending.values());write(STATE/'queue.json',current);write(R/'unfinished-before-record.json',current)
ranking=read(R/'ranking.json');reviewed={x['candidate_id']for x in initial};assert all(x['id']in reviewed for x in ranking['review_pool'])
report={'run_id':'2026-10-23-evening-native','date':'2026-10-23','status':'reading_completed_no_candidate_execution','recommended':[],'digest':digest,'completed_ids':[],'counts':{'collected_unique':len(rows),'ranking_input_with_carry':ranking['counts']['input'],'ranked':ranking['counts']['ranked'],'review_pool_read':20,'primary_read_total':30,'native_followup_candidates':6,'candidate_code_executions':0,'candidate_model_inferences':0,'pending_after_record_expected':len(pending)},'budget':{'money_jpy':0,'candidate_cpu_seconds':0,'candidate_weights_bytes':0,'gpu_seconds':0,'candidate_repairs':0,'requested_start':'2026-10-23T09:04:00Z','deadline':'2026-10-23T09:34:00Z'},'host_requested_model':'gpt-6-luna','service_runtime_model_id':'unknown','limitations':['HF complete returned responses plus ten interest queries; no collection stop at 40. GitHub 290-result search paginated through all three pages. arXiv/Crossref searches used 100-result windows, not exhaustive pagination; public literature coverage is incomplete.','OpenReview five searches returned HTTP403; no bypass.','Thirty candidates actually primary-read, including final top20; six reread with current official artifacts. Initial quote mismatches are rejected, not adopted. Followup quotes exact and schemas validated.','Independent hot-signal threshold not established: TTR HF votes and AIWeekly article are distinct publication surfaces but article summarizes same source; conservative no deep_dive_value.','No candidate execution because credential-isolation hold remains unresolved. No repeat permission question, CLI fallback, auth changes, paid API, training, GPU bypass, email/SMTP/cron changes, SKU or PR19 changes.','No throughput, RAM, quality or CPU measurements; author values never counted as reproduced. Current price comparison not performed.','TokenEconomizer/AKV/SearchJev suppressed absent meaningful new evidence. Morning research not rerun.','All unfinished items preserved; neither digest nor raw native reject labels mark candidates completed. Old holds and historical completed decisions are retained.']}
write(R/'report.json',report)
write(R/'review-validation.json',{'schema_valid':36,'initial_quote_mismatches':[x for x in quotechecks if x['batch']!='followup'and not x['exact']],'followup_quotes': [x for x in quotechecks if x['batch']=='followup'],'corrections':{'TTR':'Ready checkpoints confirmed; 345m name differs from full parameter count; CPU unsupported status unconfirmed, flash_attention_3 config observed.','SCOPE':'Dataset real MIT; program optimization is not proven to require model-weight training.','Same Simulation and Meta Expert':'Do not apply external search-specific acceptance conditions to tool-output interpretation or internal MoE expert access.','VerifierBench':'Latest Gavel result has materially higher attack false-accept rate; not uniform improvement. Full reproduction resources distinguished from static data inspection.','IQuest and RouteWeaver':'Real official IDs and weight sizes verified from HF API; no small feasible successor asserted.'},'historical_completed_not_resurrected':sorted(historical),'completed_ids':[]})
write(ROOT/'benchmarks/research-repro-radar/2026-10-23-evening.json',{'run_id':report['run_id'],'measurement_level':'reading_only','candidate_model_inferences':0,'candidate_code_executions':0,'cpu_seconds':None,'peak_ram_bytes':None,'quality':None,'throughput_items_per_minute':None,'estimated_10000_items_cpu_seconds':None,'money_spent_jpy':0,'weights_downloaded_bytes':0,'gpu_seconds':0,'error':'Existing credential-isolation hold; no candidate execution attempted.','expected_difference':'Native judge success is not candidate inference success.','artifact_metadata_only':{'sihany/ttr_345m':{'parameters':446631936,'safetensors_bytes':893293472},'sihany/ttr_1b':{'safetensors_bytes':2247931312},'sihany/ttr_vlm_conv_3_3':{'safetensors_bytes':9465033968},'e2rea1/RouteWeaver-4B':{'safetensors_bytes':8822894520},'IQuestLab/IQuest-Q1':{'safetensors_bytes':649978660440}},'author_claims_only':[{'id':'2610.31574','source':'https://arxiv.org/html/2610.31574','visual_token_reduction_pct':55.6,'average_score_ttr_3x3':54.85,'average_score_mlp_2x2':54.93},{'id':'2610.28679','source':'https://github.com/DwanZhang-AI/verifier-suite','BA_norm_gavel':68.3,'BA_norm_pi':62.1,'attack_FPR_gavel':18.2,'attack_FPR_pi':7.4}]})
# Full audit table is supplementary; raw native drafts are never authoritative over saved primary facts.
lines=['# 一次資料読解台帳（30件、推論未実施）','','初回native草案には引用不一致があり、その引用は不採用。最新訂正はreview-validation.jsonと本日の報告を優先。','']
for x in initial:
 v=by[x['candidate_id']];row=targets[x['candidate_id']];lines += ['## '+row['title'],row['url'],'','判定草案: '+v['decision']+' / '+v['verification_level'],v['reason'],'','確認状況: '+' / '.join(v['unconfirmed']),'']
(R/'reading-ledger.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(report['counts']))
