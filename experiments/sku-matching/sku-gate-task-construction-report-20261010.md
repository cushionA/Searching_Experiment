# 固定ペアSKU同一性判定タスク構築 — 方式A/B 実装・検証報告（2026-10-10）

作業ブランチ: `claude/task-construction`（handoff元 `origin/codex-sku-model-comparison-20261009` = `381f67b`、基準 `8e10f14` を祖先として確認）。pushは未実施（rootへhandoff）。

## 1. 結論

- 固定AU商品ペアの中で、楽天の選択済み1 SKUを同一AU URLの全SKU行と照合する2方式を、CPUのみ・モデル不使用の決定的処理として実装した。
  - **A（同一行の構造ゲート）**: 選択SKU値を型付き要件（寸法・名前付きサイズ・枚数・構成要素の有無/数・段数・色・透過・生地・座幅・品種語）に分解し、候補AU行ごとに評価する。行・固定AUタイトル・スコープ付き説明の順に照会する。明示的な「内容」リストは、その条件スコープ内で閉じた構成として扱う（この仮定はablationで外せる）。
  - **B（引用付き要件ゲート）**: 同じ要件を、楽天原本HTML内の文字offset付き原子カード（quote・span・scope・軸provenance）にする。根拠は原本で解決できる逐語引用だけに限る。リスト中の不在は引用ではないため、支持にも矛盾にも数えない。acceptするのは、全カードが1行で支持され、他の全行が検証済みの矛盾で排除される場合だけ。
- **v1（ラベルを開く前にfreeze）**: 1,383行ではA・Bとも gold unmatched への誤accept 0・誤行accept 0・誤delete 0。ただし両方式とも gold review の31行をacceptしたため、保守的accepted precisionは A 93.3%（435/466）、B 89.0%（251/282）で、baseline v10 hybrid の 100%（337/337）を下回った。行一致recallは A 100%（435/435）、B 57.7%、baseline 77.5%。
- **v2（v1採点後、gold review 31行の根拠を読んで改訂 = label-informed）**: 同じ範囲の内容リストにある明示的な個数、単一値の本体寸法、楽天のSKU別構造化属性を「ページ仕様の矛盾 → review」として追加した。A: accept 435／確認済TP 435／未確認accept 0／誤delete 0／review 38（2.7%）。B: accept 251（全てTP）／review 226。**v2は同じ診断データで調整した結果であり、汎化・holdout性能は主張しない。**
- **v2c（ラベル不要の検証を追加。判定ロジックはv2と同じ）**: 選択値に原本spanが無い要件は、A・Bともコード側でreviewにする（従来はschema検証頼み）。ラベルを使わない不変条件6種を、freeze済みのチェッカーで全件検査した。結果は違反0件（§8 末尾）。v2cの入力・16予測ファイルはv2とバイト一致し、採点結果もv2と同一。
- 代表ケースは全て要件どおりに処理される（§7）。
- 文字列の一致率、引用の原本一致、判定の意味的正しさは別々に扱っている。

## 2. 開始手順の検証

- `git merge-base --is-ancestor 8e10f14 HEAD` 成功。
- `20261010-claude-task-checkpoint.zip`: SHA-256 `89909023…96d4` 一致、11,397,724 bytes、`sha256sum -c` OK。隣接manifest SHA-256 `a273cc7f…ac46` 一致。payload 136 files / 142,123,313 bytes の各SHA一致、embedded manifest SHA `17357b35…eca6` 一致、source base commit `8e10f145…a4a4`。`unzip -n` で展開（既存ファイルの上書きなし）。
- 補足 `20261010-cpu-minimal-claims.zip`: SHA-256 `c5b184e7…d80a` 一致、payload 5件のSHAも一致。CHECKPOINT.jsonは本体ZIPのものを保持し、補足側は照合のみ。
- 両ZIPとも、展開時にリポジトリルートへ未追跡の `CHECKPOINT.json` を作る（コミット対象外）。

## 3. 実装ファイル

| ファイル | 役割 |
|---|---|
| `experiments/sku-matching/sku_gate_sources.py` | 原本SHA照合、楽天HTML（EUC-JP）の `selectorValues`／`variantSelectors`／per-SKU `attributes`、AU item JSONのleaf、JSONL leafへのspan生成と再検証（`verify_span`） |
| `experiments/sku-matching/sku_gate_atoms.py` | NFKC＋offset写像付きの原子分解、タイトル事実（単一値のみ製品レベル）、説明の節・【条件】・ページ宣言・内容リスト・例外文の抽出 |
| `experiments/sku-matching/sku_gates.py` | 入力カード構築、比較、方式A/B、source config（ablation） |
| `experiments/sku-matching/run_sku_gates.py` | `prepare` → `freeze` → `predict`（全ステップで上書き拒否、freeze hash照合） |
| `experiments/sku-matching/evaluate_sku_gates.py` | freeze・予測manifestを照合した後にラベルを開いて採点（v2c以降は不変条件の結果SHAも記録） |
| `experiments/sku-matching/check_sku_gate_invariants.py` | ラベルを開かない不変条件チェック（§8 末尾）。freeze対象 |
| `experiments/sku-matching/package_sku_gates.py` | 結果ZIP＋manifest＋sha256 |
| `experiments/sku-matching/schemas/sku_gate_{product_context,case_input,a_output,b_output}.schema.json` | 入出力schema（JSON Schema 2020-12） |
| `tests/test_lab_sku_gates.py` | source-preservation・判定安全性テスト（20件） |
| `experiments/sku-matching/results/20261010-sku-gate-tasks.zip`（＋`.manifest.json`, `.zip.sha256`） | v1/v2の入力・freeze・予測・評価 |
| `experiments/sku-matching/results/20261010-sku-gate-tasks-v2c.zip`（＋`.manifest.json`, `.zip.sha256`） | v2c（と廃止したv2b）のfreeze・manifest・不変条件・評価。入力・予測はv2とバイト一致のため含めず、上のZIPから複製する |

## 4. 方式の定義と主な規則

