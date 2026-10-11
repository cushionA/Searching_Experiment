# Gemini SKU task package

このpackageは凍結済み2 runでproviderへ送った全147 callを保存する。今回のLOW4は既存SKUを再試験した14 call（matching 9 / signature 1 / review 4）で、新規SKU・labelは0。比較用baseline50はMINIMAL50（matching 70 / signature 13 / review 50、133 calls）で、matchingのprior-byte再利用を含むが全answerを収録。147はcall数であり、異なるSKUが147件という意味ではない。LOW4とbaseline50の条件を混同しないこと。

各callに`request-body.json`（generationConfig込みの送信body）、`prompt.txt`（実際に送信した全文。入力JSONも含む）、`input.json`（INPUT= / CASE_JSON=以降）、`response-schema.json`、`answer.json`、安全なprovider metadataを置いた。思考テキストは保存していない。review理由は独立レビューの短い理由欄であり、思考過程ではない。構造検査の成功は意味的な正しさを示さない。

`manifest.json`は全source fileのSHA-256とcallごとのcase/stage/source対応を記録する。ローカル入力とsource contextは各runの`local/`にある。元case IDと引用元文脈の復元はローカル専用データに依存する。

仕様の参照先: [source scope and local rules](../../../../experiments/sku-matching/README.md)、[matching/review stages](../../../../experiments/sku-matching/sku_gemini_replay_stages.py)、[LOW subset runner](../../../../experiments/sku-matching/trial_gemini_thinking_off_subset.py)、[LOW run report](../../../../experiments/sku-matching/results/20261011T011921Z-gemini-thinking-off-errors4/report.md)。生成基盤コードはコピーしていない。

## 入り口と3段階

| 段階 | 入力 | 実出力 | ローカル処理 |
|---|---|---|---|
| matching | 楽天の選択値・属性、AU候補行、固定商品原文 | rows → rakuten_checks / au_checks → status / source_ids | 全行・全条件・現在行の根拠IDを検証して候補を絞る |
| signature | 現在の候補行、選択済み寸法、固定商品根拠 | checks → status / source_ids | 単位・部位・寸法の一致と候補回答hashを検証 |
| review | 原文、今回の候補・照合trace・寸法結果 | 1要素の配列：case_id / verdict / row_key / reason / review_evidence_ids | confirmの候補・寸法・根拠条件を検証して採用。無効reviewは未評価 |

まず [task-contract.json](task-contract.json) を読むと、3段階の実Schema代表とローカル採否条件をまとめて確認できる。

- [今回LOWの照合](latest4/matching/001/prompt.txt) / [Schema](latest4/matching/001/response-schema.json) / [実出力](latest4/matching/001/answer.json)
- [今回LOWの寸法確認](latest4/signature/001/prompt.txt) / [Schema](latest4/signature/001/response-schema.json) / [実出力](latest4/signature/001/answer.json)
- [今回LOWの意味レビュー](latest4/review/001/prompt.txt) / [Schema](latest4/review/001/response-schema.json) / [実出力](latest4/review/001/answer.json)

`prompt.txt` は入力を含む送信全文。指示と入力を分けて読む場合は `input.json` を使う。再現には `request-body.json` をそのまま参照する。全パラメーターは各bodyのgenerationConfigに含まれる。LOW4ではthinkingLevel=LOWを明示、baseline50はthinkingConfig未指定（公式既定MINIMAL）。完全OFFの試験は行っていない。

matching/signatureは理由文や引用文を返さずstatus/source_idsだけを返す。A0/A1は現在AU行の条件、S番号は固定原文の参照ID。元ID・引用原文・出典はlocalデータから復元する。AU skuIdはグリッドIDであり、選択セルはrow_indexとcolumn_indexで特定する。

本文contextの重複除去、カーテン内容表、伸縮寸法などのタスク分岐もbaseline50の実プロンプトに含まれる。古い回答と今回の回答を混ぜて救済しない。無効な実回答もそのまま収録し、Schemaに合っているだけで正答とは扱わない。
