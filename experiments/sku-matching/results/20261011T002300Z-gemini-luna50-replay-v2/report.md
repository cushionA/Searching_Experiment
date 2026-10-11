同じ50組・934 AU候補行を、Vertex AIのgroundingsearch / global / gemini-3.5-flash-liteで再実行した。既知ラベル48組に対するGeminiの正答率は28/48（58.33%）、Luna V13は44/48（91.67%）。Geminiのみ正解のケースは0で、Lunaのみ正解は16。未評価や保留も既知48件の分母へ含め、真偽未確定2件だけを分母から除いた。これはLunaが作成した人手未確認ラベルとの一致率であり、初見商品や原文を分離したholdoutではない。

| 同じ50組の結果 | Gemini | Luna V13 |
|---|---:|---:|
| 正答率（既知48組） | 28/48＝58.33% | 44/48＝91.67% |
| 採用 / 除外 / 保留 / 未評価 | 8 / 23 / 4 / 15 | 10 / 37 / 3 / 0 |
| 採用行の精度（既知ラベルのみ） | 6/7＝85.71% | 10/10＝100% |
| 正例14組の紐付け再現率 | 6/14＝42.86% | 10/14＝71.43% |
| 真偽未確定への採用 | 1 | 0 |

構造検証はmatching 61/70リクエスト、42/50組。失敗9chunk・8組はいずれもsupport/contradictionなのに根拠source_idsが空だった。全70応答はHTTP200/STOPでJSON解析できたが、解析成功を構造成功と数えない。寸法13/13は構造valid、実際の寸法確認passは8/13。意味レビュー50応答中35件が検証valid、15件はconfirm_gate_failed 14件・review_source_scope_mismatch 1件で最終未評価。matchingが無効の8組中2組は独立した有効レビューが全行の不一致を確認して除外、残る6組は未評価。raw評価と最終reviewed-summaryを区別する。

採用8件のうち、case-49c7d5a7b36f3f21641fはラベル不一致。楽天「レースカーテン=あり」に対し、現在幅100のAU内容表には遮光カーテン2枚・タッセル2枚・フックのみ。モデルは見出し「内容」だけを支持根拠にし、レースの同梱を誤認した。原文ZIPのmember SHA、完結した内容row、次のカラーsection境界を別監査でも確認した。case-aef841c600881fb29a99はtruth未確定で、サイズ・色・幅150のレース同梱は支持されるが、メーカー型番CT02135SG・原産国のAU裏付けが不足するため、誤り確定とはせず保留対象とする。採用8のrawリンクはderived/links.jsonlに保持し、採点後の現在ラベルと同じ行を指す6件をlinks-label-agreement.jsonlへ分けた。この6件を新しいモデル精度や人手確認済みと扱わない。

モデルに送るmatchingは楽天の選択値・属性、現在AU候補行と固定商品原文のみ。choices・長い元ID・provenanceは送らず、responseSchemaはstatus/source_idsの出力を維持した。元ID・引用原文はローカルで復元。Luna V13の実際に使われた入力を再現し、45旧V9入力＋4新V13入力＋1V9 fallback入力を70chunkで各行1回カバー。Lunaの回答は使っていない。reviewでは固定指示と原文を維持し、prediction・trace・寸法だけをGeminiの結果へ置換して全50件をGeminiで推論した。入力・contextはLunaとbyte一致、各caseの出力と全元SKUセルIDを検証した。

初回は厳密decoderの4番でHTTP400 INVALID_ARGUMENTとなり停止（初回成功3件と失敗1件は別runに保持）。この記録だけでは400の詳細原因は未確定。新しいv2出力先で、source ID enumと入力依存の配列min/maxだけをdecoderから外し、厳密な件数・scope・ID検証をローカルで継続した。プロンプトと選択値、温度・出力上限は同一で、Schema以外のbody不変を全70件で確認した。初回3応答はhash検証後に再利用し、再送しない。残67matching、13寸法、50reviewをそれぞれ一度実行。無効回答の自動再試行やLuna回答による救済はしない。

API応答のmodelVersionは全133 taskでgemini-3.5-flash-lite。今回v2の新規generateContentは130回、再利用したGemini成功3回と初回400を含むこの比較の全呼出は134回。usageは成功133応答を各一度集計してprompt 1,132,182 / candidates 98,599 / total 1,230,781 tokens。成功API時間の合計341.596秒、v2 wall 337.304秒（約5分37秒）。初回400は別に0.787秒、usage未報告。auth確認・準備・採点時間や初回から続行までの間隔を推論時間へ加えない。各caseのusage/modelVersion/時間はprovider-and-structure-audit.jsonに保存。

