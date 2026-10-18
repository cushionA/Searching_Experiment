"""Persist a reading-only report without completing pending trials."""
import collections,datetime as dt,hashlib,json
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];load=lambda p:json.loads(p.read_text());reviews=load(R/'reviews.json');rows=load(R/'review-targets.json');ev=load(R/'review-evidence.json');rank=load(R/'ranking.json');audit=load(root/'benchmarks/research-repro-radar/2026-10-18-evening/metadata-audit.json');pending=len(load(root/'.agents/skills/research-repro-radar/state/queue.json')['candidates']);pri=dict(collections.Counter(x['priority']for x in reviews));by={x['id']:x for x in rows}
blocks=[{'target':'Squeez','status':'stopped_repair_cap','action':'candidate execution','reason':'Prior repair cap exceeded; no restart or reset.'},{'target':'YOLO26n','status':'stopped_repair_cap','last_commit':'0c6ddaa7b3d82eff6b0e2a889f76f99009aeac63','action':'candidate inference','reason':'/app access failure remained after one repair, zero inference.'},{'target':'Harness2','status':'stopped_repair_cap','last_commit':'efd3c909d2d729cfc0e8f189a01dd213bd400257','action':'candidate demo','reason':'Isolation wrapper stat /root/.codex PermissionError after one repair; candidate code not reached.'},{'target':'PP-LCNet','status':'stopped_prior_failure','action':'candidate inference','reason':'Respect existing stop; no new trial.'},{'target':'BitNet','status':'on_hold','action':'candidate inference','reason':'Respect recorded compatibility hold.'},{'target':'unknown-source candidate code, including pico-type and Bekko System One','status':'approval_unconfirmed_not_attempted','action':'external code execution','reason':'No established source approval; no code execution, authentication copy, privilege relaxation or alternate backend.'}]
notes=[
('2609.18063','Edge0','SSDから専門家重みを先読みする設計。HF trending2／GitHub5,666 stars。','8B本体4,508,697,328 bytes、adapter込み4,563,913,288 bytesで2GB超。MLX allocator peakはRSSでない。'),
('hf:hotchpotch/bekko-system-one-v0-17m','Bekko System One v0-17m','生成をせず候補を並列評価する英語typed decision encoder。Embeddingとは別。','重み67,198,244 bytes／browser ONNX29,023,607 bytes。ライセンス宣言未確定・未知ソース承認未確認・一般化弱い。'),
('2609.15779','EvoOntology','MCP意味層を評価ゲート付きで更新する設計。HF trending41／GitHub954 stars。','無課金LLM経路・公開固定データ・DBアクセス範囲が未確定。検索順位変更だけの実装はしない。'),
('2610.19789','Self-Distillation AgentRL比較','10手法とGRPOを同条件比較し、計算量増加と改善が一致しない点を検討。','著者の132run/12,124.4 H800 GPU-hoursの評価。学習禁止・資源超過のため読解のみ。'),
('2610.17953','Agent-Reflex','ツールノイズと訂正への反省をRSD/ReSFT/ORPOで教える。','実在Agent-Reflex-8Bは重み16,381,516,752 bytes。学習・多GPU再現は実施しない。'),
('2606.23050','Unlimited OCR','R-SWAで長文書のKV cacheを一定にする。HF trending6／GitHub27,765 stars。','6,672,547,120-byte重みとNVIDIA推論前提。2GB超、GPU利用条件未成立。朝OCRと重複実験しない。'),
('2503.11576','SmolDocling → GraniteDocling','公式cardが後継を明記、旧IDはdocling-projectへ解決。','後継granite-docling-258Mは515,093,104-byte重み。日本語・表/数式品質とCPU/RAMは未検証、旧モデルの重複導入なし。'),
('2609.05334','唐辛子病害ViT圧縮','3分類・村を分離した評価と同容量student比較がある。','著者値では直接学習student94.87%も6.01MB。圧縮工程の追加価値・公開実行物・権利が未確認。'),
('2401.17919','LOCOST','state-space encoder-decoderで長文の抽象要約を扱う。','公開年で除外しないが、現在のモデルID・ライセンス・CPU要件と独立した話題性が未確認。'),
('2602.09373','AfriNLLB','層剪定した481Mの実モデルとCTranslate2配布形式を確認。','CC-BY-NC-4.0、商用利用は別許諾が必要。日本語対象でなくCPU実測もない。'),
('2610.08699','nanoMuse','複数端末の会話・記憶・承認を持つ個人agent。','同梱モデルなし。既存無料backend・端末権限・情報持出し条件が未確定で導入しない。'),
('2607.25180','Bekko Embedding','a8m/a25mの実ID・MIT・ONNX配布を確認。','System Oneの多言語根拠にはならない。新規coverage/前処理短縮の具体的需要がないため検索追加をしない。')]
rd={x['id']:x for x in reviews};digest=[{'id':ident,'title':title,'url':by[ident]['url'],'priority':rd[ident]['priority'],'reason':why,'hold':hold}for ident,title,why,hold in notes];deep=[dict(id=x['id'],title=by[x['id']]['title'],url=by[x['id']]['url'],priority=x['priority'],category='deep_dive_value',verification_level='reading',reason=x['judgment']['reason'],next_check=x['judgment']['next_check'])for x in reviews if x['judgment']['recommended_category']=='deep_dive_value']
digest=[x for x in digest if x['id'] not in {d['id'] for d in deep}]
report={'run_id':'2026-10-18-evening','date_jst':'2026-10-18','backend':'native','requested_model':'gpt-6-luna','actual_service_model':'unknown','canonical_base':'efd3c909d2d729cfc0e8f189a01dd213bd400257','upstream_skill':'55ceed634506c5942d6a8e84c71d7e80acc103ff','recommended':[],'completed_ids':[],'reading_deep_dive_pending':deep,'digest':digest,'counts':{'collected_unique':len(load(R/'all-candidates.json')),'processed_including_carry':rank['counts']['input'],'ranked':rank['counts']['ranked'],'review_pool_read':20,'primary_candidates_read':len(reviews),'primary_files_verified':audit['primary_files'],'candidate_code_executions':0,'candidate_inferences':0,'comparisons':0,'pending_before':5186,'pending_after_record':pending,'priorities':pri,'native_candidate_judgments':len(load(R/'review-validation.json'))},'blockers':blocks,'constraints':{'cash_jpy':0,'run_minutes':30,'cpu_candidate_minutes':20,'ram_gb':8,'download_gb':2,'repair_cap':1,'carry_days':28},'semantics':'Reading and priority C alone do not complete existing pending work. All unfinished, failed, interrupted and digest-only items retained. No candidate execution; A does not require PoC but no A was supported this run.','source_limitations':['OpenReview 5 queries returned403; no bypass.','HF returned no next Link. Query result caps remain; no total coverage claim.','240 partial/future dates retained as unknown, original values retained.','Model family searches cover visible results, not proof that no successor exists.'],'research_completed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
(R/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');(R/'blocked-continuations.json').write_text(json.dumps(blocks,ensure_ascii=False,indent=2)+'\n')
text=f'''# 2026-10-18 JST 夕方 Research Repro Radar

結論：**実測改善0件、候補コード実行・モデル推論0件。一次資料29候補を読解し A{pri.get('A',0)} / B{pri.get('B',0)} / C{pri.get('C',0)}。未完了{pending:,}件を保持。** PoC成功はAの必要条件にしていない。今回の候補は資源、権利、対象用途、または既存の停止条件が残り、導入を保留した。

独立した読解成果は、Edge0のallocatorメモリと総RAM/取得量の区別、Bekko System Oneの未確定ライセンスと評価指標の限界、SmolDoclingからGraniteDoclingへの公式案内、AfriNLLBの非商用条件。再現成功とは呼ばない。

## 方法・版・入力

最新Skill `{report['upstream_skill']}` を新規clone。指定8資料、checkout AGENTS.md、関連 .agents/skillsを確認し導入コピーとのhash一致を記録。Python3.12.14、jsonschema4.26.0。profile v7、最低40/review_pool20、0円/30分/CPU20分/RAM8GB/download2GB/修復1回/carry28日を維持。

最初から `RADAR_LLM=native`。公式prepare→指定gpt-6-lunaのホストspawn→終了通知→validateの順でprobe成功。request_id/task SHA256/schema/期待値を検証し、保存済み資料1件のschema判定も確認。サービス実モデルIDは**unknown**。CLI fallback、新規API経路、認証コピー・変更、権限緩和なし。原文・要求を相互に混ぜないため親が全引用とcandidate_idを照合し、引用8要求・意味5要求を各要求1回まで修復した（保存資料の意味修復1回は別）。判定経路の成功は推論成功ではない。

HF trending50、新着50、広い10クエリで1,218一意を取得。MDPI/Crossref/GitHubの826レコード、arXiv5検索500レコードを重複排除し2,515一意、公式モデルの追加確認1件を統合し**2,516件**。workloadを検索語にはせず、分類・要約・抽出・軽量画像・CPU小型・AI開発を探索。OpenReview5検索は403で停止。返却ページにnext Linkなしの範囲であり全世界の網羅ではない。公開年のみで除外していない。

繰り越し込み**6,049件を全件処理、5,198件採点**。review_pool20件をすべて読み、trending7件・要約1件・別系統のtyped-decisionモデル1件を追加して29件。一次資料116ファイルをbytes/SHA256照合。朝`1c304fcdf53c132cf970fc92cfc2243f84b43afa`のWebMCP/SetFit/SmolVLM2を再実験していない。コード・README・model cardをテキストとしてのみ取得し、外部ソースをimportしない。

`estimated_cpu_minutes=20`は当方の確認割当であり著者値でも推論予測でもない。`requires_gpu=false`の既定加点もCPU適合の証明ではない。初回順位表と最終順位表を残し、追加モデルで再採点する前にcanonicalの4JSONを復元したのでcarry_countを二重加算していない。

## 実測・比較・想定との差

自前の品質、CPU時間、RSS、スループット、1万件換算はすべて未測定。品質相対5%以上の向上、または品質低下2%以内＋資源30%以上削減を裏付ける比較は0。著者値を転記して効果認定しない。GPU0秒、候補重み/依存download0 bytes、現金0円。資料監査は{audit['metadata_audit_wall_seconds']:.4f}秒、保存一次payload {audit['primary_payload_bytes']:,} bytes（通信headerやgit等の全wire bytesは未計測）。監査時間はモデル速度ではない。

A/B/Cは試す価値/条件付き/今回低優先度。Cでも未完了検証や資源保留を機械的に削除しない。Aを作るための合成fixture実行も行わない。未知ソース承認が未確認、既存隔離失敗が継続しているため候補実行へ進まない。CPU性能不足を実測しておらず、Kaggleへの移行条件も未成立。GPU残量・機種・非公開設定・認証は今回照会していない。

## 読解による深掘り候補
'''
if not deep:text+='\n今回はprofileの独立した注目信号と具体的検証仮説を満たす確定推薦なし。次のダイジェストを未検証の条件付き候補として保持。\n'
for x in deep:text+=f"\n### {x['priority']}：[{x['title']}]({x['url']})\n\n{x['reason']}\n\n次の確認：{x['next_check']} 現条件で実行・追加取得を承認する意味ではない。独立した注目と独立再現は区別し、推論0件。\n"
text+=f'\n## 未検証ダイジェスト（{len(digest)}件）\n'
for x in digest:text+=f"\n- **{x['priority']} [{x['title']}]({x['url']})** — {x['reason']} 保留：{x['hold']}\n"
text+='''
## 主要な実在ID・導入条件

- Edge0: `Edge0/Edge0-8B-A1B-preview@269b9a2c4a69d897c50e9f4e125328481d7c0fcf`。主重み4,508,697,328 bytes、adapter込み4,563,913,288 bytes。35B配布のsafetensors合計19,689,863,445 bytes。READMEの約4.2/23GBと実ファイル列挙値を分離。2.9GiBはMLX allocator peakでRSSではなく、file-backed pages/OS等は別。Python例はmacOS/Apple Silicon/MLX。Apache-2.0、商用時も依存・元重み条件を確認。
- Unlimited OCR: `baidu/Unlimited-OCR@8249d20b2822a7d8a4c22c8d8c9069b5ac36bda3`、MIT。6,672,547,120 bytesの重み名が2つ掲載されるが両方の取得を必須としない。単体でも2GB超。CUDA/remote-code条件を含む実行は未実施。
- Bekko System One: `hotchpotch/bekko-system-one-v0-17m@b886a1f9b91f4e8368d7830080d52d955e2c8dfa`。重み/同梱推論codeのライセンスはcard自体が未確定と明記。EmbeddingのMITを流用不可。CPU SDPA、custom code、torch/transformers/STを要求し、依存込みサイズやRSSは未測定。browser ONNXはlookupをINT8、他はFP32で、全面INT8とは言わない。S1MBのTask Avg27.57は正答率ではなく、合成600 English casesは自己レビュー。17Mの一般化の弱さ、英語対象、切り詰め/校正不足が制約。
- SmolDocling: 旧`ds4sd/SmolDocling-256M-preview`照会は`docling-project/SmolDocling-256M-preview@ce51f56c4ebe36e0b1c3a55f67b261ba22a50bf8`へ解決。513,028,808 bytes、CDLA-Permissive-2.0。公式cardは後継`ibm-granite/granite-docling-258M@982fe3b40f2fa73c365bdb1bcacf6c81b7184bfe`を案内し、こちらは515,093,104 bytes、Apache-2.0。旧256Mと後継258M、code MITと重みlicenseを分ける。朝のSmolVLM2検証を再実行したものではない。
- AfriNLLB: `AfriNLP/AfriNLLB-12enc-4dec-iterative-481m-ft`。CC-BY-NC-4.0。商用許諾を得たとは扱わず保留。CTranslate2/Transformersの導入例があり、掲載例CUDAはCPU実証でない。

他の実在ID、revision、likes/downloads、ファイルサイズは[数値監査](../../benchmarks/research-repro-radar/2026-10-18-evening/metadata-audit.json)参照。APIの更新時刻は論文公開日と別。最新候補を探索したが全後継の不存在は証明しておらず、未確認を明示した。API/GKE/AWSの実料金比較は行わず、昔の参考単価を最新料金として使用しない。

## 全29候補の判断と残課題

以下はnative判定を原文照合した読解結果。資源だけで実行できない候補は保留、Cは現時点の優先度で完了IDではない。著者報告の数値は独立実測ではない。

|ID・一次リンク|判定|目的・新規性・導入判断|未確認・次の条件|
|---|---|---|---|
'''
for x in reviews:
 j=x['judgment'];unc=' / '.join(j['unconfirmed']);text+=f"|[{x['id']}]({by[x['id']]['url']})|{x['priority']}|{j['reason'].replace('|','/')}|{unc.replace('|','/')} 次: {(j.get('next_check')or'現条件では着手しない').replace('|','/')}|\n"
text+=f'''
## 問題・継続・保存

Squeez修復上限、YOLO26nの`0c6ddaa`での/app権限失敗、Harness²の`efd3c909`での隔離wrapper PermissionError、PP-LCNet停止、BitNet保留を維持。日付/環境変更、Kaggle、追加修復で再開していない。自動調査一時停止の既存質問は返答待ちのまま、再質問・自動停止なし。SKU、PR25/26/28、PR19中止案件に介入しない。検索はcoverage・不足補完・前処理・archive不要の新サイト発見に具体的見込みがない限り実装しない。NLPは別枠。

canonical `{report['canonical_base']}` の5,186件から継続。採点時5,198件、順位外未完了6件を復元して**5,204件**。`completed_ids=[]`、`recommended=[]`、読解深掘りは`reading_deep_dive_pending`へ分離し、digestや未実行を自動完了にしない。Skill直下でrank/record双方`--state-dir state`を用い、record件数0でもfreshnessへ正常runを追加。4JSONを同じevening-stateへ同期しseen/mailを維持。朝・weekly ci-stateとは混ぜない。全開始キューIDの残存とremoteのhashを確認する。

コードは本run、数値はbenchmarks、報告は本ファイルへ保存。実用機能を完成させていないのでdraft PRなし。メール/SMTP/cron/merge/deployなし。最終commit/remote hash/そのcommitのCI結果は最終返信で報告し、未確認の成功を先取りしない。CI成功もモデル検証成功ではない。

次は英語typed decisionの利用許諾と独立正解、Edge0のRSS/SSD前提、文書変換の具体的欠落を読む。候補実行には停止解除やソース承認を勝手に補わず、既存保留を維持する。

収集2,516／一次読解29（review_pool20を含む）／候補実行検証0／記録後未完了{pending:,}。
'''
(root/'reports/2026-10/2026-10-18-evening-radar.md').write_text(text)
print({'counts':report['counts'],'deep_dive_ids':[x['id']for x in deep]})
