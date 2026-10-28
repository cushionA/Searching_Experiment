# 2026-10-29 JST 朝：Laya現行版を日本語の固定入力で実比較

## 結論・概要

**Laya multilingual 0.4.17は、今回の日本語開催状況分類・明示ゲーム規則の置換にはC（不要）。他言語・別用途の候補評価はB（条件付き）のまま。** 新規の実推論32入力で、日本語NLP12/24、ゲーム4/8。前夕保存の日本語ModernBERT23/24・5/8、規則14/24・8/8と同じ入力・正解で比較した。軽量でCPU実行可能だったが、この用途で品質改善はなかった。

**実試行1モデル・1回成功・32入力、修復0。** 既報の中国語公開応答の再集計でも、日本語ModernBERTの再実行でもない。16場面の32表現であり独立32課題ではない。一般的なモデル優劣は主張しない。検索実用機能・draft PRは作らず、merge/deployなし。

## 進捗と対象選択

main `4d2984a454fc766137784fd6a51579e10b6fafd6`、朝 `fac824ba63be26ea07f6c3fd31b24c8ea605aeea`、夕canonical `674113afd1576a6244e9103387401cd2fc0a2762`、TinyJev `ca7b14a3a1c14fb0dec20d94d82624b313194979` をremote照合。研究branchと直近朝夕レポート、open PR19/21/23/25/26/28を確認。AGENTS.mdと関連skillの適用条件を確認し、Cloud runtime skillのネットワーク・Docker指示を使用。通常の一次情報確認とモデル比較であり、jse.lab grounding・bot scenario・Kaggle操作はない。

前朝はLaya新規推論0、前夕はLaya未試行だった。そこで「既存の日本語モデルに対する、別の小型判断モデルの固定入力比較」を選んだ。TinyJevの48問や停止中jobは触らず、夕方のstate/pending9481も変更なし。Moli/Lightpanda個別承認待ち、PR19検索追加停止を維持。SKU・通常CI保守・メール・SMTP・cron操作なし。mainから独立した `research/2026-10-29-morning` に保存。

## 一次情報・版・用途・利用条件

