# 2026-10-14 JST 夕方 Research Repro Radar

**1,420件収集・繰越込み1,572件全採点、review20＋trending補足4件読解。候補推論0、未完了1,072件（前回1,057件を全保持＋新規15件）。**

## 詳細推薦（読解確認・導入済みではない）

### A: BitNet.cpp — 次に試す価値あり

[原論文](https://arxiv.org/abs/2502.11880) / [公式コード](https://github.com/microsoft/BitNet) / [2Bモデル](https://huggingface.co/microsoft/BitNet-b1.58-2B-4T)。三値重みをCPUで効率よく扱う実装。HF trending 5位、GitHub 41,142 stars、2026-07-27更新を確認。異なるplatformの注目/利用指標だが利用者の重複・相関はあり、独立した性能検証を意味しない。一次artifactと「小型CPUで短い分類/構造出力が実用速度か」という短期仮説があり、深掘り候補。

論文公開は2025年で、現在の注目度に基づく掘り起こし。著者の最大6.25倍はfull-precision比較の著者値。今回の品質・時間・RAMは未測定。2Bモデル実在revision `04c3b9ad9361b824064a1f25ea60a8be9599b127`、重み1,178,623,988 bytes、MITを一次metadataで確認（取得していない）。repoはMIT、Clang>=18/CMake>=3.22/Python>=3.10が必要。後続のembedding 270M/0.6Bも実在しGGUFは367,487,040/427,935,008 bytesだが、生成2Bの代替ではない。大量で短い出力に向くか、独立入力で品質とCPU throughputを比較するのが次。

### B: Unlimited OCR Works — 原理を深掘り、実行は上限外

[原論文](https://arxiv.org/abs/2606.23050) / [公式コード](https://github.com/baidu/Unlimited-OCR) / [モデル](https://huggingface.co/baidu/Unlimited-OCR)。長い複数ページ文書でdecoder KV cacheを一定にする設計。HF trending 9位、GitHub 27,330 stars、2026-10-12更新を確認。独立性に限界はあるが異種の話題性指標と一次artifactがある。ページを増やしてもmemoryを抑えながら抽出の完全性を保てるか、という検証仮説が具体的。

数十ページを単一passで処理するという主張は著者値。朝のRapidOCR静的確認とは別の長文脈OCR課題で、朝の推論結果として流用しない。repo/モデルmetadataはMIT、Python3.12/CUDA12.9/NVIDIA GPUの導入例がある。実在revision `8249d20b2822a7d8a4c22c8d8c9069b5ac36bda3` の単一重みは **6,672,547,120 bytes**。同サイズ別名2つを重複合算せずともDL2GB上限を超えるので取得もKaggle投入もしていない。軽量配布の有無は未確認。現在条件では導入保留。

## 未検証ダイジェスト（採点上位15、詳細推薦と分離）

1. [Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct) — 1.5Bコードモデルで関心一致。配布BF16は取得枠超過見込み、量子化版・後継の実適合未確認。
2. [A Survey on Inference Optimization Techniques for Mixture of Experts   Models](https://arxiv.org/abs/2412.14219) — MoE推論効率の俯瞰。個別CPU PoCと商用実装条件を選べておらず保留。
3. [Understanding and Improving Knowledge Distillation for   Quantization-Aware Training of Large Transformer Encoders](https://arxiv.org/abs/2211.11014) — encoder量子化と蒸留。teacher/student学習・データ準備が必要で今回未実行。
4. [I-ViT: Integer-only Quantization for Efficient Vision Transformer   Inference](https://arxiv.org/abs/2207.01405) — 整数専用ViTで軽量画像認識に一致。著者速度はGPU値、CPU/TVM環境未確認。
5. [Image Recognition with Online Lightweight Vision Transformer: A Survey](https://arxiv.org/abs/2505.03113) — 軽量ViT survey。HFのIBM/CrossViTリンクは別論文、公式実装扱いしない。
6. [Bekko Embedding: Parameter-Efficient Multilingual Retrieval with Ultra-Compact Encoders](https://arxiv.org/abs/2607.25180) — 超小型多言語encoder。公開ファイル取得まで、用途適合・CPU品質未測定。
7. [Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents](https://arxiv.org/abs/2604.04979) — toolログの逐語抽出でAI開発前処理に一致。150M版準備後Docker buildx書込失敗、推論0。
8. [SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents](https://arxiv.org/abs/2601.16746) — coding agentの0.6B文脈pruner。前日の判定成功とは別にCPU推論は未実施。
9. [What Survives When You Compress a Recursive Reasoner for the Edge?](https://arxiv.org/abs/2606.26488) — 再帰reasonerの小型化。パズル以外の用途・公開重み・商用条件未確認。
10. [ZeroQuant: Efficient and Affordable Post-Training Quantization for Large-Scale Transformers](https://arxiv.org/abs/2206.01861) — Transformer量子化。GPU向け評価からCPU適合を確認できず保留。
11. [ABQ-LLM: Arbitrary-Bit Quantized Inference Acceleration for Large   Language Models](https://arxiv.org/abs/2408.08554) — 任意bit量子化。CUDA kernel前提、今回のモデル・資源条件を確認できず未実行。
12. [Adaptive Block-Scaled Data Types](https://arxiv.org/abs/2603.28765) — ブロックごとのINT/FP切替。CUDA12.8+中心、CPU参照実装の実用速度未確認。
13. [BEAVER: A Training-Free Hierarchical Prompt Compression Method via Structure-Aware Page Selection](https://arxiv.org/abs/2603.19635) — 学習不要の長文ページ圧縮。0.6B導入例あり、モデル/依存総量とLICENSE実体未確認。
14. [DeepCode: Open Agentic Coding](https://arxiv.org/abs/2512.07921) — 論文からコードを作るagent、repo更新あり。モデル呼出し費用・最小構成未確認。
15. [Dual Precision Quantization for Efficient and Accurate Deep Neural   Networks Inference](https://arxiv.org/abs/2505.14638) — INT4/FP8の二重精度。CPU backend適合と導入物が未確認。

## 実行・比較・問題

Squeezの公開150M抽出モデル `KRLabsOrg/verbatim-rag-modern-bert-v2@6a967332efedfe5aca9b85b8310cc68d9ac6f881` を準備。重み598,439,784 bytes、SHA256 `3127f7ead12786b4ab740191105d8a8e0febe6f8109e479ea0c1e9238fd79398` を親側でも照合。公開合成toolログ10件（5 clean/noisy pairs、入力hash保存）を作成したが未実行。モデルカードはApache-2.0を示す一方metadataのlicense=null/モデルrepo LICENSEなしの不整合があり、商用再配布条件は未確定。

制限付き公式Python Docker helloはexit0（networkなし、nonroot、root read-only、cap-drop ALL、no-new-privileges、CPU2/RAM8GB）。その後の `docker build --file Dockerfile --tag evening-radar-squeez-poc:20261014 .` は0.4秒・exit1、`mkdir /home/agent/.docker/buildx: read-only file system` で停止。自動承認審査による拒否ではなく、実ファイルシステムの書込エラー。候補imageが存在しないことを親側でも確認。候補コード・torch/Transformers import・推論は0。別経路での回避、権限変更、CLI再試行、Kaggleへの障害回避は行わない。

BekkoとSqueezの公開ファイル取得は計766,610,989 bytes。Docker展開サイズ131MBを足した約898MBは参考値で実ネットワーク計測値ではない。Docker転送量と小さいmetadata通信の合計は未計測。候補依存pip取得0、追加現金0。時間・peak RAM・品質・1万件換算は未測定。著者F1/速度やimport成功を再現実績と呼ばない。API/GKE最新価格比較は実測条件がなく実施せず、古いprofile価格を現行価格として使っていない。

## 方法・確認範囲

最新Skill `cushionA/zabuton_skills@55ceed634506c5942d6a8e84c71d7e80acc103ff` と導入コピーが一致、profile v7の上限は維持。開始時からRADAR_LLM=native。指定ホストモデルはgpt-6-luna、サービス実IDはunknown。今回のprobeのrequest ID/hash/schema/期待値を検証しexit0後に探索開始。CLIは使っていない。20件＋補足4件と最終推薦もnative要求/応答をschema検証した。初回バッチの外側配列はdict契約に適合せず、candidate schemaを緩めずreviews objectへ1回修復。話題性信号を「分野2個」と誤解した回答は修正し、元回答だけで推薦を確定していない。

HF trending/new各50件＋関心別10検索の全応答を取得、HF一意1,220件。MDPI AI/Information RSS各100件を追加し一意1,420件。arXiv/公式GitHub/HFモデルmetadataを補助。workloadを固定検索語にせず、公開年で除外しない。APIはnext Linkを返さず、返却ページ全体は保存したが検索基盤の上限を越える網羅性は主張しない。OpenReviewの新規取得は今回なし。採点には再発見と重なるcarryを除く152件も投入し全1,572件、報告対象1,069件。scheduled=1は実測数ではない。

原文確認はreview20の一次landing/model card、19本のarXiv HTML、14repoのREADME/API、および補足trending4件。nativeへ渡したreview20の本文は7本が整形全文、12本は先頭70,000文字まで（全文を読んだとはしない）。原取得は保存。補足4件も各資料85,000文字上限。全1,420件の本文を精読したわけではない。目的/新規性/著者値/ライセンス/導入/資源/制約/次の確認はreviews JSONに記録、不明は未確認。schema成功は引用正確性を保証しないので逐語不一致のあるnative引用は監査フラグを残し、報告の根拠には原資料を照合して使う。

同系列確認ではSqueezの150M抽出版、BitNetのembedding派生、QServe→OmniServe/LServeの現行入口を照合。軽量ViT surveyのCrossViTリンクは別論文で、公式実装として扱わない。後継を確認できなかった候補は未確認。SKU GLiNERは既存の誤抽出保留を尊重し、PR26や朝stateに介入しない。

## state・導入判断・次

正規remote `research/2026-10-12-evening-radar@a4f02d484addcd13be05671cd02f06ad49f32a25` の4JSONを復元しhash一致を確認。Skill直下のrank_candidates/record_runとも `--state-dir state` を使用。試採点のstateは破棄し、最終採点前に正規snapshotを再復元したためcarry回数を重複加算しない。順位低下でrankerから落ちたYuE2/nanoMuseも、未完了なので元carry metadataのまま保持。

詳細推薦2件は読解の既報/freshnessだけ記録する。今回ユーザー指示は未実行を残すため、record_runのrecommended自動queue除去後に未完了snapshotから復元する。completed_idsは空、digestだけで既報化しない。候補の資源不足を理由とするnative rejectも完了扱いしない。Unlimited OCRはkeyword採点で対象外だったが、補足読解で価値を認めた未完了候補として新規保留1件を追加した。seen/mail/freshness/queueを正規evening-stateへ保存し、朝/weeklyと分離。未完了は1,072件。

次は通常の許可されたDocker build経路が利用可能になった実行でSqueez10件を開始し、重要行の欠落・誤抽出・clean/noisy差・wall time・peak RAMを測る。今回の書込拒否を回避する新経路は使わない。BitNetは小型生成で品質/コストを確認、Unlimited OCRは2GB内の軽量配布が確認できるまで実行保留。研究記録のみで実用機能PRは作らず、merge/deploy/メール/SMTP/cron変更なし。

検証: Skill unit tests 74件成功、native gate 3件成功、carry保持の重複・casefold・冪等性確認。remote保存/復元の結果は同run `remote-roundtrip.json` を参照。

成果: `experiments/research-repro-radar/runs/2026-10-14-evening/`（取得/採点/読解/台帳）、`benchmarks/research-repro-radar/2026-10-14-evening/`（数値/失敗）、`experiments/research-repro-radar/staging/squeez-poc/`（未実行コード/公開合成入力）。
