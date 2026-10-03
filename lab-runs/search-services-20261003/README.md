# 検索サービス50件の保存観測

対象マスターと検証ごとの結果表は [docs/search-services](../../docs/search-services/README.md)。今回の結果は [50行9列の表](../../docs/search-services/results/20261003-headful-patchright/results.md) と詳細JSON・条件JSON、集約レポートは [検索サービス観測](../../docs/search-services-20261003.md) に保存した。

Gitには設定・実行ソース・結果イベント・集約検証・表から参照する画面を登録する。HTTP ledger・response/DOM blobs・その他の画面は、元の実験ディレクトリに保持し、検証済みcheckpoint ZIPに別保存した。取得した内容や旧失敗記録は削除していない。

原通信の全履歴を含むcheckpointは `search-services-20261003-browser-observation.zip`、SHA256は `d2e8f1a74f304d99afed97ba982c15f4f36b2a24752e846a1d437aeb4ef8788d`。2986要求・331結果、4実サイト台帳と2fixtureをverify済み。表と参照画面だけのbundleは `search-service-tables-20261003.zip`、SHA256は `1288c1c95fe098c50d95756069c4ffb081bd959d1ddfa2ee2be47f205a4ee2fc`。

Git checkoutだけではraw blobs/ledgerがそろわないため、元の通信証拠のverifyやsearch-summary.pyによる再集計にはcheckpointを同じ相対パスに展開する必要がある。保存済みの表・条件JSON・参照画面の確認には展開不要。

今回の観測は2026-10-03 JST / 2026-10-02 UTC。通常のheadful再検証38対象、検索応答確認18対象（明示0件3対象を含む）。BraveはVerifyを1回クリック後に200、10件中公式9件。Yandexの画像回答はcheckcaptcha POSTが環境proxy/upstream 503になり、正誤と検索結果は不明。OneSearch/Yahoo Search、Peekier/Kagiの帰属と採用した過去観測は条件ファイルに明記した。
