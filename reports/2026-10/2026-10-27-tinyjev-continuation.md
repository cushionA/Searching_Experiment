# TinyJev追加修復1回の結果

**元の `/wheels` 権限エラーは解消、依存導入成功。PyTorch importで別のエラーが発生したため停止。48件のモデル推論は未実施。**

ユーザーが許可した追加修復1回だけを使った。朝夕の追加探索・正本evening-state更新・モデル変更・新規download・学習・Kaggle投入は行っていない。元の夕方報告は変更していない。

## 原因の確認と行った修復

前回はread-only bindの `/wheels` を読み、別の `/deps` へpip installするコマンドだった。wheelディレクトリはホストUID/GID1000、mode0700、各wheelは0600。ホストを変更せず、同じread-only/cap-drop ALL/no-new-privileges条件でアクセスだけを確認すると、既定UID0ではread/traverseがfalse、非root UID/GID1000では両方trueだった。したがって前回のエラーは `/wheels` への書込み失敗ではなく、所有者とプロセスUIDの不一致による読取り段階の失敗と説明できる。

追加修復ではコンテナを **非root 1000:1000** で実行。wheel、モデル、候補package、固定入力、既存実行harnessだけをread-onlyで渡し、install/tmp/cacheはコンテナ所有の `/work` tmpfsへ分離した。ホストchmod・権限変更、root回避、chmod777、認証・ネットワーク・安全設定変更はしていない。正解ラベル、baseline/scorer、通常workspace、個人ファイルは候補コードに渡していない。

既存wheelのみからtorch2.7.1+cpu、transformers4.57.1等の導入はexit0、25.024秒で成功した。次のimport確認はexit1、0.218秒で失敗：

```text
OSError: /work/deps/torch/lib/libtorch_global_deps.so:
failed to map segment from shared object
```

失敗コンテナは終了・自動削除済み。候補コードを実行しない別の最小診断コンテナを**同一mount条件**で起動し、`/work` が `rw,nosuid,nodev,noexec`、`statvfs.ST_NOEXEC=true`であることを確認した。失敗コンテナそのもののmountinfoは取得していないが、同一設定のnoexecが共有ライブラリmappingエラーを説明する。**exec許可への変更や別配置への再修復・再importは行っていない。** 続行には今回とは別の追加修復が必要になるため、依存する処理は停止した。自動承認レビューの拒否ではなく、既知のローカル実行失敗であり、不明な外部ジョブはない。

## 保持した入力・結果

- code SHA：`fec868b6b44ac5fddac64ff87d318ccceaff27ea`
- model revision：`c559c2f7ea95069f92af8b999f512345bbb87d22`。全ファイルhashはmanifest内の既存download ledgerへ保存。
- 固定48入力SHA256：`8c0bcef169e398d83b896068bcfa4566c7b2468c146f341f172162375f034f15`
- 独立正解SHA256：`98bca7e5ad3dc3402264ffcf8dea1327ab6bf2d8123e6b97badf700ed90acf7a`
- 元のinputs/gold/run_model/run_baseline/scoreをhash一致で照合し、変更なし。baselineの既存結果も保持。
- モデル推論 **0/48**、候補出力0。NLP/gameAI別品質・invalid outputs・推論latency・推論peak RSSは評価不能。invalid0件と成功0件を混同しない。
- 今回のsetup/import全体25.243秒、子CPU25.228秒、子peak RSS157,800KiB。**推論資源の実測ではない。** RAM8GB/2CPU、CPU上限1200秒、network none、read-only/cap-drop ALL/no-new-privilegesを維持。
- 追加download0、費用0、追加修復1回、訓練0、外部job0。

成果物：[実行コマンド](../../benchmarks/research-repro-radar/2026-10-27-tinyjev-continuation/run.sh)、[実行harness](../../benchmarks/research-repro-radar/2026-10-27-tinyjev-continuation/bootstrap.py)、[完全なinstall/import出力](../../benchmarks/research-repro-radar/2026-10-27-tinyjev-continuation/execution.json)、[mount診断](../../benchmarks/research-repro-radar/2026-10-27-tinyjev-continuation/mount-diagnostic.json)、[hash・原因台帳](../../benchmarks/research-repro-radar/2026-10-27-tinyjev-continuation/manifest.json)。再実行コマンドの保存は追加再試行の許可を意味しない。

独立継続ブランチへ保存する。正本 `research/2026-10-12-evening-radar@398863ba6611ec4c2c502c9cd5533a0922528108` とevening-state4JSONは変更しない。merge/deployなし。remote SHAとexact-SHA CI結果は会話の最終結果に添える。
