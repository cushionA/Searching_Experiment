# 代表ページだけで生成した巡回コードの80サイト検証

2026-11-16 JST。対象は `targets.jsonl` の重複なし80登録ドメイン。先行と同じ方式の6サイトは結果・費用・HTMLを移入し、残り74サイトを追加した。全80サイトを管理・試行したが、全サイトで巡回コードを実行できたわけではない。

**有効なHTML保存は43/80サイト・204ページ、目的ページ型の確認は23/80サイト・101ページ。実行時のLLM呼び出しは0。** 少量の代表ページから生成したコードを使う方式には採用できる部分がある。一方、特定の目的ページを自動で漏れなく探す機能としては、今回の結果では全面採用できない。

## HTML保存と目的型を分けた結果

| 指標 | 初回コード実行 cold | 別プロセスでの適用 warm | 合計・サイトの和集合 |
|---|---:|---:|---:|
| 運用上の分母 | 80サイト | 80サイト | 80サイト |
| 実行結果あり | 59サイト | 48サイト | 59サイト |
| 有効HTML保存サイト | 43/80 (53.8%) | 28/80 (35.0%) | 43/80 (53.8%) |
| 目的型確認サイト | 23/80 (28.8%) | 15/80 (18.8%) | 23/80 (28.8%) |
| 選択・保存を試した対象 | 137 | 90 | 227 |
| 有効な原HTML＋描画後DOM | 125 | 79 | 204 |
| うち目的型確認 | 60 | 41 | 101 |
| うち目的型未確認 | 65 | 38 | 103 |
| HTML欠損・空枠等 | 12 | 11 | 23 |
| スクロールで初めて現れた辺から有効HTML保存 | 6 | 0 | 6 |

HTML保存の判定は、HTTP 200、原HTMLと描画後DOMの存在・ハッシュ、本文、空のSPA枠・チャレンジ・ログイン・メンテナンス等の固定チェックを用いた。目的型は実行器の `saved` フラグと切り離し、実験前に固定した型判定器で検査した。型判定は価格・SKU・商品属性の正確さを評価していない。

原HTMLとDOMの両ファイルが書かれた対象は219件。23件の無効証拠は原HTML欠損8件と空枠等15件。実行器の `saved=true` でも有効HTMLではないものが15件あり、成功数から除外した。目的型未確認103件は誤ページと断定していない。型を誤って確認済みにした件数は独立した人手goldがないため不明で、0とはしない。

構造入力（代表観察とコード生成入力）が揃った59サイトを分母にすると、有効HTML保存43/59 (72.9%)、目的型確認23/59 (39.0%)。warmはこの59サイト中28/59と15/59、実行できた48サイトだけでは28/48と15/48になる。取得不能サイトを除いた値を80サイト全体の性能として扱わない。

先行6サイトの採用分はcold 18 HTML/6型確認、warm 12 HTML/3型確認。新規74サイト分はcold 107 HTML/54型確認、warm 67 HTML/38型確認である。新規分のサイト和集合はHTML37/74、目的型21/74。先行の失敗試行4件も費用と履歴に残し、再取得していない。

## 実施・停止・未実行

入口の記録は80/80サイト。入口で停止17サイト、代表観察中の停止4サイトを含む。コード生成・初回実行まで進んだのは59サイト（先行6＋新規53）。未着手サイトは0、対象HTMLの初回実行ができなかったサイトは21。これらを分母から除いていない。

- 入口停止: 22,23,24,27,39,46,47,48,49,55,57,60,65,66,68,70,79。HTTP 403は11、401は1、200でも停止判定は1、応答status不明は4。全17件をHTTP拒否と同義には扱わない。スコープ・画面判定も停止要因に含む。
- 初期観察停止: 35,38,52はHTTP/画面/スコープの停止、43は代表観察の異なる文書上限到達。
- cold実行中の停止: 26,28,37。後続のwarm取得はしなかった。
- warm未実行32サイト: 上記の初期停止21、cold停止3、未観測の別一覧候補なし8（03,07,19,21,29,34,40,77）。最後の8件は `holdout-start.json` 欠如による例外も生ログに残っている。
- 31,32,33は生成コードが `api.links` に文字列でない引数を渡した。cold/warmとも実行契約違反として残し、生成後の修復はしていない。
- warmの予算停止4サイト: 16,44,45,72。44では1件の有効HTML保存後に停止した。停止したrunでも、その前に保存できた証拠を別に数えた。

