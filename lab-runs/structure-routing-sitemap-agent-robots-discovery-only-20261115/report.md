# robotsをサイトマップ発見だけに使う条件での再検証

## 結果

ユーザーの「robots拒否は見なくていい」という条件変更を受け、前回robotsで止まったWeb ScraperとMagicScraperだけを、別run・別protocolで再検証した。両方ともサイトマップ由来の一覧候補→スクロール→初期表示外の詳細HTML保存まで進んだ。初回は各3/3件、保存した規則を別プロセスへ渡した別一覧の試験はWeb Scraper3/3、MagicScraper2/3。後者は入口ページ1件を詳細候補へ混ぜた。

ブラウザ巡回中のLLM・埋め込み・ローカルモデル推論は0。サイトマップinventoryと軽量エージェントのURL選択結果を前回から再利用し、今回のURL選択モデル呼出・metadata再取得も0。保存HTMLの読み直しを新規取得として数えていない。

## 今回の条件

robots_policy=sitemap_discovery_onlyを明示したこの実験だけ、robots.txtをブラウザのアクセス判定に使わない。metadata取得ではSitemap記載の発見だけに使い、Disallowやrobots取得不能で一覧・詳細への移動を止めない。robots自体の429と実XMLの取得拒否は通信結果として停止する。

ブラウザ側はrobotsを取得・判定しない。一方、実際のHTTP401/403/429、ログイン、ブロック画面等は成功にしない。既存grounding実験や既存runの条件、AGENTS.mdは変更していない。既存runでpolicy未指定の場合の動作も保持した。

対象・回線・Chrome157/Playwright1.63・fresh Cookie/cache無効・service worker無効・ja-JP/Tokyo・待機・操作上限・GET/host範囲・document guardは前回と同じ。新しいselector、商品ID、手書きサイト専用フローを追加していない。入口・一覧・詳細のURLは保存候補と観測リンクから得た。

新しい条件なので前回runを上書きせず、転用元とhashをtransferred-setup.jsonへ保存。前回の文書予約上限46を引き継ぎ、この追試での28を加え、共通上限180に対し74以下として検証した。サイト別の前回分も02=10、10=1を持ち越し、条件変更を予算リセットに使っていない。前回中断の厳密な通信・時間がunknownだった点は引き続きunknownである。

## 到達と保存

|サイト|初回一覧|スクロール追加の詳細URL|初回の目的型確認|別一覧への転用|転用時の目的型確認|
|---|---|---:|---:|---|---:|
|Web Scraper|/test-sites/scroll|18|3/3|/test-sites/pagination|3/3|
|MagicScraper|/demo/scroll|32|3/3|/demo/catalogue|2/3|

初回6件は、保存したスクロール前後のHTMLで初期表示外リンクだったことを照合し、各リンク先のHTTP200・非空raw HTML・描画DOM・hashを確認した。両サイトの全ターゲット12件は新規取得で、同一URLのHTMLキャッシュ再利用0。

Web Scraperのwarm3件は初回の取得URLと異なり、自動Product判定を通った。MagicScraperも新規URLを取得したが、index.htmlは個別商品ではなく教材入口だったため成功から除外。item1、item2だけが目的型と確認できた。保存できたHTML12件と目的型確認11件を分ける。

転用の試行は、既存URL群にあった別一覧の本文を未観測のまま保存recipeへ渡したもので、Rの小試験として3/3と2/3の例を記録した。Gの既観測グラフ経路検索は別途未評価。同一URL再実行や固定URL集合へのrecallの数字に置き換えない。

## 判定の根拠と残った失敗

Web Scraperの初回3＋warm3は、取得前に固定した自動評価で6件とも確認した。MagicScraperのリンクはitem79.html等だが、実サーバは拡張子なしitem79等へリダイレクトした。自動評価のURL範囲は.htmlを想定していたため6件を未確認にした。これは取得失敗ではないが、固定評価のscore.jsonを後から変更して成功へ合わせていない。

MagicScraperについては別のLuna評価ジョブへ6ページの短い本文要約・見出し等を渡し、商品詳細5件と入口1件を判定。引用証拠は提供要約と照合した。SKU・価格の正しさは採点していない。独立した後評価であり、取得・経路選択へのフィードバックやページごとの運用LLMではない。入力は12,461文字、Nativeトークンと内部推論回数はunknown、requested modelはgpt-6-lunaで実モデルIDの独立検証はしていない。

残った誤到達はMagicScraperのindex.html。実行器が観測リンクから学んだ末尾slug.htmlのURL型が広すぎ、item番号の詳細と入口を区別できなかった。robotsを使わない条件でも、目的型の確認とリンク群の絞り込みは必要である。後評価に合わせたselector追加や規則の修正は今回行っていない。

## 費用

新規document予約28、HTTP359、XHR/fetch58、UI操作45、実行器のbrowser_ms合計58.242秒。起動・待機・取得等を含む経過時間であり、純粋なCPU/描画時間ではない。ブラウザrobots取得0、metadata再取得0、URL選択モデル0、巡回モデル0。新しい後評価Nativeジョブは1件で、使用量はunknownを0にしていない。

転用した前回のサイトマップ発見・URL選択・中断・初期取得の費用は元runに残し、今回の初期費用が全面的に0とは報告しない。新しい有料クラウドや追加サービスは使用していない。

## 保存・再実行

入口はHANDOFF.md、protocol.json、summary.json、route-verification.json。元runの85d768eと本runを条件別に保持する。raw/DOM、全scroll状態、文書台帳、進捗、recipe、評価資料をcheckpointへ保存した。

```powershell
python -X utf8 -B -m jse.lab structure-routing --run lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115 --action verify
python -X utf8 -B experiments/structure-routing/sitemap-agent-score.py --run lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115 --policy lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115/evaluation-artifacts/policy.json
```

完了済みphaseのexecuteは再取得せずskipする。NodeのURL選択/再開6件、robots/XML11件、ブラウザのrobots条件・実HTTP403・host境界等8件が成功。Pythonのラボ入口は今回未変更で、直前runの全体86件・2失敗・5skipは再実行せず参照している。