共通（A・B）:
- 1ケース = 固定ペア＋楽天選択1 SKU。候補は固定AU URLの全SKU行のみ。再検索・別URLへの振り分けはしない。
- 選択SKUの全軸・複合値の全要素を要件にする。`21枚セット(パネル20枚＋ドア1枚)` → total 21 ∧ panel 20 ∧ door 1、`スリム / グレー` → variant スリム ∧ color グレー、`毛布セット` → blanket 同梱。オプション軸の `なし` は、兄弟選択肢が追加する構成要素が無いことを意味する。
- 要件は1つのAU行で同時に満たす必要がある（行をまたいだ合成はしない）。行に該当する型が無い要件は固定AUタイトル → スコープ付き説明の順に照会する。タイトルや説明で同じ族に複数値があるもの（例: `幅100 幅150`、ランナップ行）はシリーズ情報として適用しない。
- 色・品種語の相違を矛盾とするのは明示的な対比がある場合だけ（同じAU配列の別行にその値がある、または楽天の同じ選択肢族にその値がある）。それ以外は表記未解決（unknown）。名前付きサイズ・数値・有無は閉じた型なので、値の相違を矛盾とする。
- 【幅100cm】等の条件付き記述は、同じ候補行の値で評価する。`※…選択の場合` は選択SKU側の値で評価する。
- AU行だけにある原子（例 `(2枚)`、`（パイル）`）は、楽天側の兄弟対比・同じ範囲の内容・タイトル単一値で検証する。矛盾していればその行を除外する。未検証のままなら判定を止めない（記録のみ）。
- 派生的なページ仕様の矛盾（v1: 構成要素の有無・材質、v2: ＋同じ範囲の個数・本体寸法・楽天SKU属性）はacceptを止めてreviewにする。これだけでunmatchedにはしない。片側に2値ある場合は曖昧として扱い、矛盾にしない。
- 価格・在庫・送料・クーポン・販促は入力カードに入れない（テストで値を注入しても判定は不変）。

A: 全要件を支持する行がちょうど1つならaccept。全行に明示矛盾があればunmatched。それ以外はreview。
B: 原本spanで検証済みの根拠だけを使う。複数ソースが食い違えばambiguous。分解が不完全ならreview。acceptには、他の全行が検証済み矛盾で排除されていることも要る。コード（例 `QT`）を名前付きサイズに読み替えるには、ページ上の対応文（例 `QT(クォーター)`）の引用が必要。

## 5. source-preservation tests

`python3 -B -m unittest tests.test_lab_sku_gates -v` → **20 tests OK**（実データ検証を含む、約17秒）。

- 引用は入力の部分文字列である（offset一致）。HTML・JSON・JSONLのspanは、SHA・offset・quoteのどれか1つを改ざんすると検証に失敗する。
- 1,383ケース全ての選択値spanと楽天SKU属性spanは原本HTMLで解決し、quoteは選択値と一致する。29ペア全てのAU行値・AUタイトル・説明行spanも原本JSONで解決する。`source_row_key`・`sku_record_key`・ファイルSHAは保持される。入力・出力はschemaに適合する。
- 原子の一部だけが支持されてもacceptしない（ドア1のみ支持 → total 21はunknown）。
- 異なるAU行の軸を合成しない（赤+S行・青+M行に対し、赤+M の要件はunmatched）。
- 軸欠落（unknown → review）と、明示的な反対値（AUタイトル `レースカーテンセット` → conflict → unmatched）を区別する。
- シリーズタイトル（`2段 3段`）を選択SKUに適用しない。
- 内容リストの閉包（Aはaccept、Bはreview、`full_no_closed_list` のAはreview）。
- 条件を同じ候補行で評価する（幅150行だけにレース）。
- Bは競合行が排除されていなければacceptしない。
- 派生矛盾はreviewにするだけでunmatchedにしない。
- v2: 同じ範囲の個数矛盾（フック7 vs 9）、本体寸法、片側2値は矛盾にしない。
- 価格・在庫フィールドを注入しても判定は不変。
- v2c: 選択値のspanを外すと、本来matched／unmatchedになる要件でもA・Bともreview（`unquoted_requirement`）になる。出力schemaは、null spanをreviewのときだけ許す。
- v2c: 兄弟値の入れ替え（実データ、29ペアのうちAがacceptした各ペア最大2ケース）。選択値の1軸を、楽天ページ自身の選択肢一覧 `variantSelectors[i].values` から引用した兄弟値に置き換える。その場合、元のSKUでacceptした行を再びacceptしない。
- 代表ケース（§7）。

全体の `python3 -B -m unittest discover -s tests -p 'test_lab*.py'` は 326 tests 中 failures=1 / errors=12 / skipped=17。error 12件は、チェックポイントZIPに含まれない `.lab-output` の既存成果物（v2注釈入力、Kaggle v5計画、Luna評価summary等）が無いことによる。failure 1件（`test_lab_bot_diagnostics` の robots redirect）は基準コミット `381f67b` の独立worktreeでも同じく失敗する。いずれも今回の変更とは無関係。

## 6. freeze manifest / hash

| run | freeze.json SHA-256 | labels_read_before_freeze | git HEAD | 予測manifest SHA-256 |
|---|---|---|---|---|
| v1 | `544ae090768b5e14a2dd3926135e39c7a2a31e14f8a76c1693fc854f0535ee7f` | false | `5c1bc28` | `b23f4618a525391331cd2292b2c95ba57194840020948cc37da9135d625c9e42` |
| v2 | `cdb1315417f52735e3ed538e19b74fddc4768f0aef8fe6bcdf2e925da8550b77` | **true（label-informed、変更内容をfreezeに記録）** | `3dca08d` | `b453d5158da810f1bea6ac02ed06bdcadc7b71f96e49db43cf2586ca9016c645` |
| v2b（廃止） | `6185fd2afb7ef4649cb538d64cafc92d1eeb89bc51e6521672b9e8a0527fb3a5` | true（v2の改訂を継承。追加のlabel-informed変更なし） | `74901a6` | 予測はv2とバイト一致。初版チェッカーがtwinを固定ペア無しで照合していたため廃止。ラベル採点はしていない |
| v2c | `0ee3b37e434198561a36424ea6f21dd0289150233d1a8265aac38b94b8a23051` | true（同上） | `9688906` | `2e0d34204a9853c64d4fa8ad81d6bbf59249f49148de57b7624fd7a247068240` |

