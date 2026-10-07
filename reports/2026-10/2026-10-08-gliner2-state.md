# 2026-10-08 JST: GLiNER2.5-multi-Decideで本文の状態を分類できるか

**結論: 試す価値はAだったが、この販売状態分類への導入はB（保留）。** 日英48件で32/48正解、TF-IDF+logregの34/48を上回らず、誤った「販売中」が8件対2件。日本語は両者17/24だが、CPU約315ms/件・peak RSS 2.88GiBの追加負担に見合う優位は今回確認できなかった。採用や実サイト操作には進めない。否定的結果も再現可能なPoCとして保存する。

実行日: 2026-10-08 JST（UTC 10-07夜）。基点 `main f74698d`、既存PR #19〜#22と直近成果を確認。#19 mLateOnの再ランキングは今回変更していない。WebArchive網羅性も検索前処理も解決しない分類器を検索改善と呼ばず、独立したNLP枠で実施した。

## 候補調査と選定

A=直ちに検証する価値、B=条件付き、C=今回不要。starsなどは実行時のAPI取得値で固定日付の将来値ではない。根拠メタデータを `benchmarks/gliner2-state-20261008/{candidate-sources,selected-model-metadata,source-metadata}.json` に保存。

| 候補 / 目的・既存との新規性 | 公開・更新 / コミュニティ | license・商用 / 導入・資源・運用制約 | 判定・検証価値 |
|---|---|---|---|
| **GLiNER2.5-multi-Decide** / 呼出時に指定したラベル・質問に対する多言語分類。検索の類似度スコアと異なる分類専用checkpoint。生成なしのencoder、旧2のspan方式から2.5のboundary方式へ。 | HF作成09-24、更新09-28。GLiNER2 repo 2,343 stars / 210 forks、main更新09-24。モデル57 likes / 13,049 downloads（表示期間依存）。 | コード・重みApache-2.0表記、商用利用可（条件順守）。Python、torch CPU、gliner2[local]＋追加tokenizer依存。287M、FP32重み1.15GB、実測RSS2.88GiB。GPU不要。短文で検証、長文・言語差・スコア校正は別課題。 | 調査A→今回用途の導入B。state＋typed choicesに近く、日本語と文脈判定の価値を実測できるため選定。 |
| GLiNER2.5-Decide英語版 / 同じ分類方式の英語特化対照 | HF作成09-23、公開告知09-24、更新09-28。開発コミュニティは同上。 | Apache-2.0表記、商用可。CPU/GPU、340Mと公式説明。RAMは未測定、最低限重み約1.36GBの概算にruntimeを加算（HFサイズ表示との齟齬あり）。導入は同程度。 | B。日本語を主対象とする今回にはmultiを優先。英語に絞る用途なら再検証価値あり。実行しない。 |
| SetFit / 少量教師例でSentence Transformerと分類headを学習 | repo作成2022年、main更新2026-10-06。2,836 stars / 270 forks。 | コードApache-2.0、商用は選ぶencoder重みの条件も確認。Python/setfit、CPU推論、学習はencoderとデータ次第でGPUが有用。RAM未確認。学習例・モデル保存管理が必要。 | B（今回は実装しない）。教師あり比較には有望だが、今回は新しいzero-shot判定器の検証を優先し、学習側の下限をTF-IDFで置く。 |

