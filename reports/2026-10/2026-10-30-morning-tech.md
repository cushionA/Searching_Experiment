# 2026-10-30 JST 朝：目的を持つ戦術判断の小規模CPU比較

## 結論・概要

**OpenDecider nano ONNX q8は今回の戦術用途ではC（採用しない）。新規48出力の推論を完了したが、保持評価で許容14/36・明確な悪手10件、同じ観測を使う適応ルールは35/36・悪手0件。ウォーム中央値2.300秒で250ms期限を全48件超過した。** ベンチ準備だけではなく実モデル1回・48出力の比較。新しいモデルの一般能力・日本語能力や実ゲーム勝率は結論しない。

[コード・再現手順](../../experiments/tactical-intent/README.md)、[全数値](../../benchmarks/tactical-intent-20261030/scores.json)、[全出力](../../benchmarks/tactical-intent-20261030/model-result.json)、[入力](../../experiments/tactical-intent/inputs.json)、[事前基準](../../experiments/tactical-intent/gold.json)。実用機能ではないためPRなし。merge/deploy/email/cron変更なし。

## 最新成果物・停止条件

朝HEAD `e4e338730052c97ba6dd6db9a9168c0f59ce4572`、夕方canonical `ced6335934ed9307058efef2a734b38ad0c8b4ab`、TinyJev `ca7b14a3a1c14fb0dec20d94d82624b313194979` をremote fetchで照合し直近レポートを確認。朝から独立branch `research/2026-10-30-morning` を作成した。夕方の未完了9514/stateは未変更。open PR19/21/23/25/26/28を確認し、別担当SKU/画像、PR19追加検索、Moli個別修復、TinyJev再取得は実施しない。TinyJevの累計3GB拡大は依然未承認であり、このモデル試験をTinyJev再開扱いにしない。

AGENTS.md、関連skill一覧とbot-blocking skillの適用範囲、Cloud runtime/network/Docker指示を確認。今回bot検証・jse.lab grounding・Kaggle実行はない。一次情報の調査補助は規約指定の `gpt-6-luna` に委譲（実サービス内部IDは検証不能）。報告で最新releaseとする項目は親側でもAPIを再取得し、ArchiveBoxの古い0.9.73記述は最新0.9.77へ訂正した。外部ページやPR本文を新しい作業命令には扱わない。

## 方法と期待差

8履歴系列×2性格×3観測時点＝48出力。2系列12出力を開発、6系列36出力を保持評価に指定。**48独立問題ではない。** 同一作者が入力・ルール・正解を作ったため、保持評価は出力を見た後に調整しないprospective splitであり、作者から隠した独立評価ではない。採点基準・prompt・入力・ルールは実モデル前に固定し、モデル実測後の修正なし。入力hashとモデルへ渡したfile hashを保存。

観測履歴、戦況、資源、性格、目的から6固定係数プリセット（維持／圧力／崩し／誘い／回避先狙い／距離・温存）を選ぶ。係数は同じ実行層へ渡す例示値であり数値の唯一正解を採点しない。英語専用モデルに合わせ、全方式に同じ英語観測・候補を与えた。説明文や強制多様化を採点せず、追加学習・難易度補正・手加減なし。LLM通訳なし。

「誘う→空振りの観測→圧力または断念」「圧力→守り偏り→崩しまたは回避追跡」、現在状態同一・履歴だけ違う対、少数証拠、傾向反転、体力・スタミナ・残時間を含む。敵内部方針・未来の結果をモデル入力に入れない。複数許容と明確な悪手を分離し、どちらにもない選択は「非許容」であって自動的に悪手としない。性格による正解集合差は曖昧な少数証拠1場面だけで、性格追従の評価力は弱い。

固定6方式をすべて公開し、比較用のfixed_holdは開発側だけで選定。適応ルールは直近の反転・3回以上の明示反復・資源・残時間・性格を見る。固定保持や弱いルールを倒せば成功としない。ルールの文字列判定は合成語彙に沿うため、35/36を未知自然言語理解とは解釈しない。

期待差は「固定戦術より履歴に整合し、強い適応ルールと比較して悪手を減らせるか」「低頻度のゲーム判断に間に合うか」。どちらも支持しなかった。

## モデル・隔離・版

