# 2026-10-07 新技術調査と日本語検索CPU PoC

## 結論

**mLateOnの最新公式INT8 ONNXは、短文の日本語検索をCPUで試す価値がある（調査A、導入B）。** 合成36文書・24質問ではBM25より良い順位を得て、FP32比でquery処理が2.48倍速かった。ただしFP32と首位が3/24件変化し、量子化による品質同等性は成立していない。日本語全般の性能や本番採用を保証する結果ではない。

main `3a197da67cac7103f659c3c00833b1142a64c8df` をfetchして着手。既存のブラウザ/4play修理とCAPTCHA評価の再実行を目的にせず、新しい検索PoCを追加した。400M索引には触れていない。GPU/Kaggle、課金サービス、新規credentialは不要だった。実験は約17GiB RAMのCPU環境で完走した。

## 技術概要・少数候補の判定

評価日はいずれも2026-10-07 UTC。A=すぐ試す価値あり、B=条件付き、C=現時点不要。stars/likes/downloadsは異なる指標であり、品質の根拠とはしない。APIの取得日時・値は[ソース記録](../../benchmarks/mlateon-20261007/sources.json)へ保存。

|候補・判定|目的と新規性、公開/更新|開発・コミュニティ|ライセンス・導入/資源・用途判断|
|---|---|---|---|
|mLateOn **A**（今回実行）|307M multilingual ColBERT、128次元/tokenのMaxSim。7/30公式発表、10/5 ONNX再exportとtokenizer/config修正。日本語は検索学習の9言語に含まれず、公式はzero-shot一般化を主張|HF 41 likes / 7,204 downloads。実装PyLate 897 stars / 95 forks / open issues+PR 31、GitHub pushed_at 7/23。モデル側は10/5まで更新|モデルApache-2.0、PyLate MIT（各条件の下で商用利用可能）。公式ONNX＋registry版runtimeで導入は低〜中難度。今回GPU不要、INT8 peak RSS 749MiB。短文検索の候補比較に適し、大規模索引は別検証|
|LightOnOCR-2-1B **B**（未実行）|文書画像を読み順付きテキストへ変換する1B VLM。1/19公式発表、HF最終更新7/8は評価記載更新。新しいOCR用途として価値はあるが、今日の新鮮さはmLateOn更新が上|HF 834 likes / 232,820 downloads、Community 47。公式GitHubコードリポジトリのstarsは未確認（推測URLは404）。論文v2は6/30|Apache-2.0。Transformers v5/PyTorch/画像処理が必要で中難度。BF16重みだけで約2.01GB（パラメータ数から計算）、実RAM/VRAM未測定。CPU経路は公式にあるが速度未確認。公式H100 80GB測定を最低VRAMと解釈しない。ja metadataだけで日本語品質は保証できず、スキャン正解データが必要|

