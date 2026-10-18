# 2026-10-18 JST 朝の技術調査

## 結論

**A 0 / B 2 / C 1。一次情報と導入条件の監査を完了。候補コード実行・モデル推論0件、性能改善の実証0件、機能PRなし。**

新しい検証余地は、Playwright 1.64のWebMCPによるサイト内操作の構造化と、SetFit 1.2の独立分類器のONNX出力互換性。いずれも利用対象・独立正解が足りずB。一般的なクローラー比較、順位の並べ替え、SKU比較は行わない。SmolVLM2は実在する軽量画像理解候補だが、今回の日本語文書用途への追加価値が未確定でC。古いモデルを新公開と扱わない。

有用な確認は次の3点。

1. **WebMCPの`page.webmcp`はmain frameのみ。** 子frameのtoolを取得するには各`frame.webmcp`が必要。`readOnly`等のannotationもページ側の申告で、サイトが公開したtoolの安全性・網羅性を保証しない。新サイト発見や検索coverage改善は未証明。
2. **Playwright 1.64.1を入れるだけでは既存adaptiveのredirect制限は解消しない。** 修正はChromium 155+のroute登録時のpreflighted cross-origin redirect回帰。現行`jse/lab/adaptive.py`は追加resourceの30xを明示拒否し、offline＋Impit経由でfulfillする。上流のブラウザ修正と、この設計上の拒否は別。
3. **SmolVLM2の全配布物4.06GBと必須取得量は別。** API上のINT8主要3部品合計は260,186,962 bytes。q4の合計は264,221,217 bytesでINT8より小さくない。`embed_tokens_q4`が113,541,438 bytesあるためで、量子化名だけの予算判断はできない。実行可能な組合せ・量子化品質の確認ではない。

## 概要・着手前の確認

観測は2026-10-17 21〜22時台UTC、10/18 JST朝。base/main `7460493cc3f7242748c95f711e71a59537ef34ea` から独立branch `research/2026-10-18-daily-tech`。

`AGENTS.md`、`docs/cloud-lab.md`、checkoutの`.agents/skills/bot-blocking-scenarios/SKILL.md`を先に確認。今回は通常の公開一次資料調査であり、jse.lab grounding探索・サイト診断は実施していない。規約に従い一次調査を`gpt-6-luna`指定agentへ委譲し、親が既報照合と数値・判断を補正した。サービス内部model IDは独立確認できず`model_runtime_verified=false`。

- remote heads、open PR6件、open issue #24、同issue comments（0件）、PR28本文/comments/reviewsを確認。[状態snapshot](../../benchmarks/daily-tech-20261018/open-work.json)。PR25/26/28のheadは`62daa3e` / `e614a3f` / `ae63d65`。別担当のSKU入力・推論・修理・再開・変更は行わない。PR28の160/165軸一致は正答率ではないという記録も維持。
- 朝10/13、10/15 `42d625a`、10/16 `1adb4a0`、10/17 `0c6ddaa`、夕10/17の報告を確認。過去のSKU continuationは古いv8記録であり、10/15・16朝報告が確認した後日のresume-s000 v1正常完了・15,616records回収を巻き戻す根拠にしない。今回Kaggle上の現状態は照会していない。
- **夕方canonical `efd3c909d2d729cfc0e8f189a01dd213bd400257`、queue 5,186件を読み取り確認。** freshness/mail/queue/seenのSHA256をsnapshotに記録。夕方branch/stateへ書込みなし。
- Squeez、YOLO26n、Harness²の修復上限到達による停止を維持。先行報告にあるPP-LCNet停止とBitNet保留も再開しない。日付・環境を変えた再試行なし。自動調査停止の質問は繰り返さず、自動化にも変更なし。
- PR19 mLateOn検索追加開発は中止継続。PR23の分類比較やSKUの新比較も行わない。400M索引・生データ・メール・cron・merge/deployに変更なし。

## 方法

GitHub metadata/直近3release、固定tagのlicense・実装・文書、Hugging Face APIのmodel metadata/file size・固定revision card/config、公式authorのSmolVLM系列最大100件（応答13件）を照合した。20保存資料のうち1件は本repoのSKU継続文脈、残り19件は候補の一次資料。URL・取得時刻・bytes・SHA256は各manifestに保存。観測範囲内の更新確認であり、全分野・全後継モデルの網羅調査ではない。