- v2c: 不変条件 `invariants/summary.json` `84c14a7e…51ac`（all_passed）、評価 `evaluation/summary.json` `38326cd5…f3fa`。`results` と `gold_review_audit`、`disagreements.jsonl` はv2と完全一致した。
- 入力: `inputs/products.jsonl` `bad2dee1…74b9`（v1=v2=v2c）。`inputs/cases.jsonl` は v1 `03369e5b…8e4a` → v2 `8ff9b8e0…1f18`（v2で楽天SKU属性を追加。v2cもこれと同一）。
- freezeには、コード（sources/atoms/gates/runner、v2はevaluatorも、v2cは不変条件チェッカーも）・schema・テスト・入力の各SHAが入る。predict時に照合し、不一致なら停止する。
- 採点に使ったもの: labels `a4617174…ea28`（Luna機械注釈 435/911/37、人手未確認）、baseline `v10/hybrid/predictions.jsonl` `b2a7cbf0…df23`、196コホート `inputs.jsonl` `0b5e5c9c…8814`（カーテン32）。
- 結果ZIP `experiments/sku-matching/results/20261010-sku-gate-tasks.zip`: SHA-256 `e173e71815d39fc71046f9159659bf049324b3915760fa28cdfdef43fb955c3f`、9,784,893 bytes、payload 46 files / 168,670,290 bytes。展開は `unzip -n … -d .`。生のlabels.jsonlは含まない。`evaluation/` には集計と、gold review 37行・不一致行のgold判定・根拠が入る（manifestに明記）。
- 追加ZIP `experiments/sku-matching/results/20261010-sku-gate-tasks-v2c.zip`: SHA-256 `9cd72ad762998355d0d69fb8a0c76d3f1d906edcd45a837acfc7ae57b4a759cb`、32,027 bytes、payload 12 files / 369,126 bytes。v2cとv2bの freeze・入力manifest・予測manifest・不変条件、v2cの評価だけを含む。予測manifestに記したSHAは、上のZIPにあるv2の各ファイルと一致する。

## 7. 代表例（v2 full。spanは原本で検証済み）

| case | 楽天選択SKU（原本quote） | A | B | 根拠（AU行キー・quote） |
|---|---|---|---|---|
| `case-001a5707d96903cfe7da` | `100×220cm` / `スモークグレー` / `なし`（ct0 `sku[variantId=CT01220SGH].selectorValues[0..2]`、offset 331521–331545） | matched `au:704500131:331814079:3:12` | review | 寸法: 行 `$.itemInfo.skuInfo.columnNames[12]`=`幅100×丈220cm`。色: `rowNames[3]`=`スモークグレー`。レースなし: Aは【幅100cm】の内容 `遮光カーテン 2枚 / タッセル 2枚 / カーテンフック 14個` を閉じた構成として支持。Bはリスト中の不在を引用と認めないためunknown。AU行のみ `(2枚)` は楽天 `カーテン 2枚`（なし選択時）と一致 |
| `case-003b9c60664f6b5059a8` | `100×110cm` / `スモークグレー` / `あり` | unmatched | unmatched | AU 704500131 のタイトル・内容にレース無し（Aは閉包で矛盾）。両方式とも、AU行 `(2枚)` が楽天 `カーテン 2枚 + レースカーテン 2個`（あり選択時）の合計4と矛盾。楽天タイトルの `レースカーテンセット` は選択軸と同じ族なのでシリーズ扱い。704502086 は別ペアで、本ケースの出力には現れない（テストで確認） |
| `case-03eb5704d3bb159f757b` | `21枚セット(パネル20枚＋ドア1枚)` → `21枚セット`／`パネル20枚`／`ドア1枚` の3カード、`70×50cm`、`半透明` | unmatched | unmatched | total 21 vs AUタイトル `13枚セット`・ページ宣言 `13枚set`、panel 20 vs 内容 `パネル×12枚`、door 1 は `ドアパーツ×1枚` で支持（一部のみ支持）。寸法 vs `50×50cm` |
| `case-0722ef51b816b9837580` | `スリム / グレー` → `スリム`・`グレー` | review | review | `グレー` は行 `rowNames[0]` で支持、`スリム` は未解決。v2では本体高さ 90 vs 91 の仕様差も記録 |
| `case-03df33141d94ac3c09fc` | `ダブル` / `アッシュグレー` / `毛布セット` | unmatched | review | `毛布セット` = blanket同梱の要件。Aは内容 `吸湿発熱敷きパッド ダブル × 1` の閉包で矛盾。Bは引用が無いためunknown。サイズ・色だけでは満たさない |
| `case-180a9baafb740879a120` | `150×135cm` / `ノクターンネイビー` / `あり` | review（v1はmatched） | review（v1はmatched） | AU 704502086 の【幅150cm】`カーテンフック 7個` vs 楽天 `フック 9個`（gold review 27件の型） |
| `case-7711fdf6a039757f2dfe` | `ダークブラウン`（fgc010） | review（v1はmatched） | review（v1はmatched） | AU `幅66x奥行57x高さ70cm` vs 楽天 `幅50x奥行57x高さ70cm`（SKU属性 `本体横幅=50`） |
| `case-57a9a1da4a30f6b21903` | `もことろん毛布` / `ダブル(180×200cm)` / `ブラウン` | unmatched | unmatched | gold=review。楽天は同じ もことろん毛布×ダブル で `ブラウン` と `カフェオレブラウン` を別SKUとして持つので、AU `カフェオレブラウン` 行は明示対比で矛盾とした（ラベルとの不一致として記録） |
| `case-069d6decd5e4c69f266e` 他3件（a19e） | `50cm` / `78cm×30cm` / 色 | review | review | 高さ・天板はAUのページ宣言 `幅78×高さ50cm`・`天板サイズ：78×30cm` で支持。しかし `持ち手：プラスチックメッキ`（AU）vs `持ち手：ポリエステル`（楽天）、楽天には `天板高さ50cmのシルバーには持ち手がありません` もある → 仕様矛盾でreview |

## 8. 指標

用語の定義:
- 保守的accepted precision = 確認済TP ÷ 予測accept全件。gold reviewのacceptは未確認/unsafeとして分母に入れ、TPには数えない。
- known-only precision は補助指標（gold reviewを分母から除外）。
- 確認済TP = 予測matchedで、gold matchedかつ返したAU行キーが `matching_au_row_keys` に含まれるもの。
- 商品macro は29固定ペアの非加重平均（定義可能なペア数を併記）。
- 1,383行・196コホートとも同一ケース・同一ラベルで評価した。

