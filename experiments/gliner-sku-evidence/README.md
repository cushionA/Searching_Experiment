# GLiNER SKU evidence smoke

PR26 `e614a3f`の原本とv7 handoffを読み取り利用する、12実商品の抽出検証。既存ラベル・入力・判定器は変更しない。結果と制限は [report](../../reports/2026-10/2026-10-12-gliner-sku-evidence.md)。

## 再現

Python 3.12。リポジトリルートから実行。初回実行は次の手順。推論出力は上書き拒否なので、再測定時はこのディレクトリを複製した別checkoutで保存結果を別途退避し、過去出力と混ぜない。既存の推論を再実行せず検算する場合は最後のunit testだけでよい。

```bash
python3 -B experiments/gliner-sku-evidence/prepare.py
python3 -B experiments/gliner-sku-evidence/audit_latest.py
python3 -m venv /tmp/gliner-sku-venv
/tmp/gliner-sku-venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1
/tmp/gliner-sku-venv/bin/pip install 'gliner2[local]==2.1.0' transformers==5.17.0 sentencepiece protobuf psutil
/tmp/gliner-sku-venv/bin/python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'experiments/sku-matching')
from backend_gliner_extract import download_snapshot
download_snapshot(Path('/tmp/gliner-sku-model'))
PY
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/gliner-sku-venv/bin/python -u experiments/gliner-sku-evidence/run.py --count 2 --name smoke
# 2商品の動作・資源・literal spanを確認してから残り10商品だけ実行
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /tmp/gliner-sku-venv/bin/python -u experiments/gliner-sku-evidence/run.py --start 2 --count 10 --name bounded
python3 -B experiments/gliner-sku-evidence/evaluate.py
python3 -B -m unittest discover -s experiments/gliner-sku-evidence -p 'test_*.py'
```

厳密な依存一覧は `benchmarks/gliner-sku-evidence-20261012/requirements.lock`。CPU版torchのindexを指定して使う。モデルdownload helperはPR26にある既存の固定revision/サイズ/SHA検証器を再利用する。`source-audit-probes.json` は保存済みAI原文監査メモで、推論の入力ではない。人手独立goldではない。

`input.json` は商品名と説明3項目のテキスト全部をHTMLのdata部分から作り、各文字列片にJSONPath・原本SHA・offsetを保存する。価格・在庫や別AU URLによる判定は行わない。画像やalt属性からの仕様復元は対象外。全6fieldの候補抽出と、未対応条件のliteral baselineを比較する。aligned条件の再判定、支持/矛盾/不明のモデル判定、最終SKU採否は実装していない。

`prepare.py` はv7 handoffと旧v2のAU行配列を直接原本で検査し、v10のバイト同一入力としては扱わない。v10 inputsは共有物に見つからなかった。v2/v3独立入力は所在・原本span・splitの監査だけで、商品ペア確認が未完了なので推論には含めない。