一次情報: [GLiNER2 repo](https://github.com/fastino-ai/GLiNER2)、[multi-Decide card](https://huggingface.co/fastino/GLiNER2.5-multi-Decide)、[英語Decide card](https://huggingface.co/fastino/GLiNER2.5-Decide)、[公開告知](https://fastino.ai/blog/gliner-2-5-decide-open-weight-decision-model)、[SetFit repo](https://github.com/huggingface/setfit)、[SetFit quickstart](https://huggingface.co/docs/setfit/quickstart)。公式のDecide比較にはJevK5があるが、TypeSafeJevと同一だとは扱わない。multi版の公式比較データも英語であり、日本語性能は未実証だった。

## 技術概要と期待差

GLiNER2.5-multi-DecideはmDeBERTa-v3-baseをencoderに使う分類特化モデル。`AutoExtractor.classify_text`へ本文・4候補・候補説明・質問を渡す。実測パラメータ数287,355,159。学習やAPI推論は行わず、FP32・CPUローカルで完結した。

期待した差は、単語一致では難しい「古い在庫表示」「他商品の在庫」「否定」「予約と即納」を文脈で区別すること。結果はその期待を満たす採用根拠にならなかった。スキーマに収まる出力が得られることと、意味的な正しさは別である。

`typed_state()` は `state` をenum検証し、`can_ship_now`をtrue/false/nullへ写像する薄いアダプタ。ここは分類結果から導く決定的な変換で、別の質問をモデルが正しく答えた証拠ではない。TypeSafeJevのstate＋typedquestions / probabilisticChoiceScoreNoulの実装互換性は検証していない。mLateOnを分類器へ流用できるかも今回の対象外。

## 方法と公平性

- 公開checkpointと手作り合成文だけを利用。train48文/test48文、各言語24文、各クラス6文。各24組の日英翻訳ペアであり、言語をまたいで独立な48観測とは言えない。
- 状態は `available / sold_out / preorder / unknown`。train/test本文重複なし。モデル実行前にfixture・schema・protocol・閾値をhash固定し、test結果を見た調整なし。
- baseline 1: 固定キーワード、複数状態がヒットしたらunknown。単純な下限で、最適化済みルールシステムではない。
- baseline 2: char 2–5 TF-IDF＋logreg（C=1、max_iter=1000、seed42）、train48文で教師あり学習。GLiNERはzero-shot。同一testで比べるが、事前学習・教師データ量が同じという比較ではない。
- 同一CPUで各engineを別プロセス、2 threads、batch1、固定順、各文2回。warm-up1文を除外。中央値/p95は96呼出、精度の分母は48文。繰返しlabel一致は全件。実行順はrules→TF-IDF→GLiNER。
- スコア0.8以上かつ非unknownを残す診断用gateを事前固定。校正・閾値学習なし。比較器間のスコア尺度を同一とは仮定しない。
- AMD EPYC 9V74仮想CPU（可視5CPU）、Python3.12.14、torch2.14.1+cpu、gliner2 2.0.0、sklearn1.9.1。peak RSSはLinux単一プロセス全寿命最大値で、import/loadを含む。GPU/Kaggle・有料APIなし。

## 結果

| engine | 全体正解 / accuracy | 日本語正解 / macro-F1 | 英語正解 / macro-F1 | warm中央値 / p95 ms | peak RSS MiB |
|---|---:|---:|---:|---:|---:|
| 固定ルール | 18/48 / 37.5% | 9/24 / 0.3222 | 9/24 / 0.3326 | 0.0022 / 0.0063 | 13.6 |
| TF-IDF+logreg | **34/48 / 70.8%** | **17/24 / 0.7097** | **17/24 / 0.6931** | **0.3867 / 0.4801** | **119.9** |
| GLiNER2.5-multi-Decide | 32/48 / 66.7% | 17/24 / 0.6922 | 15/24 / 0.6242 | 314.89 / 372.94 | 2,951.0 |

GLiNER setup/import/load 7.11秒、初回warm-up314ms、実行全体37.45秒（ダウンロード除外）。初回download約95.10秒、モデルファイル1,149,461,028 bytes。TF-IDFの学習/importを含むsetupは0.703秒。warm推論はこの測定でGLiNERが約814倍遅いが、マイクロ秒領域baselineの比率を一般化しない。

「available」への誤分類はGLiNER8件（日本語3）、TF-IDF2件（日本語1）、ルール2件（日本語1）。例えば日本語の「最後の1点がたった今購入されました。補充をお待ちください。」をavailable（score0.593）、「商品は重さ230グラムで4色展開です。」もavailable（0.533）とした。英語でも古いsold-outに引きずられた。単純ルールより全体正解率が高くても、誤った販売可能判定を増やす可能性がある。

事前固定gateはGLiNERで14/48件だけ通過し14件正解、日本語7/24件。残る70.8%は要確認。TF-IDFは0/48通過で、この閾値をモデル横断で使えないことも明らか。14件無誤りは100%信頼や正解確率0.8の検証ではなく、校正済みとは言えない。unknownは真のタスクラベル、低score棄却はreviewの判断として別途保存した。

## 問題・限界

1. 合成・小規模・単一作成者。4状態だけでクラス均等。実サイトHTML、対象商品の特定、複数variant、配送地域、DOM変化、業務損失分布は評価していない。英訳・和訳ペアで相関があり、統計的な優位や本番精度を主張しない。
2. prompt/label名は英語、分かち書きは学習に合わせupstream既定whitespace。日本語向けchar splitterやpromptの比較は行わず、失敗後のtest調整もしない。長文・バッチ・量子化の最適化未検証。
3. `gliner2[local]`だけでは初回loadがprotobuf不足で失敗。sentencepiece/protobuf追加で解消しlockへ記録。初回tracebackを保存。
4. tokenizerのlegacy metadata補正、SDPA非対応によるeager fallback警告が出た。明示的にflashdebertaを使わず、実行ログに記録。警告を隠して最適化性能とは報告しない。
5. ローカルlab準備時npm既定cacheが書込不可。cacheを/tmpに指定して解消。repo保守へ変更範囲を拡大していない。
6. typed出力や高いscoreだけで実行可否を決める設計には不十分。classificationは根拠spanの正しさや真偽判定器の正解確率を保証しない。

## 導入判断と次

今回用途への組込みは保留。用途別に見ると、zero-shotで毎回ラベルを変更したい分類実験には価値がある一方、この固定4クラスではTF-IDFが有利だった。次に進むなら独立した実ページ本文の正解集合と業務別コスト、校正用validationを先に用意する。今回一巡で終了し、追加checkpoint探索・mLateOn検索開発・自動購入・merge/deployはしない。

成果: `experiments/gliner2-state/`（コード・固定input/protocol・lock・再現コマンド）、`benchmarks/gliner2-state-20261008/`（全予測・数値・hash・source・エラー・検証ログ）。重みは/tmpへ保存しcommitしない。専用CIは6件の契約・data leak・保存数値再計算テストを行い、巨大モデルはCIで取得しない。ローカル新規6 tests成功、既存lab151 tests成功（15 skipped）、pip check成功、固定revisionの再取得manifest一致を確認。初回エラーログは行末空白だけ除去。最終remote commitとCI結果はPRで確認する。