### v1（ラベル前freeze）

| 方式 | 集合 | n | accept | 確認済TP | 誤accept(gold unmatched) | 未確認accept(gold review) | 誤行accept | unmatched | 誤delete(gold matched) | unmatched on gold review | review | 保守的accepted precision | known-only precision | 行一致recall | coverage | 商品macro precision | 商品macro recall | 商品macro review率 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 既存best v10 hybrid | 1,383行 | 1383 | 337 | 337 | 0 | 0 | 0 | 815 | 0 | 0 | 231 (16.7%) | 100.0% | 100.0% | 77.5% | 83.3% | 100.0% (n=16) | 61.5% (n=26) | 27.6% |
| 既存best v10 hybrid | 196コホート | 196 | 37 | 37 | 0 | 0 | 0 | 105 | 0 | 0 | 54 (27.6%) | 100.0% | 100.0% | 67.3% | 72.4% | 100.0% (n=14) | 60.9% (n=23) | 27.2% |
| 既存best v10 hybrid | カーテン720 | 720 | 279 | 279 | 0 | 0 | 0 | 360 | 0 | 0 | 81 (11.2%) | 100.0% | 100.0% | 83.8% | 88.8% | 100.0% (n=2) | 50.0% (n=4) | 27.2% |
| 既存best v10 hybrid | 非カーテン663 | 663 | 58 | 58 | 0 | 0 | 0 | 455 | 0 | 0 | 150 (22.6%) | 100.0% | 100.0% | 56.9% | 77.4% | 100.0% (n=14) | 63.6% (n=22) | 27.6% |
| 既存best v10 hybrid | 196内カーテン | 32 | 6 | 6 | 0 | 0 | 0 | 16 | 0 | 0 | 10 (31.2%) | 100.0% | 100.0% | 42.9% | 68.8% | 100.0% (n=2) | 50.0% (n=4) | 31.2% |
| 既存best v10 hybrid | 196内非カーテン | 164 | 31 | 31 | 0 | 0 | 0 | 89 | 0 | 0 | 44 (26.8%) | 100.0% | 100.0% | 75.6% | 73.2% | 100.0% (n=12) | 63.2% (n=19) | 26.5% |
| A full | 1,383行 | 1383 | 466 | 435 | 0 | 31 | 0 | 910 | 0 | 1 | 7 (0.5%) | 93.3% | 100.0% | 100.0% | 99.5% | 95.6% (n=27) | 100.0% (n=26) | 4.8% |
| A full | 196コホート | 196 | 61 | 55 | 0 | 6 | 0 | 129 | 0 | 1 | 6 (3.1%) | 90.2% | 100.0% | 100.0% | 96.9% | 93.8% (n=24) | 100.0% (n=23) | 4.7% |
| A full | カーテン720 | 720 | 360 | 333 | 0 | 27 | 0 | 360 | 0 | 0 | 0 (0.0%) | 92.5% | 100.0% | 100.0% | 100.0% | 95.6% (n=4) | 100.0% (n=4) | 0.0% |
| A full | 非カーテン663 | 663 | 106 | 102 | 0 | 4 | 0 | 550 | 0 | 1 | 7 (1.1%) | 96.2% | 100.0% | 100.0% | 98.9% | 95.7% (n=23) | 100.0% (n=22) | 5.6% |
| A full | 196内カーテン | 32 | 16 | 14 | 0 | 2 | 0 | 16 | 0 | 0 | 0 (0.0%) | 87.5% | 100.0% | 100.0% | 100.0% | 87.5% (n=4) | 100.0% (n=4) | 0.0% |
| A full | 196内非カーテン | 164 | 45 | 41 | 0 | 4 | 0 | 113 | 0 | 1 | 6 (3.7%) | 91.1% | 100.0% | 100.0% | 96.3% | 95.0% (n=20) | 100.0% (n=19) | 5.5% |
| B full | 1,383行 | 1383 | 282 | 251 | 0 | 31 | 0 | 906 | 0 | 1 | 195 (14.1%) | 89.0% | 100.0% | 57.7% | 85.9% | 95.1% (n=24) | 88.5% (n=26) | 9.4% |
| B full | 196コホート | 196 | 52 | 46 | 0 | 6 | 0 | 129 | 0 | 1 | 15 (7.7%) | 88.5% | 100.0% | 83.6% | 92.3% | 92.9% (n=21) | 87.0% (n=23) | 8.6% |
| B full | カーテン720 | 720 | 180 | 153 | 0 | 27 | 0 | 360 | 0 | 0 | 180 (25.0%) | 85.0% | 100.0% | 45.9% | 75.0% | 91.2% (n=2) | 50.0% (n=4) | 25.0% |
| B full | 非カーテン663 | 663 | 102 | 98 | 0 | 4 | 0 | 546 | 0 | 1 | 15 (2.3%) | 96.1% | 100.0% | 96.1% | 97.7% | 95.5% (n=22) | 95.5% (n=22) | 6.9% |
| B full | 196内カーテン | 32 | 8 | 6 | 0 | 2 | 0 | 16 | 0 | 0 | 8 (25.0%) | 75.0% | 100.0% | 42.9% | 75.0% | 75.0% (n=2) | 50.0% (n=4) | 25.0% |
| B full | 196内非カーテン | 164 | 44 | 40 | 0 | 4 | 0 | 113 | 0 | 1 | 7 (4.3%) | 90.9% | 100.0% | 97.6% | 95.7% | 94.7% (n=19) | 94.7% (n=19) | 6.0% |

### v2（v1採点後のlabel-informed改訂）

