"""Finalize factual reading report. No inferred model measurements or implicit completion."""
import collections,datetime,hashlib,json,re
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];load=lambda p:json.loads(p.read_text());reviews=load(R/'reviews.json');targets=load(R/'review-targets.json');rank=load(R/'ranking.json');ev=load(R/'review-evidence.json');key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
ids=[x['id']for x in reviews if 'dm-code-agent'in x['id']and x['priority']=='C'];assert len(ids)==7
pending=load(R/'retention.json')['pending']-len(ids)
by={x['id']:x for x in reviews};pri=dict(collections.Counter(x['priority']for x in reviews))
digest=[
{'id':'2608.14658','title':'pico-type / ARTH V2','priority':'A','url':'https://huggingface.co/eulogik/pico-type','new_findings':'1.5M級のbyte分類器。現行ARTH V2は14種リスク用で、FP32 11,664,968 bytes、実験的INT8 4,277,268 bytesをAPIで確認。一般文書分類の代替ではない。','hold':'9 stars・3 likesの小規模ソース。未知ソース実行の事前承認未確認で未実行。まず公開固定入力で単純規則との比較が必要。Aは試す価値であり実行許可・採用ではない。'},
{'id':'2610.14726','title':'LightVAE','priority':'B','url':'https://github.com/ModelTC/LightVAE','new_findings':'10月10日公開、14日重み更新。Wan2.1 Lite decoderは70,875,748 bytes、H3 INT8は833,732,248 bytes。全配布物が2GB超という解釈は誤り。','hold':'主評価はGPU。CPU/RAM不足の実証も既存Kaggle無料枠等の確認もなくGPU利用条件未成立。モデルcard Apache-2.0、repoライセンスAPIはnullでコードの利用条件は別確認。'},
{'id':'2610.22200','title':'OpenGCUA','priority':'B','url':'https://github.com/Tongyi-MAI/OpenGCUA','new_findings':'10月15日公開、HF trending32／GitHub56 stars。GUI・コード実行・状態判断をまとめたスタック。現行READMEではGCUAモデル自体は今後公開の項目。','hold':'macOS14+実機セットアップとAPIキーを要求。モデルの実在・配布とハーネス公開を混同しない。Linux/無料推論・権限・RAM未確認。'},
{'id':'2610.22341','title':'3DCodeVerse','priority':'B','url':'https://github.com/gaoypeng/3dcodeverse','new_findings':'10月15日公開、HF trending31／63 stars。実行・幾何測定・視覚判定を分けるAI開発の評価設計。code Apache-2.0とdataset非商用CC-BY-NC-SAは別。','hold':'Python3.13/Node/ベンダーCLIなどが必要。学習は実行禁止、無課金でのモデル実行経路と資源は未確認。商用データ利用に転用しない。'},
{'id':'2610.18813','title':'Self-Evolving Agent Harness','priority':'B','url':'https://arxiv.org/abs/2610.18813','new_findings':'10月13日公開、HF trending10／145 stars。66頁のsurveyで、固定モデルの周辺状態の更新と改善の実証を区別する。','hold':'実行ツールではなく設計・評価の読解資料。CC-BY-4.0。サーベイの分類をそのまま性能向上の証拠にしない。'},
{'id':'2502.07286','title':'SeNER / Small Language Model Makes an Effective Long Text Extractor','priority':'B','url':'https://arxiv.org/abs/2502.07286','new_findings':'2025年2月公開の掘り起こし。長い固有表現のスパン相互作用を扱う。公式repoとbio_modelsの導入資料を照合した。','hold':'公式説明はPython3.8・旧CUDA/PyTorch、実行権利・推論専用重み・CPU資源量が未確認。古いことではなく導入条件未確定で保留。'},
{'id':'github:frank6948/dm-code-agent','title':'DM-Code-Agentの追加重複5件','priority':'C','url':'https://github.com/frank6948/DM-Code-Agent','new_findings':'当日確認した8 repoのREADME SHA-256が完全一致。新たに見つかった5コピーも0 stars/forks、NOASSERTION、README内のownerは別。','hold':'Harper代表を保持し、追加5件と既に見送った2件だけ理由付き除外。独立した研究・利用実績として重複加算しない。'}]
report={'run_id':'2026-10-17-evening','date':'2026-10-17','recommended':[],'reading_deep_dive_pending':[{'id':i,'priority':'B','category':'deep_dive_value','reason':by[i]['judgment']['reason']}for i in ['2610.14224','2605.23904']],'digest':digest,'completed_ids':ids,'completion_reason':'Only seven byte-identical redundant repository IDs dismissed; five newly discovered duplicates and two previously dismissed IDs re-discovered. Harper representative retained. Survey C is priority only and not a terminal dismissal. No unrun, failed, or reading-only candidate completed.','counts':{'collected_unique':2512,'processed_with_carry':rank['counts']['input'],'ranked':rank['counts']['ranked'],'review_pool_read':20,'additional_read':8,'unique_primary_reviewed':28,'native_candidate_judgments_validated':43,'candidate_attempted':1,'container_attempts':2,'candidate_code_executions':0,'candidate_inference_attempted':0,'candidate_inference_success':0,'pending_after_record':pending,'priorities':pri},'constraints':{'money_jpy':0,'run_minutes_cap':30,'cpu_candidate_minutes_cap':20,'ram_gib_cap':8,'download_gb_cap':2,'candidate_repairs':1,'gpu_used':False},'blockers':[{'action':'Harness2 deterministic demo','target':'google-research/harness2@1cfe254e8d25b15f37048c3730134686abe60335','status':'stopped_repair_cap','reason':'Second attempt wrapper PermissionError on stat /root/.codex, before candidate code; no further repair/retry without new explicit continuation.'},{'action':'pico-type model/code execution','target':'eulogik/pico-type','status':'not_attempted_pending_source_approval','reason':'Unknown-source preapproval not established; A does not authorize execution.'}], 'state_semantics':'recommended stays empty to avoid automatic completion of reading-only deep dives; digest and failures remain pending. Existing seen/mail preserved.','canonical_base':'1fd340259b665e953f174ac3e3706ff6f42cf582','upstream_skill':'55ceed634506c5942d6a8e84c71d7e80acc103ff','limitations':['No candidate inference or useful-function implementation completed.','HF/API response lists have backend ceilings; no total-field coverage claim.','OpenReview five requests403; no bypass.','239 partial/future publication values retained as unknown.','Serving model unknown; host accepted requested gpt-6-luna.','No API/GKE/AWS price comparison; no measured throughput/quality/RSS.'],'finished_research_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
(R/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
body=f'''# 2026-10-17 JST 夕方 Research Repro Radar

結論：実測改善0件。一次資料28候補を読解しA{pri.get('A',0)}／B{pri.get('B',0)}／C{pri.get('C',0)}。pico-typeは限定用途で試す価値、Harness²とSkillOptは条件付き深掘り。Harness²デモは準備段階の権限例外で修復1回の上限に達して停止し、候補コード・モデル推論は未実行。失敗を完了や性能測定として扱っていない。

## 概要・方法

- 最新Skill `55ceed634506c5942d6a8e84c71d7e80acc103ff` の指定8資料は導入コピーとSHA256一致。profile v7（最少40、review_pool20、0円/30分/CPU20分/8GiB/2GB/修復1回/carry28日）を維持。
- 最初からRADAR_LLM=native。指定gpt-6-lunaをホストが受理し、probeの要求ID・タスクSHA256・Schema・期待値すべてexit0。実サービスモデルIDはunknown。43候補判断は28候補の追加資料による再判断を含み、すべてID・hash・Schema・原文引用を検証。引用修正4要求、意味修正2要求は各1回。判定成功は候補実行成功ではない。
- HF trending50件、新着50件、10クエリを先に取得（1,217一意）。MDPI/Crossref/GitHub補助16要求、arXiv5クエリ500レコードを統合し2,512一意。OpenReview5要求は403で停止、迂回なし。返されたページに次リンクなしという範囲で、世界全体の網羅性を主張しない。
- 繰り越し込み6,040件を全件処理、5,186件を採点。review_pool20件全て＋trending5件＋分類/動画/抽出3件を読解。最低件数で探索を打ち切っていない。freshnessは維持し、星0の新規repoの高得点を採用根拠にしない。
- 日付不明239件は原値を残してunknown。`requires_gpu=false`の既定加点はCPU適合の実証ではなく、実行は別途資源/権限で制限する。GitHub created/pushed/updated、HF更新revisionと論文公開日は区別。

## 実測と比較

品質、CPU時間、RSS、スループット、1万件換算はいずれも未測定。品質相対5%向上、または資源30%削減＋品質低下2%以内を支持する自前の比較は0件。著者の速度・精度・人気指標を実測効果へ置き換えない。API/GKE/AWSの金銭比較は行わず、過去の参考単価も利用しない。

**Harness²の簡易確認試行は失敗。** official commit `1cfe254e8d25b15f37048c3730134686abe60335`、package0.1.0、公開tarball7,056,332 bytes、Python公式image（展開サイズ119,431,103 bytes）を用意。固定image digestと42公開ソースのhash、ホスト側wrapperと2試行ログをbenchmarksへ保存した。ホスト側Python3.12.14、コンテナPythonの実バージョンはwrapper停止で未計測。

初回：source rootがumaskで0700になり、uid10001から`/app/run_demo.py`を開けず。公開ソースrootのみ0755へ1回修正。2回目：隔離確認wrapperが読めない`/root/.codex`をstatしてPermissionErrorになり、`run_module`より前に停止。sourceの公式デモ、帳簿2課題、LLMはすべて未実行。修復上限のため別環境・GPU・追加修正へ進んでいない。再開には新たなユーザー継続指示が必要。

隔離条件：network none、rootfs read-only、uid10001、capabilities全削除、no-new-privileges、RAM8GiB、CPU2、pids128、timeout120秒。mountは公開コードread-onlyと空の出力先のみ。認証・私有ファイルを渡していない。GPU0秒、学習0、現金支出0。例外は認証情報を取得したことを意味しない。

## 読解による深掘り候補

### B：Harness²

[論文](https://arxiv.org/abs/2610.14224)／[公式実装](https://github.com/google-research/harness2)。10月9日公開、repo更新10月14日、HF trending4、744 stars/77 forks。モデル重みを変えず、同一タスクで提案・試行・合成し、次タスクへ改善経験を持ち越す二段階の設計。AI開発の繰り返し修正を減らせるかが仮説。HFとGitHubの別基盤の注目はあるが、独立再現ではない。

Apache-2.0（第三者部分は別）、Linux/Python3.12、core依存0。決定論的デモは認証不要だが編集が決め打ちで、成功してもモデル能力の証明にならない。実モデルbenchmarkはNode/Cloud CLI/モデル設定等を要求し、READMEはAPI費用とsandbox無効の既定を明示している。当方はその経路を実行・採用していない。採用には同予算の固定課題で単純ハーネスと成功率・呼出回数を比較する必要がある。現在は上記停止状態。

### B：SkillOpt

[論文](https://arxiv.org/abs/2605.23904)／[公式実装](https://github.com/microsoft/SkillOpt)。5月22日公開、論文v2は5月25日、repo更新10月6日。HF trending37、18,496 stars/1,753 forks、MIT。古さでは除外せず、継続利用のあるスキル改善法として読む。

学習済み重みを変えず、skill文書の編集をheld-out評価で採否する。Harness²のオンライン試行とは違い、事前最適化した文書の再利用が中心。追加推論呼出し不要という記述は**配備時**で、最適化のrollout費用まで0ではない。大量の反復タスクで初期評価費を回収できるかが次の仮説。Python3.10+、pip導入、モデルbackendが必要。現在の無課金経路・呼出上限・具体的RAMは未確認で未実行。実利用スキルとは別の固定公開タスクで、採否用と最終評価用入力を分ける必要がある。

両候補は読解のみなのでreading_deep_dive_pendingに保存し、record_runが自動完了扱いにするrecommendedへ入れない。freshnessの確定推薦数は0件。その他のnative深掘りラベルは自動採用せず、以下の制約付きダイジェストへ留めた。

## 未検証ダイジェスト（新たな確認点）
'''
for x in digest:body+=f"\n- **{x['priority']} [{x['title']}]({x['url']})** — {x['new_findings']} 保留：{x['hold']}\n"
body+='''
## ID・利用条件の照合

pico-typeの現行model IDは`eulogik/pico-type`、revision `34b82db0aa1bafd9e63848df73d4ce1c864e475b`、9月26日更新。旧base7-headとARTH V2の14-risk-headを区別。code/model cardはApache-2.0。著者自身が一般分類AG/SST-2/Enronはchance水準と述べ、長文では固定窓の希釈や誤検出が残る。18ms等は著者値。意味分類・秘密情報除去の安全策として単独採用しない。評価するなら、形式/言語を扱う旧baseとリスク専用ARTHを分ける。

LightVAE IDは`lightx2v/LightVAE`、revision `4925ea978147b9de492d7a5fd4f9c6d1854ca1cc`。H3通常decoderは3,248,356,840 bytesで上限超過だが、Wan2.1 Liteは約70.9MB。軽量decoder単体サイズと元encoder・依存・RAMは別。ComfyUI用H3 INT8を通常benchmarkへ代入しない。

Qwen2.5-Coder-1.5B-Instructの再確認ではrevision `2e1fd397ee46e1388853d2af2c993145b0f1098a`、必須FP重み単体`model.safetensors`は3,087,467,144 bytes。repo usedStorageの6GB超を必須downloadとして使う誤読を訂正した。2GB上限内の具体的量子化artifact・後継の用途適合は未確認。既報としてダイジェストを繰り返さない。

## 判定と未確認事項

A=今試す価値（PoC・承認済みを意味しない）、B=条件付き、C=今回の優先度は低い/不要。終端の見送りはREADME重複7IDのみ。各候補の根拠引用、原文hash、ライセンス・導入・リソースのunknown、次の確認点はreviews.jsonとreview-evidence.jsonに保存。

|ID|判定|理由・保留|
|---|---|---|
'''
for x in reviews:body+=f"|{x['id']}|{x['priority']}|{x['judgment']['reason'].replace('|','/')}|\n"
body+=f'''
## 継続・次回

正規branch `research/2026-10-12-evening-radar@1fd340259b665e953f174ac3e3706ff6f42cf582` を開始時にfetch確認。独立研究branchから進め、朝branchのremoteは`0c6ddaa7b3d82eff6b0e2a889f76f99009aeac63`と一致。朝YOLO26n停止、Squeez停止、BitNet互換性hold、PP-LCNet停止を尊重し再試行していない。SKU、WorkAssistantClaude、別プロジェクト、PR19、メール/SMTP/cron/merge/deployに変更なし。

開始未完了5,176件。採点後5,186件に順位外7件を復元して5,193件。重複7IDを明示的に見送り、record_run後は{pending}件。以前の1,072件の保持を検算する。seen/mailを維持し、freshnessへ今回runを1件追加。Skill直下でrank_candidatesとrecord_runをともに--state-dir stateで実行し、4JSONを正規evening-stateへ戻す。

新規実装機能はなくdraft PRは作らない。保存commit・remote exact file hash・そのcommitのCI結果は最終返信に記載し、未確認で成功を先取りしない。CI成功はこの候補デモの成功を意味しない。

次回はpico-typeのソース承認と用途別公開fixtureを具体化する。Harness²の再実行は新たなユーザー継続指示まで停止。今回の準備失敗を理由にKaggleや別環境へ移さない。モデル/code実行0・収集2,512・一次読解28・未完了{pending}。
'''
(root/'reports/2026-10/2026-10-17-evening-radar.md').write_text(body)
print(json.dumps({'counts':report['counts'],'completed':ids},ensure_ascii=False))
