# サイトマップURL選択・全80サイト評価

## 結果

**判定:** URL-only sitemap経路は型検査を通ったページを一部の対象で取得できたが、採用済みstrict successは9/80サイト（入力のあった36サイト中9）に限られる。共通ルートの実験部品としては有望だが、全サイトで使える標準経路と結論づけるには根拠が足りない。固定URL recallと比較ベースラインは未測定。

全80サイトを分母に維持。URL inventoryあり36、URLなし44。original/repair rawには80 phase rows（50+30、うち18は同じsite/phaseのrepair）あり、採用したunique phase rowsは62、未実行phaseは98（URLなし88、warm skip10）。cold/warm status内訳: `{"cold": {"not_run_no_sitemap_input": 44, "html_saved": 15, "no_valid_detail": 13, "access_stopped": 8}, "warm": {"not_run_no_sitemap_input": 44, "html_saved": 11, "no_valid_detail": 14, "not_run_cold_access_or_error": 9, "access_stopped": 1, "not_run_cumulative_budget": 1}}`。URLなしはサイトがブロックした意味ではない。

frozen typed evaluatorの採用集計: 89 item attempts、80 saved HTML valid、41 strict successes、48 unverified、34 saved candidates without strict evidence。unverifiedをfalseとは断定しない。site strict IDs: 02, 05, 13, 14, 18, 52, 53, 54, 76。strict success件数はraw HTML独立判定なので、candidate-side success flagがfalseでもstrictとなる例がsite 52に1件ある。自動評価がknown wrongと判定した候補はなし。別個の採用済み02/10 post-hoc検証ではMagicの誤ったindex候補1件を記録。

candidate-reported listing-found + before-source SHAの観測proxyは21サイト（9 strict sites / 21 proxy sites）だが、独立したlisting goldではない。scroll-growth strict successは3件で、全て採用済み02 coldの既存観測。all80延長のrepairでは0件。

Warmのitem target attemptsは35。cold全試行のselected item URLとvisited listing candidatesを（失敗attemptも含めて）除外集合とし、新規target 11、same-URL replay 24、新規target strict success 4。このうち02/10再利用分は6 attempts / 3 strict、all80延長の新規target分は5 attempts / 1 strict ({"73": {"new_target_attempts": 2, "strict_successes": 0}, "76": {"new_target_attempts": 3, "strict_successes": 1}})。list seed/candidate choiceは16件を別集計し、target denominatorには含めない。これはrouteのwarm replayであり、graph search Gは未実装・未測定（分母0）。

実行経路: sitemap URL group → listing → scroll → observed item → raw HTML + rendered DOM保存。HTML本文はURL choice modelへ渡さず、pageごとの追加LLMは0。09はg1を選んだがdetail URL groupではなく、listing discovery後のrepeated-anchor fallbackも行われず失敗。これは共通ルートにfallback改善余地があることを示し、サイトの不可能性を示すものではない。
HTTP拒否と局所scope停止を区別する。repair scope/login guard: 23 idp.nature.com authorize/login、30 zenodo.org/login unsafe_route、37 NOAA related host scope外、26 Frontiers Partnerships related host scope外（停止前にHTML 1件保存）、52 main-content block。repair対象から外したsite 46はcold main URLが実際にHTTP 403。残るaccess停止理由は各phase evidence参照に記録。

## 既存観測と費用

02/10の同一条件結果は明示採用し、過去route evidenceとして明示した。新しいall80 URL choicesとは区別。参照: [adopted-prior-evidence-ref.json](adopted-prior-evidence-ref.json)、[adopted-prior-route-verification.json](adopted-prior-route-verification.json)、元資料の `lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115/route-verification.json`。post-hoc確認はMagicの期待商品5件と誤ったindex1件で、自動frozen scoreには混ぜない。

browser events (source totals incl. imported 02/10): HTTP 22986 requests / 21852 responses, XHR 3622, measured response body 1,435,919,656 bytes, UI 486, browser time 2327.2s. Metadata this run: 323 requests / 310 responses / 58,466,529 response bytes. Document reservations: new parent 157 + imported 02/10 28 + repair 133 + prior global 46 = 364. Imported phase reservations 28 are part of parent 185; the earlier per-site prior 11 is part of global prior 46. No double count. Reservations are ceilings, not successful document loads. Full breakdown: [cost-audit.json](cost-audit.json).