| 方式 | 集合 | n | accept | 確認済TP | 誤accept(gold unmatched) | 未確認accept(gold review) | 誤行accept | unmatched | 誤delete(gold matched) | unmatched on gold review | review | 保守的accepted precision | known-only precision | 行一致recall | coverage | 商品macro precision | 商品macro recall | 商品macro review率 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 既存best v10 hybrid | 1,383行 | 1383 | 337 | 337 | 0 | 0 | 0 | 815 | 0 | 0 | 231 (16.7%) | 100.0% | 100.0% | 77.5% | 83.3% | 100.0% (n=16) | 61.5% (n=26) | 27.6% |
| 既存best v10 hybrid | 196コホート | 196 | 37 | 37 | 0 | 0 | 0 | 105 | 0 | 0 | 54 (27.6%) | 100.0% | 100.0% | 67.3% | 72.4% | 100.0% (n=14) | 60.9% (n=23) | 27.2% |
| 既存best v10 hybrid | カーテン720 | 720 | 279 | 279 | 0 | 0 | 0 | 360 | 0 | 0 | 81 (11.2%) | 100.0% | 100.0% | 83.8% | 88.8% | 100.0% (n=2) | 50.0% (n=4) | 27.2% |
| 既存best v10 hybrid | 非カーテン663 | 663 | 58 | 58 | 0 | 0 | 0 | 455 | 0 | 0 | 150 (22.6%) | 100.0% | 100.0% | 56.9% | 77.4% | 100.0% (n=14) | 63.6% (n=22) | 27.6% |
| 既存best v10 hybrid | 196内カーテン | 32 | 6 | 6 | 0 | 0 | 0 | 16 | 0 | 0 | 10 (31.2%) | 100.0% | 100.0% | 42.9% | 68.8% | 100.0% (n=2) | 50.0% (n=4) | 31.2% |
| 既存best v10 hybrid | 196内非カーテン | 164 | 31 | 31 | 0 | 0 | 0 | 89 | 0 | 0 | 44 (26.8%) | 100.0% | 100.0% | 75.6% | 73.2% | 100.0% (n=12) | 63.2% (n=19) | 26.5% |
| A full | 1,383行 | 1383 | 435 | 435 | 0 | 0 | 0 | 910 | 0 | 1 | 38 (2.7%) | 100.0% | 100.0% | 100.0% | 97.3% | 100.0% (n=26) | 100.0% (n=26) | 8.6% |
| A full | 196コホート | 196 | 55 | 55 | 0 | 0 | 0 | 129 | 0 | 1 | 12 (6.1%) | 100.0% | 100.0% | 100.0% | 93.9% | 100.0% (n=23) | 100.0% (n=23) | 9.1% |
| A full | カーテン720 | 720 | 333 | 333 | 0 | 0 | 0 | 360 | 0 | 0 | 27 (3.8%) | 100.0% | 100.0% | 100.0% | 96.2% | 100.0% (n=4) | 100.0% (n=4) | 2.2% |
| A full | 非カーテン663 | 663 | 102 | 102 | 0 | 0 | 0 | 550 | 0 | 1 | 11 (1.7%) | 100.0% | 100.0% | 100.0% | 98.3% | 100.0% (n=22) | 100.0% (n=22) | 9.6% |
| A full | 196内カーテン | 32 | 14 | 14 | 0 | 0 | 0 | 16 | 0 | 0 | 2 (6.2%) | 100.0% | 100.0% | 100.0% | 93.8% | 100.0% (n=4) | 100.0% (n=4) | 6.2% |
| A full | 196内非カーテン | 164 | 41 | 41 | 0 | 0 | 0 | 113 | 0 | 1 | 10 (6.1%) | 100.0% | 100.0% | 100.0% | 93.9% | 100.0% (n=19) | 100.0% (n=19) | 9.5% |
| B full | 1,383行 | 1383 | 251 | 251 | 0 | 0 | 0 | 906 | 0 | 1 | 226 (16.3%) | 100.0% | 100.0% | 57.7% | 83.7% | 100.0% (n=23) | 88.5% (n=26) | 13.2% |
| B full | 196コホート | 196 | 46 | 46 | 0 | 0 | 0 | 129 | 0 | 1 | 21 (10.7%) | 100.0% | 100.0% | 83.6% | 89.3% | 100.0% (n=20) | 87.0% (n=23) | 12.9% |
| B full | カーテン720 | 720 | 153 | 153 | 0 | 0 | 0 | 360 | 0 | 0 | 207 (28.7%) | 100.0% | 100.0% | 45.9% | 71.2% | 100.0% (n=2) | 50.0% (n=4) | 27.2% |
| B full | 非カーテン663 | 663 | 98 | 98 | 0 | 0 | 0 | 546 | 0 | 1 | 19 (2.9%) | 100.0% | 100.0% | 96.1% | 97.1% | 100.0% (n=21) | 95.5% (n=22) | 10.9% |
| B full | 196内カーテン | 32 | 6 | 6 | 0 | 0 | 0 | 16 | 0 | 0 | 10 (31.2%) | 100.0% | 100.0% | 42.9% | 68.8% | 100.0% (n=2) | 50.0% (n=4) | 31.2% |
| B full | 196内非カーテン | 164 | 40 | 40 | 0 | 0 | 0 | 113 | 0 | 1 | 11 (6.7%) | 100.0% | 100.0% | 97.6% | 93.3% | 100.0% (n=18) | 94.7% (n=19) | 10.0% |

### source ablation（1,383行）


#### v1

| 方式 | 集合 | n | accept | 確認済TP | 誤accept(gold unmatched) | 未確認accept(gold review) | 誤行accept | unmatched | 誤delete(gold matched) | unmatched on gold review | review | 保守的accepted precision | known-only precision | 行一致recall | coverage | 商品macro precision | 商品macro recall | 商品macro review率 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A sku_only | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| B sku_only | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| A sku_rakuten_title | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| B sku_rakuten_title | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| A sku_au_title | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| B sku_au_title | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| A sku_both_titles | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| B sku_both_titles | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| A sku_descriptions_no_titles | 1,383行 | 1383 | 451 | 420 | 0 | 31 | 0 | 892 | 0 | 1 | 40 (2.9%) | 93.1% | 100.0% | 96.6% | 97.1% | 95.1% (n=24) | 88.5% (n=26) | 7.9% |
| B sku_descriptions_no_titles | 1,383行 | 1383 | 267 | 236 | 0 | 31 | 0 | 888 | 0 | 1 | 228 (16.5%) | 88.4% | 100.0% | 54.3% | 83.5% | 94.4% (n=21) | 76.9% (n=26) | 12.5% |
| A full | 1,383行 | 1383 | 466 | 435 | 0 | 31 | 0 | 910 | 0 | 1 | 7 (0.5%) | 93.3% | 100.0% | 100.0% | 99.5% | 95.6% (n=27) | 100.0% (n=26) | 4.8% |
| B full | 1,383行 | 1383 | 282 | 251 | 0 | 31 | 0 | 906 | 0 | 1 | 195 (14.1%) | 89.0% | 100.0% | 57.7% | 85.9% | 95.1% (n=24) | 88.5% (n=26) | 9.4% |
| A full_no_closed_list | 1,383行 | 1383 | 282 | 251 | 0 | 31 | 0 | 906 | 0 | 1 | 195 (14.1%) | 89.0% | 100.0% | 57.7% | 85.9% | 95.1% (n=24) | 88.5% (n=26) | 9.4% |
| B full_no_closed_list | 1,383行 | 1383 | 282 | 251 | 0 | 31 | 0 | 906 | 0 | 1 | 195 (14.1%) | 89.0% | 100.0% | 57.7% | 85.9% | 95.1% (n=24) | 88.5% (n=26) | 9.4% |
| A full_no_derived_conflicts | 1,383行 | 1383 | 470 | 435 | 0 | 35 | 0 | 910 | 0 | 1 | 3 (0.2%) | 92.6% | 100.0% | 100.0% | 99.8% | 92.2% (n=28) | 100.0% (n=26) | 3.4% |
| B full_no_derived_conflicts | 1,383行 | 1383 | 286 | 251 | 0 | 35 | 0 | 906 | 0 | 1 | 191 (13.8%) | 87.8% | 100.0% | 57.7% | 86.2% | 91.3% (n=25) | 88.5% (n=26) | 8.0% |

