# mLateOn 日本語 CPU PoC（2026-10-07）

公式の固定 ONNX revision を用い、合成36文書・24質問で文字2-gram BM25、FP32/INT8 MaxSim、FP32平均プーリングの ablation を比較する。既存検索サービスには接続しない。

## 再現

リポジトリ root、Python 3.12以上、Linux CPU。モデル取得に約1.6GBの通信、ディスクはvenvを含め余裕を確保。今回のFP32ピークRSSは約2.1GiB、実行用RAMは4GiB以上を目安とする（最低要件保証ではない）。公開モデルのデータのみ取得し、外部コードやexport_onnx.pyは実行しない。認証不要。

```bash
python3 -m venv /tmp/mlateon-venv
/tmp/mlateon-venv/bin/pip install -r experiments/mlateon-cpu/requirements.lock
/tmp/mlateon-venv/bin/python experiments/mlateon-cpu/benchmark.py --download --model-dir /tmp/mlateon-model
# 既存ファイルは再取得しない。下のコマンドで保存済み期待hashと照合する。
python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest=json.loads(Path('benchmarks/mlateon-20261007/model-manifest.json').read_text())
for name, item in manifest.items():
    with (Path('/tmp/mlateon-model')/name).open('rb') as f:
        assert hashlib.file_digest(f, 'sha256').hexdigest() == item['sha256'], name
print('model hashes OK')
PY
# 再実行は新しい出力フォルダへ保存する。
for arm in bm25 fp32 int8 mean-ablation; do
  OPENBLAS_NUM_THREADS=1 /tmp/mlateon-venv/bin/python -B experiments/mlateon-cpu/benchmark.py \
    --arm "$arm" --threads 2 --repeats 3 --output "/tmp/mlateon-recheck/$arm.json"
done
python3 -B experiments/mlateon-cpu/summarize.py /tmp/mlateon-recheck
python3 -B -m unittest discover -s experiments/mlateon-cpu -p 'test_*.py'
```

入力・ラベルは `fixture.json`、その著作時の生成コードは `make_fixture.py`。評価後に生成器を調整して同じrunの入力を変更しない。`fixture-before-inference.sha256` が推論前の固定値。新規データや条件は新しいrunに保存する。

`benchmark.py` は一つの arm を独立プロセスで実行する。モデルロードと文書エンコードは別計測、query warmup一回、各質問を連続3回計測。latencyはtokenize＋query encode＋36文書の全走査score＋float変換、順位sortやJSON保存は含まない。文書ベクトルはメモリ内で再利用。BM25の構築時間はload_secondsに計上。RSSはLinux ru_maxrss（ロード中も含むプロセス全体の最大値）。ORT 2 threads、BLAS 1 thread、batch=1。BM25は逐次Pythonなのでthreads値は適用されない。

PyLateの公開tokenize実装に合わせてBOSの直後に専用[Q]/[D] IDを挿入する。query expansion・skiplistなし、paddingなし。公式グラフが全Dense層とL2正規化を含み、全有効トークンのMaxSimを合計する。512トークン超はエラーにし、切り捨てない。今回の長さは10–44 tokens。PyLate/PyTorchとの独立一致検証、長文・padding・batch精度検証は未実施。

mean-ablationは同じColBERT出力を平均し再正規化するだけであり、学習済みdense検索モデルやmDenseOnではない。BM25はNFKC＋小文字化＋英数字/日本語文字を残した文字2-gram、k1=1.2、b=0.75、正のRobertson IDF。形態素解析器やパラメータの調整なし。1文字クエリには弱い。

`summary.json` のBM25 top10再ランキングは保存スコアによるオフライン計算。カスケード速度を測定した値ではない。全候補の順位・score・クエリごとのlatencyを各armのJSONに保存する。