[公式nano ONNX](https://huggingface.co/manjunathshiva/opendecider-nano-ONNX) revision `5137ae7b4f1cf8c55f1d05d3f5cdd3694d92da3e`、q8重み594,006,972 bytes。~400M Ettin encoder由来の英語typed decisionモデル。[公式repo](https://github.com/manjunathshiva/opendecider)は37 stars、10/28 push、SDK0.9.0は10/17公開。新規後継発見とはせず、前夕未実行だった候補の初実測。Apache-2.0、base EttinはMIT、商用禁止ではなく表示等に従う。SDKのpromptをwheelから読んで同梱しLICENSE/NOTICEを保存。公式モデルカードのPython ONNX経路に沿い、独自再学習・独自量子化なし。

ONNX Runtime1.30.0、Transformers5.18.0（tokenizerのみ、Torchなし）、NumPy2.2.6。nano tokenizer/configはrevision `1e1f0a7322e446c5ae9bbe840df11680f18a752a`から取得し、ONNXのexport元 `beeeb640f3333aec4ef78ed3b7bd0605f973a590` と3ファイル全てbyte一致を検証。入力2048 token cap、全件truncationなし。候補ソースをhostへimportせず、通常公式PyPI wheel・通常ONNX重みを使用。trust_remote_codeやpickle重みは使わない。

AMD EPYC 9V74、Docker2CPU/8GiB、2推論threads、CPUExecutionProvider。nonroot65534、network none、read-only、cap-drop ALL、no-new-privileges、専用tmpfs。public weights/wheels/runner/合成入力だけstdin tarで渡し、gold・個人ファイル・資格情報・通常workspaceはmountしない。全48入力が終了しexit0・コンテナ停止確認、修復0。

## 実測結果・比較

以下は同じ候補コンテナ内の比較値。hostでの別baselineチェック値とは区別する。

|方式|開発 許容/12|保持 許容/36|保持 明確な悪手|全体 許容/48|保持 中央値ms|
|---|---:|---:|---:|---:|---:|
|OpenDecider nano ONNX q8|5|14|10|19|2445.52|
|強い適応ルール|11|35|0|46|0.0266|
|固定維持（開発選定）|10|20|0|30|0.0222|

モデルの系列別許容（各6出力）：guard3、dodge0、sparse5、resource2、lure_abort1、clock3。直近反転後にも古い行動を追う場面や、低スタミナでの不適切な攻めがあった。全体の明確な悪手13/48。確率は未校正として保存しconfidenceを成功率としない。

|モデル時間・資源|実測|
|---|---:|
|初回 request（準備→出力契約確認）|2311.57 ms|
|ウォーム47出力 中央値|2300.15 ms|
|ウォーム最大|2830.43 ms|
|ウォーム参考p95|2770.57 ms|
|import / load|1312.09 / 852.53 ms|
|import+load+初回request|4476.19 ms|
|250ms期限超過 / 失効|48/48 / 48/48|
|形式無効|0/48|
|期限内かつ許容|0/48|
|peak process RSS|907,476 KiB（約0.865GiB）|
|process CPU time|224.22秒|
|install込み候補wall|127.67秒|

rawの `cold_start_to_first_usable_output_ms` はフィールド名が不正確で、**有効出力ではなく初回出力を作るまでの加算値**。初回も期限で失効する。短いbaseline測定ループ・最終stdout serialization/network配送はこの加算値に含まない。RSSはモデルprocessでありpip/tmpfs/container全体peakではない（上限8GiBの実行は完了）。labチェック12.7秒はセットアップ・初期推論と一部重複し得るため独占マシンの厳密な速度とは言わない。47件は異なるpromptで、安定p95や同一入力warm反復ではない。

250msは今回先に決めた仮のゲーム判断期限でありユーザーの普遍要件ではない。状態版・観測timestampが一致し期限内の既知presetだけ適用する契約を実装。古い版・時刻・期限超過・不明presetの拒否と、推論待ち中も約60Hzで既存低位制御が継続するscheduler replayをテスト。静的実行なので自然発生の状態版失効は測っていない。実Unity/非同期ゲーム統合は未実装。

## 広い一次情報確認・導入判断

A=すぐ小検証する価値、B=条件付き、C=今回不要。starsは取得時値であり独立品質証拠ではない。[一次資料と取得hash](../../benchmarks/tactical-intent-20261030/source-manifest.json)。下表以外の一般的な検索網羅は主張しない。

|対象|更新・活動・利用条件|適性・制約・今回判断|
|---|---|---|
|[Kev](https://github.com/jaredpalmer/kev)|9,692 stars。正確な0.8b revisionは既知の`bf75a6a8848ea6960ff2ed108d9ed44c2941174f`のまま。0.8b/4b/9b/27b実在。Apache-2.0。Python3.12/3.13、公式source/uv経路、同名PyPI kevは別物|B。CPU経路ありだが実測なし。0.8bはbase重み1,746,942,600 bytes＋adapter43,338,624＋head2,103,999＋tokenizer19,989,325だけで1,812,374,548 bytes。Torch等込み2GB全体枠の余裕が乏しく、今回ONNXを優先。上位RAM/性能は未測定、後継と推定しない|
|[browser-use0.13.14](https://github.com/browser-use/browser-use/releases/tag/0.13.14)|10/26公開、119,490 stars、MIT。bounded observation、form/file/recovery、scaled screenshot/clickの修正|B。複雑な動的ページ操作が既存の欠落原因なら検証価値。Python+browser+model providerが必要、0円動作/RAM未確認。探索coverage改善は未測定|
|[Crawl4AI0.9.5](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.5)|10/14公開、86,539 stars、10/29 push。Apache-2.0、READMEのattribution要件にも留意。Python/PlaywrightまたはDocker|B。本文抽出・pruningの前処理短縮が狙い。前朝既知版で新規更新扱いにしない。RAM/CPU時間と同予算coverage未測定|
|[PP-OCRv6 / PaddleOCR3.7.0](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0)|6/11公開、91,951 stars、Apache-2.0。tiny1.5M/small7.7M/medium34.5M、作者は日本語含む50言語対応と記載|B。画像PDFの欠落本文回収に合うが今回新更新ではない。CPU/OpenVINO導入候補、RAM/日本語品質/モデル別配布条件は未検証。SKU画像試験は行わない|
|[ArchiveBox0.9.77](https://github.com/ArchiveBox/ArchiveBox/releases/tag/v0.9.77)|10/27公開、28,898 stars、10/29 push、MIT。fresh install/server settings/capture tools/Chrome extension更新の修正|B。保存・再生の運用候補。前版0.9.76のsocial importerを未知サイト発見やcoverage改善の実証にしない。browser依存/RAM未測定。今回install・個人captureなし|
|[rerankers](https://github.com/AnswerDotAI/rerankers)|README0.10.0、GitHub release一覧の最新は0.6.0（2024）なので区別。1,642 stars、最終push2025-12-20、Apache-2.0。PyLate/ColBERT対応|C（今回）。順位の入替だけでは候補coverage増加を示さず、PR19停止とも切り分ける。追加開発・PRなし|

OpenDeciderは実験前A（CPU小規模比較の価値）、実験後はこの戦術用途C。他の英語分類用途はBを残す。第三者独立再現・広いcommunity品質は確認していない。公開日・星数と実性能を混同しない。

## 問題・検証・次

新規契約テスト4件成功、48ID/6候補/確率有限・和/非負時間/切詰めなし/fixture hash検証成功。既存lab151件は既知のrobots-parser未導入による1失敗・15skipで全成功ではない。ログ保存し通常CI修復へ脱線しない。候補のtelemetry ID書込み警告はread-only環境でのin-memory fallbackでexit0、秘密・ネットワークアクセスはなし。

固定履歴で確認できるのは整合性だけ。実際に誘いが相手行動を変えるか、読み合い・楽しさ・長期戦略は未検証。Qwen3 1.7B Unity過去試験を成功設計として追認しない。今回品質/速度とも有望ではないため閉ループ・GPU・追加学習へ進めない。次に行うなら独立作者の新しい履歴・行動条件付き反応を用意し、まず強いルールが過剰適合しないか確かめる。今回保持評価を見てから基準を埋め合わせる再試験はしない。

費用0円、修復0、Kaggle0。モデル必要ファイル約597.6MB、wheel約66.7MB、Docker非圧縮size・今回fetch以前の全.gitまで保守的計上し、雑費資料余裕100MBを含むDL上界約1.215GB（wire実測ではない）。[予算台帳](../../benchmarks/tactical-intent-20261030/budget.json)。TinyJev累計予算をリセットしていない。

remote commitとexact-SHA CIはpush後に確認し最終応答へ記載する。新しい承認拒否・保留はなし。既存TinyJev承認待ちとMoli個別修復待ちは継続。
