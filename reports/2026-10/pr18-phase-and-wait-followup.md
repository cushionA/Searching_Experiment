# アセンブル版の差の切り分けと、追加DOM待機のクロール検証

## 結論・マージ判断

アセンブル版は`camoufox-fourplay`（Camoufox＋4play＋web-ext）。**追加待機の機能は動くが、今回の実サイトでは検索結果の改善を確認できなかった**。遅延JS fixtureでは元条件0/4→追加待機4/4。Brave実サイトは両条件とも関連語一致34リンク、追加待機は2/2 ready。Bingは両条件で関連候補0、追加待機も2/2 timeoutだった。Bingの無関係な結果の原因は未解明。

ユーザーの条件付き承認「それでいけたらまとめてメインにマージ」は受領したが、実サイトでの改善とBingの目的達成を確認できないため、今回は**マージしない**。PR17/18を残し、部分成功を全面成功に読み替えない。新しい有料経路、認証、CAPTCHA解答・操作、追加サイトは使用していない。

判定は **B（条件付き）**。待機は未完成DOMを避ける実用的な観測オプションとして有用。ただし待てばブロックを回避できる、検索品質が上がる、Camoufoxより速い、といった結論は出さない。

## 1. 同じload待機で残る差

`phase-02`はコード`45cdf3e`、同一Camoufox 152.0.4-beta.30・生成fingerprint・表示・Cloud host・Docker image・load gate。外部通信禁止。500ms遅延画像のfixtureに対して各方式4新規profile、各profileで初回1回＋後続2回。計12起動・36ページ。順序はH/P/F、F/P/H、P/H/F、F/H/Pで、方式間の前後を均衡させた。

- H: hybrid。従来どおり新規タブを作成。
- P: Camoufox/Playwrightでタブ再利用。
- F: **同じPでタブを毎回新規作成・旧タブを閉じることだけを変更**。
- coldは新規profileの初回であり、OSディスクキャッシュcoldではない。warmもHTTPキャッシュは`no-store`。親Nodeでは依存を事前ロード。
- phase-01は検証プロセスが一部重なった予備試験。結論には使わず別ZIPに保存。phase-02測定中はテスト・Docker buildを併走させていない。

|中央値 ms|H hybrid|P 再利用|F 新規タブ|
|---|---:|---:|---:|
|open全体（4起動）|2027.39|1648.84|1709.56|
|cold観測（4ページ）|773.90|677.38|955.68|
|warm観測（8ページ）|728.71|639.20|847.48|
|warmタブ準備（goto外）|0 ※native goto内|0|194.30|
|warm goto|606.66|529.27|544.96|
|warm DOM content読取|17.25|4.41|4.68|
|warm window.stop|2.14|2.83|2.66|

計測はネイティブ呼出しを包む壁時計。content内部のevaluateは重複計上しない。100ms観測、証拠保存、event回収等も全体に含む。中央値同士を厳密に加算してはいけない。

**一要因で実証した差**: P→Fではwarmのblock内中央値差が203.21～216.51ms（4/4 blockで増加、差の中央値208.35ms）。新規タブ政策はこの実装ではコストになる。Fはhybridより遅いため、H/Pの大小を「4playという制御方式そのもの」の差と断定できない。

H−Pのwarm block差は67.89～101.95ms（中央値86.00ms）。大部分はgoto区間（中央値差約77ms）、DOM content読取は約13ms差。Hのnative内訳はtab_open約17～26ms、旧タブclose約9～24ms、残りのcomplete gate待ち約561～575ms。これらは直列計測だが、ブラウザのloadはtab操作と並行するため、原因を単純加算できない。ブラウザNavigationTimingも保存した。