#### v2

| 方式 | 集合 | n | accept | 確認済TP | 誤accept(gold unmatched) | 未確認accept(gold review) | 誤行accept | unmatched | 誤delete(gold matched) | unmatched on gold review | review | 保守的accepted precision | known-only precision | 行一致recall | coverage | 商品macro precision | 商品macro recall | 商品macro review率 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A sku_only | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| B sku_only | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| A sku_rakuten_title | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| B sku_rakuten_title | 1,383行 | 1383 | 21 | 17 | 0 | 4 | 0 | 18 | 0 | 1 | 1344 (97.2%) | 81.0% | 100.0% | 3.9% | 2.8% | 83.3% (n=6) | 19.2% (n=26) | 77.6% |
| A sku_au_title | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| B sku_au_title | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| A sku_both_titles | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| B sku_both_titles | 1,383行 | 1383 | 239 | 208 | 0 | 31 | 0 | 675 | 0 | 1 | 469 (33.9%) | 87.0% | 100.0% | 47.8% | 66.1% | 92.2% (n=15) | 53.8% (n=26) | 23.6% |
| A sku_descriptions_no_titles | 1,383行 | 1383 | 420 | 420 | 0 | 0 | 0 | 892 | 0 | 1 | 71 (5.1%) | 100.0% | 100.0% | 96.6% | 94.9% | 100.0% (n=23) | 88.5% (n=26) | 11.7% |
| B sku_descriptions_no_titles | 1,383行 | 1383 | 236 | 236 | 0 | 0 | 0 | 888 | 0 | 1 | 259 (18.7%) | 100.0% | 100.0% | 54.3% | 81.3% | 100.0% (n=20) | 76.9% (n=26) | 16.3% |
| A full | 1,383行 | 1383 | 435 | 435 | 0 | 0 | 0 | 910 | 0 | 1 | 38 (2.7%) | 100.0% | 100.0% | 100.0% | 97.3% | 100.0% (n=26) | 100.0% (n=26) | 8.6% |
| B full | 1,383行 | 1383 | 251 | 251 | 0 | 0 | 0 | 906 | 0 | 1 | 226 (16.3%) | 100.0% | 100.0% | 57.7% | 83.7% | 100.0% (n=23) | 88.5% (n=26) | 13.2% |
| A full_no_closed_list | 1,383行 | 1383 | 251 | 251 | 0 | 0 | 0 | 906 | 0 | 1 | 226 (16.3%) | 100.0% | 100.0% | 57.7% | 83.7% | 100.0% (n=23) | 88.5% (n=26) | 13.2% |
| B full_no_closed_list | 1,383行 | 1383 | 251 | 251 | 0 | 0 | 0 | 906 | 0 | 1 | 226 (16.3%) | 100.0% | 100.0% | 57.7% | 83.7% | 100.0% (n=23) | 88.5% (n=26) | 13.2% |
| A full_no_derived_conflicts | 1,383行 | 1383 | 470 | 435 | 0 | 35 | 0 | 910 | 0 | 1 | 3 (0.2%) | 92.6% | 100.0% | 100.0% | 99.8% | 92.2% (n=28) | 100.0% (n=26) | 3.4% |
| B full_no_derived_conflicts | 1,383行 | 1383 | 286 | 251 | 0 | 35 | 0 | 906 | 0 | 1 | 191 (13.8%) | 87.8% | 100.0% | 57.7% | 86.2% | 91.3% (n=25) | 88.5% (n=26) | 8.0% |

### source ablation（196コホート, v2）

