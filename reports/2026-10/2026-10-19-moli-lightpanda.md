# Moli / Lightpanda 比較 — 実測前の隔離疎通で停止

## 結論・概要

**ブラウザ比較は未完了。速度・品質・成功率・RSS/PSSの新規実測値はない。** 2026-10-19、指定環境で自作の無害なPythonをbubblewrap内から実行する初回probeがUID map作成時に失敗した。隔離を外した実行、昇格、別環境への迂回は行っていない。双方の優劣や本番採用可否は判定できない。

## 固定版と方法

最新はGitHubのlatest stable releaseとして選択し、nightlyは除外した。公式APIの配布hashであり、ダウンロードした実体のhash照合ではない。

| 対象 | 固定版 | release commit | Linux x86_64配布bytes |
|---|---|---|---:|
| [Moli](https://github.com/lexmount/moli/releases/tag/v1.1.18) | v1.1.18 | 2a52e9333894722d0e1e8724a3a41f0688a07613 | 44,973,610 |
| [Lightpanda](https://github.com/lightpanda-io/browser/releases/tag/1.0.1) | 1.0.1 | 99edca45844b5968c3ec8e50ad8e1acec8f3b759 | 75,670,856 |

SHA-256・取得元・取得時刻: [versions.json](../../benchmarks/moli-lightpanda-20261019/versions.json)。合計約115 MiBの配布候補だがバイナリdownloadは0 bytes。API/README取得の通信量は計測していない。追加有料サービス・packageインストールなし。

[固定fixtureと比較仕様](../../experiments/moli-lightpanda-20261019/README.md)を準備した。static、JS生成、150ms待機、クリック後更新、iframe、fetchの6条件で同一本文・リンクを照合し、geometryは別評価。並列1、5反復、cold/warm分離、10秒の同一期限を予定する。HTTP200を成功としない。Moli DOM既定/Lightpanda DOM既定を比較しMoli --layoutを別列にする。実サイトは未訪問。

## 結果

| 指標 | Moli既定 | Lightpanda既定 | Moli --layout |
|---|---|---|---|
| ブラウザ実行試行 | 0 | 0 | 0 |
| 品質・成功率 | 未測定 | 未測定 | 未測定 |
| cold/warm median・ばらつき | 未測定 | 未測定 | 未測定 |
| browser tree RSS/PSS | 未測定 | 未測定 | 未測定 |

0試行を0%成功率と扱わない。唯一の実行結果は隔離probeの終了コード1。Python本体に到達しなかったため、隔離内UID/ディレクトリread/executeは未確認。probe単独時間も未計測。

ホストはLinux 6.18.55 x86_64、Intel Xeon Platinum 8573C、5論理CPU可視、cgroup CPU quotaは4 CPU相当、ホストUID1000。環境cgroup RAMは16 GiBで、実験指定8 GiB上限は未設定・未実行。Python 3.12.14、Chromiumは/usr/bin/chromiumに存在するが実行していない。[機械可読結果](../../benchmarks/moli-lightpanda-20261019/results.json)。

## 既存比較・進行中作業

AGENTS.md、.agents/skillsの一覧と関連bot-blocking-scenariosを確認。このskillは既存サイトのブロッキング検証用であり今回の合成fixture実験への適用対象外。既存crawler/stateは変更しない。remote全branch名とopen PR一覧を確認し、Moli比較を示すbranch/PRはなかった。未pushの別人作業までは確認できない。

[既存2026-10-03比較](../../docs/browser-benchmark.md)はLightpanda 1.0.0・Playwright/Patchright 1.63.0で384ページ抽出照合成功。Lightpanda warm並列1は21.827〜63.915ms、worker群peak RSS中央値208.913〜212.228 MiB（4シナリオ）。過去の別環境・別版・別計測範囲の値であり今回のMoli比較には使わない。過去に1 CDP接続1 context制約が観測され、独立接続で修正済み。

固定版の[公式Moli README](https://github.com/lexmount/moli/blob/2a52e9333894722d0e1e8724a3a41f0688a07613/README.md)と[公式Lightpanda README](https://github.com/lightpanda-io/browser/blob/99edca45844b5968c3ec8e50ad8e1acec8f3b759/README.md)を読んだ。Moliの既定はmock geometry、--layoutで実layout/software paint。LightpandaにもMarkdownとPandaScriptがありPNG/PDFはtext-only。これらをMoli固有やChrome同等の描画として扱わない。著者の92 MiBは限定warm-process PSSであり本実測値・一般性能ではない。

## 問題・停止証拠

初回自作probeのstderr:

```text
bwrap: setting up uid map: Read-only file system
```

[再現コード](../../experiments/moli-lightpanda-20261019/preflight.py)に同じargvを保存。結果は既知の失敗で、pendingや不明ではない。auto-review拒否ではなくOSのnamespace設定失敗。昇格要求なし、修復試行0、再試行0。権限緩和をせず解消できる経路を確認できなかったため外部バイナリは取得・実行しなかった。新規の比較作業であり、停止済みSqueez/YOLO26n/Harness²/PP-LCNetやBitNetは再開していない。SKU・proxy認証・非公開データを使用していない。

## 導入判断・次

- DOM本文/リンク抽出: 両者を評価候補として維持。今回は本番採用・置換を判断しない。
- JS待機/クリック/iframe/fetch依存: fixtureによる意味的成功判定が必要。実測なしでは採用推奨できない。
- CSS座標/視覚出力: Moli --layoutを別途評価候補とする。Lightpanda text-only出力を同等代替としない。

同じ選択環境で秘密を公開せず利用できる隔離経路が必要。提供後にprobe・実作業ディレクトリ・UID・8 GiB制限を確認し、バイナリhash検証とCDP adapter/runner実装を行う。**現時点でfixture生成とpreflightだけが再実行可能で、性能計測runnerは未実装。** 手順は上記README。単なる研究準備のためPR・merge・deploy・cron/state変更は行わない。

## 検証・保存

自作Python構文、全HTMLのparseとinline JSのNode構文検査、7件の期待値を確認済み。ブラウザ上の意味的正しさは未検証。[検証記録](../../benchmarks/moli-lightpanda-20261019/validation.json)。既存規約の `python3 -B -m unittest discover -s tests -p 'test_lab*.py'` は151件、62.473秒、failure 1、skip 15。既存bot diagnosticsのrobots-parser未導入が原因であり、今回の比較のために依存導入・修復は行っていない。[全ログ](../../benchmarks/moli-lightpanda-20261019/lab-tests.log)。実行中にexec transportが切断したが同じ環境へ復旧し、同一processの終了ログを確認した。テストの重複投入なし。
