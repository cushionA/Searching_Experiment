# 2026-10-23 JST 朝の独立技術調査

## 結論

**A0 / 詳細B1。SearchJevをNLPの分類・判定部品として条件付き候補に残す。検索PRには進めない。** 新しい確認は、公開0.8B版がLoRA配布であること、任意TTAでは単一forwardの費用説明が適用できないこと。これは配布・実装の静的監査で、推論性能の再現ではない。現金0円、候補実行0、draft PRなし。

## 概要・重複確認

最新main `7460493cc3f7242748c95f711e71a59537ef34ea`から独立branch `research/2026-10-23-daily-tech`を作成。AGENTS.md、checkoutのbot-blocking-scenarios Skill、夕方branchのresearch-repro-radar Skill/profileを読解。朝の一次資料調査であり、bot診断・jse.lab・夕方radarのstate更新ループは起動しない。AGENTS指定に従い調査担当をホスト指定gpt-6-lunaで起動した。サービス側の実モデルIDの独立確認はしていない。

通常権限repoから10/22朝 `69c3c158194aa7ca79b5a71f4cb18a752216c65d`、10/22夕 `e7f2392b3115e44d9b5e40873339881a19943c8f`の報告と10/21夕を確認。拒否済み朝taskへのアクセス・再実行・別経路回収はしていない。23本のremote研究branchのreportsに候補名・論文IDの一致なし（意味的重複不存在の証明ではない）。[context](../../benchmarks/daily-tech-20261023/context.json)にrefs/open PR/issuesを保存。PR19、Moli/Lightpanda、SKU、GRACE既報は再開・変更していない。新規open PRは確認されず、既存PR19/21/23/25/26/28とissue24を確認した。

## 方法・入力

通常の公開Web検索からarXivと著者GitHub/HFへ進み、固定revisionのREADME、推論コード、license、公開モデルmetadataを読解。候補のimport・実行はせず、自作Python3.12標準ライブラリの[取得・AST監査](../../experiments/daily-tech-20261023/audit.py)のみ実行した。版・URL・時刻・bytes・SHA256・取得時間・errorは[manifest](../../experiments/daily-tech-20261023/manifest.json)。数値は[audit.json](../../benchmarks/daily-tech-20261023/audit.json)。日付は依頼されたJST朝枠、実取得時刻はmanifestのUTCを正とする。

## B — SearchJev：自由生成を避けた短い分類・判定