| 方式 | 集合 | n | accept | 確認済TP | 誤accept(gold unmatched) | 未確認accept(gold review) | 誤行accept | unmatched | 誤delete(gold matched) | unmatched on gold review | review | 保守的accepted precision | known-only precision | 行一致recall | coverage | 商品macro precision | 商品macro recall | 商品macro review率 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A sku_only | 196コホート | 196 | 21 | 17 | 0 | 4 | 0 | 4 | 0 | 1 | 171 (87.2%) | 81.0% | 100.0% | 30.9% | 12.8% | 83.3% (n=6) | 21.7% (n=23) | 77.6% |
| B sku_only | 196コホート | 196 | 21 | 17 | 0 | 4 | 0 | 4 | 0 | 1 | 171 (87.2%) | 81.0% | 100.0% | 30.9% | 12.8% | 83.3% (n=6) | 21.7% (n=23) | 77.6% |
| A sku_rakuten_title | 196コホート | 196 | 21 | 17 | 0 | 4 | 0 | 4 | 0 | 1 | 171 (87.2%) | 81.0% | 100.0% | 30.9% | 12.8% | 83.3% (n=6) | 21.7% (n=23) | 77.6% |
| B sku_rakuten_title | 196コホート | 196 | 21 | 17 | 0 | 4 | 0 | 4 | 0 | 1 | 171 (87.2%) | 81.0% | 100.0% | 30.9% | 12.8% | 83.3% (n=6) | 21.7% (n=23) | 77.6% |
| A sku_au_title | 196コホート | 196 | 41 | 35 | 0 | 6 | 0 | 108 | 0 | 1 | 47 (24.0%) | 85.4% | 100.0% | 63.6% | 76.0% | 90.0% (n=15) | 60.9% (n=23) | 23.3% |
| B sku_au_title | 196コホート | 196 | 41 | 35 | 0 | 6 | 0 | 108 | 0 | 1 | 47 (24.0%) | 85.4% | 100.0% | 63.6% | 76.0% | 90.0% (n=15) | 60.9% (n=23) | 23.3% |
| A sku_both_titles | 196コホート | 196 | 41 | 35 | 0 | 6 | 0 | 108 | 0 | 1 | 47 (24.0%) | 85.4% | 100.0% | 63.6% | 76.0% | 90.0% (n=15) | 60.9% (n=23) | 23.3% |
| B sku_both_titles | 196コホート | 196 | 41 | 35 | 0 | 6 | 0 | 108 | 0 | 1 | 47 (24.0%) | 85.4% | 100.0% | 63.6% | 76.0% | 90.0% (n=15) | 60.9% (n=23) | 23.3% |
| A sku_descriptions_no_titles | 196コホート | 196 | 53 | 53 | 0 | 0 | 0 | 124 | 0 | 1 | 19 (9.7%) | 100.0% | 100.0% | 96.4% | 90.3% | 100.0% (n=21) | 91.3% (n=23) | 12.1% |
| B sku_descriptions_no_titles | 196コホート | 196 | 44 | 44 | 0 | 0 | 0 | 124 | 0 | 1 | 28 (14.3%) | 100.0% | 100.0% | 80.0% | 85.7% | 100.0% (n=18) | 78.3% (n=23) | 15.9% |
| A full | 196コホート | 196 | 55 | 55 | 0 | 0 | 0 | 129 | 0 | 1 | 12 (6.1%) | 100.0% | 100.0% | 100.0% | 93.9% | 100.0% (n=23) | 100.0% (n=23) | 9.1% |
| B full | 196コホート | 196 | 46 | 46 | 0 | 0 | 0 | 129 | 0 | 1 | 21 (10.7%) | 100.0% | 100.0% | 83.6% | 89.3% | 100.0% (n=20) | 87.0% (n=23) | 12.9% |
| A full_no_closed_list | 196コホート | 196 | 46 | 46 | 0 | 0 | 0 | 129 | 0 | 1 | 21 (10.7%) | 100.0% | 100.0% | 83.6% | 89.3% | 100.0% (n=20) | 87.0% (n=23) | 12.9% |
| B full_no_closed_list | 196コホート | 196 | 46 | 46 | 0 | 0 | 0 | 129 | 0 | 1 | 21 (10.7%) | 100.0% | 100.0% | 83.6% | 89.3% | 100.0% (n=20) | 87.0% (n=23) | 12.9% |
| A full_no_derived_conflicts | 196コホート | 196 | 64 | 55 | 0 | 9 | 0 | 129 | 0 | 1 | 3 (1.5%) | 85.9% | 100.0% | 100.0% | 98.5% | 90.0% (n=25) | 100.0% (n=23) | 3.4% |
| B full_no_derived_conflicts | 196コホート | 196 | 55 | 46 | 0 | 9 | 0 | 129 | 0 | 1 | 12 (6.1%) | 83.6% | 100.0% | 83.6% | 93.9% | 88.6% (n=22) | 87.0% (n=23) | 7.3% |

ablationの読み方（v2、1,383行）:
- SKU原文のみ: Aのaccept 21件（うち4件はgold review）、review 1,344件。楽天タイトルを足しても判定は変わらない（楽天タイトルはAU行のみの原子の検証にしか使われず、今回は判定を変えなかった）。
- ＋固定AUタイトル: accept 239件、unmatched 675件。名前付きサイズ・段数・枚数の単一値が効く。
- 説明のみ（タイトル無し）: A accept 420件。
- full: A accept 435件。
- 内容リストの閉包を外すとAはBと同値になる（accept 251）。
- 派生矛盾を外すと、gold reviewのaccept 35件が戻る。

### ラベル不要の不変条件（v2c、ラベル未使用）

`check_sku_gate_invariants.py` は、freezeと予測manifestを照合したうえで、ラベルを開かずに次を全件検査する。結果は全て違反0（`all_passed: true`）。

| 不変条件 | 内容 | A | B |
|---|---|---|---|
| 行の単射性 | 1つのAU行を、異なる楽天選択値の組に対して重ねてacceptしない | accept行 435、違反0 | accept行 251、違反0 |
| B ⊆ A | Bのacceptは全て、Aでも同じ行のaccept | — | B accept 251、違反0 |
| 逆判定なし | A・B間で matched と unmatched に割れない | 違反0 | 〃 |
| config間で行が変わらない | どのsource configのacceptも、fullでは同じ行のacceptかreview（証拠を足すと保留にはなるが、別の行やunmatchedには移らない） | 全7 config 違反0 | 全7 config 違反0 |
| 兄弟値の入れ替え | full acceptの各ケースで1軸を兄弟値（ページの選択肢一覧から引用）に置き換える。元の行を再acceptしない | 8,724件、違反0 | 4,578件、違反0 |
| 実twinとの一致 | 入れ替え後の選択値と同じ選択値の実ケースが同じ固定ペアにあれば、判定と行がそのケースの予測と一致する（必要ならtwinをSKU属性無しで再実行して比較） | 8,344件すべて一致（twin無し380） | 4,198件すべて一致（twin無し380） |
| 引用欠落ガード | 決定済み（matched/unmatched）の全ケースで選択値のspanを外すと、reviewになる | 1,345件すべてreview | 1,157件すべてreview |

- 入れ替えの内訳（A）: 別行にaccept 7,360、unmatched 890、review 474。（B）: 別行にaccept 3,406、unmatched 698、review 474。
- AとBの差（Aだけがacceptした184件）は、全て `レースカーテン=なし`（2ペア、153+27件）か `オプション=なし`（1ペア、4件）だった。差分はラベルを見ずに確認した。Bでは不在要件がunknownになり、`unresolved_requirement` でreviewになる。
- 初版チェッカー（v2b）は、twinを楽天ページと選択値だけで照合していた。そのため、同じ楽天ページを持つ別の固定ペアのケースをtwinとして拾い、約半数で「不一致」と出た。固定ペアもキーに加えたv2cで解消し、v2bは採点前に廃止した。

