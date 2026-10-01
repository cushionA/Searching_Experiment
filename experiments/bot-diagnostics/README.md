# Bot検知と初回応答の診断

Rebrowser Bot DetectorとFingerprintJS BotD 2.0.0を固定ソースのローカルページで実行し、指定4サイトのトップページ応答を比較する任意のNode.js実験。既存の発見型クロールや過去runへ接続・変更しない。

## 導入

Node.js 22以上、Python 3.12以上、Chromium、環境のHTTPS_PROXYとCA信頼が必要。

```bash
python3 -B experiments/bot-diagnostics/setup.py
node experiments/bot-diagnostics/runner.test.mjs
python3 -B -m unittest discover -s tests -p 'test_lab*.py'
```

npm依存はpackage-lock.jsonを使用し、.deps/bot-diagnosticsへ配置する。Rebrowserソースはコミット固定。Lightpandaはversions.jsonのSHA256と一致するビルドだけを使う。nightlyの配布が更新されていた場合は停止する。検証済みバイナリを.deps/bot-diagnostics/lightpandaへ別途配置するか、新しいバージョンの別実験として計画する。

ブラウザ用CAを環境が配布している必要がある。ERR_CERT_AUTHORITY_INVALIDはサイト側のブロックとして扱わない。TLS検証を無効化して続行する設定は実装していない。恒久的なユーザー/システムのCA追加をsetupから行わない。

## 実行

```bash
node experiments/bot-diagnostics/runner.mjs all lab-runs/bot-diagnostics-new
node experiments/bot-diagnostics/runner.mjs verify lab-runs/bot-diagnostics-new
```

既存runを上書きしない。detectorsまたはsitesをallの代わりに指定して対象を分けられる。依存の場所はBOT_DIAGNOSTICS_DEPS、Chromiumの実行ファイルはBOT_DIAGNOSTICS_CHROMIUMで指定できる。

失敗を修復して同じ台帳・残予算内で再試行する場合はresume-sitesを使う。過去の失敗、確保済みbytes、robots取得も保持する。方式とサイトの選択は既存configの要素に限定する。

```bash
BOT_DIAGNOSTICS_CLIENTS=patchright BOT_DIAGNOSTICS_TARGETS=joshin \
  node experiments/bot-diagnostics/runner.mjs resume-sites lab-runs/bot-diagnostics-new
```

runの横に.lockを作り、同じrunの二重実行を止める。実行中のプロセスを強制終了した場合、PIDを確認してからロックを処理する。未確定リクエストは上限いっぱいのbytesを計上したまま扱い、予算を返却しない。

ブラウザは各HTTPリクエストとリダイレクトを捕捉し、robots、同一origin、GET、回数・保存body上限を検査する。ChromiumはCDP、LightpandaはPuppeteerのrequest interceptionを使う。公開デモには接続せず、外部originの検知器依存も実行しない。

## 今回の結果の再集計

```bash
node experiments/bot-diagnostics/summarize.mjs
```

このコマンドは今回の固定runパスからsummaryとdocsのレポートを再生成し、原履歴は保持する。一般の新しいrunを自動選択するコマンドではない。

raw HTML、DOM、スクリーンショット、BotDのgetComponents/getDetections、Rebrowserの原rating、設定、台帳、環境とソースハッシュを保持する。HTTPクライアントのHTML取得をJS検知器の合格に換算しない。検知器の判定から実サイトのWAF原因を断定しない。