単純な終了statusと独立検査は一致しない。生結果の `html_saved` はcold45/warm29サイトだが、有効HTMLが残ったのは43/28サイト。最終判断には `summary.json` と `item-evaluation.jsonl` を用いる。

## GとR、目的対象への到達

保存コードのSHA一致と別PIDでcold/warmを実行したのは48/59サイト（全80に対して48/80）。未観測のURLへ同じコードを使って有効HTMLを保存できたwarmは28/80サイト、目的型まで確認できたwarmは15/80サイト・41ページ。

より保守的に、初期観察・coldとのURL同一性（監査時のみfragmentを除外、queryは維持）と原HTMLハッシュも検査した。204ページのうち新しい文書URLかつ未観測ソースハッシュは199件、目的型確認は99件。cold 120/58件、warm 79/41件である。同じ本文の再取得を少量観察からの一般化と同じに扱っていない。保存対象間の完全一致URL・final URL・ソースハッシュ・DOMハッシュの重複はいずれも0だった。

これはRの成立例を示すが、任意の指定対象への再現率ではない。独立手順で先に固定した候補114 URLのうち、有効HTMLまで保存できた一致URLは6/114、目的型確認できた一致URLは0/114。候補選定は共通のURL・リンク形状による方法であり、人手で正解型を確認したgoldではない。探索で見つかった227対象だけを分母にして高い目的到達率を主張しない。意味的なrecallは不明。

今回のwarm入口も共通の候補選定器が選んだもので、一覧ページと独立確認できていない例がある。楽天では商品詳細がwarm入口に選ばれた。このため「別カテゴリ・同型一覧への一般化」を全48サイトで達成したとはいえない。

G（観測済みグラフからの経路検索）は今回測定していない。分母0、N/A。同一コードの再実行をGの経路検索成功に置き換えていない。Rの一般化と、グラフ内経路探索の評価は分けた。

## LLMを使った範囲

入口の代表HTML・観測リンクから一覧候補を選び、一覧のスクロール前後と代表詳細を含む最大3異なるURLの観察から巡回コードを生成した。本文内の指示はデータとして扱った。未観測ページや採点対象・正解経路は生成担当へ渡していない。

新規分はsampling 13ジョブ・57サイト、code generation 19ジョブ・53サイト。入力ファイルの文字数はそれぞれ4,681,691と10,062,346（合計14,744,037）。これは転送したnative contextや実トークン数ではない。指定モデルは `gpt-6-luna`、実モデルIDは独立検証できず、実呼び出し回数・入力出力トークン数・モデル実行時間・金額は不明。先行6サイトの成功8ジョブ・中断dispatch 2件は別に保持した。

実行器の107 phase結果は外部LLM呼び出し0、ローカル推論0。追加の本文別LLM型判定、未観測ページを見た後の生成コード修復も0。最終集計は固定Python判定器で実施した。基盤修正・レポート作成に使ったnativeモデルの利用量は別枠で不明。

1ページごとのLLM判断をなくす条件は達成したが、初期入力は軽量とはいえない。これだけでは低トークン・低金額を実証できない。Skill採用前に代表DOMとリンク候補の圧縮、実usageを取れる生成経路への変更が必要。

## 通信・時間・取得費用

| 段階（費用台帳） | 文書予約 | UI操作 | request event | XHR/fetch | 本文bytes | ブラウザ秒 |
|---|---:|---:|---:|---:|---:|---:|
| 入口観察 | 97 | 80 | 8,431 | 1,134 | 503,171,163 | 716.574 |
| 一覧・代表詳細観察 | 126 | 167 | 12,534 | 2,045 | 757,666,137 | 1,063.081 |
| 独立候補選定用の取得 | 57 | 201 | 6,364 | 1,166 | 360,307,568 | 1,115.887 |
| cold（先行修復試行含む） | 227 | 326 | 21,861 | 4,070 | 1,321,350,850 | 1,803.756 |
| warm（先行修復試行含む） | 140 | 256 | 17,272 | 3,223 | 967,088,466 | 1,412.955 |
| 合計 | 647 | 1,030 | 66,462 | 11,638 | 3,909,584,184 | 6,112.253 |

