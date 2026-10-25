"""Build this reading-only run's report from saved evidence; no candidate execution."""
import json,datetime as dt
from pathlib import Path
R=Path(__file__).parent;ROOT=R.parents[3];S=ROOT/'.agents/skills/research-repro-radar'
def read(p):return json.loads(p.read_text())
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
ranking=read(R/'ranking.json');reviews={x['candidate_id']:x for x in read(R/'reviews.json')+read(R/'updated-reviews.json')};targets={x['id']:x for x in read(R/'review-targets.json')};pending=len(read(S/'state/queue.json')['candidates'])
specs=[
('2610.30181','OpenDS-World','A','200問のlive Web課題。新サイト発見・欠落ページ回復を測る評価設計の候補。','改善手法や実測ではない。公式評価の1問1時間・100 tool callsは今回の枠外。無料実行経路と隔離が未確認。'),
('2610.31623','F2G-Pose','B','RGB-Dから姿勢・寸法・形状を1パスで推定。HF trending38位、公式コード・重みを確認。','必須RADIOは研究・評価の非商用限定。NVIDIA GPU/CUDA拡張必須、実行隔離保留。'),
('2610.30949','IQuest-Q1','B','CLIでの開発エージェント用MoE。公式配布と第三者GGUFの実在を確認。','本体BF16約641 GB、確認したGGUF約88 GBでDL2 GB超。15B活性化は全重み容量ではない。'),
('2610.31305','Cola2','B','意味スパンの連続圧縮と離散生成を協調させる新設計。','公式ページはCode/Model Soon。検索に出た同名CoLA分類モデルは別物。学習しない。'),
('2610.28889','Policy-Mask','B','拡散特徴の時刻・層・チャネルを予算付きで選択し、分類等へ転用。','下流目的で学習する方式。公開済み推論専用経路、license、CPU資源未確認。'),
('2610.27598','Meta Expert','B','MoE expertの層間共有で重みの冗長性を減らす設計。','70–80%のexpertで同等という著者主張。訓練不要の既存モデル適用・配布・資源未確認。'),
('2610.30755','RAW-RAG','B','生の会話履歴を保持する、長期会話理解の単純な比較基準。','外部LLM/embedding/reranker設定が必要で無課金経路未確認。検索結果のrerank追加には採用しない。')]
digest=[{'id':i,'title':t,'url':'https://arxiv.org/abs/'+i,'assessment':a,'why_picked':w,'deferred_reason':d,'verification_level':'reading','measured':False}for i,t,a,w,d in specs]
report={'run_id':'2026-10-25-evening-native','date':'2026-10-25','status':'reading_completed_no_candidate_execution','recommended':[],'completed_ids':[],'digest':digest,'counts':{'collected_unique':len(read(R/'all-candidates.json')),'ranking_input_with_carry':ranking['counts']['input'],'ranked':ranking['counts']['ranked'],'review_pool_read':20,'primary_read_total':len(reviews),'native_followup_candidates':5,'candidate_code_executions':0,'candidate_model_inferences':0,'pending_after_record_expected':pending},'host_requested_model':'gpt-6-luna','service_runtime_model_id':'unknown','budget':{'money_jpy':0,'candidate_cpu_seconds':0,'weights_downloaded_bytes':0,'gpu_seconds':0,'candidate_repairs':0,'started_at':'2026-10-25T09:02:00Z','deadline':'2026-10-25T09:32:00Z','report_created_at':dt.datetime.now(dt.timezone.utc).isoformat()},'limitations':['Reading only; no candidate reproduction or cost advantage measured.','Existing isolation boundary remains pending; no candidate execution or retry, no Kaggle bypass.','OpenReview five queries returned HTTP403; Cola2 HTML404 recovered from primary PDF/project page.','HF API returned complete lists without next Link; backend search ceiling not exhaustive. Crossref uses relevance windows, not corpus census.','All 40 arXiv pages and 3 GitHub coding-agent search pages fetched; duplicates removed, all normalized candidates scored.','Independent two-signal deep-dive gate not established; author effect sizes are not measurements.','No morning/weekly/SMTP/cron/SKU/PR19/Moli-Lightpanda changes; no training, paid API or additional permission.','Native request payloads and responses remain private; public ledger contains hashes and host task IDs.']}
write(R/'report.json',report)
bench={'run_id':report['run_id'],'measurement_level':'reading_only','candidate_code_executions':0,'candidate_model_inferences':0,'cpu_seconds':None,'peak_ram_bytes':None,'quality':None,'throughput_items_per_minute':None,'estimated_10000_items_cpu_seconds':None,'weights_downloaded_bytes':0,'gpu_seconds':0,'money_jpy':0,'blocker':{'action':'External candidate installation/inference','target':'Candidate PoCs','status':'pending/not attempted','reason':'Existing credential/personal-file isolation approval boundary remains unresolved; no bypass or denied action retry.'},'metadata_checks':{'F2G-Pose':{'model_id':'J3rr1/F2G-Pose','model_revision':'22493e6f8db2e646c31ba02fb38be009aab4d225','checkpoint_bytes':365929689,'mandatory_radio_bytes':1104806006,'mandatory_weights_total_bytes':1470735695,'runtime':'NVIDIA GPU, Python3.10, torch2.4.0, CUDA12.4 extensions','commercial_block':'Required RADIO license limits use to non-commercial research/evaluation; integration code Apache2.0 does not remove this.'},'IQuest-Q1':{'model_id':'IQuestLab/IQuest-Q1','model_revision':'5c21b0630586ef77d38cff1094b8cf37a417fcd8','parameters':320318615552,'main_bf16_shards':175,'main_bf16_bytes':640637367088,'optional_mtp_bytes':9341293352,'third_party_gguf_id':'Baekpica/IQuest-Q1-Mixed-Quant-GGUF','gguf_parts':6,'gguf_bytes':87995249696,'license':'Modified MIT: commercial product/service UI attribution required'}}}
write(ROOT/'benchmarks/research-repro-radar/2026-10-25-evening.json',bench)
lines=['# 一次資料読解台帳','', '候補実行0。A/testは試す価値であり実行完了ではない。追加判定を初回より優先。']
for ident,v in reviews.items():
 row=targets[ident];lines+=['','## '+row['title'],row['url'],'',v['decision']+' / '+v['verification_level'],v['reason'],'','確認・未確認: '+' / '.join(v['unconfirmed']),'次: '+str(v['next_check'])]
