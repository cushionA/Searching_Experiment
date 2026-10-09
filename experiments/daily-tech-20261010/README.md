# 2026-10-10 JST 日次技術調査 / Docling OCR領域選択の限定再現

Docling 2.136の新しい不要OCR回避を、2.135.0と2.137.0のCPU処理で確認する。
モデル推論、文書全体の変換、検索機能、SKU照合を実装する実験ではない。
詳細と導入判断は `reports/2026-10/2026-10-10-daily-tech.md`。

## 再現

Python 3.12以上、Linux。作業ディレクトリはリポジトリルート。
依存はPyPIから取得。モデルのダウンロード、GPU、Kaggle、API課金は不要。

```bash
python3 -m venv /tmp/docling-daily-venv
/tmp/docling-daily-venv/bin/python -m pip install -r experiments/daily-tech-20261010/requirements.lock
/tmp/docling-daily-venv/bin/python -m pip install --no-deps --target /tmp/docling-daily-old docling-slim==2.135.0
mkdir -p /tmp/docling-daily-results
PYTHONPATH=/tmp/docling-daily-old /tmp/docling-daily-venv/bin/python -B experiments/daily-tech-20261010/ocr_regions.py --output /tmp/docling-daily-results/docling-2.135.0.json
/tmp/docling-daily-venv/bin/python -B experiments/daily-tech-20261010/ocr_regions.py --output /tmp/docling-daily-results/docling-2.137.0.json
python3 -B experiments/daily-tech-20261010/summarize.py --results /tmp/docling-daily-results --output /tmp/docling-daily-results/summary.json
```

旧版は同じvenvに`PYTHONPATH`でパッケージ本体だけ差し替える。
依存版と入力hashの一致を保存結果で検査する。Python起動を含む外部wall timeではなく、
`ocr_regions.py`開始から出力直前までのprocess内経過時間を記録する。
RSSはLinuxプロセス寿命最大値（importを含む）。選択処理は初回と20回のwarm測定を分ける。
全出力は既存ファイルの上書きを拒否する。

保存済み結果の再計算だけなら標準ライブラリで実行できる:

```bash
python3 -B experiments/daily-tech-20261010/summarize.py --results benchmarks/daily-tech-20261010 --output /tmp/docling-daily-verified.json
```

`prepare_inputs.py` は最初の取得記録用。既に入力を同梱しているため通常は実行不要。
再取得する場合は**別checkout**で生成先が存在しないことを確認して実行し、保存済み証拠を消さない。
固定revisionの5PDFとupstreamテスト・MITライセンスを取得し、推論前にprotocol/hashを保存した。
upstream由来の13領域であり、独立した日本語評価集合ではない。
期待値は「OCRに回すか」のupstream契約で、OCR文字起こしの正解ラベルではない。

一次情報の更新確認は次の読み取り専用スクリプト（既存の`gh`認証が必要）。
現在値が変化するため取得時刻を含めて別ファイルへ保存する。

```bash
python3 -B experiments/daily-tech-20261010/snapshot_sources.py --output /tmp/daily-sources.json
python3 -B experiments/daily-tech-20261010/snapshot_sources.py --supplement --output /tmp/daily-supplement.json
```

## 保存と制限

- `inputs/` と `protocol.json`: MITのupstream fixture、出典・SHA256・入力領域・反復数。
- `requirements.lock`: 新版＋共通依存。旧版wheelのPyPI SHA256はsupplement内。
- `benchmarks/daily-tech-20261010/`: 78条件の結果、全タイミング、数値再計算、環境、ソース、失敗ログ。
- モデル推論はadapterが例外で拒否。layoutは固定領域を手動注入する。
- 3経路は同じ13領域なので39件を独立サンプルの精度と解釈しない。
- 本番性能・日本語OCR精度・対象サイトの網羅性・モデルの必要RAMは未測定。
- lab既存テストはrobots-parser未導入で1件失敗。既存コードやCIは変更しない。