HTTP response eventは62,044。文書647予約にはmain frame 633と子frame等14を含み、成功文書数ではない。通信数はブラウザのrequest/responseイベント数で、wire上のユニーク通信数や課金bytesとは同一ではない。本文bytesは取得できたresponse bodyの計測量。新規74サイト分は571文書・905操作・63,399 request・5,744.309ブラウザ秒。先行分76文書・125操作・3,063 request・367.944秒を一度だけ加算した。

新規取得の最初から最後までは約49.1分。ブラウザ時間は並行セッションの合算で、モデル待機やレポート時間を含まない。USD/JPY等の費用は未計測。台帳の7資源量に欠測行は0だが、nativeモデル利用量は欠測である。

同時サイト最大4、サイト内1、文書間待機は練習1秒・実サイト5秒。Chrome 157.0.8103.51 / Playwright 1.63.0 / Node 22.15.0 / Python 3.13.7、ローカル回線で取得した。各セッションは新しいブラウザcontext。CookieとHTMLキャッシュをwarmへ移していない。robotsはサイトマップ発見用で、閲覧停止gateには使っていない。HTTP/画面/スコープで停止した先を回避して取得する処理は追加していない。

初期探索からの費用もcold/warm合計のサイト枠へ計上し、サイト別文書・UI上限、全体1,450文書上限を検証した。対象選定の `evaluation-cost.jsonl` は台帳の内訳であり再加算しない。保存物を読むだけの集計を新規取得件数にしていない。10/30/100件の連続増分実験、同条件B0/B1/B2比較は今回の追加実験では実施していない。

## 楽天・au PAYマーケット

楽天61: 検索一覧から実際の `item.rakuten.co.jp` リンク3件をたどり、複数店舗への遷移は記録できた。しかしcold 3件とも空のSPA枠相当、warmも原HTML欠損1・空枠2で、有効HTML0、目的型0。200や実行器の保存フラグを成功としなかった。広告・店舗・カテゴリの完全な識別、店舗別の描画完了、別一覧候補の選定は未達。warm入口が商品詳細になった候補選定ミスも含む。

au PAYマーケット62: 指定の公開店舗カテゴリから商品3件を取得し、商品型を3件確認。同じコードを未提示の別カテゴリ `/user/16630442/shopcategory/1076775?sort1=shopdisp,a&sort2=ppop30,d` に適用して、新商品1件の有効HTMLと商品型を確認した。クエリ付きの観測リンクをそのまま使った。同一店舗内の別カテゴリまでの実証であり、別店舗への一般化や商品ID単位のquery同一性は未検証。固定商品ID・手書き正解経路は埋め込んでいない。plus.wowma.jp等へ範囲を増やしていない。

## 採否とSkillへの最小統合

限定採用: 「代表観察 → コード生成 → ハッシュ固定 → 別プロセスでプログラム実行 → 原HTML/DOM保存 → 独立した型確認」を任意の取得モジュールとして追加する。観測リンクのみ、有限ループ、停止理由、費用台帳、再開可能な保存形式は再利用できる。

追加実装が必要: 一覧候補を本当の一覧と確認する工程、目的型の識別とドリフト検出、型を保った候補探索、生成API契約の事前検査、描画完了条件、同一文書状態の重複処理、実usage計測。iframe/Shadow DOM内部の系統的なリンク探索や仮想スクロール全区間の取りこぼし防止は今回の実行器では検証できていない。スクロール後リンクの新規HTML保存6件は先行デモの成果で、全サイトの動的操作対応を意味しない。

サイト専用コードは59サイト分あり、すべて初期代表観察からLLM生成した。人によるセレクタ・経路の追記0、未観測結果を見た後の生成コード修復0。共通実行器は4サイト並列、停止gate、文書予算の停止理由、保存パスの可搬性を整備した。文書予算の事前停止判定は新規warm前に追加したため、実行器全体のcold/warmソースが完全一致するとは主張しない。生成サイトコードのSHAは一致する。先行6サイトにあった共通実行器修復の費用も保持した。

