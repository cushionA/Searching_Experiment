# 実行・再集計メモ

検索語「早稲田大学 教員」、50サービス、Patchright、headful Chromium 151.0.7922.173。実測日時は2026-10-02 UTC / 2026-10-03 JST。APIキー・アカウント・有料呼出しは使用していない。

ユーザーの「今は関係ないから全部無視でいいぞグラウンディングサーチの時だけ使う」を受け、旧25要求/8MiB/応答2MiB・robots gate・同一origin/GET/resource block・人工delayを通常検索検証から外した。AGENTS.mdとbot-blocking-scenarios/SKILL.mdも適用範囲を明示。bot diagnosticsの既定はbrowser_observation、groundingだけ明示モード。画像・CDN・iframe・POST・service workerを通常どおり許可した。BrowserContextのHTTP request/response/requestfailedを記録し、WebSocket handshake/framesは計測対象外。

検索ルート40対象のうち未完了38対象を再検証。Wibyの0件表示とYouCare閉鎖告知は前段階の証拠を採用。残る10対象はAPI・ディレクトリ・セルフホスト・画像/学術検索のルート未設定で、公開Webクエリ未実測。検索語は1種類のまま。基本観測時間は遷移後6秒で、遅い非同期回答は未確認の可能性がある。

全取得は共通Evidence/ToolAdapterを通し、general/meta/ai-api/nicheの既存ledger/resultsへ追記。旧445要求は変更せず、通常観測2541要求を追加、合計2986要求・331結果。通常モードの失敗に予約2MiBは追加しない。保存本文会計は実転送量ではない。各recordのpolicyとpolicy-history.jsonのユーザー許可hash列で適用方式を区別する。

headful DISPLAY :91は保存済みdisplay-xorg.confとXorg dummy driverで実行、ヘッドレスfallbackなし。環境proxyとTLS検証を維持。初回のCA読込失敗は履歴を残し、既存の配布CAをChromiumが使えるよう実行時だけ/home/agent/.pkiへの書込権限を付与した。Cookie・NSS DB・秘密鍵は成果物に含めない。

検索応答確認18サービス（3件は明示0件表示）、最新queryのrobots停止0件。Braveは429 Verifyを1回クリック後に200検索結果を確認、Web結果10件のうち公式9件。短いpollのpostcondition未成立はそのまま保存し、最終HTTP/DOM/画面で通過を確認。Yandexはチェックボックス後の6シンボル画像課題へ1回送信したが、checkcaptcha POSTが環境proxy/upstream HTTP503。回答の正誤と検索結果は不明。

5検索ルートを保存済みフォーム・iframe・metadataから修正、同じ検索語で再観測。Baiduは通常Web結果カードmu/h3から6件、GoogleはSERP metadataから部分復元。他の最大10候補にはsitelink/混合moduleが入り得るので、順位や検索精度の比較値ではない。結果の遷移先は訪問していない。

別検証はexternal-observations.jsonとexternal_*列に保持。Brave200/10件/公式9件は今回も一致したが、別検証の実クエリ・手順は未提供。Yandex200/10件は外部報告のまま。公開APIトップ200は検索成功ではない。

旧初回395要求と6対象robots継続50要求は履歴として保持、詳細はrobots-retry-report.md。旧予約bytes・robots停止・CDN blockは今回の条件ではない。過去3つのZIPを保持し、今回は別名ZIPにする。

既存Python基盤61件、Node runner/framework/normal-policy、通常ネットワークのローカルheadful fixtureを検証。新fixtureでcross-origin image/iframe、POST、service workerを許可、robots未取得を確認。4実サイト台帳と2fixtureのhash・会計・policy履歴をverify。旧ZIPのledger/results/configのprefix不変、38再検証完了、全setup headless=false、旧制限block0も確認。

最新レポートはreport.mdとsearch-services-summary.json/CSV、集約検証はaggregate-verification.json。コード・state・body・画面を含めてexportする。復帰時もpolicy履歴と既存台帳を維持し、通常ブラウザにはgrounding予算を適用しない。Cookie/sessionは作り直す。

```bash
python3 -B experiments/bot-diagnostics/search-summary.py
python3 -B lab-runs/search-services-20261003/finalize-observation.py
python3 -B experiments/bot-diagnostics/export.py --run lab-runs/search-services-20261003 --output .lab-output/search-services-20261003-browser-observation.zip
```

既存ZIPはexportで上書きできない。再exportでは別のfilenameを使う。
