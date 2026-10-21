# 2026-10-22 JST 朝の独立技術調査

## 結論

**A0 / B1。候補実行0、現金0円。** AISTRAL v2の公開者登録型discoveryは、アーカイブを前提にしない別設計としてB。ただし公開配布は評価データ中心で、検索基盤をそのまま導入できるkitではない。公開者の参加と事前処理が必要で、未登録サイトの不足補完は未解決。

新しい有用な確認は、**LOONG復元helperは取得器ではなくhash検証器**であること。READMEの「実行してfetch」との差を静的読解で確認した。配布監査ではLOONG参照144件に対して同梱文書0件。実用機能完成ではないためdraft PRなし。

## 概要・最新作業の確認

最新main `7460493cc3f7242748c95f711e71a59537ef34ea`から独立branch `research/2026-10-22-daily-tech`。実取得時刻はmanifestのUTCを正とし、本見出しは依頼されたJST朝枠を示す。

AGENTS.md、cloud-lab手順、bot-blocking-scenarios Skill、夕方branchのresearch-repro-radar Skillを読解。今回は通常の一次資料読解であり、jse.lab探索・bot診断・夕方radarループは起動していない。朝10/20・21と夕10/21を読解、22研究remoteのreportsに今回の名称・論文ID一致なしを確認（意味的重複不存在の証明ではない）。[確認記録](../../benchmarks/daily-tech-20261022/context.json)。

remote heads、open PR19/21/23/25/26/28、issue24を確認。新成果物SB-CLASSIFIER/Discovery CurvesとHarness²/TokenGate等は既報と判断し再実験しない。SKUは別担当、PR19検索追加は停止。Moli/Lightpandaのlibfontconfig承認待ち・修復上限、既存隔離保留を維持。GRACE補足は再報告しない。夕方state4ファイルは読み取りhashのみ保存し、state・自動設定・cron・他タスク・メールを変更していない。

## 方法

公開Web検索とarXiv新着・改訂から一次論文へ進み、著者がリンクしたrepoの固定revisionを確認。外部候補コードは実行せず、自作標準ライブラリの[取得・監査](../../experiments/daily-tech-20261022/README.md)のみ実行。入力URL、版、取得時刻、bytes、SHA256、時間、errorは[manifest](../../experiments/daily-tech-20261022/manifest.json)、数値は[audit](../../benchmarks/daily-tech-20261022/audit.json)。

## B — AISTRAL: 公開者側の準備と登録から情報源を発見

一次資料：[v2論文](https://arxiv.org/html/2511.18354v2)、[版履歴](https://arxiv.org/abs/2511.18354v2)、[固定配布](https://github.com/sands-lab/aistral-dataset/tree/e5c9beaa52252239b9fb77446ae2c7dcc062c506)。初版2025-11-23、**v2改訂2026-10-20 14:26:26 UTC**。新版は旧visionからprototype評価へ進んだ。取得時、版なしHTMLはv1を返したため、結論にはv2固定URLを使用。

目的・新規性は、公開者のAgentic Twinと、情報源の要約・endpointを扱うresolverを分離する点。検索条件③への接点があり、既知サイト内の経路選択だった昨日のSB方式とは異なる。一方、公開者側のchunk/embeddingとresolver登録が前提で、全Webを自動発見する完成サービスではない。前処理を無くすのではなく、その担当を分散する。

著者実験は10 twin、各subqueryで最大半数へroute。Ubuntu22.04.5、32vCPU EPYC9754、64GB RAM、ChromaDB、LangChain、text-embedding-3-small、gpt-5-miniを使う。これは実験構成で最小要件ではない。APIを使わない同等構成、CPUのみの全処理時間、peak RAM、総導入量は未確認。独立日本語Web網羅性、twin数増加時のrouting精度、前処理wall時間の優位も未確認。著者自身が規模拡大のrouting評価未実施を記載する。

著者の最大4.2倍正答や2.9〜41倍の転送削減は当方実測ではない。公開検索との比較では事前選定コーパスとlive webの条件が異なる。回答正解を必要な証拠・サイトの回収率と同一視しない。

開発・community: 著者のsands-lab配布、取得時0 stars/0 forks、push2026-10-20 13:16:05 UTC。独立採用・性能再現は未確認。コードMIT、注釈CC BY-SA4.0、上流文書は個別条件。商用利用はこれらの条件遵守が必要で、LOONG文書再配布の許諾は含まれない。実装全体の商用license・導入手順は未確認。論文licenseを未配布システムの許諾としない。

## 結果・配布比較・想定との差

|公開配布の実測監査|結果|意味|
|---|---:|---|
|非truncated treeのblob|214|固定revision全treeのmetadataを確認|
|KILT文書ファイル|199|tree上の配布数。文書本文は今回未取得|
|LOONG manifest / unique refs / 64桁hash|144 / 144 / 144|原文の照合用参照がある|
|LOONG同梱文書|0|別途、元配布から準備が必要|
|BrowseComp query IDs / unique|100 / 100|IDのみ取得。設問・回答・原文の復号なし|
|Pythonファイル|1|`loong/rebuild_docs.py`のみ。resolver/server runtimeはこの配布で未確認|

[README](https://github.com/sands-lab/aistral-dataset/blob/e5c9beaa52252239b9fb77446ae2c7dcc062c506/README.md)はhelperを実行して文書取得と案内するが、[helper](https://github.com/sands-lab/aistral-dataset/blob/e5c9beaa52252239b9fb77446ae2c7dcc062c506/loong/rebuild_docs.py)は`--docs-root`内の存在・SHA256を確認する。ASTのimportはargparse/hashlib/json/osだけで、本文にもネットワーク取得処理なし。READMEの説明を再現成功と扱わない。他の非公開・別配布コードの不存在までは主張しない。

## 予備選別

[FLINT v1](https://arxiv.org/html/2610.27273v1)（10/19公開）はquery/documentを固定数vectorへpoolする新着。著者のindex圧縮5.3〜9.3倍・検索短縮を確認したが、主な速度改善はquery後のsearchであり、文書前処理短縮の証明ではない。今回の検索目的ではC相当として深掘り・PR化を見送る。配布stars/license/導入最小要件は未確認。mLateOn停止案件の再開には使わない。NLP分類・抽出一般への不採用判定ではない。

## 問題・導入判断・次

AISTRALはBを維持。必要サイトが登録済みか、または公開者が参加できる対象に限定して検証価値がある。次に比較するなら、独立した必要サイト集合に対する発見率・未登録分、初回準備時間、更新反映時間、query wall/bytesを分ける。既知の正解文書だけを登録した評価をWeb全体の網羅性と呼ばない。現時点では対象・配布runtimeが揃わずPoC/機能実装に進まない。

取得14/14成功、hash・bytes一致14/14、保存1,081,459 bytes、取得合計12.039秒、監査約0.0022秒。候補推論/性能trial0、依存・重みDL0、GPU0秒、候補修復0回、現金0円。これは配布監査の実測で、検索性能ではない。Kaggleは無料残量・非公開範囲・既存許可未確認のまま未使用。学習なし。

継続ブロッカー: action=外部候補実行、target=AISTRAL再現、reason=承認済み資格情報・個人ファイル隔離を未確認、かつ公開配布runtime未確認、status=pending / not attempted。新しいauto-review拒否なし。停止質問を再送せず、別環境・権限緩和・認証コピーで迂回していない。資料確認と配布監査は完了。

保存物のみをcommit/pushし、remote SHA一致と当該CI終端を最終返信へ記録する。CIは既存repo検証であり候補性能の検証ではない。merge/deployなし。
