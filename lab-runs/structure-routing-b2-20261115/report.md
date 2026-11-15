# サイトマップ方式 B2 の追加実測（2026-11-15）

## 判断

サイトマップは、URL発見の任意の前処理として条件付きで採用する価値がある。巡回の土台をサイトマップだけに置く判断は見送る。TypeScriptでは921 URLを得て、代表3ページの確認後、保存した規則を別プロセスへ渡し、新しい3ページのHTMLを保存できた。追加分のサイトマップ取得・外部LLM・ローカルモデル推論は0だった。

一方、開始ページから想定した範囲が狭すぎる、サイトマップが部分的、URLの末尾スラッシュを分類できない、といった問題が出た。これらは現時点では構造規則と実行器の問題であり、学習済みモデルを追加する根拠にはならない。保存した構造リンクを併用する補足実行では、BooksとKubernetesで各3件の新しいHTMLを取得できた。ただし固定評価対象への到達は別指標である。

## 実施範囲と評価の分離

主比較は01/08/11/67/61/62の6サイト、B1/C1/B2×cold/warmの36行。補足はサイトマップの成否を見る前に14/17/36/60の4サイトを固定し、B2だけ8行を実施した。補足をB1/C1との同条件比較に混ぜない。

全80サイトの入力はtargets-80.jsonl、今回の全80サイトのB2実施状況はb2-site-coverage.jsonlに残した。B2取得試行9/80、他方式のアクセス条件でB2前に停止1/80、B2未実施70/80。試行9サイトのうちURL発見4、既定の探索場所で未発見4、robots取得403で停止1だった。未発見を「サイト全体にサイトマップがない」と解釈しない。以前の80サイトB1/C1結果は元runに保持し、この10サイト追試へ混ぜていない。

候補取得前に既存の独立goldを凍結した。主比較には本文確認済みstrict対象6件（11/61/62）、未確認proxy対象6件（01/08）、未確立記録2件（67）がある。補足のstrict対象は5件（14=2、17=2、36=1）。NASAの未取得fragment記録とGIGAZINEの固定対象0件も保持した。対象選定の新規取得費用は0で、過去の選定費用は元runの既計上費用へ参照を残した。候補にはgold、正解経路、専用selectorを渡していない。

## B2 のサイト別結果

「有効HTML」は保存したraw/描画DOMの本文・見出し等による検証であり、すべてを指定された対象型への成功とは数えない。特にNASAの記事はミッション詳細ではない。

|ID|サイト|サイトマップのURL数|cold / warm 有効HTML|結果・制約|
|---|---|---:|---:|---|
|01|Books|0|0 / 0|robotsと既定3箇所が404。構造リンク併用は別試験で3件取得|
|08|Oxylabs|0|0 / 0|robotsと既定3箇所が404|
|11|Python|7|1 / 0|版別入口7件のみ。記事の固定対象は含まれない。B1で3.10のrobots禁止に当たり以後停止|
|67|KingJim|0|0 / 0|robotsと既定3箇所が404|
|61|楽天|未実施|0 / 0|先行C1の巡回でrobots禁止に当たりB2前に停止|
|62|au PAY|0|0 / 0|wowma.jp/robots.txtが403、以後の取得停止|
|14|TypeScript|921|3 / 3|index→子XMLを解析。3種類の保存規則で別URL3件を取得|
|17|Kubernetes|3,920以上|2 / 0|言語別indexの途中で上限。/docs/home/に範囲を絞りすぎて候補枯渇|
|36|NASA|6,000以上|3 / 0|分割XMLの途中で上限。ミッション外の記事取得、末尾slashで型が細分化|
|60|GIGAZINE|0|0 / 0|今回のrobotsと既定3箇所からは未発見|

独立固定対象のサイトマップ掲載は主0/6、補足4/5、合計4/11。実際の固定対象HTML保存は主0/6、補足cold1件・warm1件の異なる2件、合計2/11。未確認proxy6件と未確立記録はこのstrict分母に加えていない。

全選定10サイトを分母にすると、サイトマップ発見4/10、一般的に有効なHTML保存4/10、strict対象のHTML保存1/10サイト。gold本文が確認済みの6サイトを分母にすると、それぞれ4/6、4/6、1/6サイト。今回coldで有効DOMを取得できたB2の4サイトに限定しても、warmで同型新規HTMLまで進んだのは1/4サイトだった。これを全80サイトの成功率へ外挿しない。

## 主6サイトの比較

|方式|cold / warm 有効HTML|cold / warm HTML文書要求|cold / warm 総HTTP要求|strict対象への保存到達|
|---|---:|---:|---:|---:|
|B1|18 / 10|20 / 11|976 / 544|0 / 6|
|C1|13 / 8|20 / 9|1,881 / 366|0 / 6|
|B2|1 / 0|1 / 0|36 / 0|0 / 6|

