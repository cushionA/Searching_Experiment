# 2026-10-24 JST 夕方・独立研究レーダー

## 結論・概要

**詳細推薦0件、未検証ダイジェスト8件。候補コード実行・モデル推論0件、現金0円。** native判定経路は成功したが、候補の再現成功ではない。独立した話題性2信号を確定できず、著者の大きな改善値を当方実測として昇格させない。

朝のReScraper/Host-Aware Crawling、昨日のTTR-Compressor/SCOPE/VerifierBenchほか、TokenEconomizer/AKV/SearchJev/Harness²/GRACEは新根拠なしの再通知を抑制。朝を再実行・変更していない。既存の隔離停止、Moli/Lightpandaのlibfontconfig不足・修復1回到達と追加修復待ち、PR19検索追加中止、SKU担当作業を維持。

## 方法・版・入力

開始08:57 UTC、上限09:27 UTC（17:57–18:27 JST）。CPU20分/候補、RAM8GB、DL2GB、修復1回、carry28日、現金0円。checkoutのAGENTS.md、関連research-repro-radar SKILL.mdと上流最新版 `55ceed634506c5942d6a8e84c71d7e80acc103ff` の必須8ファイルを読み、実行コピーとの全hash一致を確認。

`RADAR_LLM=native` を開始時から明示。probe要求ID `09cb1607-3b46-4d33-a83b-66a85f01fd2f`、task hash・schema・期待値を検証したexit 0の後だけ探索した。ホスト指定 `gpt-6-luna` と実サービスモデルIDは区別し、後者は取得できず `unknown`。CLI fallback・認証コピー・新規キー・有料backend・権限緩和なし。

HF Papers trending全返却50件、新着50件、関心5分野の10検索を取得。MDPI2誌、Crossref5検索、GitHub4検索、arXiv5検索を補完。GitHubの292件検索は3ページ全取得。arXivは最初の5ページに残り35ページを追加し、表示総数3,792レコードの全40ページを取得した。年齢による足切りなし。HFにはnext Linkがなく検索backend上限は未確認、Crossrefは関連度順100件の窓であり全件走査ではない。OpenReview5検索はHTTP403、迂回なし。

最終収集 **5,747一意候補**、繰り越し込み **9,376入力**を全件採点、ranked **8,022**。途中の追加収集後は元の正規stateから再採点し、carryを二重加算していない。最終review_pool20件を含む **31件**の一次資料を実際に読解し、4件を配布・license資料で再判定。native応答は要求ID/hash/schemaと180秒期限を確認。初回引用7件は原文不一致で不採用。追加4件の引用修正は1回、同じ154.34秒以内に完了し12引用すべて原本一致。

台帳: [開始](../../experiments/research-repro-radar/runs/2026-10-24-evening/start-manifest.json)、[native](../../experiments/research-repro-radar/runs/2026-10-24-evening/native-host-ledger.json)、[読解](../../experiments/research-repro-radar/runs/2026-10-24-evening/reading-ledger.md)、[数値・未測定項目](../../benchmarks/research-repro-radar/2026-10-24-evening.json)。

## 結果・既存比較・導入判断

