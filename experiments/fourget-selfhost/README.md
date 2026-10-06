# 4getセルフホスト

このCloud環境のDockerで4getと4playを起動し、検索APIを1回ずつ検証する。
公式イメージは取得したdigestに固定。自前サーバーの画像認証を無効にし、
各scraperの外向き通信は環境のHTTPプロキシを通す。環境のCA bundleを
読み取り専用でマウントし、TLS証明書の検証を維持する。
公式イメージのNSS版curlはこの環境のPEM CA bundleを読み込めなかったため、
`LD_PRELOAD` を空にしてAlpine標準のlibcurlを使う。

```bash
python3 -B experiments/fourget-selfhost/start.py
python3 -B experiments/fourget-selfhost/smoke.py --query 'Python documentation' --scraper ddg
node experiments/bot-diagnostics/fourplay-smoke.mjs .lab-output/fourplay-fixture-new
```

4getのURLは `http://127.0.0.1:8084/`。APIは `/api/v1/web?s=検索語&scraper=ddg`。
4playのHTTP bridgeは `http://127.0.0.1:3004/`、起動確認は `/health`。
Firefox拡張のWebSocket 3030番と4getから呼ぶHTTP 3000番はコンテナ内部用。
4playは専用FirefoxプロファイルとXvfbで起動し、AMOの拡張1.10をSHA256で検証する。
共有パスワードは起動時に生成して `.runtime/fourplay-password.txt` に保存する。
プロキシ・CA・認証・拡張設定は `start.py` が用意する。
各検索の生応答・条件・集計を新しい `lab-runs/search-fourget-selfhost-*` に保存する。
既存の実験データは上書きしない。起動成功と上流からの検索結果取得は別々に確認する。

停止・再起動はこのディレクトリで実行する。

```bash
docker --host=unix:///var/run/docker.sock compose down
python3 -B start.py
```

CloudのDocker・ネットワーク利用許可が必要。新しいセッションでは
`start.py` を再実行し、プロキシアドレスとCA bundleを更新する。
外部公開先や永続ホストへのデプロイはこの設定には含めていない。

2026-10-06 UTCの4get初回実測では、`Python documentation` でDuckDuckGo 10件、
Brave 20件を取得し、HTTP 200かつAPI `status=ok` を確認した。このrunは
4play導入前で、Google scraperは未構成だった。後続の4get Google試行は
HTTP 200を返したものの、rendererが設定済みfailure pageへ到達し、結果を取得できなかった。
この失敗は、Googleを直接開いた4playのブラウザ実測とは別の経路・結果である。
4playの直接Google runはHTTP 200と結果DOMを観測したが、公式Python docsのcitationが
opaque Google `/goto` URLにしか結び付かず、遷移先を解決できなかったため検索成功は未確認。
Braveの直接4play runでは `docs.python.org` の解決済みリンク3件を確認した。
4playのfixtureでFirefox接続、既存ToolAdapterのページ移動・`followLink`、JavaScript実行、
応答本文とDOMのEvidence保存も確認した。
4get初回の条件・結果は[結果表](../../docs/search-services/results/20261006-fourget-selfhost/results.md)、
4play統合と後続runは[4play結果表](../../docs/search-services/results/20261006-fourplay-integration/results.md)に保存。

ブラウザ実験では `tools: ["4play"]` を指定して既存frameworkから使える。
旧CLIは `BOT_DIAGNOSTICS_CLIENTS=4play` を受け付ける。
`ToolAdapter.goto` / `followLink` とEvidence保存に対応する。selector操作、challenge操作、
response headers、Playwright events、CDP、screenshots、referrer継承、budgeted navigation、
grounding経路は未対応。導入・実測結果・観測上限は[4play統合ガイド](../../docs/bot-diagnostics-fourplay.md)を参照。

公式資料: [Docker](https://git.lolcat.ca/lolcat/4get/src/branch/master/docs/docker.md)、
[プロキシ設定](https://git.lolcat.ca/lolcat/4get/src/branch/master/docs/configure.md)、
[API仕様](https://4get.ca/api.txt)。
