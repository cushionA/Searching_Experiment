# Joshin 3方式比較

プロキシなし・headfulの3方式を並行実行し、全方式でJoshinの内部2ページまで完走しました。Googleの検索検証は対象外です。

| 方式 | 初回トップ | 同じセッションで再訪 | お買い物方法 | JavaScript/Cookie案内 |
|---|---:|---:|---:|---:|
| Camoufox単体 | 200 | 不要 | 200 | 200 |
| Camoufox＋4play | 403 | 200 | 200 | 200 |
| 4play単体 | 403 | 200 | 200 | 200 |

Camoufox単体と併用版には同じ指紋・134フォントの設定を渡しました。単体はPlaywright 1.60.0で操作し、併用版は4play拡張で操作します。両者はCamoufox 152.0.4-beta.30を使い、4play単体は通常Firefox 157系を使います。起動時の設定、コンテナー、制御方法、ブラウザ版の差は残っています。

併用版と4play単体は、初回403と指定センサーのscript GET 200・POST 201を保存した後、同じセッションで拒否された最終URLを一度再訪しました。フォント設定を変えずに200へ変化したため、フォントだけを拒否の原因とは判断できません。セッション初期化後に判定が変わった可能性がありますが、Cookie単独の因果や製品全体の通過率・速度の優劣は未確定です。

初回の並行試行では併用版が証拠保存のEACCESで途中停止しました。そのrunを保持し、WSL側で保存する新runで3方式を再実行しました。以下は完走した新runの詳細です。

結果は保存済みrunのDOM、台帳、pipeline、verificationから集計しました。

| 方式 | 初回home result | ledger document status | 最終URL | home DOM候補 | Sensor GET 200 | Sensor POST 201 | 再訪result status | 再訪ledger document | pipeline | verify |
|---|---:|---|---|---:|---:|---:|---:|---|---|---|
| camoufox | 200 | 301@https://joshinweb.jp/, 200@https://joshinweb.jp/top.html | https://joshinweb.jp/top.html | True | True | True | 未観測 | 未観測 | navigation_completed | True |
| camoufox-fourplay | 403 | error@https://joshinweb.jp/, 403@https://joshinweb.jp/top.html | https://joshinweb.jp/top.html | False | True | True | 200 | 200@https://joshinweb.jp/top.html | navigation_completed | True |
| 4play | 403 | error@https://joshinweb.jp/, 403@https://joshinweb.jp/top.html | https://joshinweb.jp/top.html | False | True | True | 200 | 200@https://joshinweb.jp/top.html | navigation_completed | True |

## 対象ページ

各セルのDOM確認はHTTP状態、結果のoutcome/title、およびresults.jsonが指すDOM blobを使った簡易判定です。

| 方式 | URL末尾 | result status | ledger document status | title/outcome | 通常本文候補 |
|---|---|---:|---|---|---:|
| camoufox | tobuy.html | 200 | 200 | お買い物方法 | True |
| camoufox | javacookie.html | 200 | 200 | JavaScript、Cookieの設定方法 | True |
| camoufox-fourplay | tobuy.html | 200 | 200 | お買い物方法 | True |
| camoufox-fourplay | javacookie.html | 200 | 200 | JavaScript、Cookieの設定方法 | True |
| 4play | tobuy.html | 200 | 200 | お買い物方法 | True |
| 4play | javacookie.html | 200 | 200 | JavaScript、Cookieの設定方法 | True |

## Home DOMとsensor応答

| 方式 | home title | deny marker | sensor ledger順（method/status/role） |
|---|---|---:|---|
| camoufox | ジョーシン公式家電通販サイト｜Joshin webショップ【家電・日用品・お酒など何でも揃う】 | False | GET/200/homepage, POST/201/homepage, POST/201/homepage, GET/200/target, POST/201/target, POST/201/target, GET/200/target, POST/201/target, POST/201/target |
| camoufox-fourplay | Access Denied | True | GET/200/homepage, POST/201/homepage, POST/201/homepage, GET/200/session_initialization_revisit, POST/201/session_initialization_revisit, POST/201/session_initialization_revisit, GET/200/target, POST/201/target, POST/201/target, GET/200/target, POST/201/target, POST/201/target |
| 4play | Access Denied | True | GET/200/homepage, POST/201/homepage, POST/201/homepage, GET/200/session_initialization_revisit, POST/201/session_initialization_revisit, POST/201/session_initialization_revisit, GET/200/target, POST/201/target, POST/201/target, GET/200/target, POST/201/target, POST/201/target |

## 実行時制御とエラー

| 方式 | setup runtime | child exit | fixture verify | 実行エラー |
|---|---|---:|---:|---|
| camoufox | {"engine":"firefox","version":"152.0.4-beta.30","camoufox_js":"0.12.0","playwright_core":"1.60.0","executable":"/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps/browser/camoufox-bin","headless":false,"display":":0"} | 0 | True | なし |
| camoufox-fourplay | {"engine":"firefox","version":"152.0","camoufox_js":"0.12.0","fourplay":"@lawlers/4play","fourplay_upstream_commit":"unknown","browser_engine":"Camoufox","browser_control":"4play WebExtension","launch_backend":"web-ext + native Firefox process","executable_path":"/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps/browser/camoufox-bin","config_sha256":"4b62aea9b29d1c114d086f8d7b8aa0d3baadd347ea4dda006bd9b0db11816c6e","font_count":134,"font_list_sha256":"b62fe49b3c8b9fcf67f12e3ca5f266ffbff5d5ae082787fdb1c06cef2bd70603","playwright_control":false,"headless":false,"display":":0","observation_limits":["screenshots_unsupported","viewport_not_measured","frame_matching_incomplete","redirect_chain_not_reported","response_headers_not_provided_by_4play","websocket_frames_not_recorded"]} | 0 | True | なし |
| 4play | {"engine":"firefox","version":"157.0","fourplay":"@lawlers/4play","fourplay_upstream_commit":"72f27922a8cdcfaae24531db5db2cd6f17470a02","headless":false,"display":":0","observation_limits":["screenshots_unsupported","viewport_not_measured","frame_matching_incomplete","redirect_chain_not_reported","response_headers_not_provided_by_4play","websocket_frames_not_recorded"]} | 0 | True | なし |

## 実行条件と制約

matrix-runの3方式同時区間: {'overlap': True, 'overlap_seconds': 39.209}。

launch template blob SHA-256: {"4play": "b311fe92c4afce207ff17f6dfab21e26321c128e36a21bba76d13289dceb0f8b", "camoufox": "b311fe92c4afce207ff17f6dfab21e26321c128e36a21bba76d13289dceb0f8b", "camoufox-fourplay": "b311fe92c4afce207ff17f6dfab21e26321c128e36a21bba76d13289dceb0f8b"}

同じlaunch template SHAは同じファイルを記録したことを示します。4play単体にCamoufoxのfingerprint設定が適用されたことまでは示しません。
Cookie単独の因果、性能差、住宅ISP経由、未記録のSet-Cookieやresponse headersは結論していません。exit IP測定と住宅ISP確認はconditions.jsonの明示値に従います。
通常本文候補はDOM/title/outcomeに基づく機械的な印で、ページ内容の人手評価ではありません。sensorのstatus/methodはledgerのstatus/request_methodから集計しています。