**DOM通信回数の一要因試験**: 同じwarmページ上でtitle・本文印・完成印の3値を、3回のevaluateと1回のevaluateで取得。順をABBA/BAABに変え、値の一致をassertした。Hは中央値4.76→1.83ms、blockごとの削減2.29～3.80ms。Pは2.58→1.02ms、削減1.17～1.72ms。往復をまとめる効果は確認できたが、約86msの全体差の主因とは説明できない。32 extraction試行/方式だが独立な標本は4profileであり、有意差検定や一般化はしない。

起動ではHのprofile/server準備66.32ms、launcher開始→拡張接続1952.20ms、session設定8.64ms。PはlaunchPersistentContext→Juggler接続1513.39ms、UA/viewport等metadata132.82ms。両方ともブラウザ起動とprotocol接続を含む区間であり、純粋なOS process spawnと接続だけを分離した値ではない。各方式に等価なbrowser-ready信号がなく、追加のブラウザ内部traceなしには分離不能。

**残る未解明部分**: H/Pは拡張のresponse filter、proxy relay、container、起動prefs、page lifecycle、証拠計装が同時に異なる。残るgoto差を単一の要素に帰属できない。切り分けを続けるなら同一ページ・同一relay・同一containerで、拡張filterだけのON/OFFとネットワーク開始/終了traceが必要。今回、証拠欠落を生むfilter無効化を本比較へ混ぜていない。

## 2. 既存HTTP bridgeの観測

既存`fourget-selfhost/fourplay/server.cjs`も同じCamoufox・fixtureで動かした。これはnative hybridとは別経路。コードは変更せず、get_tab_list/イベントの受動traceを追加。200/503を各2回、**DOM完成0/4**。約211～224msで返り、500ms遅延画像を待たなかった。

traceでは新規タブid=3が`about:blank / complete`の時点でpollされ（23:39:28.333 UTC）、実URLのmain responseは28.411、window.stop後のcomplete/abortが28.512～513。既存bridgeはURLやmain-responseを確認せずstatusだけでgateを通すため、初期blankのcompleteを実ページ完了と取り違える。これが、このfixtureでbridgeが「速く見える」具体的な由来である。native hybridのcomplete-event経路とは同一ではない。

この既存bridgeの修正は今回の追加待機機能へ混ぜていない。実装修正にはURL/応答とcompleteの対応確認、およびredirect・503・subresource失敗の回帰試験が必要。既存mainにもある挙動で、新しいDOM待機による回帰ではない。

## 3. 追加待機の実装とfixture

通常のhybridは既に`tabs.onUpdated status=complete`を待ち、その後既存のobserve時間を置く。loadは遅延JS後の本文準備完了を保証しない。

`ToolAdapter.options.domWait`を明示した場合だけ、共通runnerが既存observe後・DOM証拠保存前に`waitForDOMStability`を実行する。今回は最大5000ms、250ms間隔、本文200文字以上、クエリの両語が本文に存在し関連語一致リンクが1件以上、本文とリンク集合が750ms安定、をready条件にした。challenge/access-deniedなら停止。クリック・CAPTCHA操作・navigationは行わない。stalled DOM readもdeadlineで切り上げる。結果は`dom_wait.outcome`に保存し、HTTPの`content_observed`だけでreadyとは扱わない。

遅延JS fixture（`wait-fixture-02`）はload後900msに本文とリンクを追加する。fixtureのobserveは100ms、追加待機なし0/4、あり4/4 ready、全8run verify成功。最初のfixtureはcharset未指定で日本語が文字化けし、terms不一致でtimeoutした。UTF-8を明示して新runで再検証し、失敗runも別保存。

## 4. 実サイト比較

測定コードcommit `c4adaa1`、`wait-public-02`。**Cloud側ブラウザ**で、既存catalogのBing/Braveのみ。query「早稲田大学 教員」。両方式で同一binary/fingerprint/host/display/proxy/CA、navigation 25秒、元条件observe 6000ms。差分はその後の最大5秒DOM待機だけ。新規profileごとに検索URLへ直接1回、全8ナビゲーション。サイト別開始間隔20秒以上、順序反転、各条件2反復。通常のsubresourceは制限せず、台帳上の通信件数は以下のとおりで、8「HTTP要求」ではない。