(R/'reading-ledger.md').write_text('\n'.join(lines)+'\n')
text='''# 2026-10-25 夕方・独立研究レーダー

詳細推薦0件、未検証ダイジェスト7件。全て読解確認で、候補コード実行・モデル推論0件、現金0円。Aは「試す価値」であり再現成功を意味しない。独立した話題性2信号とprofileの深掘り条件を確認できた詳細推薦は今回はない。

## 方法・版・件数

開始09:02 UTC、上限09:32 UTC。現金0、CPU20分/候補、RAM8 GB、DL2 GB、修復1回、carry28日、学習なし。上流 `cushionA/zabuton_skills@fe6925cd75838a9b52130f284cb3b931bfaf36db` の指定8文書と実装を読み、導入コピーとのhash一致を確認。profile v7は変更なし。

`RADAR_LLM=native`を開始から明示。指定ホストモデル`gpt-6-luna`で実probeを実施し、要求ID `57d5a37b-338c-4a48-b2d4-5d6605bfdffb`、入力hash、Schema、期待値の検証exit0後に探索開始。実サービスモデルIDは取得できず`unknown`。CLI/API fallbackなし。判定器38テスト成功。

HF trending全返却50件、新着50件、5関心分野を跨ぐ10検索で1,220一意候補。MDPI2誌、Crossref5検索、GitHub4検索、arXiv5検索で補完。arXivは全40ページ（返却3,792レコード）、GitHub coding-agent検索は全3ページ297件。OpenReview5検索はHTTP403で未取得、迂回なし。HFにnext Linkはなく検索backend上限は未確認、Crossrefは各100件の関連度窓。網羅率とは呼ばない。

最終5,753一意候補、繰り越し込み9,385入力を全件採点、ranked8,030、invalid0。最終review_pool20件を含む27件を読解、追加5件を再判定。native8要求のrequest_id/hash/schema、50引用の原文一致・25語以内を確認。初回1引用を同じ180秒枠内に1回修正。最終応答はいずれも180秒以内。Schema成功は候補推論成功ではない。

## 詳細確認事項（読解のみ・推薦採択とは別）

**F2G-Pose — B。** [論文](https://arxiv.org/abs/2610.31623)は10/22公開、[公式repo](https://github.com/jrryzh/F2G-Pose)は58 stars、10/23更新。RGB・深度・mask・camera intrinsicsから姿勢/寸法/形状を1パスで出す。28 FPSは著者の姿勢推論値で当方実測ではない。普通の画像分類器の置換ではなくRGB-D用途に特化する。

実在ID `J3rr1/F2G-Pose`、重み365,929,689 bytes。必須`nvidia/RADIO`の指定v2.5-L重み1,104,806,006 bytesとの合計は1,470,735,695 bytesで、依存・任意SAMを含まない。全variantの重みを合算していない。[導入](https://github.com/jrryzh/F2G-Pose/blob/main/docs/INSTALL.md)はLinux/NVIDIA GPU/Python3.10/PyTorch2.4.0/CUDA12.4と拡張buildを要求。RAM/VRAM/CPU時間は未測定。作者モデル一覧では後継・大型版を確認できず、最新唯一とは断定しない。

本体コードはApache-2.0でも、必須[RADIO LICENSE](https://huggingface.co/nvidia/RADIO/blob/main/LICENSE)3.3は非商用の研究・評価用途に限定。商用サービスへのそのままの導入候補にしない。GPU環境と用途だけでなく、この依存条件も保留理由。Docker/hosted ZeroGPUは作者側でも未検証と明記される。

**IQuest-Q1 — B。** [公式モデル](https://huggingface.co/IQuestLab/IQuest-Q1)は320,318,615,552 parameters、15Bは活性化分で全重み容量ではない。主BF16は175 shard/640,637,367,088 bytes、任意MTPは別に9,341,293,352 bytes。[第三者GGUF](https://huggingface.co/Baekpica/IQuest-Q1-Mixed-Quant-GGUF)でも6分割合計87,995,249,696 bytesでDL2 GBを超える。重みは取得せずAPI metadataで確認。

[公式repo](https://github.com/IQuestLab/IQuest-Q1)181 stars、10/19更新、HF149 likes/4,777 downloadsは人気の観測で独立性能検証ではない。公式SGLang/vLLM例はtensor parallel8、text-only。CPU速度/必要RAM/無料提供枠は未確認。公式一覧のFlemingやIQuest-Coder7B/14B/40Bは別系統で、Q1の後継や互換小型版として扱わない。[LICENSE](https://huggingface.co/IQuestLab/IQuest-Q1/blob/main/LICENSE)は商用UIにIQuest-Q1表示を要求する変更付きMIT。公式文書中の認証・sandbox変更例は実行していない。

**Cola2 — B。** [論文](https://arxiv.org/abs/2610.31305)は10/22。意味スパン圧縮と連続/離散生成の共同学習が新規点。HTML404から一次PDFを読み、[公式project](https://hongcanguo.github.io/Cola2)へ到達した。現時点の表示はCode/Model Soon。HF検索のCoLA分類fine-tune同名モデルは別物で、公式配布として採用しない。導入・license・CPU/RAM・重み容量・後継ID未確認。210B/346.03B training-token実験は著者報告で、今回学習しない。公開後に分類・要約への具体的価値を点検する。

**OpenDS-World — A（評価設計を試す価値）。** [公式repo](https://github.com/Yu-xm/OpenDS)と論文を照合。200問のlive Web課題はarchive-free新サイト発見・欠落ページ回復を測る評価設計の材料になる。ただし手法による改善を実測したものではなく、既発見結果のrerank追加も行わない。公式条件の1問1時間/100 tool callsは今回の枠を超え、まず課題記述の適合を読む段階。ライセンス表示Apache-2.0、無料実行経路未確認。native初回の「ベンチマークだけだから不要」は追加資料と用途を踏まえ再判定したが、完了IDには入れない。

## 未検証ダイジェスト

'''
for x in digest:text+=f"- **{x['assessment']} — [{x['title']}]({x['url']})**：{x['why_picked']} 保留：{x['deferred_reason']}\n\n"
text+='''## 比較・限界・保存

実測CPU/RAM/品質/throughput/1万件換算は全て未測定。profileのAPI/GKE/AWS参考単価を今日の確定価格として用いず、コスト優位を主張しない。RAW-RAGは会話理解の比較基準として読み、検索追加へ流用しない。Policy-MaskやMeta Expertは学習を伴う設計で、今回の推論成功にはしない。新規性と実行可能性、著者値と当方測定を分離する。

外部候補コードの導入・推論はpending/not attempted。既存の資格情報・個人ファイルからの隔離承認境界が未解決であり、新しい拒否ではない。禁止済み操作の再試行、Kaggleへの迂回、追加認証、費用、学習はなし。Moli/Lightpandaの依存導入・修復上限待ち、PR19停止を保持。朝/weekly state、SMTP/cron、SKU、CI設定は変更なし。実用機能完成ではないのでPR作成/merge/deployなし。

正規remote `2709554fb84c0a16bce4fd4ba035dfe6cd251f95` の夕方state4ファイルをSkill-local stateへhash一致で復元。`rank_candidates.py --state-dir state`、`record_run.py report.json --state-dir state`を使用。9,375旧未完了を全て維持し、新規未完了9件を追加、保存後9,384。最古carryは10/12で28日内。digest/read/native判定だけで完了にせずrecommended/completed_idsは空。seen/mail不変、freshnessは本runを1回記録。詳細0でも正常runとして永続化する。

証拠: [開始・版](../../experiments/research-repro-radar/runs/2026-10-25-evening/start-manifest.json)、[native台帳](../../experiments/research-repro-radar/runs/2026-10-25-evening/native-host-ledger.json)、[全読解](../../experiments/research-repro-radar/runs/2026-10-25-evening/reading-ledger.md)、[数値と未測定](../../benchmarks/research-repro-radar/2026-10-25-evening.json)、[検証](../../experiments/research-repro-radar/runs/2026-10-25-evening/verification.json)。正規ブランチへ保存し、final remote SHA・復元一致・exact-SHA CIは会話の実行結果に添える。

収集5,753／一次読解27（最終pool20）／追加再判定5／候補実検証0／詳細推薦0／未完了9,384。
'''
(ROOT/'reports/2026-10/2026-10-25-evening-radar.md').write_text(text)
print(report['counts'])
