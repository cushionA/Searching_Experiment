# CLIP / OWLv2 のローカル実行

[Robot Visions](https://arxiv.org/abs/2609.18518) のローカル方式を、既存の保存画像評価へ追加した。AIエージェントや外部AI APIは使わない。文字OCR、微調整済みMobileCLIP2-S3、過去の評価入力・結果は変更していない。

公開リポジトリにはローカルsolverのコードがないため、論文の手法を独立実装した。論文は具体的な重みのID・revisionを明示していない。本実装で採用した重み・前処理・評価条件を記録し、論文の数値を再現したとは扱わない。公開データの出典は [ssivakorn/reCAPTCHA-study](https://github.com/ssivakorn/reCAPTCHA-study)、同リポジトリのデータはCC BY-NC 4.0。

## 実行環境と重みの準備

Python 3.12以上の専用環境を使う。CPU版のPyTorchを先に入れる。

```bash
python3 -m venv .deps/local-visual-venv
.deps/local-visual-venv/bin/python -m pip install \
  --index-url https://download.pytorch.org/whl/cpu \
  torch==2.8.0 torchvision==0.23.0
.deps/local-visual-venv/bin/python -m pip install \
  -r experiments/captcha-small-model/requirements-local-visual.txt
.deps/local-visual-venv/bin/python -B \
  experiments/captcha-small-model/prepare_local_visual_models.py \
  --profile large --output .lab-output/local-visual-models-large
```

準備コマンドだけがHugging Faceへ接続する。新しい出力先を指定する。既定のrevisionは固定commitで、`--clip-revision` / `--owl-revision` で別の40桁commitを明示できる。safetensorsと設定・tokenizerだけを取得し、各ファイルのSHA-256を `manifest.json` に保存する。中断した出力は保存されるが、推論には完成したmanifestが必要。再試行には別の出力先を使う。

| profile | Type A | Type B |
|---|---|---|
| `large` | [LAION CLIP ViT-H/14](https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K) | [Google OWLv2 base ensemble](https://huggingface.co/google/owlv2-base-patch16-ensemble) |
| `small` | [LAION CLIP ViT-B/32](https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K) | 同上 |

`small` は軽い構成での動作確認用。profileを変えても閾値は自動調整しない。CLIPの既定閾値はH/14向けの0.01。B/32で0.02を使う場合は、認識・評価コマンドへ `--clip-threshold 0.02` を指定する。重み、CPUメモリ、電力は必要で、ローカル実行にAPI従量料金は発生しない。

## 保存画像の認識

Type Aは独立した9枚を左上から行優先順で指定する。CLIPの正規化cosineについて `a photo of {target}` と `a photo of something else` の差が閾値以上のタイルを選ぶ。

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.deps/local-visual-venv/bin/python -B \
  experiments/captcha-small-model/recognize_local_visual.py \
  --model-root .lab-output/local-visual-models-large \
  --type A --target cars --images /path/to/tile_{0..8}.png \
  --output .lab-output/local-visual-example-a.json
```

Type Bは4×4に分割される写真全体を1枚指定する。OWLv2の検出confidenceが0.5以上、かつbboxとタイルの交差面積がタイル面積の10%以上なら選ぶ。複数bboxの面積は合算しない。

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.deps/local-visual-venv/bin/python -B \
  experiments/captcha-small-model/recognize_local_visual.py \
  --model-root .lab-output/local-visual-models-large \
  --type B --target "traffic lights" --images /path/to/board.png \
  --output .lab-output/local-visual-example-b.json
```

結果の `selection` は0始まりの行優先index。`scores` はType Aでcosineの差、Type Bで条件を満たしたbboxの最大confidence（選択されないタイルは0）。両者の値は別の意味を持つ。入力SHA、重みのrevisionとファイルSHA、閾値、実行時依存バージョン、コードSHAも保存する。

`elapsed_ms` は前処理と推論を含み、各モデルの初回呼び出しでは読込時間も含む。論文のモデル単体の推論時間との直接比較には使わない。

推論は `local_files_only=True` / `use_safetensors=True` / `trust_remote_code=False` を使う。読み込む前に全ファイルのhashを検査し、snapshotにmanifest未記載ファイルがある場合も拒否する。準備後はネットワークがなくても実行でき、要求した側のモデルだけをメモリに読み込む。CPU・2スレッドが既定で、`--device cuda` と `--threads` も指定できる。

Pythonからは `local_visual.LocalVisualSolver` の `predict_type_a(tile_paths, target)` / `predict_type_b(image_path, target)` を呼べる。どちらも同じ `selection` / `scores` 形式を返す。

## URL指定でブラウザから実行

`run_local_visual_browser.mjs` は既存bot-diagnosticsの `playwright-baseline` ブラウザを使い、画像取得→ローカル判定→タイル選択→送信→チェックマーク確認まで実行する。対象はreCAPTCHA v2の9タイル/16タイル形式。英語の動的差し替え指示にも対応する。

Node.js 22以上とChromium、ローカルモデル用venv・重みを用意する。Playwrightは既存bot-diagnosticsの依存として配置する。既存の依存がない環境での最小セットアップ例:

```bash
npm install --prefix .deps/bot-diagnostics --no-save --ignore-scripts \
  --package-lock=false playwright@1.63.0 robots-parser@3.0.1
node experiments/captcha-small-model/run_local_visual_browser.mjs \
  --url https://www.google.com/recaptcha/api2/demo \
  --model-root .lab-output/local-visual-models-large \
  --output-dir .lab-output/local-visual-browser-demo \
  --timeout-ms 120000 --max-attempts 20
```

Chromiumは既定で `/usr/bin/chromium`。別の実行ファイルは `BOT_DIAGNOSTICS_CHROMIUM` で指定できる。既定はheadlessで、表示環境がある場合は `--headful` を追加する。Pythonは既定の `.deps/local-visual-venv/bin/python` を使い、別環境は `--python /path/to/python` で指定する。CLIの `--help` には各閾値・上限の指定を掲載している。

ブラウザと推論は別プロセス。ブラウザは既存のproxy・TLS検証を使い、推論プロセスはローカルファイル読み込みだけを許してPythonのDNS・ソケット通信も禁止する。モデルプロセスを維持し、繰り返し判定での重みの再読み込みを避ける。検知回避の注入やプロキシ切り替えは追加していない。

出力先は新しいディレクトリを指定する。取得画像、画像SHA付きの予測JSON、操作イベント、ブラウザのURL/HTTP応答状態、最終画面、実行条件とモデルの由来を保存する。推論の正解indexが不正ならクリックしない。問題が判定中に変わった場合は予測を破棄し、動的タイルは画像の差し替えと安定を待つ。静的タイルは現在の選択状態との差分だけをクリックする。

`summary.json` の主な結果:

| outcome | 意味 |
|---|---|
| `passed_after_image_challenge` | 画像の判定・送信後にチェックマークを確認 |
| `passed_without_image_challenge` | 画像問題なしでチェックマークを確認。モデルによる突破には数えない |
| `captcha_not_observed` | 対応する可視widgetを確認できなかった |
| `no_progress` | 同じ問題と予測が繰り返されたため停止 |
| `refresh_timeout` | 動的タイルの更新を確認できず停止 |
| `attempt_limit` / `deadline_exceeded` | 回数・時間の上限に到達 |
| `service_rejected` | サービス側の拒否・エラー表示を観測 |

CLIはチェックマーク確認で終了コード0、それ以外の観測結果で2、引数・事前準備のエラーで1を返す。未対応の認証形式・他サービスへの自動fallbackは行わない。ローカルfixtureで実モデル接続を確認し、公式デモでも画像判定・クリック・送信を確認した。公式デモの1セッションは120秒の期限で停止し、通過チェックは未確認。[ブラウザ接続の検証記録](results/20261026-local-visual-browser/REPORT.md)を参照。

## 既存ボードとの比較

`public_full.json[.gz]` または固定test foldのmanifestと、元画像を復元したディレクトリを渡す。

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.deps/local-visual-venv/bin/python -B \
  experiments/captcha-small-model/evaluate_local_visual.py \
  --manifest /path/to/grouped-test.json \
  --image-root /path/to/restored-public-input \
  --model-root .lab-output/local-visual-models-large \
  --output-dir .lab-output/local-visual-test
```

Type A/B別・target別の完全一致率を `metrics.json`、生予測を `predictions.jsonl`、条件と完了状態を `protocol.json` に保存する。`--type A|B|all` と `--limit N` で対象を絞れる。limitは型で絞った後の先頭N件で、無作為標本ではない。選択対象のタイルはすべて推論前にSHA照合し、正解の選択枚数・indexをモデルへ渡さない。失敗時にも部分結果とfailed状態を残す。

Type Bの評価入力は、hashを検証した16タイルを元の画素数と並びで再構成する。導出画像のSHAも記録する。元の全体画像との同一性は未検証で、この前処理条件は論文との比較に併記する。

保存画像の完全一致はライブ通過率を示さない。ブラウザ用CLIは別の実行入口で、既存の検索サービス50件の設定や自動recovery経路は変更していない。

CPUでの実モデル動作確認とテスト結果は[取り込み確認記録](results/20261026-local-visual/REPORT.md)を参照。