一次資料：[論文v1](https://arxiv.org/abs/2610.05107v1)、[固定公式コード](https://github.com/EvoScientist/SearchJev/tree/054ea81d43071b93e2d75b7396a729a2598ac6ac)、[0.8Bモデル](https://huggingface.co/SearchJev/SearchJev-0.8B/tree/9af4b6c653e48abd74032510f080e467534ca6dd)。論文初版2026-10-04、repo最終push10/04、モデル最終更新10/09。直近24時間の新公開ではなく、今回未報告の10月公開候補を配布監査したもの。取得時34 stars/2 forks、作者配布と公開学習ログあり。独立性能再現・大規模採用は未確認。

目的・新規性は、Qwen3.5上のLoRAで短い構造化判断を選択肢ラベルの確率分布に変換し、soft-label CE＋Brier lossと質問型別温度校正を組み合わせる点。yes/no、choice、ordinal scoreを扱い、低信頼判断を別モデルへ戻せる。自由文の抽出・説明生成器ではない。大量データの分類・証拠十分性判定の部品として検討する。日本語の雑な入力への転移、未知分類体系、校正の維持は未確認。

検索用途では、著者のactive search時間短縮は文書の前処理短縮とは異なる。新規サイト網羅性や不足補完を実証したとは扱わず、検索条件①②③の充足は未確認。検索追加PRの根拠にせず、NLP判定用途を別枠Bとする。

### 結果・比較・想定差

|項目|確認結果|解釈|
|---|---:|---|
|0.8B LoRA safetensors|43,340,856 bytes|独立した全モデルではない|
|Qwen3.5-0.8B基底safetensors|1,746,942,600 bytes|別途必要、HF revision `2fc06364715b967f1860aea9cf38778875588b17`|
|上記重み計|1,790,283,456 bytes|tokenizer・依存・実行時RAM等を含まない。重みDLは今回0|
|任意`decide_batch_tta`既定|trials=4|choice/noulはidentity＋4回。scoreは順序入替対象外|
|通常推論|質問別exampleをbatch化|全フィールド任意数が無条件に1回で済む意味ではない|
|CPU|fallback分岐あり|READMEはCUDA kernelを要求。CPU動作可否・時間は未確認|

[固定推論コード](https://github.com/EvoScientist/SearchJev/blob/054ea81d43071b93e2d75b7396a729a2598ac6ac/training/inference.py)のAST・本文を確認。TTAは任意経路であり、通常の`decide`が常に5回になるわけではない。またモデル呼出し回数の増加をwall時間5倍と断定しない。

[モデルカード](https://huggingface.co/SearchJev/SearchJev-0.8B/blob/9af4b6c653e48abd74032510f080e467534ca6dd/README.md)の著者値は同一0.8B backbone AR JSONとの比較でp50 144.2→27.6ms（約5.22倍）、batch1・H200。校正や正解率の数値も著者評価であり当方実測ではない。CPUの1万件費用やGKE/AWSでの価格優位には換算しない。

### 導入・権利・制約・判断

コード・0.8BモデルカードはApache-2.0を明示し、条件遵守の商用利用候補。基底モデルmetadataもApache-2.0表示。上流学習データは個別licenseで、これらを一括許諾と扱わない。通常導入はLinux/Python3.12、PyTorch/PEFT/TransformersとCUDA系kernelが必要。学習6 H200は著者学習構成で、推論最低要件ではない。導入総容量、VRAM/RAM、CPU互換、fallbackモデル費用は未確認。入力上限2048の切詰めと選択肢順序依存が運用評価点。

次に進める条件は承認済み隔離の成立と対象分類データの確定。進めるなら同じ公開/合成入力で通常判定と任意TTAを分離し、正解率・ECE・棄却率・p50/p95・throughput・peak RAM/VRAM・全DL量を測る。選択肢順序を変えて安定性を測り、単純encoderと同品質で比較する。学習・新GPU利用は今回提案を実行しない。

## 予備選別

- **Mango：今回の追加実装はC（保留）**。[論文v1](https://arxiv.org/abs/2604.18779v1)、[公式repo](https://github.com/VichyTong/Mango)。4/20論文、最終push4/22、取得時4 stars/0 forks、Apache-2.0。BFS＋Google Searchで既知サイトのURL集合を事前作成し、BM25事前分布とThompson Samplingで入口を選ぶ。アーカイブ不要だが、未知サイト集合の発見ではなく既知サイト内の探索で、別途前処理とAPI設定を要する。新更新の根拠がなく、既報SB-CLASSIFIERに対して追加の実用価値を十分示せないため穴埋めPoCをしない。Playwright/Crawl4AI、Google Custom Search、LLM設定が必要。商用API条件・無料枠・hardware・独立比較は未確認。
- **CHIP2026 phenotype recognition：C**。[新着論文v1](https://arxiv.org/abs/2610.29606v1)は10/21公開の患者別表現型抽出benchmark。現対象への具体的用途がなく深掘りしない。論文とデータの許諾は別で、データlicense・導入・hardware・公開実装/communityは未確認。医療判断を実施していない。

## 問題・検証・保存

17/17資料取得成功、721,314 bytes、取得合計6.141秒。これは取得・配布監査の測定で、モデル性能測定ではない。候補推論0、インストール0、重みDL0、GPU0秒、修復0、現金0円。Kaggleは無料残量/非公開範囲/既存許可未確認のまま未使用。

継続ブロッカー: action=外部候補推論、target=SearchJev PoC、reason=資格情報・個人ファイルからの承認済み隔離未確認、status=pending/not attempted。新しいauto-review拒否なし。既存10/17の停止質問を再送せず、権限緩和・認証コピー・GPUや別環境への迂回はしていない。独立一次資料調査・配布監査は完了。

夕方state、自動設定、Actions cron、メールは変更なし。実用機能追加なしのためdraft PRなし。保存/push、remote SHA一致と当該CI終端は最終返信に記録する。既存repo CIの成功は候補再現成功を意味しない。merge/deployなし。