新規URL choiceは7 jobs / 34 sites、入力349,098文字、HTML 0 bytes。native token/invocation実数とmodel identityは未検証・不明（0ではない）。route executionの外部/ローカル推論は0。engineering/report usageも不明。B1/C1再実行なし、比較優位性は主張しない。

## 全80サイト phase表

`strict/saved` はstrict success数 / saved HTML valid数。候補選択や観測段階の失敗を隠さない完全なphase別status・証拠参照は [operational-results.jsonl](operational-results.jsonl) を参照。

| ID | domain | sitemap URLs | cold | warm |
|---:|---|---:|---|---|
| 01 | toscrape.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 02 | webscraper.io | 453 | html_saved (3/3) | html_saved (3/3) |
| 03 | scrapingcourse.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 04 | scrapethissite.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 05 | web-scraping.dev | 638 | html_saved (3/3) | html_saved (3/3) |
| 06 | listly.io | 108 | html_saved (0/3) | html_saved (0/3) |
| 07 | automationexercise.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 08 | oxylabs.io | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 09 | scrapingsandbox.com | 5 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 10 | magicscraper.app | 88 | html_saved (0/3) | html_saved (0/3) |
| 11 | python.org | 7 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 12 | mozilla.org | 14884 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 13 | nodejs.org | 1757 | html_saved (3/3) | no_valid_detail (0/0) |
| 14 | typescriptlang.org | 921 | html_saved (3/3) | html_saved (3/3) |
| 15 | rust-lang.org | 3 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 16 | go.dev | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 17 | kubernetes.io | 3920 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 18 | docker.com | 2193 | html_saved (3/3) | html_saved (3/3) |
| 19 | djangoproject.com | 13253 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 20 | scikit-learn.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 21 | arxiv.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 22 | nih.gov | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 23 | nature.com | 2859 | access_stopped (0/0) | not_run_cold_access_or_error |
| 24 | sciencedirect.com | 20000 | access_stopped (0/0) | not_run_cold_access_or_error |
| 25 | plos.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 26 | frontiersin.org | 20000 | access_stopped (0/1) | not_run_cold_access_or_error |
| 27 | doaj.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 28 | semanticscholar.org | 20000 | no_valid_detail (0/3) | not_run_cold_access_or_error |
| 29 | crossref.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 30 | zenodo.org | 20000 | access_stopped (0/0) | not_run_cold_access_or_error |
| 31 | e-stat.go.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 32 | e-gov.go.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 33 | stat.go.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 34 | jma.go.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 35 | gsi.go.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 36 | nasa.gov | 6000 | html_saved (0/3) | html_saved (0/3) |
| 37 | noaa.gov | 5961 | access_stopped (0/0) | not_run_cold_access_or_error |
| 38 | worldbank.org | 20000 | no_valid_detail (0/0) | access_stopped (0/0) |
| 39 | oecd.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 40 | www.who.int | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 41 | gutenberg.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 42 | openlibrary.org | 20000 | no_valid_detail (0/2) | no_valid_detail (0/0) |
| 43 | archive.org | 20000 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 44 | aozora.gr.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 45 | metmuseum.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 46 | si.edu | 4050 | access_stopped (0/0) | not_run_cold_access_or_error |
| 47 | britishmuseum.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 48 | www.europeana.eu | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 49 | artic.edu | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 50 | www.rijksmuseum.nl | 2508 | html_saved (0/3) | no_valid_detail (0/0) |
| 51 | wikipedia.org | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 52 | sciencenews.org | 966 | access_stopped (1/1) | not_run_cold_access_or_error |
| 53 | bbc.com | 3444 | html_saved (3/3) | html_saved (3/3) |
| 54 | theguardian.com | 348 | html_saved (3/3) | html_saved (3/3) |
| 55 | reuters.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 56 | nhk.or.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 57 | apnews.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 58 | itmedia.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 59 | impress.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 60 | gigazine.net | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 61 | rakuten.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 62 | wowma.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 63 | amazon.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 64 | yahoo.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 65 | yodobashi.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 66 | biccamera.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 67 | kingjim.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 68 | uniqlo.com | 250 | access_stopped (0/0) | not_run_cold_access_or_error |
| 69 | ikea.com | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 70 | www.nitori-net.jp | 20000 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 71 | www.japan.travel | 5187 | html_saved (0/2) | not_run_cumulative_budget |
| 72 | jalan.net | 238 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 73 | rurubu.jp | 5 | html_saved (0/2) | html_saved (0/2) |
| 74 | gotokyo.org | 12260 | html_saved (0/1) | no_valid_detail (0/0) |
| 75 | ja.kyoto.travel | 5648 | no_valid_detail (0/0) | no_valid_detail (0/0) |
| 76 | osaka-info.jp | 311 | html_saved (0/3) | html_saved (1/3) |
| 77 | www.visit-hokkaido.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 78 | suumo.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 79 | homes.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |
| 80 | athome.co.jp | 0 | not_run_no_sitemap_input | not_run_no_sitemap_input |

