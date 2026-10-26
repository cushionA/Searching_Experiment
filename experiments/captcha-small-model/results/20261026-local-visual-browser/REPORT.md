# ローカルモデルとブラウザの接続確認

2026-10-26。`run_local_visual_browser.mjs` を追加し、URL指定で画像取得、CLIP/OWLv2によるローカル判定、タイル選択、送信、チェックマークの確認まで接続した。[実行手順](../../LOCAL_VISUAL.md)を参照。既存のブラウザ起動処理を使い、検索サービスの対象設定・自動recovery経路と過去の評価記録は変更していない。

## 公式デモでの1セッション

[Google reCAPTCHA v2公式デモ](https://www.google.com/recaptcha/api2/demo)へ接続し、HTTP 200と画像問題を確認した。CLIP H/14をCPUで使い、動的な消火栓の画像問題で4回判定、6タイルのクリック・差し替え確認、送信1回まで実行した。その後、通過チェックの確認前に全体の120秒の期限で停止した。**通過成功とは数えない。**

[結果](official-demo-summary.json)、[実行条件](official-demo-protocol.json)、[操作ログ](official-demo-events.jsonl)を保存した。判定は1回約20～28秒で、動的な画像の再判定に時間を使った。初回にはモデル読み込みを含むため、モデル単体の推論速度のベンチマークではない。最大送信回数は2回に設定したが、実際の送信は1回。上限後の新規セッション・再試行は行っていない。

画像と生予測は元のrun、および[ローカルZIP](/workspace/Searching_Experiment/.lab-output/local-visual-browser-official-demo-20261026.zip)に保存した。[SHA受領記録](official-demo-evidence-receipt.json)で照合できる。ZIPはこのworkspace内の書き出しで、外部への永続保存はしていない。ブラウザprofile・cookie・HTTPヘッダー・query付きの通信ログはZIPに含めない。[通信記録](official-demo-network-redacted.jsonl)はURLのqueryを除いている。

## ローカルfixtureと実モデル

実Chromiumでローカルfixtureを開き、実際のOWLv2による画像取得→ローカル推論→空選択の送信→チェックマーク確認が完了した。[結果](summary.json)、[実行条件](protocol.json)、[モデル入力](0001-board.png)、[最終画面](final.png)、[生予測](0001-prediction.json.gz)を保存した。fixtureは自作の色と数字の画像で、対象物を含まない16タイル問題。外部サービスの通過率や画像認識精度を示すものではない。

クリック位置や動的更新には固定予測を使う別のブラウザテストを設けた。11件すべてが通過した。静的9/16タイル、動的差し替え、差し替え待ちの失敗、画像問題なし、widgetなし、不正index、同じ誤答の反復停止、サービス拒否、回数上限、推論中の画像変更、停止した推論の期限処理を確認している。起動時の極端に短い期限についても[停止結果](startup-deadline-summary.json)を確認した。

Pythonの回帰テストは178件中163成功・15 skip。[実行ログ](lab-tests.log)を保存した。ブラウザテスト11件は、このPython suite内のwrapperからも実行した。workerのJSONL protocol、複数要求でのモデル再利用、root外画像の拒否、不正予測と初期化失敗もテストしている。

## 再実行

ローカルモデル・Node/Playwright・Chromiumは[実行手順](../../LOCAL_VISUAL.md)に従って準備する。fixtureでの実モデル接続確認は次のコマンドで再実行できる。新しい出力先を使う。

```bash
node experiments/captcha-small-model/results/20261026-local-visual-browser/smoke.mjs \
  --model-root .lab-output/local-visual-models-large \
  --output-dir .lab-output/local-visual-browser-smoke-repeat
```

ブラウザの通信とモデル推論は別プロセスで、推論側は `local_files_only` に加えPythonのDNS・ソケット接続も禁止している。ブラウザは既存proxyとTLS検証を使う。検知回避の注入、proxy切替、ログインやアカウント操作は追加していない。
