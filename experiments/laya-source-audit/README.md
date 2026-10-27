# Laya公開応答の独立監査（2026-10-28 JST 朝）

新規モデル推論ではなく、公式repo内の公開合成入力・保存応答を標準ライブラリだけで再計算する。上流Pythonをimport/execしない。TinyJev、Moli、Lightpanda等の停止ジョブを再利用しない。

```bash
python3 -B experiments/laya-source-audit/audit.py
```

入力は `benchmarks/daily-tech-20261028/sources/`、URL・取得UTC・SHA256は同階層の `source-manifest.json`。出力は `audit-results.json`。実行時間・RSSは監査だけの値で、再実行すると変わる。

- 上流固定commit: `nandhakishorm/laya@b9fe351ce81e2d974bca2bf219c1e4030c0a05ea`。
- 中国語64件×choice/four_noul×3repeat×2backend、768応答。正解は上流のAI補助合成ラベルで独立人手goldではない。
- 応答から選択ラベルを再復号し、ID/ラベル/重複/欠落/確率/両backendのrequest hashを検査。品質は事前のrepeat=0、3回を独立192件の精度として数えない。
- `four_noul`は公開の固定閾値（related/action/value=0.5、urgent=0.75）を独立実装。入力の指示文や基準を変更しない。
- `max_probability>=0.8` と返却 `confidence>=0.8` の絞込みは**今回追加の事後診断**。閾値を学習・最適化した結果でも将来の安全性保証でもない。
- 別の公開position-sensitivityの200保存予測も正答数を再集計。20問の位置違いなので200独立課題とはしない。
- APIのLFS hash比較は配布metadataの照合。重みをダウンロードしてhash検証したとは扱わない。

初回の自作manifestファイル名不備と、Jevの1応答の確率和0.99を `errors.json` に保存。公開許容差0.01へ合わせ、0.001超のずれは別欄で報告する。確率を修正・再正規化しない。

上流コード・モデルはApache-2.0表記、中国語benchmark contributionはMIT表記。各LICENSEとREADME、出典URLを保存。保存コードは読むための資料であり実行対象ではない。新規推論には別途外部コード実行の承認・隔離確認が必要。モデルinstall、API推論、学習、Kaggle submitは行わない。