domain列は登録時の原文を保持し、正規化labelと混同しない。

## 失敗・修正と採否

汎用の標準経路としての採用は見送る。最小構成の採用候補は、サイトマップ取得・URL群の保存/分類・URLだけによる入口候補選別・観測したリンクからのHTML保存。既存Skillの代表経路生成に渡す前段として扱い、生成グラフだけでライブ構造や反復処理まで発見できるとは仮定しない。

追加実装が必要なのは、URL群が得られない場合の入口からの限定探索、一覧/詳細groupを取り違えた場合の棄却と反復リンクfallback、観測根拠に基づく関連ホストの範囲設定、保存HTMLの型検査、クリック型ページ送り/Load More等の実行部。今回のscroll操作はこれら全操作の代替ではない。目的URLが既知ならB0として直接取得し、この方式の到達成功へ混ぜない。

61楽天・62au PAYマーケットは今回の取得上限/ホスト条件でサイトマップURLを得られず、この方式による一覧→商品HTML取得は未実施。商品取得不能やサイトマップ不存在を証明した結果ではない。固定商品ID・正解経路・専用セレクタは投入していない。

共通実装の修正は3系統: XML解析時の反復ハッシュ処理をSAXの単一走査へ変更、保存済みmetadataレスポンス再利用時の消費済みbodyを修正、子frame拒否による主ページ全体の誤停止を修正。途中結果・修正前試行・費用は保持した。サイト専用セレクタ/巡回コードの手作業補正は0。評価用のページ型条件は事前固定し、専用経路とは別管理。

HTTP拒否・login・許可ホスト外停止と、取得後のgroup不一致・探索不足・型未確認を区別した。目的型一致は固定したHTML型判定器による結果で、全件を人が正誤確認した値ではない。誤成功の完全な件数は不明。既知の誤選択は採用済みMagicのindex1件。要求item URLの同一phase内重複は全試行で0、最終URLの重複は修正前1/修正後3（失敗redirectを含む）。warm再訪24件とは別の値。

過去のinventory/URL選択を再利用したためfull coldの総費用は未計測。02/10の再利用phase28文書は親run185文書に含み、個別site prior11文書は既存global46文書に含む。モデル非使用は取得実行部分に限り、URL選別や開発・報告のnative token量は不明。保存規則内の追加LLM呼出しは0だが、未対応ページ型への修復費用は未測定。今回は10/30/100の連続取得を追加実行しておらず、その増分コストは推定しない。

## 再現・継続

構造ルーティング実装は `experiments/structure-routing/sitemap-discovery.mjs`、`sitemap-agent-routing.mjs`、`sitemap-agent-browser.mjs`。記録済みrunの再集計は `python -B lab-runs/structure-routing-sitemap-agent-all80-20261115/summarize.py --repair lab-runs/structure-routing-sitemap-agent-all80-20261115/repair-run`。HTML本体を読まず、評価JSONとphase JSONLのみを使用する。

検証結果・依存lock・ frozen evaluation policy・package/checkpoint情報は [HANDOFF.md](HANDOFF.md) を参照。
