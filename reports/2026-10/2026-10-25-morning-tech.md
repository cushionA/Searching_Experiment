# 2026-10-25 JST 朝・独立技術調査

## 結果

新規詳細候補1件、予備確認1件。**SeekをB（固定コーパスのrecall改善に条件付き）、CADE-QSを今回の検索結果収集にはC**と評価する。候補コード実行、実モデル推論、依存導入、weight/DL、外部対象site取得はすべて0、費用0円。

既報の10/24朝ReScraper / Host-Aware Crawling、夕方のGoiBERT / WeVisOne-VL / RIDAR / T²Quantほか、およびSearchJev、TTR-Compressor/SCOPE/VerifierBench、TokenEconomizer/AKV/Harness²/GRACEを再紹介しない。過去report検索ではSB-CLASSIFIERは10/21に既報、10/22に既報認識、10/23にMangoとの比較内でも言及済みと確認し、重複に気づいた時点で今回の候補から除外した。

指定remote branch `research/2026-10-25-morning` は未作成だった。最初の `git fetch` はbranch不存在で失敗し、`git ls-remote --heads origin` で確認後、10/7〜10/24 daily-tech / 10/24 evening branchの報告を検索した。今回のlocal branchは `origin/main` の `4d2984a454fc766137784fd6a51579e10b6fafd6` から作成した。親のread-only GitHub API確認では、issue 24は2026-10-24 06:57:27 UTCにclosed、PR29はmergedでissue 24をclose。これらは状態確認のみで操作なし。PR19検索追加停止、NLP分類抽出の別枠、外部隔離停止、SKU担当作業、9,375件stateに変更なし。

## 調査方法と入力

通常の一次情報技術調査として、論文本文・著者repo・README・config・requirements・license/activity表示を静的に読んだ。論文値は著者報告として記録し、今回の独立実測とは区別。検索候補追加は網羅不足に具体的に効く場合だけを評価した。利用制約は0円、約25分、RAM8GB、download2GB。隔離実行停止を保ち、候補コード・モデル・依存を取得も実行もしていない。

## B — Seek: 反復検索と関連度評価で候補poolを拡張

[一次論文](https://arxiv.org/abs/2609.28980)は2026-09-24公開、CIKM '26は11/7〜11/11予定。[著者repo](https://github.com/aminbigdeli/SEEK)は1 star / 0 forks。親のread-only GitHub API確認によるlatest main commitは[32f89f2](https://github.com/aminbigdeli/SEEK/commit/32f89f2f8be57e0b7b487b613a6d9a68b482e675)、committer dateは2026-06-13 18:19:15 UTC。GitHub repoでlicense表示を確認できず、コード商用利用条件は未確認。arXivページ冒頭のmetadataはperpetual non-exclusive license、本文脚注はCC BYと表示が異なる。コードやモデル重みのlicenseを定めるものとは扱わない。

この方式は、最初のBM25候補だけを再順位付けするrerankerと異なり、LLMでqueryからpseudo-passageを作り、固定コーパスを再検索し、新規文書を0〜3で関連度評価して次の生成へ返す。最大5round・1roundあたりpseudo-passage 5本、新規候補10文書を評価する設定。つまり**既知のindex内で一度目に取れなかった文書を再探索する**方法であり、外のWeb siteや新規URLを見つけるcrawlerではない。

著者はTREC DL19/20とBRIGHTでBM25よりRecall@100を改善したと報告。BRIGHT biologyの42→82はFigure 2本文の比較値。別のFigure 3本文はQwen2.5-7Bとしてbiology recallがBM25の22%から5round後58%と記す一方、Figure captionはQwen3。図中終盤のnDCG約46%もTable 1のSeek/Qwen2.5 biology値37.7と一致しない。評価条件が揃っていると確認できないため、Figure 3をFigure 2/Table 1と同一条件の連続結果として結合しない。すべて著者値で、当方の再現やWeb全体の網羅率ではない。

公式READMEの標準例はPyserini BM25 indexにOpenAI互換LLM APIを組み合わせ、configはOpenRouterをdefault endpointとしてAPI key環境変数を読む。open-weight検証はQwen2.5-7B、Qwen3-4B等。requirementsはTransformers、PyTorch、Accelerate、datasets、Pyserini、faiss-cpu、QueryGymなど。ReasonIR-8B選択時はcorpus embedding作成も要る。CPU/RAM最小条件、遅延、model weights/license、code licenseは未確認。今回の0円、download2GB、8GB RAM、認証変更禁止、隔離停止の範囲では実証しない。

**条件付きで試す価値あり。** 対象が既存index内の文書で、single-pass retrievalのrecall不足が測定されているなら、BM25 baselineとのRecall@k、取得候補重複率、1 queryあたりAPI/token費用を小さな既存評価集合で比較する設計候補。公開結果から期待できる差はfixed-corpus recallで、サイト/URL discoveryや前処理時間短縮ではない。

## C — CADE-QS: 次の検索語の提案

[SIGIR 2026論文](https://dl.acm.org/doi/10.1145/3805712.3809694)と[公式repo](https://github.com/lycorp-jp/cadeqs)を確認。conference programは2026-07-19付で掲載、会議は7/20〜7/24。repoは0 stars / 0 forks。親のread-only GitHub API確認によるlatest main commitは[73e686b](https://github.com/lycorp-jp/cadeqs/commit/73e686bb48f4397b82a38aa414c1944ea121c6bc)、committer dateは2026-07-17 00:56:21 UTC。codeはCC0-1.0と表示され商用再利用できるが、一時公開で非公開/read-onlyになる可能性を著者が明記。query log/model weightsの条件は未確認。

セッション履歴から過去query indexをdense retrievalして「次に使うquery」を提案する。会議abstractは日本語web検索ログ/AOLでの評価とCPU約30msを著者値として報告する。一方、query suggestionが実際に新しいURLや検索結果を発見し、現行workflowのcoverageを増やすかは一次資料から確認できなかった。標準pipelineにはPyTorch/Transformers等とMiniLMv2学習例、GPU向けextrasが含まれ、minimum RAM/CPU、model weights/license、日本語log利用条件は未確認。よって**現在の検索結果収集への適用はC**。別途query提案を主目的とする場合にだけ再評価する。

## 所要時間、問題、導入判断、次

静的調査は117秒。候補ソフト実行・実モデル検証・install・重みDL・外部site取得はいずれもなし。未解決事項は、remoteに指定branchがなかったこと（新規local branch作成で継続）と、承認済み隔離が停止中のため実モデル検証をpending/not attemptedとしたこと。auto-review拒否はなし。実モデル検証はfalse。固定indexでRecall@100を上げうるSeekはB、search suggestionから新規URLを発見する根拠が不足するCADE-QSは現行タスクではC。次に進めるなら、Seekを公開・許諾済み小規模コーパス上のオフライン評価だけに限定し、API費用なしの実行手段、code/model/data license、隔離可用性を先に確認した後、single-pass BM25に対するrecallとコストを比較する。今回は実行を開始していない。

詳細な一次出典、数値、資源・license未確認点、既報重複監査、時間とblockerは[primary-evidence.json](../../benchmarks/daily-tech-20261025/primary-evidence.json)。追加の自作検証コードなし。PR/merge/deploy、外部メール/SMTP/cron変更なし。