初回の環境設定spec25はreadyでも、runtime GCP_ACCESS_TOKENの実認証はtokeninfo400 invalid_token / Vertex401。ユーザー指定一時トークンを非表示stdinでプロセス内GCP_ACCESS_TOKENだけへ適用し、実際にtokeninfoとVertex読み取りが両方200になってから開始した。値・header・tokeninfo URL・identity・指紋は表示/保存せず、環境設定は変更していない。次回は再度有効な環境トークンで実認証を確認する。

推論前に継承guardと既存hash lineageを検証し、新しいprotocol、matching、寸法、レビュー、gradingのfreezeを段階ごとに生成・検証した。各応答はattempt ledger、provider response hash、parsed answer hash、provider textとの同一性を収集前と再開時に検証する。推論完了後の外部評価でGeminiの840 hashとV13の985 hash、50件の完全coverage、同じラベル1439件のhash不変を確認。raw labelsはcheckout外に留め、summary/case-results/comparison/source manifestのみ戻した。原文監査はlabelsを見ず別Luna agentが実施（指定モデル経路であり実modelVersionはツール未提供）。監査の段階集計取り違えはcoordinatorが最終reviewed-summaryと照合して訂正し、原文findingは維持した。

新規16テストは成功。要求されたfull lab suiteは615件、1 failure・17 errors・37 skipで、V13と同じ既存18 failure/error ID。full passではない。旧実験コード・過去原回答・索引・raw・ZIPは変更していない。新規ZIPなし。今回のGemini比較は50組のみで、Luna累計250組をGeminiで試験済みとは扱わない。

改善対象は、(1) 根拠IDなしの断定を出させない、(2) reviewerにcandidate/寸法/参照scopeの採用条件を守らせる、(3) 現在幅の主カーテンとレースを別構成品として確認する、(4) SKU軸だけでなく必須の商品属性を確認すること。今回の比較ではこれらのタスク文を採点後に調整せず、生出力と失敗を保持した。