|対象・条件|関連語一致リンク（各反復）|本文文字数|経過ms（起動除外）|追加待機|台帳通信件数|
|---|---|---|---|---|---|
|Bing 元条件|0 / 0|2735 / 2751|7518 / 7267|なし|615 / 621|
|Bing 追加待機|0 / 0|2090 / 2700|12423 / 12273|timeout / timeout|601 / 603|
|Brave 元条件|34 / 34|5120 / 5120|7514 / 7166|なし|173 / 173|
|Brave 追加待機|34 / 34|5120 / 5120|8395 / 7978|ready / ready|173 / 173|

全8ページHTTP 200。Braveの一致リンク34件中16件はwaseda.jpドメインで、大学の教員紹介URL等を含む。リンク先は訪問していない。意味的関連性・網羅率の正解採点は未実施。Bingはqueryを含むtitleでも本文がUSPS等の別内容で、待機では改善しなかった。原因がサービス、proxy経路、返却内容のどこにあるかは未確認。challenge/認証/CAPTCHA操作は発生していない。

最初の公開試験`wait-public`は2回ともnavigation timeoutとerror pageへの拡張host permissionエラーで停止、残り6セルはskip。hostからのHEAD診断ではBing/Braveとも200だった。Dockerとhostでproxy hostnameの解決先が異なったため、**proxyを解除せず**、Dockerの`--add-host`で既存hostが解決した同じ管理proxyへ対応付けた。proxy URIとCAを維持し、新runを実施した。HEAD診断2回、失敗2ナビゲーション、本試験8ナビゲーションを別記録とし、失敗を母数から隠さない。背景通信の完全な経路は未測定。

## 検証・保存

- 最新コードのPython lab suite: 151件成功、15 skip。Node native/Camoufox/DOM待機: 19/19成功。source package検証成功。
- Docker build成功、コンテナ内lab 151件（41 skip）とcloud environment 7件成功。network none/read-only demo/verifyは成功（ok:true）。公開source ZIPを展開し、そのディレクトリで151件成功、15 skipを再確認した。
- phase-02: 12 native cells＋bridge=13 verify成功。完全ZIP SHA256 `f042c91c91a3948af57786adebeb26591c737a5c38081d7399a72cd113010768`。
- public-02: 全8 verify成功、完全ZIP 10,497,712 bytes、SHA256 `fc716a44702e5f3618461cd59dde461f2d912fe62f04696c04a1a24fdd9e9704`。
- `[数値・条件・trace・完全checkpoint](../../benchmarks/pr17-20261006/)`。ZIPはraw DOM/body/ledgerと実行ソースを含み、browser profile/資格情報/依存バイナリを含めない。予備試験を削除せず別保存した。
- [元条件の9列表](../../docs/search-services/results/20261006-hybrid-load-baseline/results.md)と[追加待機の9列表](../../docs/search-services/results/20261006-hybrid-dom-stable/results.md)。各表は反復2の観測を表示、JSONに両反復を保存し、残る48サイトは未測定と明記。
- 測定後の汎用化修正はterms未指定時の安定本文判定のみ。実測のterms指定経路は不変。CIは最終remote headに対して確認しPR本文へリンクする。

## 導入判断・次

待機はopt-inで、既存利用の既定動作を変えない。ready/timeoutを別指標として扱う条件で導入候補。無関係なHTTP200本文を成功に数えないことが重要。今回のデータから公開サイトの検知耐性改善や全対象への導入は推薦しない。

この比較はここで区切る。残課題はBingのクエリ不一致とbridgeのblank-complete誤認、H/Pのgoto差の細分化。待機延長や大規模クロールで無条件に追試せず、別の検証計画として扱う。条件付きmainマージは未実行。