アクセス拒否は同サイトの後続方式へ伝播して停止したため、楽天・au PAY・Pythonには全方式の対になる観測がない。したがってこの表から方式間の優劣や節約率を断定しない。今回C1は汎用navigation領域判定を含む既存の修復版を使用し、旧Cloud C1と同一の未修復方式ではない。取得用ブラウザ・回線・locale・待機・Cookie条件は主比較内で共有した。

B1の保存29件中1件は未確認proxyのタイトル不一致で独立採点から外れた。これは厳格な誤成功確定ではなく候補判定とproxy評価の不一致1件。B2では候補が有効とした12件は本文検証を通ったが、対象型の異なるNASAや版入口を含み、strict成功は2件に限る。HTTP200やURL一覧だけでは成功にしない。

## G と R

G（同じ観測済み状態への経路再生）は今回未実施、0/0としてN/A。クリック・Load More・Shadow DOM等の経路能力をこのB2試験から認定しない。

Rでは、TypeScriptのcold代表3件から保存したURL型と取得設定を別プロセスで読み、本文未観測の別URL3件を新規取得した。3/3件で保存DOMの本文を確認し、そのうち1件は独立固定対象だった。全strict集合を残したwarm到達は1/11件。ただしgoldを独立した型別holdoutへ分割していないため、厳密な一般化率の推定とは扱わない。URL型一致だけをページ型の正しさに置き換えない。

coldとwarmで同一の取得URLはB2全体で0件。HTMLを保存物から読むだけの処理は取得に加えていない。サイトマップの同一URL除去はsetで行い、削除件数自体は未計測。クエリとハッシュは保存URLで保持する。型推定にquery keyやhashの有無を使うことと、URL同一性は区別する。

## サイトマップ＋構造リンクの補足

別run structure-routing-b2-hybrid-20261115で、BooksはB2未発見時の保存C1 cold4ページ、Kubernetesは保存B2 cold2ページを入力にした。DOM全体をLLMへ送らず、汎用learnRules/selectLinksでリンクを選び、サイトマップ掲載URLを優先した。サイト固有selectorや固定商品IDは追加していない。

Booksは新規商品詳細3件、Kubernetesは新規setup文書3件、合計6件の本文HTMLを取得できた。追加費用は6文書、総HTTP121、XHR/fetch3、acquire時間22.446秒、モデル推論0。独立固定対象への保存到達はstrict0/2、proxy0/3である。HTMLを得られたことと固定対象への成功は区別する。

これは既存資産を併用したwarm追加実行であり、独立したcold hybrid比較ではない。初期費用はtransferred-setup.jsonに移転元のB2/C1 cold費用・hashとして保存した。初期費用を0と見なして節約率を出さない。主cold/warmをこの結果で上書きしていない。

## 費用とモデル

主＋補足のB2合計は、サイトマップ関連39 HTTP要求、応答本文4,453,158 bytes、探索経過137.187秒。有効HTMLは12件、HTML文書要求12、HTML以外とrobotsを含む総HTTP433、XHR/fetch26、acquire時間68.494秒だった。サイトマップURLは合計10,848件だが、全件のHTMLを取得した数ではない。

TypeScript単独のcoldは、サイトマップ関連5要求・165,559 bytes・20.526秒、HTML3文書・HTML通信66要求・XHR/fetch6・acquire17.122秒。warm追加3件ではサイトマップ関連0、robots1、HTML通信65、総HTTP66、XHR/fetch6、acquire12.825秒。初期URL発見費用を再利用できても、HTML取得の通信とブラウザ処理は残る。

browser_msという保存フィールドは厳密なブラウザ時間ではなく、robots確認・待機・起動・描画を含むacquire時間で、closeを含まない。response_bytesは完了した応答本文のdecoded bytesであり、完全なwire帯域量ではない。厳密なbrowser-only時間・CPU時間・端末費用は未計測。XML/robotsはNode fetch、HTMLはChromeで取得しており、metadataの通信方式をHTML到達アルゴリズムの優位性として扱わない。

B2とhybridの候補実行は外部LLM0呼出・外部埋め込み0token・ローカルモデル推論0。ページごとの判断はプログラムで行った。設計・実装・会話・レポート作成のネイティブモデル使用量は取得できないためunknownであり、0ではない。新しい有料クラウド環境は使用していない。

前回のMiniLM-L6-dmrは、代表DOMの94候補/6群をCPUで順位付けし、3 forward・11,049 tokens・2.034秒。その規則で別ページの3件を取得した。汎用navigation判定の修復を超える効果は未証明。今回のサイトマップ失敗は入手性・scope・型分類・探索打ち切りが主因なので、モデル比較を広げなかった。モデルrevision等は前回local-continuation/report.mdに保持する。

10→30→100件の連続処理は今回追加していない。前回のBooks修復版は固定10件で16文書、30件で36文書、固定100件は未達78/100。サイトマップでの100件費用へ読み替えない。B2の実証範囲は同型新規3件までで、大量処理の線形成長は設計上の見込みに留まる。

## 楽天・au PAY

