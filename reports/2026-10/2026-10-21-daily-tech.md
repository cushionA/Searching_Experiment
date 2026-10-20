# 2026-10-21 JST 朝の独立技術調査

## 結論

**B2、A0、候補実行0。** アーカイブに依存せず既知サイト内の不足資源へ到達するSB-CLASSIFIERを条件付き候補とした。公式再現kitの配布不足をオフライン監査で確認したため、直ちに再現できるとは扱わない。Common Crawlの新しい公式紹介からは、重複率をWeb全体の網羅率へ外挿できないという評価上の注意を得た。新しい性能改善や実用機能の完成はない。

## 概要・重複と停止確認

2026-10-20 22時台UTC。最新main `7460493cc3f7242748c95f711e71a59537ef34ea`から独立 `research/2026-10-21-daily-tech` を作成。10/20朝、10/18〜20夕報告、夕方指定commit `b9682b985b1c708d5680937fdbda1c27edea53b5`、open PR19/21/23/25/26/28を確認。夕方報告群・最新夕方保存資料内で今回の論文ID/名称の一致はなかった。全履歴の意味的重複不存在は保証しない。

AGENTS.md、cloud-lab手順、bot診断Skillと夕方research-repro-radar Skillを読解。今回は通常の資料調査で、jse.lab探索・bot診断・夕方radarループは実行しない。夕方state、自動化、cron、SKU、PR19、Moli/Lightpandaや他の保留検証を変更していない。追加依存・権限緩和・別環境による再開なし。Firecrawl手順は確認したが有料credit消費を避けWeb検索と公開一次資料GETを使用。

## 方法

公開Web検索から論文、著者の再現kit、公式更新を読解。9資料のURL/取得UTC/bytes/SHA256/時間を[manifest](../../experiments/daily-tech-20261021/manifest.json)へ保存。外部コードのimport・compile・実行はせず、自作Python標準ライブラリの[オフライン監査](../../experiments/daily-tech-20261021/audit.py)だけ実行。[結果](../../benchmarks/daily-tech-20261021/audit.json)は取得・配布診断の実測であり候補性能ではない。

## B: SB-CLASSIFIER — archive非依存のサイト内不足補完

[論文v1](https://arxiv.org/html/2602.11874v1)は2026-02-12公開の掘り起こしで、新着発表とは呼ばない。DOM上のリンク経路をまとめ、sleeping banditと軽量URL分類器で目的ファイルに近い経路を選ぶ。既知seed内の不足補完という検索条件①に具体的接点がある。未知ドメイン発見や日本Web全体の網羅性は解決しない。

著者の「一部サイトで20%のページから90%のtarget」は当方実測ではない。主評価はrequest数と転送量で、全サイトのwall-clock短縮や索引前処理短縮へ読み替えない。全複製・100万ページ上限・時間上限の実験群は分母が異なるため、同じrecallとして比較しない。

[公式kitの固定版](https://github.com/AntoineGauquier/efficient_crawler_for_scalable_web_data_acquisition/tree/d58d9a754925cdce75f50d081890621ffcb2e4b6)は1 star、0 fork、push 2026-01-05。独立採用実績未確認。コードMITで条件遵守の商用利用可、論文や収集先データの権利は別。READMEは原サイト複製を権利上同梱せず問い合わせ方式と明記。**完全tree内の`.db`は`site_names_to_urls.db`の1件のみ、requirementsの6依存は全て版固定なし**と当方監査で確認した。これは公開kit単体で同じデータの再現が完結しない具体的理由。

導入はPython/Scrapy/scikit-learn/hnswlib/lxml等とSQLite複製を要する。GPU必須の記載は確認できず、CPU/RAM最小値・Python3.12互換・全導入量・日本語対象での性能は未確認。単なるclone/installを再現としない。

## B: Discovery Curves — 網羅性判定の前提診断

[公式10/12更新](https://commoncrawl.org/blog/the-common-crawl-foundation-at-the-tpdl-conference)で紹介された[論文v1](https://arxiv.org/html/2607.13636v1)は7/15公開。複数crawlの累積発見とペア重複を別々に当てはめ、均質な母集団の仮定のずれを診断する。CCはdomain単位、別評価GAWはURL単位であり、推定coverageを日本語URLの実recallへ転用できない。未観測サイトの補完器ではないため、これだけで検索機能PRにはしない。

著者・開発主体はCommon Crawl Foundation。今回読んだ資料から専用実装のstars・配布license・商用条件・導入依存・必要RAMは未確認。論文の公開licenseはarXiv perpetual non-exclusiveでコードの商用許諾を意味しない。公開集計を使う診断設計としてB、実装採用は保留。独立した必要サイト/URL集合による不足評価を置き換えない。

## 比較・想定との差・導入判断・次

SB方式は保存archiveの高網羅性を前提にしない一方、既知seedが必要で、オンライン取得時間も残る。公開コードがあっても入力複製は含まれず、現在の隔離条件でも候補実行へ進めない。今後価値があるのは、必要資源と独立した期待集合を持つ許可済みサイトで、同一request/bytes予算のBFSとの回収数・未回収・wall時間を比較すること。全site複製を先に作る時間も別計上し、準備コストを隠さない。新しい対象クロールやデータ提供依頼は開始していない。

今回の取得9/9成功、保存payloadのhash/bytes一致9/9。候補推論/実行/性能trialは0、候補依存/重みDL 0、GPU0秒、現金0円、候補修復0回。学習・Kaggle投入なし。既存無料GPU残量・非公開範囲・許可は未確認のまま。性能優位・導入成功は主張しない。新しい実用機能を実装していないためdraft PRなし、merge/deployなし。

ブロッカー（継続、今回の新しい拒否ではない）: action=外部SB-CLASSIFIERコードの実行、target=上記固定revision、reason=資格情報・個人ファイルを見せない承認済み隔離の確認なし、status=pending/not attempted。加えて著者比較の入力DBが公開kitに無い。停止質問の再送・修復上限迂回を行わず資料診断を完了。環境起動直後の一時的なtool未準備は解消、auto-review拒否なし。

保存物はexperiments/benchmarks/本報告。push後のremote SHA一致と当該CIは最終報告へ記載する。CIはリポジトリ検証であり、SBやcoverage推定の性能検証ではない。

## 検証記録

自作auditを2回実行し9/9 payload一致。取得7.597秒、監査0.00175秒、保存882,716 bytes（候補処理時間ではない）。全labテストは151件・失敗1・skip15。失敗は既存bot診断の`robots-parser`未導入（`MODULE_NOT_FOUND`）で、追加依存を導入せず[元ログ](../../benchmarks/daily-tech-20261021/local-lab-tests.log)を保存した。CIの既存workflowは同依存を導入するため、当該remote実行の結果を別途確認する。未実行の候補を成功とするテストは追加していない。