[取得・検算コードと手順](../../experiments/daily-tech-20261018/README.md)、[数値](../../benchmarks/daily-tech-20261018/audit.json)。公開資料はテキストとしてのみ読み、外部コードimport・依存install・ブラウザ起動・重みDLなし。Kaggleは計算不足の事実も利用条件確認もないため利用せず、認証・残量・accelerator・private設定は未確認のまま依存実行を保留。

## 候補・実用性

starsは観測時点の関心指標で、品質・採用数ではない。商用利用は各license条件と入力・依存・重みの別条件を満たす場合に限る。

|候補・目的・新規性|公開/更新・活動・community・実在ID|license・導入・資源・運用|判断・追加検証価値|
|---|---|---|---|
|**Playwright 1.64/1.64.1**：WebMCPでページの構造化toolを列挙・呼出。画面クリック列を減らす仮説。新サイト発見器ではない。|`microsoft/playwright` v1.64.0 10/7、latest v1.64.1 10/14 11:51 UTC。97,938 stars、10/17 push、Microsoftと公開issue/PRの継続開発。独立したWebMCP導入実績は未確認。|Apache-2.0本文保存、条件下商用可、browser配布物は別。Node/browser/OS依存で導入中〜高。通常browser処理にGPU必須ではないが最小CPU/RAM・DL総量未測定。WebMCPはexperimental、Chromium feature flag、Firefox prefsが必要、WebKit未実装。|**B**。登録済みの読み取り専用toolを持つ公開サイトが実在し、操作短縮・取得漏れ補完を測れる時だけ。ページ・frame別列挙、既存DOM経路との同じ出力/時間/失敗率が評価対象。該当サイト未確定なので導入しない。1.64.1だけの通常回帰修理は今回対象外。|
|**SetFit 1.2.0**：少数ラベル分類。新差分はTransformers5/ST6/Hub1等の互換性とONNX/OpenVINO export修正。新学習法や新重みの公開ではない。|`huggingface/setfit` latest v1.2.0 9/4、push10/6、2,843 stars。複数contributorと新規contributorをreleaseに記載。モデルではなくframework。base例`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`実在、revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`、2022/3/2作成・2026/1/28更新。|code Apache-2.0本文確認、baseもAPI card Apache-2.0。Python3.9–3.13対応、torch/ST/tokenizer/export依存で導入中。base safetensors 470,641,600 bytes、RAMとは別。CPU/GPU実測・依存込みDL量未確認。学習済分類headと独立ラベルが必要。|**B**。SKUと分離した文章分類、例：文書の通知/説明/規約という内容タグを少数例で付ける用途。新ラベル正解と学習済artifactが未確定なのでAにしない。NLP分類は検索制約と別枠で評価。ONNXと元モデルのラベル/probability一致・CPU時間を確かめる価値があるが、互換修理だけを主目的にしない。|
|**SmolVLM2-256M-Video-Instruct**：小型画像/動画理解。旧SmolVLM-256Mの後継に当たる既存公開物の導入条件を精査。|HF公式ID実在、revision `067788b187b95ebe7b2e040b3e4299e342e5b8fd`。API作成2025/2/11・更新4/8（発表日とは別）。117 likes/36,718 downloads。関連smollm repo 3,926 stars、10/6 pushは当該checkpoint更新を意味しない。系列13件にSmolVLM3は未確認。|card Apache-2.0、言語English。F32 256,484,928 parameters、単一safetensors約1.026GB。公式例はCUDA/BF16/flash-attn、動画decord。導入高。1.38GB GPU RAMは著者の動画推論値で最小要件・CPU RAM保証ではない。CPU/ONNX実動作・日本語精度未確認。|**C今回**。モデルが小さくても今回のOCR/画像タスクを改善する具体的根拠が足りない。容量を正しく区別して保管するが、YOLO停止の代替実行にしない。将来、非SKUの公開画像QAに独立正解と明確な需要がある場合だけ再検討。|

一次情報：
[Playwright 1.64.1](https://github.com/microsoft/playwright/releases/tag/v1.64.1)、[1.64.0](https://github.com/microsoft/playwright/releases/tag/v1.64.0)、[版固定WebMCP仕様](https://github.com/microsoft/playwright/blob/v1.64.1/docs/src/api/class-webmcp.md)、
[SetFit 1.2.0](https://github.com/huggingface/setfit/releases/tag/v1.2.0)、[版固定exporter](https://github.com/huggingface/setfit/blob/v1.2.0/src/setfit/exporters/onnx.py)、[MiniLM base](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42)、
[SmolVLM2固定card](https://huggingface.co/HuggingFaceTB/SmolVLM2-256M-Video-Instruct/blob/067788b187b95ebe7b2e040b3e4299e342e5b8fd/README.md)、[固定files](https://huggingface.co/HuggingFaceTB/SmolVLM2-256M-Video-Instruct/tree/067788b187b95ebe7b2e040b3e4299e342e5b8fd)。

### 対象分野と既存比較

クローリングはCrawlee 3.18.2/4RC1の初期提案を10/13既報との重複で棄却。既存Impit/adaptiveと同じHTTP-first queueを新方式と呼ばない。Crawl4AI 0.9.5、Docling 2.139、LightOnOCR-3、RapidOCR、GLiNER2 2.1は直近報告を参照し、新しい効果仮説なしの重複実験をしない。

OCR/画像認識はSmolVLMの英語・画像理解と日本語OCRを区別。小型モデル/NLPはSetFitの独立分類を評価し、検索候補用の除外条件を適用しない。Webアーカイブ/検索はCommon Crawl・warcioの既報を再掲して新発見に数えず、今回の調査でarchive-free新サイト発見、coverage増分、検索前処理短縮を実証した候補は0。新indexの現況は今回再照会しておらず、10/15の観測を現在の最新と断定しない。

## 結果・数値・想定との差

オフラインでAPI metadataを読む自作監査を実行した。候補コードは実行していない。

|項目|結果|
|---|---|
|保存資料hash/bytes照合|20/20。資料payload合計180,418 bytes|
|SmolVLM系列|13 ID照合（author/query内）|
|ONNX部品サイズ計画|8形式×3部品を照合。実行入力の検証件数ではない|
|FP32 / FP16 主要3部品|1,028,830,109 / 514,811,692 bytes|
|INT8 / q4 / q4f16 主要3部品|260,186,962 / 264,221,217 / 189,174,979 bytes|
|全listed files / safetensors単体|4,058,527,341 / 1,025,998,224 bytes|
|モデル推論 / 性能PoC / repair|0 / 0 / 0|
|モデル・依存DL / 現金支出|0 bytes / 0円|

主要3部品だけで完全なrunnable packageとは断定しない。tokenizer/config/ランタイム依存が別途必要。全ONNX形式を同時DLする必要もない。メモリ・推論速度・実画質の改善は全て未測定。cardの動画benchmark値やGPU RAMを当方実測へ転記しない。

SetFit固定exporterの静的読解では、torch exportの`dynamo=False`選択、headの`FloatTensorType`化を確認。IR version不一致を既定でcoerceするコードも残り、export成功だけで意味的同値や全runtime互換を証明しない。これは実行エラーの観測ではなく採用時の確認事項。

Python3.12.14、監査時間は[audit.json](../../benchmarks/daily-tech-20261018/audit.json)の`wall_seconds`。資料サイズ検算の時間で、候補処理時間ではない。実験基盤を変更していないためlab全体テスト・CI競合修理を主目的にしない。20資料の取得合計約13.02秒（独立3バッチのwall合計）。Git fetchとHTTP header等を含む全wire bytesは未計測。2GBの実験枠に対する重み・依存取得は0。

## 問題・導入判断・次に試すこと

- 最初の`git status`は親workspaceがGitルートでなくexit128、正しいcheckoutへ移動して確認。`gh issue view --comments`は廃止Projects classicのGraphQLエラー、標準REST readでissue/commentsを確認。権限・資格情報・隔離設定変更やauto-review拒否は発生していない。候補実験の修復回数ではない。
- 委譲先のCrawlee Aを既報として棄却。SetFit A案も独立ラベル/具体的artifact未確定のためBへ補正。8例/クラスなど著者の一般値から日本語品質タグ分類の成功を予告しない。
- **全候補導入保留。** まずWebMCPを実装した公開対象の存在とread-only操作を確認し、main/child frameを分けた固定入力でDOM経路との出力差・時間を測る。toolの存在だけでアクセス制御を回避したり任意の呼出を許可したりしない。
- SetFitは独立した権利確認済み文章集合・ラベル・holdoutを用意できる場合、単純規則/TF-IDFとのmacro-F1・class recall・review率・CPU時間を設計。ONNX採用を試すなら同一学習済head/bodyの予測一致も先に確認する。現行SKUやPR23のデータを流用しない。
- 今回はAを作るために合成fixtureを実行せず、読み取りによる独立成果で終了。実用機能未完成なのでdraft PRなし。push後の最終remote SHAとそのSHAのCI状態を最終返信で報告する。CI成功でも候補推論成功を意味しない。