[公式Laya repo](https://github.com/nandhakishorm/laya)、[release v0.4.17](https://github.com/nandhakishorm/laya/releases/tag/v0.4.17)は10/28 10:04 UTC、PyPI wheelは10:13 UTC公開。前朝0.4.15の後に0.4.16/17が同日公開されていた。現在34,558 stars、3,098 forks、9/18作成・10/28 push。contributors先頭100件を取得したが総人数とはしない。版0.4.17はhook帰属、言語振分け、SDK移植、応答件数検査などの更新で、重みの新世代ではない。C++ skeletonを完成した推論実装とは扱わない。

正確なモデルは [`convaiinnovations/laya-multilingual`](https://huggingface.co/convaiinnovations/laya-multilingual)、revision `1720e3e3357cfe1e281542e223f8273b0890ca34`、約322M、mmBERT-base由来。10/3更新で前朝と同じ。作者一覧で別の新しいLaya後継は確認できず。英語root421M、typed-decisions421Mを同じ小モデルとして混ぜない。対照は `argos1111/modernbert-ja-310m-jev` revision `07cda23579443e7a33c0f474114279fa032340d6`、作者一覧に同名の新後継なし。別のvision3Bはこの310M判定モデルの更新とはしない。

Layaは生成せず、状態＋質問＋候補からchoice等を返す。本文の否定・訂正・実施可否を判定するNLP部品として試す価値があり、自由文抽出・OCR・検索coverageを実証するものではない。コード/モデルカードはApache-2.0、商用禁止表記ではなく通知等を保持。対照の重みCC BY-SA4.0の条件は前夕報告を参照。今回は商用導入判断なし。

Python>=3.10、CPU経路、GPU不要。固定wheel `laya==0.4.17`、Torch2.7.1+cpu、Transformers5.17.0、NumPy2.2.6を使用。依存hash lockを保存。モデル必要5ファイル678,201,614 bytes、重み643,835,514 bytes。LFS SHA256と取得本体一致、標準ModernBERT/AutoModelとsafetensors、モデルconfigにauto_mapなし。公式wheelの実行箇所を読んでから隔離実行した。資料・wheel掲載だけを安全性保証とはしない。3モデルpreloadは行わない。

## 方法・期待差

[再現コード](../../experiments/laya-ja-comparison/README.md)、[全数値](../../benchmarks/daily-tech-20261029/scores.json)、[入力・gold hash](../../benchmarks/daily-tech-20261029/fixture-manifest.json)、[一次資料manifest](../../benchmarks/daily-tech-20261029/source-manifest.json)。

前夕の入力・gold・baseline・日本語モデル予測をcommit指定でコピーしSHA確認。入力・正解・採点コードをLaya実行前に固定。モデル側へgoldや旧予測を渡していない。現行SDKのnative choiceに同じ質問・候補記述・候補順を渡した。`Agent('/work/model', device='cpu', fast=False, compile=False)`、`lang='ja', max_len=512`。単一question/入力、float32、SDPA、2 Torch threads。全32件でtruncated=false。

NLPは開催/中止/未定の3択12場面×clean/noisy。ゲームは扉が開けば進む・閉じて鍵があれば使う・無ければ待つという明示規則4状態×自然文/JSON。ゲームAIは対戦成績ではなく規則解釈。期待した差は「同じ条件で単純規則より誤分類を減らせるか、日本語モデルの品質に近づけるか」。結果はどちらも支持しなかった。出力に合わせたprompt/gold/閾値調整なし、追加学習なし。

AMD EPYC9V74、Python3.12、Docker2CPU/8GiB、nonroot、network none、read-only、cap-drop ALL、no-new-privileges。専用公開ファイルをstdin tarで渡し、通常workspace・認証・個人ファイルのmountなし。既知のTMPDIR=/work/pip-tmpを初回から使用。候補exit0、コンテナ終了確認。

## 実測結果と比較

|区分|Laya今回|日本語ModernBERT前夕保存|規則前夕保存|Laya平均秒/入力|
|---|---:|---:|---:|---:|
|NLP全体|12/24|23/24|14/24|0.2937|
|NLP clean|8/12|12/12|10/12|0.2701|
|NLP noisy|4/12|11/12|4/12|0.3172|
|ゲーム全体|4/8|5/8|8/8|0.2777|
|ゲーム自然文|1/4|3/4|4/4|0.2746|
|ゲームJSON|3/4|2/4|4/4|0.2808|

Laya NLP macroF1=0.4568、ゲーム0.4905。import2.545秒、load4.948秒、推論全体9.271秒、プロセスCPU26.422秒、peak RSS2,356,024KiB（約2.25GiB）。install込みコンテナ50.043秒。候補CPU20分・RAM8GiB内。前夕ModernBERTはRSS約1.45GiB、NLP平均0.9323秒だったが、**前夕はIntel8573C・eager attention、今回はAMD・SDPA**。同じPython/Torch/Transformers・2threadsでも機材/経路が異なり、速度倍率・省メモリ優位・金額差は主張しない。今回は依存install中に既存labテストも実行し、推論のstderrを最後にまとめて保存するため厳密な重複区間は未計測。latencyは単発環境の記述値に限定する。

noisyで正答が8→4へ下がり、関連記事語などの追加に弱かった。誤答ID・生確率・SDKusageを全保存。最大確率やconfidenceを校正済み正解率として採用しない。小さな合成集合、旧fixtureを既に観測した比較、独立holdoutなし。日本語モデルの学習履歴差や候補書式差もあり、広い能力差の因果説明はできない。

## その他更新：coverage目的の候補だけを残す

[PageSift0.80.10](https://github.com/YOLOW92/pagesift/releases/tag/v0.80.10)（10/28）は、0.73.1既報監査以後の有意味な更新。adaptive crawlの停止・英語section到達・about配下の関連ページ・teaser本文抽出を変更。既知site内の取りこぼし補完と前処理品質の**B候補**であり、未知domain発見の実証ではない。AGPL-3.0-only/別commercial license、今回install/実測0、現版総依存・RAM・現starsは未確認。作者の297共通crawl比較はページ数も3,223→16,351へ増えており、同予算coverage改善とは言えない。robots無視の作者条件を当方へ持ち込まない。既定cap100→500の変更は既存許可予算の拡大根拠にならない。次に試すなら明示した同一cap・同じ許可対象でmissing-page回収と本文欠落を測る。

Playwright1.64.1、Crawlee4.0.0-rc.1（stable3.18.2）、Crawl4AI0.9.5、Trafilatura2.3.1、PaddleOCR3.7.0、Browsertrix1.26.3の一次releaseも確認。新規PoCを割当てず、OCR/vision・archiveを件数埋めで再試験しない。Ollaya0.18.0にはdecisionモデル追加があるが、wrapperの追加を全モデルの新規重み公開とは数えない。今回は正確な小型Layaを実試験する方を優先した。

## 検証・問題・採用・次

採点時に全32IDの欠落/重複、choice集合、有限確率と確率和、非負時間、fixture hashを検査し成功。新規基盤機能なし。既存lab151件はfailure1/skip15（既知のrobots-parser不足）、ログ保存。通常CI修復はしない。候補install・推論の失敗0/修復0、host上の証拠copyでPath連結とLICENSE場所の2エラーを修正しただけで、候補を再試行していない。

支出0、学習0、Kaggle0。モデル＋全wheel約0.908GB、重複wheel取得の余裕・Docker非圧縮size・今回取得以外も含むgit全体・資料余裕20MBまで課金した保守的DL上界1.606GB（[台帳](../../benchmarks/daily-tech-20261029/budget.json)）。ネットワークwire実測値ではなく、過小計上を避けた上界。

Aなし。本用途のLaya置換はC、別言語や用途のBは保持。日本語NLPを進めるなら前夕日本語モデルを強い対照に、未見の独立データを先に固定する。Layaが高速そうという理由だけで品質低下を許容しない。TinyJev cumulative3GB承認はpendingのまま、再取得・再開なし。Moli/Lightpandaも再開なし。今回は新たな承認blockerなし。remote SHAとexact-commit CIをpush後に確認して返答する。
