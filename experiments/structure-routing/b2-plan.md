# B2 sitemap 追加比較案

## 現状の依存

- `.deps/bot-diagnostics/package.json` とそのlock/`node_modules` には Playwright 1.63.0 と robots-parser 3.0.1 がある。Crawlee本体・Scrapyは現在のpackage metadataにない。
- 現在のPython 3.13.7環境では `scrapy` / `scrapy_sitemap` importは見つからなかった。
- `experiments/bot-diagnostics/README.md` は、過去に別プロセスで `@crawlee/core@3.18.2` を測定し、import時のPSS増分を約29.6 MiBと記録している。これは現行依存へ導入済みという意味ではない。

## 最小構成

候補はCrawlee JSに絞る。既存実験がNode/Playwright中心で、`SitemapRequestLoader` はsitemapをバックグラウンドで読みながら有限バッファでURLを流せる。`BasicCrawler` のrobots gateと、CheerioCrawlerの既製HTML parserを組み合わせる。現在の依存には未導入なので、試行時だけ分離した依存prefixに固定版を導入し、既存`.deps/bot-diagnostics`は変更しない。

1. robots.txtを候補UAで取得し、Sitemap行のURLだけを起点にする。候補パーサはsitemap indexを再帰し、XMLおよび`.xml.gz`を処理する。Crawleeのsitemap loader/APIはストリーム入力・include/exclude・同一origin制約を提供する。robotsとloaderの自動取得が二重計上されないよう、共有実行器で取得を予約/計上する。
2. sitemap由来URLは元URLを識別可能な形で保持し、host/originを固定したうえで凍結した一般規則でURL群を分類する。query/hashを一律削除せず、観測canonicalと同一状態の根拠を別フィールドへ保存して後集計時に比較する。path-token/階層深度等の一般特徴も保存する。URL列挙・分類はHTML取得成功に含めず、同一Chrome層での取得可否を別途評価する。
3. 各分類から事前固定ルールで少数の代表URLを選び、同じ取得上限内でHTMLを取得する。raw HTML、status、navigation/error、content type、DOM統計を証拠保存する。Cheerioは同一入力HTMLからtitle/h1/main/article、JSON-LD型、リンク数などの汎用DOM特徴を抽出する。ページ別LLM、gold URL/商品ID、専用gold selectorを候補へ渡さない。
4. 実測後に独立評価側が到達先をgoldと照合する。成功判定はURL一覧ではなく、独立固定対象への到達、期待HTML型、正常navigation、非空raw HTML保存を必要とする。商品値抽出は対象外。

## 追加比較の範囲

候補の少数層は、固定80サイト中のpractice 1〜2件、python.org (11)、楽天 (61)、au PAY (62)から選ぶ。4ドメイン程度の事前固定サブセットで、同じWindows/ブラウザ設定・UA・robots判断・遅延・サイト別文書上限の下、B1、C1、B2を同じ期間に比較する。既存480行やC1不発行を上書きせず、B2追加runとして保存する。robots、index、代表HTMLの全リクエストを既存main残予算1,706文書と同じ台帳へ先行予約し、上限を共有する。初回pilotは最大80文書程度に収め、503/robots拒否が続けば停止し、残額を保全する。成功が確認できても、このpilotのみで10→30→100や全サイト性能を主張しない。

Sitemapに商品/記事相当URLが含まれない、robots上でアクセス不可、またはURL群は得られるが代表HTMLが不成立、を別々に報告する。B2がURL一覧を作っただけではHTML収集成功にしない。

## 根拠資料

- Scrapy公式 `SitemapSpider` はrobots.txtからのsitemap発見、nested sitemap、regex routeを説明し、公式実装はgzip sitemap展開も行う: [Scrapy SitemapSpider docs](https://docs.scrapy.org/en/latest/topics/spiders.html), [Scrapy sitemap.py](https://github.com/scrapy/scrapy/blob/master/scrapy/spiders/sitemap.py)
- Scrapy公式Downloader Middlewareはrobots gateとgzip HTTP圧縮middlewareを説明する: [Scrapy Downloader Middleware docs](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html)
- Crawlee公式 `SitemapRequestLoader` はsitemapをbackgroundで読み、URLをbuffered async loaderとして流す。sitemap URL入力、include/exclude、buffer上限がある: [SitemapRequestLoader guide](https://crawlee.dev/js/docs/next/guides/request-loaders), [SitemapRequestLoader API](https://crawlee.dev/js/api/next/basic-crawler/interface/SitemapRequestLoaderOptions)
- Crawlee公式 `BasicCrawlerOptions` はrobots.txt順守設定とUA指定を示す: [BasicCrawlerOptions](https://crawlee.dev/js/api/next/basic-crawler/interface/BasicCrawlerOptions)

実施時は`next` APIではなく依存lockに記録した安定版のAPI/挙動を固定し、XML index/gzip/robots discoveryをfixtureで確認してから対象サイト試行へ進む。
