# 2026-10-24 JST 朝の独立技術調査

## 結論

**A0 / B2。ReScraperはNLP清掃、Host-Aware Crawlingは既知ホストの深度配分として条件付き候補。候補実行0、現金0円、draft PRなし。** 新しい確認はReScraperの編集失敗fallback・入力除外・モデル配置。抽出結果を原文証拠として保存する用途にはそのまま導入しない。

## 概要・最新指示と保留

main `7460493cc3f7242748c95f711e71a59537ef34ea` から `research/2026-10-24-daily-tech` を作成。remote heads、open PR19/21/23/25/26/28・issue24、10/23朝 `95c4b5c` と夕 `3b295c8`、10/22朝、10/17停止記録、10/19 Moli/Lightpanda保留を先に確認した。研究remoteの報告87エントリ（同じ報告の重複を含む）を限定照合し、ReScraper・Host-Awareの名称/論文IDの一致なし。意味的重複の不存在を保証しない。[context](../../benchmarks/daily-tech-20261024/context.json)。

AGENTS.md、checkoutのbot-blocking-scenarios、夕方branchのresearch-repro-radar SKILL/profileを読解。今回は朝の通常一次資料調査で、bot診断・jse.lab・夕方radarループは起動しない。夕方skillのstate更新や通知手順は適用しない。AGENTSの指定モデルgpt-6-lunaへ候補調査を委譲（ホスト指定でありprovider実モデルIDの独立検証なし）。古い一般紹介・Docling再紹介は新発見として採用しなかった。

Moliのlibfontconfig不足・修復1回消費と追加修復保留、既存隔離停止、PR19検索追加中止を維持。SKU担当作業、夕方未完了6,606件とstate4ファイルを変更していない。隔離の重複質問、認証操作、新規キー、別環境への迂回なし。

## 方法・入力・保存

一次論文・著者repo/HFを読解し、自作Python3.12標準ライブラリの[取得・AST監査](../../experiments/daily-tech-20261024/audit.py)を実行。候補のソースは文字列/ASTとして読むだけでimportしない。固定revision・URL・時刻・hash・bytes・時間・errorは[manifest](../../benchmarks/daily-tech-20261024/manifest.json)、結果は[audit](../../benchmarks/daily-tech-20261024/audit.json)。モデル重み、上流データ、依存物は取得しない。日付は依頼のJST朝枠、実取得は台帳のUTC。

## B — ReScraper: 抽出・清掃を統合する0.6B