| case | 最終判定 | modelVersion | prompt tokens | candidate tokens | API秒（再利用を一度含む） |
|---|---|---|---:|---:|---:|
| case-5b8778395d3d07c5e986 | 採用 | gemini-3.5-flash-lite | 26226 | 1312 | 5.993 |
| case-1bcb259507fd5fb96c90 | 除外 | gemini-3.5-flash-lite | 11668 | 1080 | 4.407 |
| case-f38a23425572f008c3be | 除外 | gemini-3.5-flash-lite | 13265 | 914 | 3.965 |
| case-04a68396ffb8ec49fae2 | 未評価 | gemini-3.5-flash-lite | 95663 | 17179 | 44.888 |
| case-74f7cb07e510ea6c8310 | 未評価 | gemini-3.5-flash-lite | 13394 | 540 | 3.349 |
| case-105af0d9f0b13877f3c1 | 未評価 | gemini-3.5-flash-lite | 126989 | 13834 | 36.451 |
| case-0758fe1da6412dd9e02c | 未評価 | gemini-3.5-flash-lite | 92593 | 15575 | 39.355 |
| case-aef841c600881fb29a99 | 採用 | gemini-3.5-flash-lite | 133220 | 11974 | 34.561 |
| case-8c12c6dc7a575b9ac8b3 | 保留 | gemini-3.5-flash-lite | 13171 | 717 | 3.447 |
| case-31f78ff882a8affd1e32 | 除外 | gemini-3.5-flash-lite | 14476 | 1648 | 5.991 |
| case-b9cbdbdfee2540b77b60 | 除外 | gemini-3.5-flash-lite | 14494 | 834 | 3.520 |
| case-d17c11f8557242ae3b83 | 未評価 | gemini-3.5-flash-lite | 20392 | 5038 | 13.155 |
| case-3f1ffbe1b649a9f90cad | 未評価 | gemini-3.5-flash-lite | 15112 | 846 | 3.539 |
| case-87d02eee08500f5962aa | 未評価 | gemini-3.5-flash-lite | 20690 | 2149 | 6.432 |
| case-57c1be06e4b0870bacbb | 除外 | gemini-3.5-flash-lite | 11254 | 443 | 2.718 |
| case-49c7d5a7b36f3f21641f | 採用 | gemini-3.5-flash-lite | 27076 | 2351 | 7.870 |
| case-6ec0791c56c4d01d49f5 | 未評価 | gemini-3.5-flash-lite | 25545 | 2151 | 6.451 |
| case-74502f3d28388318c4f8 | 未評価 | gemini-3.5-flash-lite | 12391 | 514 | 3.024 |
| case-b4338c025fd43dd6de53 | 除外 | gemini-3.5-flash-lite | 23562 | 859 | 4.570 |
| case-47fb05af18daaf361a87 | 採用 | gemini-3.5-flash-lite | 18771 | 1223 | 5.368 |
| case-954d3f0fe131e8c2a407 | 未評価 | gemini-3.5-flash-lite | 12345 | 496 | 2.997 |
| case-9939d696222c64d786b1 | 保留 | gemini-3.5-flash-lite | 12157 | 370 | 2.661 |
| case-80bf0eca24e8d22e477e | 保留 | gemini-3.5-flash-lite | 18662 | 1220 | 5.388 |
| case-615b639a7601d767385e | 未評価 | gemini-3.5-flash-lite | 12099 | 415 | 2.724 |
| case-5e021b49fbcbeda28660 | 除外 | gemini-3.5-flash-lite | 23688 | 836 | 4.708 |
| case-6d67154fb6ee81e7146e | 除外 | gemini-3.5-flash-lite | 13155 | 659 | 3.201 |
| case-f7e96ddd7d4715be75df | 未評価 | gemini-3.5-flash-lite | 13024 | 736 | 3.622 |
| case-4ca24ed09d0905f525a3 | 採用 | gemini-3.5-flash-lite | 18425 | 605 | 4.094 |
| case-d4a04f75e21afd932939 | 除外 | gemini-3.5-flash-lite | 13119 | 629 | 3.290 |
| case-9d44724dbbedb8420491 | 除外 | gemini-3.5-flash-lite | 10797 | 435 | 2.565 |
| case-7040443508479b4b8690 | 保留 | gemini-3.5-flash-lite | 18265 | 1273 | 5.268 |
| case-c7668986ffcc5ef6cedc | 採用 | gemini-3.5-flash-lite | 14536 | 574 | 4.191 |
| case-103636fa17ca1d28173e | 除外 | gemini-3.5-flash-lite | 10788 | 402 | 2.961 |
| case-9806fd9a636596880024 | 除外 | gemini-3.5-flash-lite | 12148 | 366 | 2.701 |
| case-fcc65b2f201891cde7eb | 除外 | gemini-3.5-flash-lite | 11941 | 357 | 2.673 |
| case-80fe48e8611abceef173 | 除外 | gemini-3.5-flash-lite | 18237 | 490 | 3.719 |
| case-503f2e63c8f6fde939ce | 採用 | gemini-3.5-flash-lite | 23769 | 441 | 3.785 |
| case-84b843f8c1ceb0131a51 | 採用 | gemini-3.5-flash-lite | 14540 | 532 | 3.760 |
| case-3ce2d8e67d5a70c768f3 | 未評価 | gemini-3.5-flash-lite | 10950 | 837 | 3.572 |
| case-dc71ab6dd1a2c284318e | 除外 | gemini-3.5-flash-lite | 8869 | 446 | 3.352 |
| case-30a21e40951444313016 | 除外 | gemini-3.5-flash-lite | 12141 | 541 | 3.420 |
| case-1f9603ecaf6e0c1aa9c1 | 除外 | gemini-3.5-flash-lite | 10809 | 424 | 3.383 |
| case-14a7742be34785231359 | 除外 | gemini-3.5-flash-lite | 10686 | 801 | 3.775 |
| case-111d0102cf7418c9b4d9 | 除外 | gemini-3.5-flash-lite | 10697 | 451 | 2.862 |
| case-f50e7e9ac8ebe7c49f4f | 未評価 | gemini-3.5-flash-lite | 11244 | 480 | 2.836 |
| case-da0cc9b2bc5c586a70d6 | 除外 | gemini-3.5-flash-lite | 10670 | 915 | 3.724 |
| case-e7f3ea534fed639ff188 | 未評価 | gemini-3.5-flash-lite | 11317 | 474 | 3.213 |
| case-ecacaf7cd2fc847ddea0 | 除外 | gemini-3.5-flash-lite | 10766 | 511 | 2.759 |
| case-18bf5f869978387d182f | 除外 | gemini-3.5-flash-lite | 11233 | 459 | 2.909 |
| case-a8388baa66ea1a5150f3 | 除外 | gemini-3.5-flash-lite | 11153 | 243 | 2.433 |
