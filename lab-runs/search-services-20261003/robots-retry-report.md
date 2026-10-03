# robots取得失敗6対象の再確認（2026-10-03）

ユーザーの「取得失敗はやって」に基づき、元の台帳・残予算を使って6対象をheadful Patchrightで再確認した。公開トップまたは既知転送先のHTTP 200を4対象で確認した。検索結果本文まで取得できた対象は0件。

|対象|観測結果|検索1件の状態|
|---|---|---|
|Firecrawl|www付きのrobots・公開トップともHTTP 200。SearchのPlayground入口とAPIキーのコード例を保存。|API呼出し未実施。公開トップの取得成功として分類。|
|Tavily|www付きのrobots・公開トップともHTTP 200。公開ページにSearch製品の入口。|API呼出し未実施。公開トップの取得成功として分類。|
|OneSearch|元トップでYahooへの遷移要求を観測。既知の転送先YahooトップはHTTP 200、OneSearchサポート終了の告知あり。|転送先Yahooの検索URLは明示的なrobots禁止で停止。結果未取得。|
|Peekier|robots.txtはKagiへ転送。Peekier本体のトップはCloudflare HTTP 403。別段階で訪れたKagiトップはHTTP 200。|Kagi検索は302からTurnstileへ遷移し、HTTP 200で「Verifying you’re human」。結果未取得。|
|Phind|robots・トップ・検索URLでCloudflare HTTP 403。検索本文はsecurity verificationの画面。|検索URLまで要求したが、確認画面で停止。結果未取得。|
|goo|robotsはHTTP 403で`No approved upstream IPv4 address`。トップ・検索URLは`ERR_TUNNEL_CONNECTION_FAILED`。|検索URLへの接続を試みたが、環境proxyの失敗。サイト側の取得不可とは確定できない。|

OneSearchのYahoo移転先本文には、次の告知があった。

> OneSearch product is no longer supported and you have been redirected here to start a new search. Please review the privacy policy and terms below before continuing.

出典: `https://search.yahoo.com/?fr2=p:onesearch,mkt:us` の保存DOM `f15af8ed0c8e4d685f185d865f330599191fbfeaa14fd92d31f3f98fc3861238`、metaのmain record 147。元OneSearchトップのHTTP応答statusは保存できていないが、Yahoo宛ての遷移要求は`blocked_requests`へ記録され、その既知URLを別段階で訪れた。PeekierについてKagiへの転送が観測されたのはrobots.txtで、Peekier本体からKagiへの本文転送成功とは扱わない。

|対象|元の累積リクエスト|再確認後の累積リクエスト|再確認後の計上bytes|
|---|---:|---:|---:|
|goo|1|7|8,388,608|
|OneSearch|1|7|2,315,759|
|Peekier|1|18|2,608,273|
|Phind|1|10|2,619,431|
|Firecrawl|1|4|1,202,056|
|Tavily|1|10|2,702,508|

追加は50リクエスト。元の各対象25リクエスト・8MiB上限を共有し、予算はリセットしていない。gooは接続失敗ごとの予約bytesを保持したため上限に達した。計上bytesは実際の転送量を意味しない。

robotsが取得不能/HTMLだった場合だけ、今回明示した限定観測を有効にした。GET・同一origin・元の全予算を守り、明示的なDisallowと過大crawl delayは維持した。転送は保存済みLocationで分かっていたoriginだけ追跡した。CAPTCHAの外部widgetとPOSTは既存の制限で停止し、突破難度は評価していない。OneSearch/Peekierの転送先検索URLは提案ルートであり、元サイトから検索語が転送されたという証拠ではない。

原設定は`lab-runs/search-services-20261003/robots-retry-plan.json`、継続コードは同じフォルダの`retry-robots.mjs`、原観測は`meta/robots-retry-results.json`と`ai-api/robots-retry-results.json`。すべての追加要求は既存の`ledger.json`へ追記した。元のHTML・DOM・画面、実行時コードのハッシュも保存した。

検証: 共通実装の12テスト、既存の61テスト、実ブラウザのローカルheadful fixtureで取得失敗時のprobe無効/有効を確認。4つの実サイトrunとローカルfixtureの台帳をverifyし、コード・設定・証拠を今回専用のZIPへ出力する。50サービス全体の集計は[更新レポート](search-services-20261003.md)を参照。