embeddingの寄与（実測）: 既存baselineのembedding top-1は、gold matched 435件中429件（98.6%、非カーテン96/102）で正しい行を指す。v2 Aの構造ゲートはembedding無しで435/435の行に一致した。既存bestのembedding改善は「確立」扱いせず、この差分だけを報告する。baselineの337 acceptでは、仕様ゲートがembedding top-1を上書きした件数は0。

## 9. gold review 37行の扱い（除外・二値化なし）

| 型 | 件数 | baseline | A v1 / B v1 | A v2 / B v2 |
|---|---|---|---|---|
| ct0↔704502086 幅150・レースあり、フック数 7 vs 9 | 27 | review | matched / matched | review / review |
| fgc010↔688640726 本体幅 66 vs 50 | 4 | review | matched / matched | review / review |
| a19e↔309329265 持ち手の材質・有無の矛盾 | 4 | review | review / review | review / review |
| gad100 スリム（高さ90 vs 91） | 1 | review | review / review | review / review |
| fek002↔704285114 ブラウン vs カフェオレブラウン | 1 | review | unmatched / unmatched | unmatched / unmatched |

既知の8+27件の監査例は、上の型に含まれる開発ベンチマーク内のreviewとして扱った。除外・二値化・隠蔽はしていない。A v2がreviewにしたgold unmatched 2件は gad100 の `天板付き / 天然木`・`本棚付き / 天然木`（構造部品は内容リストの閉包対象外にしたため、unknown → review）。

## 10. 実行コマンド

```bash
# 検証・展開（§2）
(cd experiments/sku-matching/results && sha256sum -c 20261010-claude-task-checkpoint.zip.sha256)
unzip -n -q experiments/sku-matching/results/20261010-claude-task-checkpoint.zip -d .
unzip -n -q experiments/sku-matching/results/20261010-cpu-minimal-claims.zip -d .
# テスト
python3 -B -m unittest tests.test_lab_sku_gates -v
# v1（ラベル前）: コード commit 5c1bc28 で
python3 -B experiments/sku-matching/run_sku_gates.py prepare --out .lab-output/sku-gate-tasks-20261010-v1
python3 -B experiments/sku-matching/run_sku_gates.py freeze  --out .lab-output/sku-gate-tasks-20261010-v1 --note "..."
python3 -B experiments/sku-matching/run_sku_gates.py predict --out .lab-output/sku-gate-tasks-20261010-v1
python3 -B experiments/sku-matching/evaluate_sku_gates.py --out .lab-output/sku-gate-tasks-20261010-v1   # evaluator commit d7b3cfc
# v2（label-informed）: commit 3dca08d で（既定 --out は v2）
python3 -B experiments/sku-matching/run_sku_gates.py prepare
python3 -B experiments/sku-matching/run_sku_gates.py freeze --note "..." --labels-seen "..."
python3 -B experiments/sku-matching/run_sku_gates.py predict
python3 -B experiments/sku-matching/evaluate_sku_gates.py
# v2c（ガード＋不変条件、既定 --out は v2c）: commit 9688906 で
python3 -B experiments/sku-matching/run_sku_gates.py prepare
python3 -B experiments/sku-matching/run_sku_gates.py freeze --note "..." --labels-seen "..."
python3 -B experiments/sku-matching/run_sku_gates.py predict
python3 -B experiments/sku-matching/check_sku_gate_invariants.py      # ラベルを開かない
python3 -B experiments/sku-matching/evaluate_sku_gates.py
for f in inputs/products.jsonl inputs/cases.jsonl predictions/*.jsonl; do cmp .lab-output/sku-gate-tasks-20261010-v2/$f .lab-output/sku-gate-tasks-20261010-v2c/$f; done
# 結果ZIP
python3 -B experiments/sku-matching/package_sku_gates.py .lab-output/sku-gate-tasks-20261010-v1 .lab-output/sku-gate-tasks-20261010-v2 --archive experiments/sku-matching/results/20261010-sku-gate-tasks.zip --purpose "..." --label-content "..."
```

v2cの追加ZIPは、`package_sku_gates.py` にv2c/v2bの freeze.json・inputs/manifest.json・predictions/manifest.json・invariants/（v2cは evaluation/ も）を個別に渡して作った。

実行時間（CPU 4コア）: predictは全16系列で約2.5分。不変条件チェックも約2.5分。入力構築は約1秒。Kaggle・GPU・GCP・外部API・商品データの追加取得・認証は使っていない。モデル重みも不要（取得・使用なし）。

## 11. 未測定・限界

- 独立holdoutは無い。ラベルはLuna機械注釈で人手未確認。v1も、パーサ／語彙を同じ29ペアの原文を読んで作ったin-sampleの結果。v2cの不変条件はラベルを使わない内部整合性の検査であり、正解率の代わりにはならない。v2はラベル（gold reviewの根拠）を読んだ後の調整結果。いずれも汎化性能・人手確定精度・日本Web全体の網羅率ではない。
- 新しいカテゴリ・別店舗への汎化: 未測定。語彙外の語はvariant/unknownになり、reviewへ倒れる設計だが、実測は無い。
- 楽天タイトルの寄与: 今回の1,383行では判定差0（測定済み）。
- 色の別名（例 ブラウン/カフェオレブラウン）の真偽: 未確定。明示対比のある場合だけ矛盾として扱った。
- B方式でのカーテン「なし」・敷きパッド「オプションなし」は、リスト中の不在以外に逐語の根拠が無い。そのため設計上reviewになる（184件がgold matched）。
- 画像のテキスト（寸法・付属品が画像だけにある場合）は未対応（入力に無い）。
- GCPの速度・費用は未測定（依頼どおり）。

## 12. 次案

1. 人手で確認したholdout（別店舗・別カテゴリ）を作り、v2 A/Bを再freezeせずにそのまま適用して、誤accept・誤delete・review率を測る。
2. Bのreview 226件の多くは「リスト中の不在」由来。人手判断の対象として、内容リストの引用＋閉包仮定を明示したreviewカードとして出す。
3. 語彙外の品種語・別名色をCPUモデル（GLiNER/NLI等）で候補化する。ただし採否は必ずliteral quoteとスコープのゲートで決め、モデルのconfidence単独で決めない。
