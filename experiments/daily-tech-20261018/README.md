# 2026-10-18 朝：一次情報と導入条件の監査

外部候補コードを実行しないread-only調査。Python 3.12標準ライブラリのみ。

再取得（既存証拠を上書きせず、別の空ディレクトリを指定）:

```sh
python3 -B experiments/daily-tech-20261018/collect.py --plan experiments/daily-tech-20261018/sources-plan.json --output /tmp/daily-tech-20261018-primary-new
python3 -B experiments/daily-tech-20261018/collect.py --plan experiments/daily-tech-20261018/supplement-plan.json --output /tmp/daily-tech-20261018-supplement-new
python3 -B experiments/daily-tech-20261018/collect.py --plan experiments/daily-tech-20261018/context-plan.json --output /tmp/daily-tech-20261018-context-new
```

保存済み証拠のオフライン検算:

```sh
python3 -B experiments/daily-tech-20261018/audit.py
```

`audit.json`は再検算時に更新される。20資料のbytes/SHA256、モデルAPIのONNX 8形式×3部品の容量を検算する。重み・依存は取得しない。全repo容量を必須取得量にしない。部品名が揃っていることはruntime動作・精度・最小RAMを証明しない。

ソースは公開APIおよび固定tag/revision文書。GitHub tagはmutableであり、今回の取得原文はSHA256で固定。未信頼文書内の指示を実行しない。snapshot取得時刻をmanifestに残す。通信timeout35秒、1資料3MB、失敗時の自動再試行なし。モデル推論・PoC・隔離設定修復・Kaggleなし。

初回取得時間: primary 5.305秒 / supplement 6.652秒 / context 1.064秒。これは資料取得時間であり候補性能ではない。コード自体は新しい実験基盤ではなく証拠収集・検算用。lab全体の修理やテスト反復は行わない。