今回の方式だけで「目的になりそうなページを割り出してどのサイトでも到達」を達成したとはいえない。サイトマップURL選別だけの先行runと直接比較できるランダム化試験でもない。サイトマップで足りるURLは通常取得へ回し、不足部分にこの生成コードを使う構成は次の統合案であり、今回の数値に未実施の改善を加えていない。

## 保存物・検証・再実行

`targets.jsonl` は80サイト、`results.jsonl` は実行できた107 phaseの生結果、`operational-results.jsonl` は未実行を含む160行、`item-evaluation.jsonl` は227対象の独立検査結果。`artifacts/` と `adopted-pilot/` に原HTML・DOM・状態・通信/操作ログを保持した。凍結コードと生成時の観察は `sites/`、入力/出力ハッシュは `model-jobs/` にある。

`verify` と `integrity-audit.json` は成功。647件の文書予約イベント、303セッション台帳、446保存証拠ファイルのハッシュを検査した。モデルjob入力・出力ハッシュと固定判定器・候補選定手順のハッシュも確認した。移入したrepair 4セッションの参照パスとcanonical phaseの参照先を補正し、費用・判定・元runは変更していない。

専用Nodeテスト4/4、CLIテスト6/6成功。指定の `test_lab*.py` は87件中2失敗・5skip（80成功）。既存のWindows Camoufoxパスの正規表現とNO_PROXY環境期待値が原因で、今回の変更対象外として残した。ログに詳細がある。

再集計はネット取得・モデル推論を伴わない。実行コマンドとチェックポイント復元は `HANDOFF.md` を参照。未実行・失敗を消して成功分だけ再実行する運用はしない。

## 全80サイト

数値は「有効HTML / 目的型確認」。`prior` は先行結果の再利用。0は確認できた成果が0を意味し、そのサイトに対象が存在しないという意味ではない。停止理由は保存したmetadata由来であり、未確認の全画面をN/Aとはしていない。

