# Bot検知・初回応答の比較（2026-10-01 JST）

今回の環境では、Amazonはwreq-js・impit・PatchrightにAWS WAFチャレンジを返し、Lightpandaには本文を返した。Indeedは全方式で403。ジョーシンはimpitが403、他の方式は本文を観測した。HOME’Sは全方式で本文を観測した。

## 実サイトの最終観測

| 方式 | Amazon.co.jp | ジョーシン | HOME’S | Indeed日本 |
|---|---|---|---|---|
| wreq-js | 202 チャレンジ | 200 本文 | 200 本文 | 403 チャレンジ |
| impit | 202 チャレンジ | 403 拒否 | 200 本文 | 403 チャレンジ |
| Playwright（対照） | 202 チャレンジ | 200 本文※ | 200 本文※ | 403 拒否 |
| Patchright | 202 チャレンジ | 200 本文※ | 200 本文※ | 403 拒否 |
| rebrowser-patches + Lightpanda | 200 本文 | 200 本文※ | 200 本文※ | 403 チャレンジ |

※ ブラウザの一部の追加リクエストは予算により止めた。本文観測は、ページ内の全機能や商品・物件・求人ページへのアクセス成功を意味しない。

対象URLは https://www.amazon.co.jp/ 、https://joshinweb.jp/ （/top.htmlへ遷移）、https://www.homes.co.jp/ 、https://jp.indeed.com/ 。ログイン、検索、商品・物件・求人の巡回、CAPTCHAへの入力は実施していない。環境修復の再試行を含む各履歴を保存しており、成功率の統計的比較ではない。

## ブロックの根拠

- Amazonの202応答は通常の本文ではなく、`token.awswaf.com/.../challenge.js` と `AwsWafIntegration` を含むAWS WAFトークンチャレンジ。原HTMLで確認した。
- IndeedのHTTPクライアントとLightpandaは `Security Check - Indeed.com` と `cf-mitigated: challenge` を返した。Chromiumの2方式は `Blocked - Indeed.com`、`Access Denied` を観測した。
- impitのジョーシン応答は403、タイトル `Access Denied`、本文 `You don't have permission to access joshinweb.jp on this server.` 。発生箇所と根本理由は未確定。
- HOME’Sは不動産サイトのタイトル・本文を取得した。このトップページの応答に明示的な拒否表示はなかった。

検知器が指摘した特徴と、実サイトが拒否した理由は別の観測である。サイト側のWAFルール・スコア・ログを取得していないため、「どの指紋が原因だったか」は確定できない。

Rebrowserの公開rating条件、BotDの公開detectorとgetComponents/getDetectionsによる判定は確認できる。BotDは一般的な数値のBotスコアを返す道具ではない。Akamai・Cloudflare・AWS WAF等の製品はヘッダーやチャレンジHTMLの手掛かりから推定できるが、製品の内部点数・重み・閾値は公開応答だけでは確定できない。サイト管理者側のログが必要で、同じIP・TLS条件の単変量比較でも分かるのは傾向までである。

## Rebrowser Bot Detector / FingerprintJS BotD v2

| 方式 | Rebrowser | BotD 2.0.0 |
|---|---|---|
| wreq-js / impit | JavaScript実行なしで対象外 | 対象外。HTML取得を検知器合格と扱わない |
| Playwright（対照） | 赤6項目 | bot=true、headless_chrome |
| Patchright | 赤3項目、一部トリガー未完了 | bot=true、headless_chrome |
| rebrowser-patches + Lightpanda | 表示処理で途中停止 | bot=false、ただし複数の特徴が未収集 |

対照の赤項目は sourceUrlLeak、mainWorldExecution、exposeFunctionLeak、navigatorWebdriver、viewport、useragent。Patchrightの赤項目は exposeFunctionLeak、viewport、useragent。Patchrightでは navigator.webdriver=false を取得した。一方、HeadlessChromeを含むuserAgent/appVersionからBotDの detectUserAgent / detectAppVersion が真になった。Patchrightの dummyFn トリガーは isolated contextからmain worldの関数を参照できず失敗した。mainWorldExecutionは呼び出し後もrating=0で、原判定を保持している。

exposeFunction等の推奨トリガーは検知器のページでのみ実行した。それらの赤判定と実サイトの通常ナビゲーションとの対応は未測定。Runtime.enableのリークは今回の対照でも赤になっておらず、現在のChromiumでの検出範囲には限界がある。

Lightpandaでは initTests は存在し、detections は1件まで作成されたが、tbody.insertRow が undefined。Rebrowserの renderDetections が使うDOM APIが不足し、検知器が完走しなかった。Rebrowser自身がChromium用として設計している点とも整合する。

LightpandaのBotDは browserKind/browserEngineKind=unknown。navigator.connection、navigator.productSub、window.external、navigator.mimeTypes、WebGLの収集に失敗した。bot=falseは検出器の対応範囲を含む結果であり、検知耐性や実サイトの通過性能の証明には使えない。

## 条件と限界

