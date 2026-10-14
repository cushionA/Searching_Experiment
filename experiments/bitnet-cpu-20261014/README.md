# BitNet CPU PoC（2026-10-14）

CPU推論まで実行し、日本語／英語の短い分類・構造化抽出を測定した。品質はsmokeで不合格。最新の結論・数値は [レポート](../../reports/2026-10/2026-10-14-bitnet-cpu-poc.md)、生出力と環境は [benchmarks](../../benchmarks/bitnet-cpu-20261014/) を参照。

## 固定対象

- engine: microsoft/BitNet `0b341e582afbf9e1011f24744b554c96a3477eb5`
- 公式submodule: isHuangXin/llama.cpp `390c307752ab78fd8189f359d6954c9ba1be74af`
- model: microsoft/bitnet-b1.58-2B-4T `04c3b9ad9361b824064a1f25ea60a8be9599b127`
- CPU用公式GGUF: microsoft/bitnet-b1.58-2B-4T-gguf `a1f2f1c765812aa8af3f6eda4a313707064bba15`
- GGUF実取得1,187,801,280 bytes、SHA256 `4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162`
- 入力SHA256 `63bb56cc62dfa05b5367e8f3e5f8b25d6170e92926d52ff42c694db641b64afe`

## 評価方法

独自合成24件＝日本語／英語×分類／抽出×各6。正解は事前固定、学習やモデル生成を使わない。分類はshipping/billing/account、抽出はitem文字列とquantity整数。厳密JSON（余分なキー・コードフェンスを拒否）、exact、field accuracyを採点。品質は各入力1回のみ、反復は速度評価用。

seed42、temperature0、4threads（batchも4）、parallel1、context2048、最大96tokens、prompt cache=false。初回要求とwarmup1を分け、定常は最大3反復。主runの600秒上限は延長しない。途中反復が出た場合は主要速度表から除き、生出力を保持する。server child RSSとcgroup全体peakを区別。OS cache完全coldは測っていない。tokens/sは失敗出力も含み、有用な処理件数/sと同一ではない。

主run `hf` は公式HF tokenizerの `User: ...<|eot_id|>Assistant: ` 形式。公式GGUF metadataが異なるtemplateを持つことが判明したため、`gguf` 条件の追加4件smokeではserver `/apply-template` を使用。入力・正解を変えず、別runに分離。tokenizer警告とtemplate差の因果関係は未確定。

24件は短いsmokeで、有意な導入・汎化性能の評価ではない。baseline比較未確認。

## 再現・検証

環境の修復経緯、公式手順との差分、隔離方法、実行コマンドは [reproduce.md](reproduce.md)。初回buildのread-onlyエラーと修復成功の両ログを保存。修復は追加許可された空の一時BUILDX_CONFIG指定1回のみ。認証ファイル、Docker接続先、host権限、セキュリティ設定を変更しない。

- `fetch_public.py`: 固定公開source／GGUF取得、SHA照合、2GB枠のうち400MBをOS等に予約。実測取得処理を整理した版で、追加ダウンロードによる全体再試験はしていない。
- `compile.sh`: 実測した公式codegen/CMake経路。変換用pip依存を不要にするため直接呼出し。
- `benchmark.py`: ローカルserver起動・固定入力実行・採点・時間／RAM・生JSON保存。
- `inspect_gguf.py`: 実ファイルのmetadata読取り専用検査。
- `analyze_results.py`: 完了反復の集計。raw、summaryを持つrun directoryを引数にする。

```sh
python3 -B -m unittest discover -s experiments/bitnet-cpu-20261014 -p 'test_*.py'
python3 -B experiments/bitnet-cpu-20261014/benchmark.py --validate-only
python3 -B experiments/bitnet-cpu-20261014/analyze_results.py benchmarks/bitnet-cpu-20261014/run-hf
```

実用不合格のためdraft PR・merge・deployなし。次は公式配布物の対応版とtokenizer/template整合性を確認し、その後に同一固定集合、別の未使用日本語入力、baselineで再評価する。