一次情報: [mLateOn固定モデルカード](https://huggingface.co/lightonai/mLateOn/blob/bbd883ae1fadb563056ff73b1061ae8172a3fcda/README.md)、[7/30公式記事](https://huggingface.co/blog/lightonai/mdenseon-mlateon)、[10/5公式export/config](https://huggingface.co/lightonai/mLateOn/tree/bbd883ae1fadb563056ff73b1061ae8172a3fcda)、[PyLate](https://github.com/lightonai/pylate)、[OCR公式1/19記事](https://huggingface.co/blog/lightonai/lightonocr-2)、[OCR固定カード](https://huggingface.co/lightonai/LightOnOCR-2-1B/blob/c97bd377f04481830395218fa8951df9deaba756/README.md)、[OCR論文](https://arxiv.org/abs/2601.14251)。HF createdAt（mLateOn 6/22、OCR 1/16）はリポジトリ作成日であり発表日と区別した。

## 検証方法

- 入力は筆者作成の**合成日本語**。12話題×正解文書1＋近接した不正解2=36文書、各話題に語彙一致/言い換え各1=24質問。同一話題の質問は独立標本ではない。公開ベンチマークやユーザー実データではない。
- 各質問の正解は1文書だけの二値ラベル。他文書は関連話題でも具体的な要求への直接回答でなければ0。ラベルは推論前に固定しSHA256を保存。独立した第三者による注釈はなく、曖昧な質問への正解判断には著者バイアスがある。
- BM25文字2-gram、mLateOn FP32 MaxSim、公式INT8 MaxSim、同一FP32出力のmean-pooling ablationを比較。最後のものは学習済みdenseモデルとの比較ではない。
- 検証した期待差は「言い換えの順位改善」「INT8の容量/速度削減と順位変化」「複数ベクトルの索引負担」。パラメータやラベルを結果に合わせて調整していない。
- Python 3.12.14、ORT 1.30.0、tokenizers 0.23.2、NumPy 2.5.3。AMD EPYC 9V74、CPU affinity 5、ORT 2 threads/BLAS 1、batch=1。各arm独立プロセスを順次実行。query warmup一回、24×3=72 latency samples。順序ランダム化・独立run反復はない。
- 指標はnDCG@10、MRR@10、Hit@1。latencyはquery tokenize/encode＋全36文書score、文書エンコードは事前計算して別記録。RSSはLinuxプロセス最大値でロード中も含む。測定コード、正解、全順位、個別latencyを保存。

再現コマンド・測定境界: [実験README](../../experiments/mlateon-cpu/README.md)。入力hash `fbe5108dce22fc67e5b30039a3d3fdc86492e04d987793b8f51459fe8194935b`。モデルrevision `bbd883ae1fadb563056ff73b1061ae8172a3fcda`、各ファイルhashは[manifest](../../benchmarks/mlateon-20261007/model-manifest.json)。巨大な重みはGitに含めない。

## 結果

|方式|nDCG@10|MRR@10|Hit@1|query中央値 / p95 ms|peak RSS MiB|36文書encode秒|
|---|---:|---:|---:|---:|---:|---:|
|BM25文字2-gram|0.6924|0.6486|14/24|0.369 / 0.626|16.75|構築0.001秒|
|mLateOn FP32 MaxSim|0.8422|0.7907|16/24|42.973 / 56.207|2099.65|2.948|
|mLateOn INT8 MaxSim|0.8784|0.8393|18/24|17.342 / 20.403|748.72|1.041|
|FP32 mean ablation|0.7490|0.6834|14/24|42.186 / 52.183|2099.64|2.805|

|方式|語彙一致nDCG@10（12問）|言い換えnDCG@10（12問）|言い換えHit@1|
|---|---:|---:|---:|
|BM25|0.9692|0.4155|3/12|
|FP32 MaxSim|1.0000|0.6843|4/12|
|INT8 MaxSim|1.0000|0.7569|6/12|
|mean ablation|0.9526|0.5455|3/12|

FP32 ONNXは1,247,204,767 bytes、INT8は312,794,488 bytes（約74.9%減）。tokenizerは別に34.36MB必要。メモリ上の非圧縮文書ベクトルはFP32/INT8とも601,088 bytes、meanは18,432 bytes。**重みのINT8化は文書索引をINT8化しない**。これらはarray payloadのみで索引メタデータを含まない。BM25のJSON換算22,451 bytesとは同一形式の容量比較ではない。

FP32/INT8の首位一致率は87.5%（21/24）。score絶対差は最大0.30088、平均0.09036。`q04-paraphrase`、`q07-paraphrase`、`q11-paraphrase`で首位が変わった。INT8のnDCG増加はこの小集合の観測に限り、量子化が一般に精度を上げる根拠にはならない。

保存済みスコアでBM25 top10のみを再ランキングすると、候補正解包含率は20/24=83.3%。FP32 nDCGは0.7617、INT8は0.7789で、全走査の値に届かない。候補にない正解は再ランキングでは回復できない。このカスケードのlatencyは未測定。

生値: [BM25](../../benchmarks/mlateon-20261007/bm25.json)、[FP32](../../benchmarks/mlateon-20261007/fp32.json)、[INT8](../../benchmarks/mlateon-20261007/int8.json)、[mean](../../benchmarks/mlateon-20261007/mean-ablation.json)、[再集計/再ランキング](../../benchmarks/mlateon-20261007/summary.json)。4armの計測内elapsed合計は約21.6秒で、依存導入・ダウンロード・hash計算は含まない。

## 既存比較・問題

既存のCAPTCHA CRNNは文書検索/OCRの公平な比較対象ではない。今回のBM25は固定した簡易baselineで、形態素解析BM25やチューニング済み検索サービスの強さを代表しない。既存400M索引・サービス精度/性能との差も未測定。

INT8でも「紙を撮っただけの文書をコピーできる文章にしたい」はOCR正解を首位にできず、「入力上限に入りきらない資料の後ろの情報も探せるようにしたい」は分割案より切捨てへの注意文を首位に置いた。言い換えの正解率は50%にとどまり、関連話題と解決策の区別に失敗する例が残る。

10–44 tokensの短文だけで、8192 tokens・長文・padding・batch・大規模ANN・別CPUの性能は未検証。公式ONNXを採用しtokenizer契約/出力normを確認したが、PyLate/PyTorchと独立に数値一致を再証明してはいない。CPU共有環境の単一runであり、小さな速度差の有意性は主張しない。

モデル実行エラーはなし。ORTのtelemetry ID保存警告が出たが推論は完走。既存labテスト初回はrobots-parser未導入で1件失敗（151件、skip15）。CIに記載されたregistry依存robots-parser@3.0.1を導入後、151件が失敗なし（skip15）で完了した。新規の採点/markerテスト5件も成功。npm初回は既定cacheディレクトリ作成に失敗し、cacheを/tmpへ変更。コードの保守修正はしていない。404になった推測OCR GitHub URLとwebツールで読めなかったAPIは未確認として区別し、HF/GitHub公式APIはPython read-only取得で補完した。

## 導入判断・次

- **A（試す）**: 公開/合成の日本語短文に対するmLateOn INT8 CPU比較。専用branchのPoCとして提案する。
- **B（導入は条件付き）**: 検索候補の再ランキング。独立注釈の実用クエリ、形態素BM25、候補recall、FP32との順位許容差、索引サイズを追加評価してから判断する。
- **B（別機会）**: LightOnOCR-2。日本語スキャンはTesseract fast jpnをbaseline、文字層のあるdigital PDFはpdftotextを別評価する。CERと読み順/表構造、処理時間・RAMを測る。CAPTCHA結果から性能を転用しない。
- **C（現時点不要）**: 今日の小集合だけで400M索引の全量再エンコード、既存サービス置換、GPU追加導入を行うこと。

今日は候補2件の評価と再現可能PoC一つで終了する。研究のmergeは未承認。後続の保守やCI修理は本タスクに広げない。