- 検知器は公式ソースを固定したlocalhostのHTTPページで実行し、BotDのmonitoringを無効化した。公開デモへの実測ではなく、公開デモのHTTPS originとの差もある。Rebrowserの元HTML・CSP・JSは変更していない。
- 全実サイト通信は環境のHTTPSプロキシを利用。TLS診断で取得したAmazon証明書はOpenAIの環境プロキシCAが発行しており、TLSが中継で終端されている。サイトが観測するJA3/JA4は測定していない。wreq-jsとimpitのTLS指紋の優劣は評価できない。
- Chromiumは151.0.7922.173、headless、各ライブラリの標準設定に識別用DiscoveryLab/0.1を追加。viewportは1280×720。UIのある通常Chromeや本番推奨設定とは条件が異なる。
- ブラウザは同一originのGETに限定し、画像・メディア・フォント・外部origin・WebSocketを止め、Chromiumではservice workerも止めた。Amazonの外部AWS WAFスクリプトもこの取得範囲の設定により未実行。観測は初回チャレンジ応答までである。
- robotsを方式ごとに確認。上限は各方式×サイトで25リクエスト、保存/計上body 8MiB、応答保存2MiB、最低間隔2秒。失敗と再試行も同じ台帳に計上。Chromiumでは受信してからbodyを切り詰めるため、通信転送量そのもののハード上限ではない。
- 初期のPlaywrightルートにはジョーシンの自動リダイレクト後の記録漏れがあり、2件をDOM証拠として事後補記した。以降はCDPで各リダイレクトを送信前に捕捉し、302と200を記録した。初期2件の元HTTP body/statusは未取得として残した。
- 証明書修復の初期試行はERR_CERT_AUTHORITY_INVALIDで停止した。恒久的なユーザー信頼ストアの変更は自動審査で拒否された。環境が既にNode/Pythonへ配布しているCAを検証中だけ1つのNSSストアで使う方法で測定し、終了後に一時証明書の残存がないことを確認した。TLS/hostname検証を無効化するフラグは使用していない。

## バージョンと証拠

wreq-js 3.2.0、impit（JavaScript版）0.14.5、patchright 1.63.0、playwright 1.63.0、rebrowser-puppeteer-core 24.8.1（公式のパッチ適用済み配布を操作層に使用）、@fingerprintjs/botd 2.0.0。Lightpanda 1.0.0-nightly.9929+e774f9bba。Lightpandaのエンジン自体へパッチを適用した構成ではない。

Rebrowserソース: e1a25b1ff264cc9a5b5ea7fe8a6dfc26e3b1c718。Lightpanda SHA256: 16ee4443e34d09c522d8c416c6096bcd5a3dffd56706b7a8e2d7d0b1172ecc62。

- 最終集計: [summary.json](../lab-runs/bot-diagnostics-004-sites/summary.json)
- 全実サイト履歴: [results.json](../lab-runs/bot-diagnostics-004-sites/results.json)、[ledger.json](../lab-runs/bot-diagnostics-004-sites/ledger.json)
- 検知器の項目別出力: [results.json](../lab-runs/bot-diagnostics-002-detectors/results.json)
- Lightpandaの特徴・互換性: [results.json](../lab-runs/bot-diagnostics-005-lightpanda-compatibility/results.json)
- 再現手順: [README](../experiments/bot-diagnostics/README.md)

原HTML・DOMは各runのblobs/SHA256へ保存し、スクリーンショットも保持した。summaryは現在の分類処理で原証拠を再分類した派生物で、旧分類を含む履歴は保持している。保存bodyのハッシュ、台帳の回数・bytes、予算上限、未確定取得の解消を検証した。Python検証44件、診断・フレームワークのNode検証19件が成功した。

## 再利用する共通フレームワーク

mainのOxiBrowser比較PR #6へリベースし、観測済みCSSセレクタと「API成功だけで操作成功と扱わない」という確認方法を取り込んだ。共通メインループへURL・セレクタ・役割を注入し、各ツールのnative機能で実行する。検索欄への入力、クリック・ホバー、簡単なボタンチャレンジ、トップ・待機・元URLへの1回再訪、操作速度と拡張の比較、Googleの最終段階をオプションとして実装した。

全5方式のローカルfixtureでCookieを持ち越す連続アクセスと302/200の計上を検証した。Patchright・対照Playwright・Lightpandaでfill/click/hoverのDOM効果、通常・操作速度変更の両条件で簡単なチャレンジの成功確認と1回の再訪が通った。操作が発生させるrobots禁止URLは3ブラウザすべてで送信前に止めた。HTTPクライアントのDOM操作・人間風入力、Lightpandaの拡張は未対応として保存する。

管理Chromium151は拡張の追加を管理ポリシーで拒否した。拡張の機能検証には専用Chrome for Testing153を使用し、Patchrightと対照Playwrightでnativeの拡張IDとDOMマーカーを確認した。初回実サイト応答の比較とはブラウザ条件が異なるため合算しない。同梱拡張は観測用であり、検知回避のパッチを加えない。

内部URL・Google・実サイトでの操作や突破は本レポートの実測には含めない。実サイトで簡単な突破ボタンが確認できていないためrecovery設定はnull。Google検索はrobotsにより止まる場合がある。ローカルでの機能確認を実サイトの突破率として扱わない。

- [共通インターフェイス・全オプションの手順](../experiments/bot-diagnostics/README.md)
- [シナリオを毎回作成するSkill](../.agents/skills/bot-blocking-scenarios/SKILL.md)
- [セレクタ・チャレンジ画面](bot-diagnostics-evidence.md)
- [オプションのfixture検証](../lab-runs/bot-diagnostics-006-options-fixture/checks.json)

公式資料: [Rebrowser Bot Detector](https://github.com/rebrowser/rebrowser-bot-detector)、[rebrowser-patches](https://github.com/rebrowser/rebrowser-patches)、[BotD v2](https://github.com/fingerprintjs/botd/tree/v2.0.0)、[Lightpanda](https://github.com/lightpanda-io/browser)、[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)、[wreq-js](https://github.com/sqdshguy/wreq-js)、[impit](https://github.com/apify/impit)。
