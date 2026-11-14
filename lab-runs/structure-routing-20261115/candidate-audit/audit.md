# 候補監査 (2026-11-15)

## 先に見る結論

- **pinchtab-webgraph** は、構造化されたナビゲーション状態グラフを作り、その後はオフラインで経路検索できる。ただし基本は広く画面を探索してグラフを作る方式で、C1失敗後に限った軽量 fallback とは用途がずれる。Windows上のPinchTab CLIは動き、localhost fixtureから1状態とリンクを採取した。2状態目への遷移でmaterializeに失敗し、クリック経路までは実証できていない。
- **MiniLM-L6-dmr** は一般文類似度だけのモデルではない。WebLINX公式説明では、タスク/行動履歴をもとにDOM要素ごとの関連度を付け、候補要素を絞るDense Markup Ranking (DMR) 用途。C1のクリック候補ランキング補助になり得るが、route/path plannerではなく、WebLINXのDOM要素シリアライズと候補抽出の適応が必要。90.9 MBの重みは取得していない。
- **autopager** はPython 3.13の隔離uv環境に依存を入れ、0.4.0をimportできた。用途はページネーションリンクのPAGE/PREV/NEXT分類に限定され、C2の補助評価向け。clone時点のCRFは含まれず、Git cloneからの利用では学習済みモデルが要る点も確認する必要がある。
- **JevForge-0.8B** はwhole-question candidate選択用のdecision model。C1で候補集合を既に生成できている場合に再ランキングを試す余地はあるが、構造解析器ではない。重みは約3.42 GBで未取得。
- **Qwen-HTML / Co-Scraper** はquery-aware DOM pruningと3 seedからのscraper合成向け。Qwen-HTML公開重みは約8.0 GB、Co-ScraperはOpenAI互換endpointを前提とし、今回のC1用途では採用コストが大きい。
- **smart-crawler** はナビゲーションモデルの自動推定という狙いは近いが、古いJava/Akka/HtmlUnit/Neo4j/Maven構成で、この環境にJava/Mavenがない。Windowsの通常checkoutは `localhost:8081.csv` というファイル名で失敗し、root licenseも見つからない。
- **webNavigator** は論文システムと5つのWebArenaドメインに対するグラフ/埋め込み資産を前提にした研究実装。自社サイトの汎用C1 failure handlerにそのまま入れる適合性は低い。

## pinchtab-webgraph ローカル試行

ソース revision `542f44d82d062ac4416ff1c99b08dc444ba1633c`。MIT、Python 3.10+、本体は標準ライブラリのみ。ライブcrawlには別途PinchTab CLIとChrome bridgeが必要。主CLIは `crawl --start URL --out graph`、問い合わせは `howto`/`paths`。コード中のグラフ抽出は画面上のリンク、ボタン、タブ等を構造的に記録し、offline BFSでclick pathを返す設計。

WindowsのPinchTab 0.15.2はnpx経由で動作。最初のWebgraph起動は `pinchtab` がPython subprocessのPATHに無く失敗。npx配布物は `.exe` が `pinchtab-windows-amd64.exe` という名前なので、監査フォルダー内に `pinchtab.exe` aliasを置きPATHを通した。次の阻害は既定security guardによるevaluate拒否(HTTP 403)。localhostだけのfixture用bridgeでevaluateを許可した後、rootページからリンク1件を読み取り、graphに1 state/0 edgeを書いた。次のmaterializeで失敗し、目的地リンクを辿る実証は完了していない。Windows側文字コードエラーも一度発生し、`PYTHONUTF8=1` で回避した。

記録 (すべて `logs/` にコピー済み):

- `fixture-crawl.log`: 初回、CLIのPATH未設定およびbridge再起動エラー。
- `fixture-crawl-pathfix.log`: CLI解決後、既定evaluate guardで403。
- `fixture-crawl-pathfix-yolo-utf8.log`: guard許可後、1 stateとCatalogリンクを取得。次のmaterializeで失敗。
- `fixture-graph-pathfix-yolo-utf8.json.json`: 実際に保存された1 stateのグラフ。
- `pinchtab.exe`: audit用に複製したWindows CLI実行ファイル。公開サイトには接続していない。export対象外。

この実行結果だけからは、経路候補としての採否を決められない。成功したのはローカルページの要素抽出まで。C1側へ取り込むなら広域crawlではなく、失敗ページ近傍の探索上限付きのgraph構築と、既存Playwright経路ログに対するG計算を別途比較する。

## autopager ローカル試行

revision `990e480bee9f780088db05e7586b2b84e255abeb`。READMEはMIT。Python APIは `autopager.urls(html)` と `autopager.extract(html)`。抽出ラベルはPAGE/PREV/NEXT等。`uv run --with ...` の一時環境でimport/version `0.4.0` は成功した。

cloneには学習済み `autopager.crf` が無いため、配布モデル無しのdefault `extract`/`urls` は両方とも `ValueError: CRF is not trained` になった。そこで別途PyPIの最新公開版0.3.1を隔離uv環境に入れて確認したところ、default pretrained modelで同一fixtureを分類し、期待リンク列を返した。入力は248 bytes/5リンク、推論API2回、`extract` 約0.0273秒、`urls` 約0.0014秒。PyPI版0.3.1とsource clone版0.4.0は同一バージョンではない点に注意。

さらに、4つの最小fixture HTMLから0.4.0 sourceに一時CRFを学習し、同じ結果を確認した。`extract` は約0.0025秒、`urls` は約0.0013秒。この人工データ上の互換smokeは実サイト性能を意味しない。source/PyPIそれぞれの依存バージョンは `logs/autopager-dependencies.txt` と `logs/autopager-pypi-dependencies.txt` に保存した。

## 候補情報

各候補のcommit/model revision、ファイルサイズ、license、依存、適合判断は `audit.json` を参照。重みはすべて未ダウンロード。候補の説明・APIと実行結果は分けて記録した。

## 出典

- [pinchtab-webgraph README](https://github.com/egouilliard/pinchtab-webgraph)
- [autopager README](https://github.com/TeamHG-Memex/autopager)
- [autopager PyPI distribution](https://pypi.org/project/autopager/)
- [WebLINX公式のDOM候補ランキング説明](https://mcgill-nlp.github.io/weblinx/docs/)
- [WebLINX公式modeling/DMR手順](https://github.com/McGill-NLP/weblinx/blob/main/modeling/README.md)
- [MiniLM-L6-dmr model card](https://huggingface.co/McGill-NLP/MiniLM-L6-dmr)
- [JevForge-0.8B model card](https://huggingface.co/AndeyTait/JevForge-0.8B)
- [Qwen-HTML model card](https://huggingface.co/Ambition0303/Qwen-HTML)
- [Co-Scraper repository](https://github.com/Ambition-0303/Co-Scraper)
- [smart-crawler repository](https://github.com/fcibecchini/smart-crawler)
- [WebNavigator repository](https://github.com/fate-ubw/webNavigator)
