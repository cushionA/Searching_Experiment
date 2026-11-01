# 2026-11-02 JST 朝：長文走査と前処理候補の配備監査

## 結果

**Laya 0.4.21のTypeScript長文走査を新更新として確認。検索前処理候補Scrapling 0.5とCrawlee Python 1.11.0も一次資料で監査したが、今回は読取調査のみ。採用A・性能実測・実用機能追加は0。** 試験数を埋める比較、既済み推論、保留試験の再開はしていない。

## 検索・前処理（NLPと別枠）

|対象・一次情報|最新版・コミュニティ・導入|適性と判断|
|---|---|---|
|[Scrapling](https://github.com/D4Vinci/Scrapling/releases/tag/v0.5)|0.5、2026-10-17公開。取得時91,720 stars/9,655 forks。BSD-3-Clause、商用利用を禁じるライセンスではなく通知・免責等の保持条件あり。Python>=3.10。parserのみなら`pip install scrapling==0.5`、lxml/cssselect/orjson/tld/w3lib等。fetchers extraにはcurl_cffiに加えPlaywright/Patchright等が入る。rag extraもfetchersを要求。|B。既知HTMLの選択・変換に使えるが、汎用parserの作者速度値を本文抽出の精度・前処理全体の速度優位と同一視できない。今回の調査範囲では新規候補だが公開は10/17であり本日新公開ではない。未知サイト発見能力や現行処理に対する独立優位は未確認。不要なブラウザ導入を伴うall/rag導入はしなかった。|
|[Crawlee Python](https://github.com/apify/crawlee-python/releases/tag/v1.11.0)|1.11.0、2026-10-15公開。9,637 stars/820 forks、Apache-2.0。Python>=3.10。HTTP抽出は`crawlee[beautifulsoup]==1.11.0`、ブラウザ経路は別extraとPlaywright配備が必要。新releaseにDataset/KeyValueStoreのasync iteration、フォーム要求抽出、tldextractから組込みPSLへの変更。|B。既存requirementsは1.10.3であり独立した新クローラ発見ではなく更新候補。JS版の3.x/4.xと版を混同しない。PSL変更による前処理削減は仮説だが、今回実データのボトルネックと効果を確認できていない。基盤更新を主目的にせず導入・比較を見送った。|

両者ともモデル重み/GPUを必須としないHTTP/parser経路があるが、最低RAM、依存解決後の取得量、CPU時間は未計測。starsは取得時点の値で品質の根拠ではない。READMEの作者ベンチマークを転載・実測換算していない。既存基盤と重なる汎用機能だけでAとせず、archiveを持たない未知サイト発見の新設計も今回確定できなかった。reranking単独・PR19への検索追加は行わない。

[Common Crawl catalog](https://index.commoncrawl.org/collinfo.json)先頭はCC-MAIN-2026-43（収集10/5–10/18）のまま。catalog確認だけで日本Webの網羅性や新サイト発見率を測ったとは扱わない。

## NLP別枠：Laya 0.4.21

[0.4.21 release](https://github.com/NandhaKishorM/laya/releases/tag/v0.4.21)は11/1 20:50:28 UTC（11/2 05:50 JST）。前回0.4.19から0.4.20を経て更新。取得時34,812 stars/3,112 forks、Apache-2.0。Python packageは0.4.21、Python>=3.10、torch/transformers/safetensors等に依存。一方、同tagの`laya-ts/package.json`は**0.1.0**で、ORT node/webは^1.31.1。release番号をそのままnpm版と誤認しない。npm配布の最新版・publish状態は未確認。

新しいAgent/Routerの`predictLong`を[固定tagソース](https://github.com/NandhaKishorM/laya/blob/v0.4.21/laya-ts/src/predict-long.ts)で静的確認した。状態token列を質問の残余予算に収まる窓へ分割し、既定strideは窓の約半分。質問ごとに強い回答を選び、noulはnoul値、それ以外はanswer_confidenceを比較する。勝った窓の確信度であり文書全体の確信度ではない。複数窓を呼ぶため高速化とは限らず、文書中の矛盾統合や過去から現在への状態更新を保証するものでもない。

この更新は長い取得本文を分類する前処理候補としてB。ゲームの履歴全体から誘いの維持/変更/断念を決める用途へ、そのまま最高確信窓を使う根拠はない。性格・戦況・履歴、複数許容解、レイテンシ、判断失効の評価基準は維持する。

[multilingual重み](https://huggingface.co/convaiinnovations/laya-multilingual)は`1720e3e3357cfe1e281542e223f8273b0890ca34`、10/3更新、Apache-2.0で前回と同じ。新たな日本語能力向上や上位重みへの更新は確認できない。fused ONNX exportと実CPU/RSSは未検証のまま。既存Laya/THX-01/ModernBERT/OpenDeciderの比較は繰り返さず、重み取得・候補実行なし。

## 最新成果・保留との照合

remote main `4d2984a454fc766137784fd6a51579e10b6fafd6`、朝 `06673ed0c41ccc00348ab462b8a11b19b14c2bfc`、夕canonical `1321d3f6637684c9ccdf4c7e02f083d6762bd969`を確認。朝11/1・夕10/31報告を読解。公開PR19/21/23/25/26/28のheadをAPIで取得し、前回列挙されたheadから変更なし。独立朝branchは11/1朝から作成、夕state/pending9,549は変更なし。

既知releaseを再照合し、browser-use0.13.14、Crawl4AI0.9.5、Trafilatura2.3.1、PageSift0.81.5は不変。RapidOCRLiteのlatest endpointは404だったためrelease一覧でalpha5.0.0a15を確認し、404をリリースなしとは解釈しなかった。同梱Indra重みの別条件は前回監査のままで、追加取得なし。

共通preflightの修復・コピー・公開・組込みは**個別承認待ち**として未実施。未公開4ea36aeの成果物は本branchに含めない。Trafilatura Coreは追加修復待ち、PageSift/Moli/Lightpanda/Squeezは修復上限、TinyJev累計3GB再取得は未承認で、いずれも操作なし。10/30夕と11/1夕のlaunch UNKNOWNは**結果不確定**として再起動なし。本runの環境で別runの完了・不存在を確認したとは言わない。今回新たな拒否はない。

## 証拠・検証・資源

[一次レスポンスと出典・hash](../../benchmarks/daily-tech-20261102/source-responses.json)、[PyPI/HF/catalogメタデータ](../../benchmarks/daily-tech-20261102/metadata.json)を保存。ソースは実行せず、TypeScript本体はhashと判定に使った短い行を記録。GitHub APIはconnector経由、HFはweb取得失敗後に許可済みproxy経由の通常HTTPで取得した。拒否回避・認証変更なし。コミュニティ数値は最終親側のAPI観測を使用し、子調査の時点値と混ぜていない。

AGENTS、関連skillの適用条件、cloud-runtime/networkingを確認。新候補調査は規約指定のLuna spawnで実施し、親が一次資料へ照合。サービス内部モデルIDは独立検証していない。grounding探索、botシナリオ、Kaggleを実施しないため、それらの実行skill・入口を新設していない。

今回は研究メモと一次資料だけの変更。新しい実験基盤・実装はなく、候補取得前のDocker実行検証も必要となる候補実行を選定していない。共通preflightを別名で作らず、穴埋めの実験コード・空ベンチ数値を作っていない。JSON/出典hash/出典数と`git diff --check`を確認し、CI保守や既存依存不足の修復には進まない。remote SHAとexact-SHA CIはpush後の最終報告へ記す。実用機能なしのためdraft PRなし、merge/deployなし。

追加費用0円、候補CPU0分、候補ダウンロード0、GPU0、Kaggle submit0、修復0。API/メタデータ/Git取得はあり、wire総量は未計測。候補RAMの測定なし。30分run枠内、資料取得のみで2GB上限を消費する大容量データ取得はしていない。次は、独立した用途優位と固定入力が得られた候補だけを隔離条件の事前確認後に評価する。今回のB候補は採用・実行許可を意味しない。
