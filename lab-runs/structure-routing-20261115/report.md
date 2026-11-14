# サイト構造解析・巡回経路比較：中間結果

2026-11-15。ユーザー指定の区切りでローカル取得を停止し、クラウドへ引き継ぐ。**全80サイトの比較と最終採否判断は未完了**。

## 実施範囲

80登録ドメインを管理し、独立評価v2は全80サイトを実施した。47サイトで計88対象を代理検証、robots制限12、ブロック8、検証未成立13。13件を取得不可と断定しない。

B1/C1の主比較は練習10サイト・cold/G/R各1行の合計60行。実サイト70件、B0、公開の追加動的課題、段階C、C2は未実施。練習内でgoldを検証できたのは4サイト・7対象。未実施は分母から消していない。全80の最終運用成績は未確定である。

## 練習10サイトの成績

| 指標 | B1 | C1 |
|---|---:|---:|
| 固定gold対象の到達・期待型HTML保存 | 0/7 | 1/7 |
| Gが成功したサイト | 7/10 | 6/10 |
| Rが成功したサイト | 3/10 | 3/10 |
| gold URLに到達したが型等が不一致 | 2 | 2 |
| 文書要求の予約カウンター | 143 | 151 |
| document response観測数 | 139 | 153 |
| HTTP request観測数 | 3664 | 3576 |
| XHR/fetch観測数 | 353 | 311 |
| UI操作 | 54 | 60 |

Gのページ試行はB1 12/17、C1 13/19。RはB1 7/24、C1 8/21。G/Rは意図したURL、cold観測、タイトル・型・保存HTML・navigation error・凍結規則出所を照合した機構の成績で、固定gold対象の到達とは別。単なる200やグラフ生成を成功にしない。

Gは既観測グラフの再生、Rはcoldで本文未観測のページへの規則適用。別プロセスのwarmで資産を読み込んだ。全80分母の運用結果と、gold取得・検証可能47サイト分母の結果はscore-v3に保持したが、そのうち70サイトの候補が未実施であり、完成した80サイト性能とは解釈しない。

固定gold成功には最終URL、200、navigation errorなし、対応するタイトル・期待型・非空HTMLを要求する。goldと型は固定コードによる代理検証で、人手による完全な意味的正解集合ではない。誤成功2件ずつはURLだけなら成功と見えたが型等が不一致の件数で、余分な別ページの数ではない。

## 費用・条件

初期探索の方式時間はB1 143.598秒/C1 126.812秒、G/Rは218.955秒/160.828秒。修復0秒。待機・ブラウザ・I/O込みで、CPU時間や金額ではない。全体wall time約363.1秒、同時2サイト。

Chrome 157.0.8103.51、Playwright 1.63.0、headless、ja-JP、Asia/Tokyo、1365×900、cache/service worker無効、新規context/Cookie。同一サイトのB1/C1条件は共通。練習初期観察は最大4ページで実施した。クラウドは別回線・ブラウザなら別環境層として記録する。

gold v2は189 document attempts、186 responses、18,106 requests、2,806 XHR/fetch、523,720,562 bytes。v1 preflightは25 captured documents/742 requestsで、正確な文書試行数とXHRは欠測。v1の費用も含め、今後の総予算予約は742を文書数の保守的上限として扱う。

主比較は294文書予約、292 document responses、7,240 requests、664 XHR/fetch。別の誤preflightはrobots.txtへ8 requests、課題文書0。台帳上HTTP合計26,096。APIRequestContextのリダイレクト等を含む完全な通信計測は未保証。欠測を0にしない。

ページ判断LLM、外部の生成・修復・埋め込みAPI、主比較のローカルモデル推論は0。設計・調査・レポート作成のCodex nativeモデル使用量は未公開でunknown。調査はgpt-6-luna指定、実モデルの独立確認はできずmodel_runtime_verified=false。サイト専用セレクタ・手書きサイトフロー0。共通実装のpreflight修正あり、厳密な修正回数は未計測。

## 不具合と予算逸脱

**07/C1で文書予約40に対しresponse 41を観測し、1文書の上限逸脱がある。** 10/C1にも20/21の計数差がある。原因は未確定で、routeを通らないredirect等の可能性を断定しない。このケースを上限内の比較実績と扱わず、追加公開取得前にredirect/iframe fixtureでハード上限を修正する。証拠や結果行は変えずdeviations.jsonへ記録した。

v1評価器は対象選定・偽ブロック判定の問題があり、候補測定前にv2を固定した。v1原本と複製を保持し費用も含める。候補にgold HTML/専用経路を渡していない。

challenges自己テストの末尾フラグ解釈ミスでrobots.txtへ8 requestsが発生した。初報4はサイト数との誤読で訂正。20行はaccidental_preflight_flag_parseと標識し、本番完了ではない。公開実行には--execute=trueと明示--runを必須に修正した。mainrunnerも末尾--resumeを修正し、完了01の再開で新規取得・結果増加がないことを確認した。測定時ソースをimplementation-snapshotsへ保存した。

## 候補と暫定判断

C1は今回の自作汎用DOMリンク群・ページ領域・URL深さ・ページ送りによる構造ルール方式。公開候補製品の成績ではない。固定対象適合率が低く、現在のC1のSkill採用は保留する。

- pinchtab-webgraph：Windows CLIでlocalhostの1状態まで。materialize失敗、実サイトG/R未証明。
- smart-crawler：古いJava/Maven/Neo4j構成、Java/Maven不在、Windowsのcolonを含む追跡パス問題、root license未確認。今回実行不可。
- Autopager：PyPI 0.3.1の配布CRFは248 bytes/5リンクfixtureを分類成功（2推論、約27.3ms/1.4ms）。Git 0.4.0は既定重み欠如で失敗。ページ送り補助として実サイトでの価値は未測定。
- MiniLM-L6-dmr：約90.9MB、DOM候補ranking向け。未取得。候補選択失敗に限るC2は今後。
- JevForge：約3.42GB、候補decision向け。未取得。
- Qwen-HTML/Co-Scraper：約8GBの公開重みと推論endpointを前提とする抽出コード生成が中心。経路用途の実測未了。
- WebNavigator：事前グラフとオンライン実行の分離は参考になるが、資産/API依存の適応と全工程測定は未了。

正確なcommit/model revision、配布条件、依存、ログはcandidate-audit/audit.jsonとaudit.md。元Skillは既存パーサーのASTから代表経路を作る方式で、ライブ構造探索・大量反復が既存機能だけでできたとは扱わない。統合にはURL発見・型別規則資産・予算実行器・raw HTML/DOM/状態保存・独立G/R検証が必要。

## 未完了とクラウド再開

楽天とau PAYは独立対象選定で各3文書を保存・代理検証した。楽天1,145 requests/158 XHR、au PAY 353 requests/53 XHR。B1/C1による商品遷移、別店舗/カテゴリ一般化、query同一性は未試験で、独立評価の取得を経路生成の成果としない。

10→30→100の独立対象到達と増分費用、構造資産の大量再利用、モデル使用量の増加、C2の改善価値は未測定。bulkはローカル予算fixture、challengesはLoad More/scroll/AJAX/rawDOM差/shadow/iframe/redirectのlocalhost自己テストに通った段階。公開課題成功とはしない。

ユーザーのサイトマップ方式B2は現方式全滅時の追加案として保留し、「今のはそのまま続けて」の指示を維持する。

再開順序・制約・コマンドはHANDOFF.md。最初に文書カウンターを修正し、完了10サイトを再取得せず残り70サイトと段階C/Dへ進む。