| ID | ドメイン | 出典 | cold HTML/型 | warm HTML/型 | 停止・未確認の理由 |
|---:|---|---|---:|---:|---|
| 01 | toscrape.com | prior | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 02 | webscraper.io | prior | 3/3 | 0/0 | warm: no_valid_detail |
| 03 | scrapingcourse.com | new | 3/3 | 0/0 | warm: 別一覧候補なし |
| 04 | scrapethissite.com | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 05 | web-scraping.dev | new | 3/3 | 3/3 | 目的型確認あり |
| 06 | listly.io | new | 3/0 | 0/0 | cold: 型未確認; warm: no_valid_detail |
| 07 | automationexercise.com | new | 3/3 | 0/0 | warm: 別一覧候補なし |
| 08 | oxylabs.io | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 09 | scrapingsandbox.com | prior | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 10 | magicscraper.app | prior | 3/0 | 0/0 | cold: 型未確認; warm: no_valid_detail |
| 11 | python.org | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 12 | mozilla.org | new | 3/3 | 3/3 | 目的型確認あり |
| 13 | nodejs.org | new | 3/3 | 3/3 | 目的型確認あり |
| 14 | typescriptlang.org | prior | 3/3 | 3/3 | 目的型確認あり |
| 15 | rust-lang.org | new | 2/2 | 2/2 | 目的型確認あり |
| 16 | go.dev | new | 2/2 | 0/0 | warm: cumulative_budget |
| 17 | kubernetes.io | new | 3/3 | 3/3 | 目的型確認あり |
| 18 | docker.com | new | 3/1 | 3/3 | 目的型確認あり |
| 19 | djangoproject.com | new | 3/3 | 0/0 | warm: 別一覧候補なし |
| 20 | scikit-learn.org | new | 3/3 | 3/3 | 目的型確認あり |
| 21 | arxiv.org | new | 3/3 | 0/0 | warm: 別一覧候補なし |
| 22 | nih.gov | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 23 | nature.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 24 | sciencedirect.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 25 | plos.org | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 26 | frontiersin.org | new | 0/0 | 0/0 | cold: main_http_login_challenge_or_scope; warm: cold_phase_ineligible |
| 27 | doaj.org | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 28 | semanticscholar.org | new | 0/0 | 0/0 | cold: main_http_login_challenge_or_scope; warm: cold_phase_ineligible |
| 29 | crossref.org | new | 0/0 | 0/0 | cold: no_valid_detail; warm: 別一覧候補なし |
| 30 | zenodo.org | new | 3/1 | 0/0 | warm: no_valid_detail |
| 31 | e-stat.go.jp | new | 0/0 | 0/0 | cold: 生成API引数エラー; warm: 生成API引数エラー |
| 32 | e-gov.go.jp | new | 0/0 | 0/0 | cold: 生成API引数エラー; warm: 生成API引数エラー |
| 33 | stat.go.jp | new | 0/0 | 0/0 | cold: 生成API引数エラー; warm: 生成API引数エラー |
| 34 | jma.go.jp | new | 3/0 | 0/0 | cold: 型未確認; warm: 別一覧候補なし |
| 35 | gsi.go.jp | new | 0/0 | 0/0 | cold: main_http_login_challenge_or_scope; warm: representatives_missing |
| 36 | nasa.gov | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 37 | noaa.gov | new | 0/0 | 0/0 | cold: main_http_login_challenge_or_scope; warm: cold_phase_ineligible |
| 38 | worldbank.org | new | 0/0 | 0/0 | cold: main_http_login_challenge_or_scope; warm: representatives_missing |
| 39 | oecd.org | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 40 | www.who.int | new | 3/0 | 0/0 | cold: 型未確認; warm: 別一覧候補なし |
| 41 | gutenberg.org | new | 3/3 | 3/3 | 目的型確認あり |
| 42 | openlibrary.org | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 43 | archive.org | new | 0/0 | 0/0 | cold: representative_distinct_document_limit; warm: representatives_missing |
| 44 | aozora.gr.jp | new | 3/0 | 1/0 | cold: 型未確認; warm: document_budget |
| 45 | metmuseum.org | new | 3/0 | 0/0 | cold: 型未確認; warm: cumulative_budget |
| 46 | si.edu | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 47 | britishmuseum.org | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 48 | www.europeana.eu | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 49 | artic.edu | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 50 | www.rijksmuseum.nl | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 51 | wikipedia.org | new | 3/3 | 3/3 | 目的型確認あり |
| 52 | sciencenews.org | new | 0/0 | 0/0 | cold: main_http_login_challenge_or_scope; warm: representatives_missing |
| 53 | bbc.com | new | 3/3 | 3/3 | 目的型確認あり |
| 54 | theguardian.com | new | 3/2 | 3/3 | 目的型確認あり |
| 55 | reuters.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 56 | nhk.or.jp | new | 3/0 | 0/0 | cold: 型未確認; warm: no_valid_detail |
| 57 | apnews.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 58 | itmedia.co.jp | new | 1/1 | 0/0 | warm: HTML検査不合格 |
| 59 | impress.co.jp | new | 3/3 | 3/3 | 目的型確認あり |
| 60 | gigazine.net | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 61 | rakuten.co.jp | new | 0/0 | 0/0 | cold: HTML検査不合格; warm: HTML検査不合格 |
| 62 | wowma.jp | new | 3/3 | 1/1 | 目的型確認あり |
| 63 | amazon.co.jp | new | 3/3 | 3/2 | 目的型確認あり |
| 64 | yahoo.co.jp | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 65 | yodobashi.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 66 | biccamera.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 67 | kingjim.co.jp | prior | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 68 | uniqlo.com | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 69 | ikea.com | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 70 | www.nitori-net.jp | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 71 | www.japan.travel | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 72 | jalan.net | new | 0/0 | 0/0 | cold: HTML検査不合格; warm: document_budget |
| 73 | rurubu.jp | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 74 | gotokyo.org | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 75 | ja.kyoto.travel | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 76 | osaka-info.jp | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
| 77 | www.visit-hokkaido.jp | new | 3/0 | 0/0 | cold: 型未確認; warm: 別一覧候補なし |
| 78 | suumo.jp | new | 0/0 | 0/0 | cold: no_valid_detail; warm: no_valid_detail |
| 79 | homes.co.jp | new | 0/0 | 0/0 | cold: entry_blocked; warm: entry_blocked |
| 80 | athome.co.jp | new | 3/0 | 3/0 | cold: 型未確認; warm: 型未確認 |