一次資料：[論文v1](https://arxiv.org/html/2609.34287v1)、[公式コード固定版](https://github.com/cxcscmu/ReScraper/tree/b240a0da39a7287a41f81e82bddccd868ef4386d)、[公式モデル固定版](https://huggingface.co/cx-cmu/ReScraper/tree/a15cfd6d4db69640985a8c55844aa2e27b615796)。論文9/28、repo最終push10/1、モデル最終更新9/28。直近24時間の新公開ではなく未報告の配布監査。取得時6 stars/1 fork、CMU著者配布。独立採用・性能再現は未確認。

目的はWeb本文の抽出後にkeep/edit/delete/rewriteを選び、事前学習データへ清掃すること。Qwen3-0.6Bが短い操作列を生成しexecutorが適用する。著者のDCLM Core相対3.8–4.7%改善は学習後のモデル評価で、検索網羅性や当方実測ではない。NLP清掃用途をBとし、検索①②③の達成とは判定しない。rewriteを含むので、原文を忠実に保存する抽出器との用途差がある。

### 静的確認・想定との差

|確認対象|観測|導入への意味|
|---|---|---|
|配布safetensors|1,192,135,096 bytes、Qwen3ForCausalLM/BF16|重みだけは2GB未満。依存総量/RAM適合は未確認|
|モデル配置|root configなし、`models/ReScraper/config.json`あり|モデルカードのroot ID指定例と配布配置に差。実ロード失敗は未確認|
|`_apply_edit_body`末尾AST|`return (rest.strip(), False)`|命令として全行を読めないとpayload本文へfallback。命令文字列を原文と誤認し得る|
|`OPS_TOLERANT`|既定off|任意の許容モードがあるが今回は変更も実行もしない|
|pool入力条件|空`main_html`をskip|生HTMLだけ無条件に通す汎用入口ではない。Dripper由来の入力前提が残る|
|prompt上限|32,768−3,072＝29,696 token|超過ページはskip。網羅性評価では除外を数える必要|

根拠：[executor](https://github.com/cxcscmu/ReScraper/blob/b240a0da39a7287a41f81e82bddccd868ef4386d/lib/rescraper_ops.py)、[pool推論](https://github.com/cxcscmu/ReScraper/blob/b240a0da39a7287a41f81e82bddccd868ef4386d/inference/infer_pool.py)、[配布カード](https://huggingface.co/cx-cmu/ReScraper/blob/a15cfd6d4db69640985a8c55844aa2e27b615796/README.md)。fallbackは静的経路確認で、発生率・品質・修正効果の再現ではない。

導入はPython3.12、torch2.9.0+cu128、Transformers4.57.6、vLLM0.11.1、Dripper/MinerU-HTML renderer等。[公式設定](https://github.com/cxcscmu/ReScraper/blob/b240a0da39a7287a41f81e82bddccd868ef4386d/inference/README.md)の224 H200・493.9 GPU-hはpool規模の著者計測で、単体推論最低要件ではない。CPU経路、8GB RAM、1万件時間、日本語、無料環境での実用性は未確認。学習再現は今回範囲外。

コードとモデルカードはApache-2.0。条件遵守の商用候補だが、上流モデル・ツール・元Web文書は別条件。原文の再配布やrewriteの事実忠実性まで許諾・保証するものではない。次に許可条件が揃う場合は公開/合成の汚いHTMLで本文欠落・rewrite率・不正命令流出・skip率・cold/warm時間とRAMを測り、原本と派生清掃物を別保存して比較する。現状は機能PRにしない。

## B — Host-Aware Web Crawling: 既知ホスト内の配分

[一次論文v1](https://arxiv.org/html/2610.13321v1)、10/9公開、Common Crawl所属著者。3ページ程度のサンプルからホスト内の品質密度を推定し、深掘りする配分を決める。ページごとの品質分類を全件に掛ける負担を減らす仮説があり、前処理/取得の効率評価候補。ただし以下を区別する。

- 著者の最大7.8倍は既取得ページ上での高品質ページ/fetchのシミュレーション。新規ホスト発見・live fetch時間・日本語検索coverageは未測定。
- 未取得高品質URL約58Mは、取得済みと未取得の品質密度が同じという仮定による推計。実回収数ではない。
- 既知ホストと分類器が前提で、アーカイブ不要の未知サイト発見の実証ではない。サンプル取得の費用と低密度ホスト経由の新規リンク発見を別に測る必要がある。

専用公開実装、stars、導入手順、hardware最小値、コードの商用licenseは未確認。論文はCC BY4.0だが未確認実装の許諾とはしない。公共研究機関の一次報告であり、本番導入communityは未確認。現段階は検索①②③の具体的効果を測る設計候補に留め、既存クローラや停止済み実験を変更しない。実測なし、PRなし。

## 予備読解・比較

- **C（今回の追加実装）— [VDR teacher signals](https://arxiv.org/abs/2610.09177)**: 10/6公開、[著者repo](https://github.com/elastic/vdr-teacher-signals)はMIT。主眼は教師による学習信号と検索順位の改善で、前処理短縮や新規サイト不足補完の直接根拠を確認できない。追加学習は今回範囲外。OCR代替による時間短縮を推測してPR化しない。最小hardware・商用依存条件は未確認。
- **B（読解のみ）— [Pixels to Structure](https://arxiv.org/html/2610.11818v1)**: 10/8公開。小型VLMのOCR→JSONを複数の文化財資料で比較し、NLP抽出として関係する。[公式コード](https://github.com/uddipan77/Analysis-of-Lightweight-Vision-Language-Models-for-Document-OCR-and-Structured-Output-Generation)は4/25最終push、2 stars/0 forks、Apache-2.0。用途別学習・評価の研究で、即利用可能な日本語checkpoint・CPU導入量/RAM・元資料商用条件は未確認。学習へ拡大しない。
- **B（読解のみ）— [Smart Content Ingestion](https://arxiv.org/html/2610.07091v1)**: 10/5公開。ページ別OCR分岐と構造保存の設計は前処理に関係するが、公開実装・stars・license・最小hardwareを確認できず、導入候補への昇格なし。

詳細候補2件＋予備一次論文3件。旧年のThinkQE・QE失敗分析と既報Doclingは、新根拠不足として最終候補数から除外。

## 検証・問題・導入判断・次

資料取得16/16成功、985,020 bytes、取得合計5.044秒。静的監査は同じ取得物からoffline再計算成功。候補推論0、依存導入0、重みDL0、GPU0、候補修復0、現金0円。CPU速度・RSS・品質の実測はなく、著者値を当方実測へ転記していない。

継続ブロッカー: action=外部候補推論、target=ReScraper PoC、reason=資格情報/個人ファイルからの承認済み隔離が未確認、status=pending/not attempted。既存の停止・修復上限を保ち、新しいauto-review拒否はなし。一次資料読解・配布監査・独立branch保存は実施。Kaggleの無料残量/機種/非公開設定は未確認のまま未使用。計算不足も発生していない。

関連repoテストは151件・12.674秒、失敗1/skip15。既存bot diagnosticsの`robots-parser`未導入による失敗で修復せず、[ログ](../../benchmarks/daily-tech-20261024/lab-tests.log)と[検証結果](../../benchmarks/daily-tech-20261024/validation.json)を保存した。差分空白検査は成功。保存物だけをcommit/push、remote SHAと当該CIのURL/終端を最終返信に記録する。CI成功は候補再現成功ではない。merge/deploy、外部メール/SMTP、cron変更なし。
