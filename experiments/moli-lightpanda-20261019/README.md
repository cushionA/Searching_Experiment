# Moli / Lightpanda ローカル比較準備（未測定）

状態: 初回namespace preflight失敗後、許可されたDocker修復1回のprobeは成功。現在はMoliのlibfontconfig.so.1不足で停止。fixture生成と隔離疎通コードのみ。CDP adapter・測定runnerは未実装なので、このディレクトリだけで性能比較は完走しません。

## 固定仕様

- 同じ127.0.0.1 HTTP fixture、UTF-8、キャッシュ無効。サーバーはfixtures配下だけを公開し、リポジトリ全体を公開しない。
- DOM共通条件: Moli既定 / Lightpanda既定。Moli `--layout` は別列。すべて並列度1、同じCPU 2コア、実験全体RAM上限8 GiB、外部ネットワークなし。条件を交互に実行する。
- 各case×engine×cold/warmを5回、DOM 6 caseなら180試行。geometryは別の機能検証で速度表へ混ぜない。
- cold: 新規process起動から抽出照合・context/process終了まで。OSファイルキャッシュは消さずprocess-coldと明記。
- warm: 同一processで1回の捨て試行後、毎回新規context。接続/context生成から照合・切断まで。起動時間を別に記録。contextごとの独立CDP接続を両方式で使う。
- 1試行の全体期限10秒、期待値を50ms周期で照合。固定sleepやnetworkidleだけで成功認定しない。クリックはDOMのHTMLElement.click()を両者に使用し、座標クリックと区別する。
- static/js/wait/click/fetch: article内h1.textContent、p.textContent、a.getAttribute('href')配列をexpected.jsonに完全照合。待機中のBefore状態は不合格。iframeは同一origin child document内の同じ抽出。
- geometry: getBoundingClientRectのx/y/width/heightを±1pxで照合。既定mock geometryの不一致はDOM抽出失敗とは分ける。画像/PDF比較は範囲外。
- 成功数/全試行数とtimeout/exception/mismatch/unsupportedを保存。速度は成功試行のみ、median、Q1/Q3、min/maxとnを併記。両者成功caseの共通集合でも比較し、生存者偏りを示す。
- /procを25msごとに観測しPID+starttimeでbrowser全子孫を追跡。RSS合計とsmaps_rollupのPSS合計を別記。driver/serverは除外し、別途補助計測。起動を含むpeak、warm定常値、sample数を保存。欠測はnull。RSS共有ページの重複、短時間ピークの見逃しを注記。
- 成功出力、生ログ、開始/終了UTC、単調時計時間、PID一覧、全エラー、パッケージ版、バイナリ実測hash、OS/CPU/cgroup制限、順序を記録。期待値を観測後に変更しない。

## 再開条件と手順

同じ選択環境に、権限緩和や秘密の公開なしで利用可能な隔離実行経路が提供されることが必要。別環境へ迂回しない。この環境では下記probeは失敗済みなので、条件が変わらないまま反復しない。

```sh
python3 -B experiments/moli-lightpanda-20261019/prepare.py
# 隔離条件の変更後のみ、上書きしない新規出力へ疎通確認:
python3 -B experiments/moli-lightpanda-20261019/preflight.py /tmp/moli-lightpanda-new-preflight.json
```

probe成功は完全なsandbox検証ではない。実作業ディレクトリへのread/executeとUID、秘密・個人ファイルの不可視、外部通信禁止、RAM上限を別途確認し、失敗時は停止する。ダウンロードは公式versions.jsonのURLのみ、取得後hash一致と安全なarchive展開を確認し、ダウンロード権限を持たないクリーン環境へ公開バイナリと自作fixtureだけを渡す。installerは実行しない。

その後に固定版CDP互換性を確認し、runnerを実装して上記180試行を実行する。fixture HTTPサーバーは隔離内部で起動する。公開サイト補足は未計画・未実行であり、利用規約/robots確認後、ローカル成績と別表にする。