**GoiBERT — B、NLP分類・抽出の条件付き候補。** [原論文](https://arxiv.org/abs/2610.30067)は10/21公開、v2は10/22。著者repoは5 stars、10/23更新。現行実在IDは [TwoheartsTechnologies/GoiBERT-S](https://huggingface.co/TwoheartsTechnologies/GoiBERT-S) / [GoiBERT-M](https://huggingface.co/TwoheartsTechnologies/GoiBERT-M)。10/24 07:33 UTC更新、26,397,696 / 49,366,016 parameters、公開F32重み105,609,040 / 197,486,112 bytes。名称変更前の2HeartsVNから現行IDへ解決することをAPIで確認。確認範囲の公式familyはS/Mで、別の公開一覧項目goi-intent-v1はカードがinternal用途・法務確認待ちで、汎用headとして代用しない。

公開checkpointはStage 1のbackboneだけで、分類/NER headなし。TransformersのAutoModel入口、推奨256 token、単語分割不要。日本語性能・雑入力耐性・本環境RAMは未確認。著者CPU latencyは128 token・8 threads・fp32でBamiBERT31.0ms、S14.2ms、M19.4ms。ただしencoderのみでtokenizer/headを除外し、2倍/3.5倍throughputはL40S GPUの値。品質比較は異なる学習条件を含み、test結果がモデル設計に影響した旨も著者が明記。大量短文に合う仮説はあるが、未配布headを学習で補うことは今回行わない。

現行の[モデルlicense](https://huggingface.co/TwoheartsTechnologies/GoiBERT-S/blob/a5652876eb722efb912d70e60d26260b78b670e3/LICENSE)は独自community条件。商用は組織グループ全体で100人未満かつ顧客1,000未満を要求し、到達時は別契約、上流BSD-3-Clause-Clear/Qualcomm RAIL等も累積適用と記載。単に「商用可」としない。GitHubコード側licenseは未確認。次は配布済みtask headと利用条件を確認し、条件が揃う場合だけ公開短文の分類/抽出を小さく比較する設計。

**WeVisOne-VL — B、視覚処理の統合候補。** [論文](https://arxiv.org/abs/2610.31329)10/22、[公式コード](https://github.com/Tencent/WeVisOne-VL)93 stars・10/23更新。検出/OCR/カウント/セグメント/mattingを統合し、小さいpixel decoderを使う。ただし全モデルが小さい意味ではない。公式2B/4BはF32重み **9.77/19.32GB**、両方DL上限超。READMEにはcpuオプションがあるが速度・RAM・小型量子化版は未確認。コードとモデルカードはApache-2.0、第三者部分は個別条件。HF229票と[hugging-apps demo](https://huggingface.co/spaces/hugging-apps/wevisone-vl-4b)を確認したが、demoは公式実装のadaptationで、独立した性能再現や作者からの独立性を確定していない。画像タスクを1系統へ集約する価値はあるものの、当方の費用・資源改善は未測定。

**RIDAR — B、AI駆動開発の監査設計。** [公式コード](https://github.com/THU-KEG/RIDAR)5 stars・10/23更新、MIT。[THU-KEG/RIDARデータ](https://huggingface.co/datasets/THU-KEG/RIDAR)も実在・MIT。研究エージェントの逸脱検知/介入に関係し、通常CI保守とは別枠。著者のF1 85.2%・違反36.8%削減は当方実測でない。Python3.10+、Analyzer/VerifierのAPI認証とCLIを使う公式経路で、無料経路・最小RAMは未確認。権限緩和コマンド例を実行しない。次は公開合成軌跡の失敗分類設計を静的確認する範囲に留める。

**T²Quant — B、量子化の時間配分。** [公式コード](https://github.com/OfekAvivi/T2Quant)MIT、3 stars・10/23更新。教師label不要の感度からdenoising区間のactivation bitを配分する。公式LLaDA-8B推論経路はCUDA40GB以上・4–5時間、今回上限外。著者READMEのGSM8K先頭500件では46.6→51.4%、sign-test p=0.055であり、当方の品質改善実測にはしない。CPUテストのみを走らせてもモデル推論再現にはならないため実行しない。

## 未検証ダイジェスト（8件）

A=試す価値、B=条件付き、C=不要。PoC完了をAの必要条件にはしない。今回は追加配布確認を踏まえB、すべて読解のみ。

- **B — [GoiBERT](https://arxiv.org/abs/2610.30067)**：26M/49Mのベトナム語encoder。分類・抽出の小型化に直結。 保留：現行TwoheartsTechnologies配布はtask headなし。商用は組織全体100人未満かつ有料顧客1,000未満等の条件付きで、上流条件も別途適用。学習へ拡大せず保留。

- **B — [WeVisOne-VL](https://arxiv.org/abs/2610.31329)**：検出・OCR・マスクを統合。HF229票、公式2B/4Bと外部名義demoを確認。 保留：2B版のF32重み9,767,053,328 bytesでDL2GB超。CPU速度・公式小型量子化版未確認。demoの独立性は未確定。

- **B — [RIDAR](https://arxiv.org/abs/2610.31372)**：研究エージェントの証拠・評価手順の逸脱を監査。MITコードと公開データあり。 保留：公式起動はAnalyzer/Verifier認証を要求。無課金経路未確認。権限緩和例を実行せず、公開軌跡の静的設計確認まで。

- **B — [T²Quant](https://arxiv.org/abs/2610.31198)**：拡散LMの時刻ごとに精度を配分し、同一bit予算で品質改善を狙う。 保留：公式LLaDA-8B経路はCUDA40GB以上・4–5時間。今回上限外、Kaggleへ迂回しない。

- **B — [Diffusion Language Models Change Speed Across Tasks and Languages](https://arxiv.org/abs/2610.25835)**：入力のコピー・編集と新規生成で推論量が変わる。要約・抽出のコスト評価設計に関係。 保留：著者の2モデル評価のみ。小型CPU実装・日本語入力での速度/品質未確認。

- **B — [EvalResearchBench](https://arxiv.org/abs/2610.04184)**：エージェントが作る評価器の順位一致と低コストな手動標本を比較する。 保留：API依存とlicense未確認。通常CI保守ではなくAI開発の評価設計候補、実行保留。

- **B — [Agent Mosaic](https://arxiv.org/abs/2610.30418)**：単独では許される文書の組合せから秘匿結論が漏れる現象を合成例で評価。 保留：公開実装・商用条件・無料の再現経路未確認。私有文書を渡さず保留。

- **B — [GAFU / What Generation Knows](https://arxiv.org/abs/2610.23987)**：生成側の視覚表現を理解へ再利用し、細部の認識を改善する設計。 保留：公式repoを確認したが後学習が中心。即利用checkpoint・商用条件・CPU資源未確認、学習しない。

## 問題・保存・次

外部候補推論は**pending / not attempted**。理由は10/17以来の資格情報・個人ファイルからの承認済み隔離が未確認のためで、新たな自動審査拒否ではない。停止を別環境やKaggleで迂回していない。GPU利用0、モデル重みDL0、候補修復0。GoiBERT repoリンクの.git除去、親の補助スクリプト生成時の引用符訂正は資料取得/文書作成の修正であり、候補実行の修復枠をリセットするものではない。費用・無料残量・公開範囲が未確認のKaggleへ投入しない。

CPU時間・RAM・品質・throughput・1万件換算は当方未測定。最新一次価格を使う比較を行っておらず、API/GKE/AWSより安いとは主張しない。実用機能完成ではないためdraft PRなし、merge/deployなし。メール/SMTP/Actions cronは変更なし。

最新文脈ではissue24はPR29により10/24 06:57 UTCにclosed。private session-ID取得はなお未対応、delivery_status unknownという記載を読み取っただけで、SKU作業へ介入していない。

正規remote `3b295c845fc3262b6a313f18d8461663dededba8` のstate4ファイルをSkill直下へhash一致で復元。`rank_candidates.py --state-dir state` と `record_run.py report.json --state-dir state` を使用。今回recommended/completed_idsは空。未実行・失敗・中断・digest・単なるnative rejectは完了扱いにせず、旧未完了6,606件を保持。詳細0もfreshnessへ正常記録し、seen/mailは不変。今回のfreshness実績は0/0で、有効な鮮度判定候補がないことを隠さず、adaptive設定は変更していない。

native検証器38テスト成功。state保存・未完了保持・一次資料hash・final review_pool読解範囲の検証結果は[verification.json](../../experiments/research-repro-radar/runs/2026-10-24-evening/verification.json)。独立研究ブランチ `research/2026-10-24-evening-radar` と正規stateブランチへ保存し、remote SHAとexact CI URL/状態は会話で追記する。

**収集5,747／一次読解31（最終review_pool20）／実検証0／詳細0／記録後未完了9,375。**
