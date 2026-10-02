# LightPanda・Playwright・Patchright比較

既存の実験・取得経路とは独立したローカルHTTP fixtureによる比較。速度、プロセス群のメモリ使用量、HTTP通信量を同時に測る。Python 3.12以上・Linux・Node.js/npmが必要。npmは入口のみで、JavaScript依存の導入は不要。

## 導入と実行

ネットワークを許可した環境で:

```bash
npm run benchmark:setup
# 画面のないLinuxではOSのxvfbパッケージも導入する
# 例: sudo apt-get install xvfb
npm run benchmark:quick
npm run benchmark:browsers
```

導入スクリプトは専用venv・ブラウザを`.deps/`に配置する。Chromiumの共有ライブラリが足りない環境では、OS依存を`PLAYWRIGHT_BROWSERS_PATH="$PWD/.deps/benchmark-browsers" .deps/browser-benchmark-venv/bin/python -m playwright install --with-deps chromium`で導入する。既存のLightPandaは`LIGHTPANDA_BIN=/absolute/path/to/lightpanda`で指定できる。未導入時の既定取得先は公式nightly。固定リリースを使う場合は`LIGHTPANDA_DOWNLOAD_URL`と`LIGHTPANDA_SHA256`を導入時に設定する。実測結果にはPythonパッケージのバージョン、ブラウザのバージョン、LightPandaバイナリのSHA-256を保存する。

```bash
# 3エンジンを同時に走らせる。ヘッドフルではChromiumの2エンジンを並走
npm run benchmark:parallel

# 条件・試行数を絞る
npm run benchmark:browsers -- --scenarios static,fetch --modes warm --concurrency 1,4 --pages 10 --iterations 3

# ヘッドレスのみ / ヘッドフルのみ
npm run benchmark:quick -- --display-modes headless
npm run benchmark:quick -- --engines playwright,patchright --display-modes headful

# 配備済みChromiumで動作確認。両ラッパーへ同じ実行ファイルを指定することも可能
npm run benchmark:quick -- --engines playwright --chromium /usr/bin/chromium --display-modes headless

# fixture・計測・エラー処理の検証（ブラウザ依存不要）
npm run test:benchmark
```

`BROWSER_BENCHMARK_PYTHON`で使用するPythonの実行ファイルを指定できる。既定は専用venvがあればそれを、なければ`python3`を使う。依存・実行環境が足りなければ理由を表示して終了する。欠けたエンジンを成功扱いで省略しない。

## 比較条件

| 条件 | 既定値・内容 |
| --- | --- |
| エンジン | LightPanda（Playwright経由のCDP）、Playwright Chromium、Patchright Chromium |
| 画面モード | headless / headful。LightPandaはheadless専用で、headfulは未対応としてメタデータに記録 |
| static | HTMLに500件の項目を埋め込む |
| dom | JavaScriptで同じ500件を生成する |
| fetch | 40msの応答遅延があるJSON APIを取得して500件を生成する |
| assets | 同じHTMLに外部JS、約32KiBのCSS、約64KiBのSVG画像を追加する |
| warm | 1ブラウザを再利用し、ページごとに新しいcontextを作る |
| cold | ページごとにブラウザを起動・終了する。起動・終了も処理時間に含む |
| 並列数 | 1 / 4。coldでは同時に起動するブラウザ数、warmでは同時に処理するcontext数 |
| 試行 | 各条件6ページ × 3回、各回の計測前に1ページをウォームアップ |
| 実行順 | 既定はエンジン間を順次実行。seedによる順序変更と試行ごとの順序巡回。`--schedule parallel`でエンジン間も同時実行 |

どのエンジンも同じURL群と抽出処理を実行し、タイトル、完了マーカー、件数、数値合計、先頭・末尾の文字列を照合する。タイムアウトや抽出不一致は失敗として残し、終了コード1を返す。全ページ成功した試行だけを遅延・処理時間・スループットの集約へ含める。

LightPandaには画像やCSSの描画機能がない。assetsは**同じDOM抽出要求を満たすまで**の比較で、描画品質が同じことを意味しない。リソース種別ごとのHTTP要求数も保存するため、省略による通信量の違いを確認できる。実サイトのブロック回避やサイト品質は測らない。

headfulは`DISPLAY`があれば利用し、なければワーカーごとにXvfbを起動する。Xvfbは仮想画面上のヘッドフル動作であり、実モニターの描画性能の検証ではない。Chromiumは隔離された実験環境向けにsandboxを無効にして起動する。

## 出力と計測範囲

既定の保存先は`.lab-output/browser-benchmark-<UTC日時>-<乱数>/`。`--output`で新しいディレクトリを指定できる。既存ディレクトリは上書きしない。

- `results.json`: 条件、環境、未対応条件、全ページの検証結果・遅延、試行ごとのメモリ・通信量、集約値。
- `summary.csv`: 条件ごとの遅延中央値・p95、処理時間、ページ/秒、起動時間、ピークRSS、送受信バイト、HTTP要求数。
- `logs/*.log`: 各試行のブラウザ・CDP・起動失敗のログ。

**速度:** driver起動とブラウザ起動を個別に記録。ページ遅延はcontext作成から抽出確認・context終了まで。coldはブラウザ起動・終了も含む。待ち行列の待機はページ遅延から除き、全体の処理時間へ含める。ウォームアップは速度・本計測の通信量から除外する。並走モードではCPU・RAM・fixtureサーバーを共有するため、順次モードの結果と区別する。

**メモリ:** 既定25ms間隔で、独立したワーカーと子孫プロセスのLinux RSSを合算する。別プロセスグループへ分離したChromiumも親子関係で追跡し、一度観測した子孫は親が変わっても追跡する。PIDと起動時刻を照合してPID再利用を区別する。Python、Playwright/Patchrightのdriver、ブラウザと子プロセス、独自起動したXvfbを含み、制御プロセスとHTTPサーバーを除外する。既存の共用`DISPLAY`は合算しない。計測フェーズのピークと、起動・ウォームアップ・終了を含む全体ピークを別に保存する。共有ページが重複計上されることと、サンプル間の短いピークを逃すことがある。計測フェーズがサンプル間隔より短く未観測の場合はnullで表す。OS全体の使用量・PSSではない。

**通信量:** fixtureサーバーで受けたHTTP要求と送ったHTTP応答のバイト数。HTTPヘッダーを含み、本文バイトも別に記録する。圧縮なし・キャッシュなし・HTTP/1.1・各要求後に接続を終了する。同じサーバー上でworker tokenごとに分離し、API・JS・CSS・画像を合算する。CDP通信、ブラウザのバックグラウンド通信、TCP/IP・TLSのオーバーヘッドは含まない。OSのネットワークインターフェース総通信量ではない。

## 検証状況（2026-10-02）

- 計測・fixture・不一致/タイムアウト・別プロセスグループへ分離した子プロセスのRSS・ヘッドフル起動オプション・並走時の条件分岐を検証する16テストが成功。既存の`test_lab*.py`42テストも成功。
- 配備済みPlaywright 1.62.0 / Chromium 151.0.7922.173のheadlessで、4シナリオ × warm/cold × 並列数1/4の16条件、各4ページ、計64ページが成功。通信量処理の最終変更後もquick相当の24ページが成功。
- 外部通信が無効の環境のため、未配備のLightPanda・Patchright・Xvfbは導入できず、それらの実測は未実施。導入時の固定バージョン1.63.0の実測も未実施。モックによる分岐検証は実測と区別する。
