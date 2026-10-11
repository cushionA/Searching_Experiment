# Gemini SKU照合：誤答4件の思考LOW再試験

前回の既知ラベル上の誤答から選んだ4組・190候補行を、`gemini-3.5-flash-lite` の `thinkingLevel: LOW` で再試験した。ラベル上は **0/4 → 2/4**。採用1、除外1、未評価2、保留0。今回は思考OFF試験ではない。

Google公式仕様では3.5 Flash-Liteの既定値はMINIMALで、完全OFFは非対応。ユーザーに仕様を提示し、指定されたLOWへ増やして実行した。`thinkingBudget: 0` は3.5に送っていない。公式出典・短い引用は [thinking-specification.json](thinking-specification.json) に記録した。

## ケース比較

| ケース | 対象 | 前回（既定MINIMAL） | LOW | 既知ラベルとの一致 |
|---|---|---|---|---|
| 49c7 | ベージュ100×110、レースあり | 誤採用 | 未評価 | 未解決。レースがunknownとなり誤採用は止まったが、レビューは採用条件外のconfirmを返した |
| 04a683 | カフェブラウン100×120、レースあり | 未評価 | 採用 | 一致。`au:704502086:331810175:8:4`、幅100の内容表は主カーテン2＋レース2 |
| 74f7 | 電気毛布、シングル140×200、グレー | 未評価 | 未評価 | 未解決。AU固定ページはダブル毛布。根拠IDなし断定と不適切なconfirmが残る |
| 8c12 | シングル、アッシュグレー、毛布セット | 保留 | 除外 | 一致。AU固定ページはダブル敷きパッドでサイズ不一致 |

ラベルは従来のLuna作成・人手未確認。同じcheckout外の1439ラベルを使い、この4件だけを採点した。誤答選抜の開発用サブセットであり、50件全体の正答率や初見データの改善率ではない。各タスク1回、対照は過去の応答で、温度0でも再試行による変動を排除できない。今回だけでLOWの因果効果を確定しない。

## 入力・出力と検証

楽天の選択済み値・属性、固定AU商品の候補行・原文を維持。照合9chunkは前回のbodyと `thinkingConfig` 以外すべて同一。行を減らさず全190行をカバーした。照合と寸法の出力はstatus/source_idsのみ、元ID・根拠原文をローカルで復元した。レビューは固定プロンプト・原文を維持し、今回のprediction/trace/dimensionだけを差し替えた。過去のモデル回答の流用0、自動再試行0。

新規14リクエスト（照合9、寸法1、レビュー4）はすべてHTTP200 / STOP / JSON解析成功、modelVersionは全件 `gemini-3.5-flash-lite`。構造検証は照合8/9chunk・3/4組、寸法1/1 valid・1/1 pass、レビュー2/4 valid。失敗は毛布のassertion_without_evidence 1chunk、confirm_gate_failed 2review。JSON解析成功と構造・意味の成功を分けて集計した。採用条件に反するconfirmは最終未評価として保持し、除外や正答に数えていない。

LOW設定を実送信しHTTP200で受理されたことを、保存bodyとprovider receiptのhashで確認。全14応答でthoughtsTokenCountは未提供のため、実際の内部思考トークン量は未観測。理由文は意味レビューの出力であり、内部思考テキストではない。

原文独立レビューでは、採用カーテンの色・寸法・レース構成、除外敷きパッドのサイズ矛盾を確認。採用カーテンのメーカー型番CT01120BR・製造国中国・ブランド表記など、選択属性の一部はAU固定商品根拠が未確認。今回のラベル一致はSKU対応の評価であり、すべての商品属性の実証ではない。監査の行/枚数取り違えは実ファイルで訂正しcoordinator_correctionへ記録した。生モデル回答と最終予測は変更していない。

## Usage・時間

LOW全14件：prompt **189,909** / candidates **17,599** / total **207,508 tokens**。API時間合計 **51.644秒**、推論wall **52.612秒**。auth・準備・採点時間は含めない。

同4件の過去既定MINIMALは14件、prompt149,304 / candidates20,787 / total170,091、API合計59.554秒。両条件で候補結果が異なるため、後続レビューの動的入力量も異なる。これを純粋な同一入力速度比較と扱わない。

全ケースのmodelVersionは `gemini-3.5-flash-lite`。以下の時間は各ケースのAPI合計。

| ケース | 最終判定 | prompt | candidates | total | 秒 |
|---|---|---:|---:|---:|---:|
| `case-49c7d5a7b36f3f21641f` | not_evaluated | 27,873 | 2,732 | 30,605 | 7.852 |
| `case-04a68396ffb8ec49fae2` | adopt | 136,228 | 13,182 | 149,410 | 35.723 |
| `case-74f7cb07e510ea6c8310` | not_evaluated | 12,526 | 973 | 13,499 | 4.280 |
| `case-8c12c6dc7a575b9ac8b3` | exclude | 13,282 | 712 | 13,994 | 3.789 |

## 保存物と再現

- [プロンプト・入力・出力構造の一式](../../task-packages/20261011-gemini-sku-low/README.md)：今回14タスクと比較元133タスクの実request、プロンプト、入力JSON、Schema、実出力、ローカル復元用データ。
- [採点比較](grading/paired-comparison.json)、[採点集計](grading/summary.json)、[原文意味監査](semantic-independent-audit.json)、[構造・provider検証](provider-and-structure-audit.json)。原ラベルはcheckout外のまま。
- 新規driver `../../trial_gemini_thinking_off_subset.py`、外部採点 `../../grade_gemini_thinking_subset.py`。過去の凍結コード・runは変更しない。
- 推論前protocol、寸法前、レビュー前、採点前のfreeze、および全request/response/answer hashを検証。

認証はruntime環境値と最初の一時値がinvalid_token/HTTP401。次のユーザー指定一時値を非表示stdinからプロセス内GCP_ACCESS_TOKENへ適用し、tokeninfoとVertex読み取りの両HTTP200を確認して実行した。token値・認証header・tokeninfo URL・identityは表示/ファイル保存せず、Cloud設定は変更していない。現在の環境値の有効性や次回認証は保証しない。

検証：新規focused4＋既存replay16の計20件成功。必須lab suiteは619件、1 failure・17 errors・37 skippedで、前回と同じ既存18件の失敗ID。新たな失敗はない。ログはcheckout外、集計/hashをtests-final.jsonへ保存。今回の指定4ケースの予定推論は完了し、追加APIは実行しない。ZIPなし。
