# BitNet.cpp CPU PoC — 実測済み、この組合せは導入保留

## 結論

2026-10-14、CPU4スレッド・RAM8GiB上限で公式2B GGUFの生成まで実行した。しかし短い分類／構造化抽出24件は **exact 0/24、指定キー・型の形式成功0/24**。現行公式engineとGGUFにtokenizer／会話テンプレートの不整合が観測されたため、BitNetモデル一般の能力が低いと結論づけず、**今回の配布物・設定の組合せはそのまま導入しない**。

公式GGUF内テンプレートをserverに適用させた追加4件のsmokeはexact **0/4**。主評価のHF tokenizer設定に沿った手動templateと混ぜず別保存した。追加smokeだけで問題が解決したとは扱わない。

初回Docker buildはread-onlyエラーで停止したが、追加指示が明示許可した空の一時 [BUILDX_CONFIG](https://docs.docker.com/build/building/variables/#buildx_config) 指定1回で成功。認証のコピー／移設、DOCKER_HOST切替、読取り専用path変更、昇格、host mountやセキュリティ設定の変更はしていない。自動承認審査による拒否ではない。

## 実測結果

主評価の品質は各群6件の1巡目だけで採点。速度は完了した反復 [0, 1] のみ集計し、反復を独立正解件数として水増ししない。

|言語／課題|exact|形式成功|定常wall中央値 秒|生成token/s|速度集計要求数|
|---|---:|---:|---:|---:|---:|
|ja/classification|0/6|0/6|10.324|9.74|12|
|ja/extraction|0/6|0/6|10.604|9.19|12|
|en/classification|0/6|0/6|9.531|9.38|12|
|en/extraction|0/6|0/6|1.740|8.30|12|

- 抽出field accuracyも日本語0/12項目、英語0/12項目。主要速度集計48要求のうち36要求は96tokens上限に達した。
- server起動ready: **2.485秒**。初回要求 **12.100秒**。起動＋初回応答は約 **14.585秒**。この初回はOS cache coldやTTFTではない。モデルSHA確認とcopyでページキャッシュが温まっている。
- server child peak RSS: **1,587,830,784 bytes (1.479 GiB)**。
- 測定用の新規container全体 cgroup memory.peak: **1,674,125,312 bytes (1.559 GiB)**。Python、ファイルcopy／ページcache等も含む。ビルドとは別containerで、ビルドpeakと混ぜていない。
- 主評価server実行 **600.58秒**、完了要求 **71件**（初回1＋warmup1を含む）。plannedは定常72要求。status=`failed`、error=`TimeoutError('timed out')`。これは600秒の残時間をHTTP timeoutに渡して停止した予算上限で、主評価の予定74要求中3要求が未完了（うち1要求は実行途中）。途中反復の **21件**は主要速度表から除外し、生出力を保持。
- GGUF標準template追加smokeは **6要求**（初回＋warmup＋4固定例）、**102.89秒**。少数の診断条件であり、24件評価と置き換えない。
- 生成token/sはserverのgenerated tokens / generation time。出力長が群によって異なり、言語ごとのモデル速度差を直接表すものではない。失敗出力を含むため「有用な分類件数/s」ではない。baselineなし、速度改善率・コスト優位・本番性能は比較未確認。

## 重要な問題

1. serverは `missing pre-tokenizer type, using: 'default'` と `GENERATION QUALITY WILL BE DEGRADED!` を出した。実GGUFのmetadata24項目を読取り専用で検査し、`tokenizer.ggml.pre` 不在を確認。重みSHAは公式API値と一致した。
2. HFモデルtokenizerの `User: ...<|eot_id|>Assistant: ` と、GGUF内の `Human: ... BITNETAssistant: ...` templateが異なる。主評価は前者。後者は `/apply-template` の実出力を使って別smokeにした。どの差が失敗の原因かは未確定で、tokenizer metadata上書きや重み変換で都合よく修復していない。
3. 例：日本語配送分類が数字の列を返し、英語品番抽出が `item` でなく `string` をキーにし、数量を `integer` という文字列で返した。括弧の反復もある。失敗例を正解へ取り込まない。
4. 24件は容易な独自合成smoke。仮に成功しても、日本語の曖昧性・否定・欠損・複数商品・実務分布に対する有意な性能判断には不足する。今回はsmoke段階で不合格。

## 技術概要・最新同系列・ライセンス

[公式BitNet](https://github.com/microsoft/BitNet) はternary重み向けCPU推論engine。最新確認head `0b341e582afbf9e1011f24744b554c96a3477eb5`、公式gitlinkのllama.cpp `390c307752ab78fd8189f359d6954c9ba1be74af` を使用。後者は公式.gitmodulesが参照する isHuangXin/llama.cpp。任意の別forkを選んでいない。

[生成モデル](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T) は `04c3b9ad9361b824064a1f25ea60a8be9599b127` のまま。公式説明は2.4Bパラメータ、4T tokens、言語は英語。MicrosoftのBitNet検索APIでは新しいembedding 270M/0.6B、VibeVoice-ASR-BitNetも確認したが、生成課題の後継として置き換えない。検索した範囲で新しい同用途生成モデルは確認できなかった。

[公式GGUF](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T-gguf) revision `a1f2f1c765812aa8af3f6eda4a313707064bba15`、実取得 **1,187,801,280 bytes**、SHA256 `4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162` が一致。前調査のsafetensors 1,178,623,988 bytesとは配布形式が違う。RAMは上記実測で、ファイルサイズから推定していない。

[engine LICENSE](https://github.com/microsoft/BitNet/blob/0b341e582afbf9e1011f24744b554c96a3477eb5/LICENSE)、[model LICENSE](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T/blob/04c3b9ad9361b824064a1f25ea60a8be9599b127/LICENSE)、submodule LICENSEはMIT。実体を保存。全OS依存のライセンス監査は未実施。

## 方法・環境・資源

独自合成24件：日本語／英語×分類／抽出×各6件。正解は実行前に手書き固定、学習なし、モデル出力なし。input SHA256 `63bb56cc62dfa05b5367e8f3e5f8b25d6170e92926d52ff42c694db641b64afe`。seed42、temperature0、4threads＋batch4threads、parallel1、context2048、最大96生成tokens。JSON grammar強制なし、cache_prompt=false。各反復をseed+repeatでshuffle。server内部の初期化に加え明示warmup1件を除外。

CPU AMD EPYC 9V74、5vCPU表示、AVX2/AVX512あり。Docker cpus=4（cpu.max=400000/100000）、memory=memory-swap=8GiB、network none、nonroot1000:1000、cap-drop ALL、no-new-privileges、bind mountなし。公開source/model・評価コードと合成入力だけをtar転送した。root filesystemはimage標準writable layer、nonrootが/tmpで作業する構成。個人ファイル・認証は渡していない。

Docker28.4.0、clang18.1.3、CMake3.28.3、container Python3.12.3、全dpkg版・binary hash・CMakeCacheを保存。公式READMEのcodegen/CMake経路を直接使用し、既製GGUFを使うため変換用torch/Transformers等は入れない。CPUのみ、OpenMP OFF、server targetのみ。GPUに切り替えていない。公式推奨との差分と再現手順は `experiments/bitnet-cpu-20261014/reproduce.md`。

ビルドwall312.56秒、主推論600.58秒、追加smoke102.89秒、base image build約66秒。主要計算段階のwall合計は約18.1分。現金0円。重み実測bytes＋apt表示約162.6MB＋source再取得含む保守枠250MB＋base80MB＋metadata等10MBの合計 **約1.69GB**。wire全体を厳密計測した値ではないが、2GB上限に余裕を残した。source初回展開のproject内相対linkはdestination範囲をproject全体へ合わせ、標準tar data filterを維持して展開した。追加のモデル・有料API・pip依存・baseline downloadなし。

## 導入判断と次の検証

**現行組合せは保留。** 次は公式の動作確認済みengine/modelの対応版とtokenizer/template整合性を先に確認する。今回は2回目の環境修復、重み書換え、追加モデル取得、上限延長はしない。整合性を確認できた場合に、今回の固定集合を再実行し、別の未使用日本語集合・既存baselineで品質と有用throughputを比較する。

## 保存・チェック

独立branch `research/2026-10-14-bitnet-cpu`。main基点 `7460493cc3f7242748c95f711e71a59537ef34ea`。夕方branch `f197d9d181e59998cfb61040ed763d308e512fa5` のAGENTS.mdはmainと一致。SKU／朝夕自動研究を再開・変更せず、canonical1057→1072件stateに書込みなし。

採点テスト4件、入力hash、Python構文、shell構文、コード・文書範囲のgit diff --check成功。生ログ／CMakeCacheは原文のCR・末尾空白を保存したため、全ファイルのdiff --checkでは空白警告が出る。既存repo labテスト151件は135成功・15skip・1失敗（対象外のrobots-parser未導入）。修復対象外なので変更していない。実用機能としては不合格のためdraft PRなし、merge/deployなし。生出力と全summaryはbenchmarks/run-hfおよびrun-gguf-smokeに保存。
