# ローカルCLIP / OWLv2の取り込み確認

2026-10-26。[実行手順](../../LOCAL_VISUAL.md)の保存画像認識と既存ボード評価を追加した。外部AI APIとAIエージェントは不要。ブラウザ操作は未接続。

small（CLIP B/32）・large（CLIP H/14）の両構成とOWLv2をCPUで実行した。largeはDNS・ソケット接続を禁止した状態でも完了し、通信試行は0件だった。[検証結果](large-verification.json)、[実行条件](large-protocol.json)、[重みの固定revisionとSHA](large-model-manifest.json)を保存した。smallはsocket.connectを禁止して完了した。[small検証結果](small-verification.json)を参照。

動作確認の入力は、既存の固定公開manifestからType A/Bの先頭1問ずつを選んだ2問・25タイル。公開Git commitと画像SHAを照合した。[入力記録](smoke-inputs.json)の出典は [ssivakorn/reCAPTCHA-study](https://github.com/ssivakorn/reCAPTCHA-study/tree/efb3595c33780abf9d326c729973e72d165366c2)、ライセンスはCC BY-NC 4.0。無作為抽出・外部独立testではなく、精度推定には使わない。

| 構成 | Type Aの1問 | Type Bの1問 |
|---|---|---|
| small、CLIP閾値0.02 | 予測 `[0,3,7]`、注釈 `[3,7,8]`、不一致 | 予測 `[]`、注釈 `[]`、一致 |
| large、CLIP閾値0.01 | 予測 `[8]`、注釈 `[3,7,8]`、不一致 | 予測 `[]`、注釈 `[]`、一致 |

予測は [small](small-predictions.jsonl.gz)・[large](large-predictions.jsonl.gz)に保存した。Type Bはhash検証済み16タイルの再構成画像を使い、そのSHAも予測へ記録した。画像本体・モデル重みはこの記録には同梱せず、原画像は固定公開commitから復元できる。予測内の導出画像名は元のrunディレクトリからの相対パス。

論文の具体的checkpointは不明なため、採用モデル・revision・前処理を独自に固定した。閾値をこの2問の正解に合わせて調整していない。全1,000問の精度、既存S3との同条件比較、ライブ通過率、動的セッションは未測定。

回帰テストは173件中158成功・15 skip。[実行ログ](lab-tests.log)を保存した。この173件には追加した22件を含み、追加分は推論用venvでも全件成功した。実行環境の既存Nodeテスト依存として `robots-parser@3.0.1` を `.deps/bot-diagnostics` に導入した。モデル用依存の `pip check` と差分の空白チェックも通過した。

## 再実行

モデル用venvとlargeの重みは[実行手順](../../LOCAL_VISUAL.md)に従って準備する。入力の取得だけがネットワークを使い、次の検証コマンドではPythonのDNS・ソケット接続を禁止する。出力先はそれぞれ新しいパスを指定する。

```bash
python3 -B experiments/captcha-small-model/results/20261026-local-visual/restore_smoke_inputs.py \
  --output .lab-output/local-visual-smoke-input-restored
.deps/local-visual-venv/bin/python -B \
  experiments/captcha-small-model/results/20261026-local-visual/verify_offline.py \
  --manifest .lab-output/local-visual-smoke-input-restored/manifest.json \
  --image-root .lab-output/local-visual-smoke-input-restored \
  --model-root .lab-output/local-visual-models-large \
  --output-dir .lab-output/local-visual-smoke-repeat
```
