# 2026-11-03 JST 朝：相手適応型戦術 SAGE の導入条件

## 判断

新たな設計候補として **SAGE と DeepSeek Search/Fetch API を B（条件付き）** とした。いずれも本日公開の更新ではなく、本調査で新たに確認した設計候補である。A（今試す価値あり）は0件。性能試験、モデル取得、機能追加は行わなかった。保留試験や既済み比較を再開して試験数を埋めていない。

## ゲーム戦術：SAGE

一次資料は [論文v2](https://arxiv.org/abs/2609.34342v2) と [公式コード固定版](https://github.com/chenzhwsysu57/SAGE/tree/5f5bbb49545756b00b0b1202f402f7d53cf0e3b2)。論文初版2026-09-28、改訂2026-10-14。コードheadは `5f5bbb49545756b00b0b1202f402f7d53cf0e3b2`（10/14）、GitHub releasesは空で、配布版番号を創作しない。取得時0 stars/0 forks。外部利用や独立再現が確立したとは言えない。コードはMITで商用利用を一律禁止しないが、著作権・許諾表示等の条件を維持する。API提供者の条件や依存物の条件は別である。

仕組みは、均衡戦略を基準にし、相手の行動傾向に対する確率的な見立てで戦略を調整し、関連する過去の局面から反実仮想的な仮説を作って判断を修正するもの。固定的な性格ラベルから行動を返す条件分岐とは異なる設計である。ただし論文の対象は Leduc Hold'em / Liar's Dice / Goofspiel の3ゲームに限る。作者の報酬・token削減値を今回の実測値や現行ゲームでの優位として転記しない。

導入手順は [README固定版](https://github.com/chenzhwsysu57/SAGE/blob/5f5bbb49545756b00b0b1202f402f7d53cf0e3b2/README.md) に Python 3.11 の環境と `pip install -e ".[dev]"`、OpenAI-compatible APIの設定が記載されている。こちらの規約はPython 3.12以上であり適合性の実測はない。新しいモデル重みの配布ではなく、`--model` 等で別途バックエンドを選ぶ。[現行公式モデル一覧](https://api-docs.deepseek.com/quick_start/pricing/)は `deepseek-flash` = V4.1-Flash-1017、上位系統 `deepseek-v4-pro` = V4-Pro-0813。旧 `deepseek-v4-flash` は現行Flashへ転送されるaliasで、固定した論文実験モデルを保証しない。SAGEのmemory refresh既定値もこの旧aliasである。公開価格はあるが、アカウント残量・適用条件は未確認で、暗黙に認証や有料APIを使わない。GPU必須の明記は確認していないが、基準戦略生成・推論先を含む最低RAM/VRAM、CPU時間、取得量は未計測。

**training-free は追加準備が不要という意味ではない。** READMEは Leduc CFR+、small Liar's Dice CFR+、Goofspiel MCCFR の基準戦略を先に訓練する手順を要求する。任意ゲームへそのまま載せられる既製ポリシーでもない。0円・追加学習なしの今回条件では、公式ベンチマークの起動は選ばない。

検証価値は、性格・戦況・履歴から「誘いを維持／変更／断念」を選ぶ際に、相手推定と過去の失敗の反映を分離する設計にある。現行ゲームへの転用は仮説であり実装済み能力とは扱わない。次へ進む条件は、費用・モデルID・利用条件が確認できる推論経路と、対象ゲームで追加学習を要しない基準戦略が存在すること。その場合も複数許容解、悪手率、判断失効、レイテンシを独立入力で評価する。OpenDecider nanoやルールの既済み48出力を再実行しない。

固定コードでは Goofspiel の直近10観測の尤度とEMA（既定alpha 0.85）によるbelief更新、類似局面top3とgradient gateを使うcounterfactual memoryを確認した。判断時以外にも記憶生成でAPIが呼ばれ得るため、行動1回分だけで費用・遅延を見積もれない。Goofspiel用コードがimportするOpenSpielは通常のpyproject依存に含まれず、導入成立は未検証。[証拠と固定版URL](../../benchmarks/daily-tech-20261103/research-candidates.json)。

## 検索・前処理と NLP 別枠

[DeepSeek Search/Fetch](https://api-docs.deepseek.com/news/news261017/) は10/17告知のホスト型API。`POST /v1/tools/search` と `/v1/tools/fetch` が実在し、作者は独自検索エンジンと説明している。クエリからURLを発見し、任意URLをMarkdown取得する別設計なので、reranker単独ではない。ローカルarchiveを事前構築せず候補URLを得る入口としてB。ただし日本語の独自URL率・有用ドメイン追加・出典到達率は未測定で、網羅性改善を認定しない。

[公式仕様](https://api-docs.deepseek.com/guides/search/) はクエリ1〜128 Unicode文字、1〜50結果、API keyを要求。モデル重みの導入やローカルGPUは不要な経路だが、ホスト型サービスのためOSS license/starsは該当しない。商用契約条件とアカウント利用可否は未確認。Search/Fetch各1,000成功要求あたり$0.75で、Search1回＋20 URLのFetchは$0.01575。**server-side toolの無料期間も10/31 16:00 UTCに終了する告知で、11/3 JSTの今回を無料と解釈しない。** 0円条件のため利用APIは呼ばず仕様のみ確認した。費用と利用条件が確認できる場合に限り、固定日本語クエリで有用な新規URL数と成功取得あたり費用を評価する価値がある。PR19への組込みなし。

[Laya 0.4.21](https://github.com/NandhaKishorM/laya/releases/tag/v0.4.21)、[Scrapling 0.5](https://github.com/D4Vinci/Scrapling/releases/tag/v0.5)、[Crawlee Python 1.11.0](https://github.com/apify/crawlee-python/releases/tag/v1.11.0)、[Crawl4AI 0.9.5](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.5)、[Trafilatura 2.3.1](https://github.com/adbar/trafilatura/releases/tag/v2.3.1)、[browser-use 0.13.14](https://github.com/browser-use/browser-use/releases/tag/0.13.14) はlatest APIで前回と同じ。配備保守を研究目的に置き換えず、再比較しない。

[Common Crawl catalog](https://index.commoncrawl.org/collinfo.json)は `CC-MAIN-2026-43` のままで、レスポンスhashも前回と一致。索引追加・archive不要サイト発見・日本Web網羅性向上を確認したとは言えない。Search/Fetchの実用採用は保留。reranking単独やPR19への追加はなし。

NLP別枠では [Laya multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) の重みSHA `1720e3e3357cfe1e281542e223f8273b0890ca34`、10/3更新のまま。THX/Laya/ModernBERTの再比較なし。SAGEのゲーム戦術設計を日本語分類モデルの能力更新と混同しない。

## 成果物・制限・検証

remote main `4d2984a454fc766137784fd6a51579e10b6fafd6`、朝最新 `06be2fcbec35dd44db8138a487599b5109e61ee1`、夕canonical `1321d3f6637684c9ccdf4c7e02f083d6762bd969` を確認。PR19/21/23/25/26/28のheadは前日の保存値と機械照合して全6件一致。夕pending9,549は前回記録値として維持し、再計算・変更なし。新成果物として試すべき公開差分はこの照合範囲にない。

AGENTSとcloud-runtime/networkingを読み、関連skillの適用範囲を確認。規約指定のLunaへ一次情報調査を委譲し親側で資料照合。ツールへモデルIDを指定したが、サービス内部の実モデルIDは独立検証していない。grounding探索、bot比較、Kaggleは実施しないため、それらの実行手順は起動しない。

Core/PageSift/Moli/Lightpanda/Squeezの修復上限、TinyJev追加取得保留を維持。共通preflightの未公開修正 `230446f60fbc23f619884ab15c38ee0d034dc3cd` はコピー・公開・定期統合しない。夕launch UNKNOWN（10/30、11/1、11/2）は不確定のまま再起動なし。今回の朝環境が起動した事実から、夕の起動失敗原因を特定したとは言えない。

一次観測は [benchmarks/daily-tech-20261103](../../benchmarks/daily-tech-20261103/) に日時・URL・固定SHAとともに保存。数値ベンチは未実施。コード追加がないため実験基盤テストの実行や既存依存不足の修復はしない。JSON整合性、PR head比較、変更範囲、`git diff --check` を確認する。通常研究成果だけを独立朝branchへ保存し、remote SHA・exact-SHA CIは保存後に確認する。実用機能がないためdraft PRなし。merge/deploy、email、定期設定、SKU変更なし。

追加費用0円、候補実行0、候補CPU0分、重み・依存download0、GPU0、Kaggle submit0、修復0。HTTPの資料取得とGit取得はあり、通信総量は未計測。30分run枠内。候補実行を選定していないため、候補download前のDocker実UID検証や隔離実行には進まなかった。
