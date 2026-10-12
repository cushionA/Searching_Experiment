# 2026-10-13 朝の日次調査

候補のインストール・推論・ブラウザPoCは実施していない。コードは公開一次資料の取得と保存証拠の検算用。性能測定コードではない。

Python 3.12.14 / 標準ライブラリのみ。repo rootで:

```bash
python3 -B experiments/daily-tech-20261013/verify.py
# 任意の資料再取得。新規ディレクトリのみ受理し、保存済み観測を上書きしない。
python3 -B experiments/daily-tech-20261013/collect.py /tmp/daily-tech-new
```

入力は `benchmarks/daily-tech-20261013/primary-manifest.json` と保存資料。
再取得時はstars、model revision、latest release等が変わり得る。
`verify.py` はハッシュ・件数・静的ソース根拠を確認するだけで、Doclingやモデルの動作を検証しない。
raw sourceはテキストとしてのみ扱い、import/eval/execしない。
夕方レーダーのskill/state、SKU、PR19、lab実験基盤には触れない。