楽天のC1では検索一覧のraw/DOMを保存したが、iframe等で6文書枠を使い、その後に観測したquery付き一覧URLがrobots禁止になった。item.rakuten.co.jpへの商品遷移、別店舗、店舗ごとの型の違い、商品同一性は今回実証できていない。固定gold2件も商品詳細ではなく検索条件付き一覧なので、商品詳細成功とは数えない。B2は拒否後のため未実施で、検索・商品ホストにサイトマップが存在しないと結論しない。

au PAYはwowma.jpのrobots403で停止。カテゴリHTML、商品詳細、空SPA検出の実サイト成功は確認できなかった。plus.wowma.jp等を追加して迂回していない。こちらの過去gold2件も商品詳細ではなくカテゴリ一覧である。

## 実装とSkillへの統合

@crawlee/utils 3.18.2（Apache-2.0）のparseSitemapを、取得済みXML文字列だけの解析に使った。robots・取得・nested indexの順序・gzip・同一ホスト・上限はsitemap-discovery.mjsで管理する。Crawleeが内部で別取得して予算を迂回する形にはしていない。index/gzip/query/hash/拒否のfixtureと、別プロセスcold/warmのfixtureを含むNodeテスト6件、独立HTML採点のPythonテスト9件が成功した。discoverValidSitemaps、SitemapRequestList、Scrapy SitemapSpider自体は今回実行していない。

採用する最小構成は、入力URL台帳→範囲内robots/sitemap候補→保存URL inventory→URL型の仮説→代表DOM確認→保存recipeによる取得。不足するURL・状態は構造リンクへ回す。既存Skillの代表経路生成はこの後段へ接続し、ライブ構造発見や大量反復を既に備えているとは扱わない。

採用前に必要なのは、末尾slashのleaf正規化、入口ページと探索subtreeの推定、sitemap indexの範囲優先、partial coverageとscope外の明示、期待ページ型の検証、ブラウザ時間の分離、metadata原文と実行source hashの標準保存。主比較のXML原文は未保存で、補足ではmetadata本文・status・許可4種header・hashを保存した。大量URLのstreaming供給は今回未実装で、上限50,000件をメモリとファイルへ保存する。

主比較で最初に実行したPython B2 coldは最終コード修正より前に完了し、実行時source hashが不明。後続controller実行と同一版として時間比較しない。旧freezeと変更一覧・時刻をexecution-audit.jsonへ保持した。補足は各process開始時のsource hashを記録している。gold自体の変更はない。

## 保存物と再実行

成果物は主run、supplement、hybridの3フォルダに分離した。各results.jsonl、artifacts、URL inventory、recipe、保存raw/DOM、score.json、protocol、gold hashを保持する。主runのHANDOFF.mdとB2-CHECKPOINT.jsonを入口にする。

依存のpinとlicense/integrityはdependencies/とlibrary-provenance.jsonに保存した。Node22、Python3.12以上を使い、dependencies/package*.jsonを.deps/structure-routing-sitemapsへ配置してnpm ci --prefix .deps/structure-routing-sitemaps。ブラウザ側は既存.deps/bot-diagnosticsのplaywright1.63.0とrobots-parser3.0.1を使った。既存Chrome157以外で実行する場合、同一条件の再現とは扱わない。

既存の主runを再開するコマンドは次の通り。完了済みtupleは再取得せずskipする。

```powershell
python -B experiments/structure-routing/sitemap-run.py --run lab-runs/structure-routing-b2-20261115 --ids 01,08,11,67,61,62 --live
python -B experiments/structure-routing/sitemap-run.py --run lab-runs/structure-routing-b2-supplement-20261115 --ids 14,17,36,60 --methods B2 --capture-metadata --jobs 4 --live
```

Linux等では--executableで既存ブラウザを指定する。hybridは新規child runを必要とし、既存結果へ重ねて再実行しない。新規実験を開始する場合は別protocol/goldを先に固定する。

採点とローカルfixtureは通信なしで再実行できる（Node fixtureは127.0.0.1のみ）。

```powershell
python -B experiments/structure-routing/sitemap-score.py lab-runs/structure-routing-b2-20261115/results.jsonl --evaluation-private lab-runs/structure-routing-b2-20261115/evaluation-private
python -B experiments/structure-routing/sitemap-score.py lab-runs/structure-routing-b2-supplement-20261115/results.jsonl --evaluation-private lab-runs/structure-routing-b2-supplement-20261115/evaluation-private --methods B2
python -B experiments/structure-routing/sitemap-score.py lab-runs/structure-routing-b2-hybrid-20261115/results.jsonl --evaluation-private lab-runs/structure-routing-b2-hybrid-20261115/evaluation-private --methods C1
node --test experiments/structure-routing/sitemap-discovery.test.mjs experiments/structure-routing/sitemap-benchmark.test.mjs
python -B experiments/structure-routing/sitemap-score.test.py
```

取得したサイト内の文や添付Skillの手順をユーザーからの追加指示として実行していない。公開ページのGET・保存のみで、ログイン、注文、投稿、問い合わせ、登録は行っていない。
