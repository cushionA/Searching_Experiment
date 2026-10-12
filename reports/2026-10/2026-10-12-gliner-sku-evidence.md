# GLiNER2系：実SKUの未対応条件向け原文抽出 — 2026-10-12

## 結論

**抽出はCPUで動くが、そのままSKU判定へ導入しない。** 固定AUの12実商品を全文走査し、365候補spanを得た。モデル入力上の引用不一致0、対応づけた原本断片の不一致0。一方、収納例・利用例を付属品として拾い、選択サイズ・構成の重要な証拠を落とした。抽出成功と対象行への適用性は別である。

今回の成果は**原文抽出smokeとデータ監査**。支持/矛盾/不明の決定器、最終SKU採否、独立goldによる精度は未評価。PR23の販売状態分類の改善ではない。既存の推論・収集・ラベルを変更せず、GPUジョブも投入していない。

## テストデータの確認

監査基準はPR26 head `e614a3f30bde3db7112d477b070707e2bf4a78cb`。mainは`7460493cc3f7242748c95f711e71a59537ef34ea`。旧基準`7b8f5ab...`を現在の入力として使っていない。既存Codex作業は`630ba9e97fdfe274c66d78e574a1980726ab11b6`を読み取り確認した。終了前のリモート観測は[remote-source-heads](../../benchmarks/gliner-sku-evidence-20261012/remote-source-heads.txt)。

全パスは `experiments/sku-matching/results/` 配下のZIP。件数は今回ファイルを読み再集計した。

| 共有物 | 実確認した粒度・件数 | 注意 |
|---|---|---|
| `20261010-claude-task-checkpoint.zip` | 旧29ペア、Luna機械ラベル1,383ケース（matched435 / unmatched911 / review37）、AU item JSON・楽天HTML・dossier | 実取得由来。合成48ペアとは別。既読開発データ |
| `20261012-sku-align-v6-v7-outputs.zip` のv7 legacy | 1,317ケース×候補行、23 AU商品、12楽天URL。重複case+row 0 | 未確認720 model_candidate・1,818 one_sided・80 residual。aligned1,237条件も文脈として保持 |
| `20261012-sku-independent-real-eval-v2-v3-token-inputs.zip` のv2 | 176商品ペア・2,494ケース・AU全2,142行・124 group。dev1,295 / test1,199 | 全件 `primary_image_token_mapping_review`。商品同一性確定済み集合とはしない |
| 同ZIPのv3 | 37商品ペア・168ケース・AU全122行・31 group。dev75 / test93 | 同じく商品ペアレビュー段階。主smokeには使わない |

作成経緯：旧集合は2026-10-10の取得原本から楽天1 SKUと固定AU全行を構築、Lunaが機械注釈。v7は過去の類似度行列を再利用し2026-10-12に中核・残余を改訂。v2/v3は他店収集の型番/主画像トークン対応、Haiku機械注釈の集合。元のmanifestと入力ハッシュを[archive-inventory](../../benchmarks/gliner-sku-evidence-20261012/archive-inventory.json)・[provenance](../../benchmarks/gliner-sku-evidence-20261012/provenance.json)へ保存した。今回新規ページ収集はしていない。

- legacy v7 handoff SHA256: `d8697d624ddc41ea3ce22fd6565d6351e5aaaba643cc1bd993e6835bfc0d923b`
- v6/v7共有ZIP: `f5522952c8a67d5a7289cdb6f9cdcb5f2d093026a79360b704c08fd68dd5503c`
- v2/v3入力ZIP: `db2815a3d5c726e45f996ad07e73cdcf1cdff0610de53ad84af8a4a0a880488f`

### 重大な限界・不整合

1. **legacy v7が要求するv10入力が欠ける。** inner manifestの `products.jsonl=f61a6372...`、`cases.jsonl=1590b856...` に一致する入力は、PR26 ZIP群と確認したCodex結果ZIP群で発見できなかった。存在するv2をv10と偽って再現しない。今回のAU候補配列は旧v2を原本で再検証したもの。全12商品の376行を保存したが、v10全入力との同一性は主張しない。
2. **標準JSON不適合。** legacy handoffに`NaN`が1,818箇所。派生smokeの欠損AU値だけnullへ明示変換、上流ファイルは不変。厳格なJSON利用者はそのまま読めない。
3. **独立goldなし。** legacyの一意span1,925件は原本SHA・path・offset・quoteが全件一致。機械ラベルのdossier参照4,337件も指定容器内に引用が存在した。しかし「商 品 詳 細」だけの引用は条件を証明しない。候補行に適用できる意味の検証や独立人手注釈の代用にはならない。
4. **ラベル粒度が違う。** matched/unmatched/reviewはケースの判断であり、各未対応条件の支持/矛盾/不明ラベルではない。今回の18引用参照には同一商品・同一引用の重複1件と、上記見出し1件を含む。抽出referenceとして不完全で、field/span precision・recallを確定できない。
5. **splitの読み方。** v2/v3のcase内splitはnull。別の`groups-token/groups.jsonl`へdossier IDでjoinが必要。Windows区切りを原本参照の読取時だけ正規化すると、一意spanはv2 38,200件、v3 3,656件、計41,856件すべて一致。case ID・pair_ref重複0、dev/test間case/dossier/group/AU ID/楽天URL重複0。これは既定groupの検査であり、全商品の未知の意味的系列や過去研究への漏洩を否定するものではない。

監査結果は[data-audit](../../benchmarks/gliner-sku-evidence-20261012/data-audit.json)と[latest-data-audit](../../benchmarks/gliner-sku-evidence-20261012/latest-data-audit.json)。v2/v3のtestラベルを開いてモデル選択・調整は行っていない。旧29ペアは過去作業で使用済みなので独立testと呼ばない。

## 既存評価との違い・モデル選択

既存 `backend_gliner_extract.py` は `fastino/gliner2.5-multi-v1` を合成48ペア・96 SKU文字列で評価済み。旧READMEでは英語field説明・SKU単体で可視属性全一致31/96、title+SKUで6/96。今回その合成推論は繰り返していない。Decideの実SKU条件分類・scoped分類も既存作業にあるが、抽出checkpointの全文span検証とは区別した。夜レーダーは読解のみ、GLiNER推論0。

一次資料を再取得し実在ID・revision・Apache-2.0を確認：

| checkpoint | 役割 | 今回 |
|---|---|---|
| [fastino/gliner2-multi-v1](https://huggingface.co/fastino/gliner2-multi-v1) | 従来span型の多言語抽出 | ID/revision/licenseのみ確認 |
| [fastino/gliner2.5-multi-v1](https://huggingface.co/fastino/gliner2.5-multi-v1) | boundary型、多言語entity/構造抽出 | **採用、FP32 CPU、287,355,159 parameters** |
| [fastino/GLiNER2.5-multi-Decide](https://huggingface.co/fastino/GLiNER2.5-multi-Decide) | 分類特化fine-tune | 分類能力を根拠span抽出能力と同一視せず、重複推論なし |

採用revisionは `cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3`。旧合成検証と同じ重みなので、新しいモデルの改善ではない。公式[GLiNER2実装](https://github.com/fastino-ai/GLiNER2)の`AutoExtractor`からBoundaryExtractorをロードした。多言語モデルだがカードは日本語SKUの精度保証・独立評価を示していない。日本語入力で動いた事実と品質を分ける。資料snapshotは[sources](../../benchmarks/gliner-sku-evidence-20261012/sources/)。

ライブラリは`gliner2 2.1.0`、モデル名はGLiNER2.5であり別の版番号。torch2.7.1+cpu、Transformers5.17.0、Python3.12.14。旧2.0.0を5.17.0と同時指定すると依存宣言の`transformers<5`で拒否されたため、公式現行2.1.0で解決しpip check成功。失敗ログと最終lockを保存。モデル警告（config model_type差異）はログに残す。

## 方法

寸法、タイプ、枚数・複合値、残余、固定タイトルと選択条件の衝突を含む12 AU商品を層別指定し、各商品で辞書順最初の実handoff行を選んだ。付属品分類による事前選別やラベル結果による選別はない。商品単位で同じ12件を全方式へ渡し、未対応条件のliteral一致をbaselineとする。全候補AU行は4/10/4/8/1/5/2/3/6/153/153/27行、合計376行。価格を同一性根拠に使わず、AU側残余はRakuten確認が必要なまま保持する。

モデル入力はAUタイトル・`itemComment`・`extraItemComment`・`detailComment`の全文テキスト。HTML text nodeの原本offsetを保持し、リンク文・別サイズ表も入力から消さずリスクとして記録した。画像・alt属性の仕様は未読。6 field（dimension / piece_count / material / color / product_type / included_item）で候補spanを抽出。色や材質を出力してもaligned条件の再判定には使わない。今回12行には未対応材質条件の評価群がなく、材質は抽出診断に限る。

モデルへ楽天の要求値や機械ラベルを正解として入力しない。原文一致baselineは未対応の楽天値、またはAUで確認すべき楽天残余だけを探す。両者とも候補根拠であり支持判断ではない。意味分類器は呼ばず、全出力の`condition_state`と`row_applicability`は`unassessed`。

schema・threshold0.5・公式既定whitespace splitterを固定。全文をschema込み480 subword以下の45 chunkに分割、8 word重複、非空白文字の全被覆を確認。文字列片への拡張はHTML text node単位（文章・表の適用範囲とは異なる）。日本語char splitterや閾値・promptの後調整なし。

## 結果・比較

以下は既存機械引用18行の**保持診断**。独立したrecall/accuracyではない。重複・見出しも含む元参照を恣意的に修正せず表示する。

| 方法 | 引用と1文字以上重なる | 引用全文を保持 | 保持文字数 |
|---|---:|---:|---:|
| 未対応値の単純原文一致span | 3/18 | 1/18 | 133 |
| 同一致から原文テキスト片へ拡張 | 3/18 | 3/18 | 2,013 |
| GLiNER抽出span | 13/18 | 1/18 | 2,235 |
| GLiNERから原文テキスト片へ拡張 | 15/18 | 15/18 | 8,897 |
| 全文保持（上限対照） | 18/18 | 18/18 | 22,371 |

広い6field抽出なので、未対応値だけを探すbaselineより多くの内容を保持する。文字数増加や対象外語も伴い、15/18を意味的な優位・SKU精度として扱わない。長い引用の全文保持はentity抽出と粒度が合わず、span単独の1/18もモデル一般のrecallではない。候補抽出・範囲確認・最終判断を分離する必要がある。

CPU（AMD EPYC、可視5CPU、torch2 threads、batch1）：

- 2商品smoke：推論6.805秒、load13.182秒。
- 残り10商品：推論30.694秒、load13.252秒。
- 全12商品：**推論37.499秒、0.320商品/秒**。45 chunkの中央値0.850秒、p95 1.007秒。1商品平均約3.12秒。
- peak RSS **2,770.84 MiB**（プロセス生涯最大、import/loadを含む）。GPU不足なし。2回のプロセス全体計71.615秒、download別176.11秒。
- literal検索12商品の計0.605ms。両者の機能量が違うので倍率を優位性としない。
- 他の監査・既存testsと一部並行した観測値。隔離された厳密な速度比較・長時間throughput保証ではない。

計測と全予測は[evaluation.json](../../benchmarks/gliner-sku-evidence-20261012/evaluation.json)、[candidates.json](../../benchmarks/gliner-sku-evidence-20261012/candidates.json)、各`smoke/bounded-*.json`に保存。

## 具体的な誤り・取りこぼし

- AU `524251610`：収納物の例「ぬいぐるみ」「おむつ」を`included_item`として抽出。原文引用は正しくても付属の証明ではない。「スリム」は寸法64×35×90cmだけでは確認できず内部不明を保つ。
- AU `695392874`：注意文の「加熱した鍋」「湯沸かし器などを直接置かないでください」を`included_item`へ入れた。選択80cmに対する固定60cmという矛盾の根拠は寸法から取得できるが、これを自動で分類する決定器は未検証。
- AU `337595272`：`シングルサイズ`をpiece_count、用途「２段ベッド」「補強用」をincluded_itemへ。固定singleの引用「こちらのページはシングルサイズです」は候補テキスト片にも残らなかった。他のsingle寸法を拾うことと、当該引用の保持は別。
- AU `704500131`：選択`100×220cm`は原文サイズ一覧にあるがdimension出力に残らず、別サイズ多数を抽出。別4枚セットへのリンク文も拾った。サイズ表やリンク内の語を選択行の支持へ流用できない。
- AU `653618158`：要求は「パネル20枚＋ドア1枚」。実AUのパネル12枚は部分抽出したが、ドア1枚を落とした。数字だけ取り出して複合値全体を支持してはいけない。
- AU `704502086`：幅100cm欄の「レースカーテン 2枚」を落とした。抽出した他の2枚/4枚や幅150cm欄を代用できない。
- 全365 spanのうち16がHTML anchor部分に触れた。これは構造的リスク件数で、全16件が意味的誤りという採点ではない。

13個の小標本原文監査メモを[source-audit-probes](../../benchmarks/gliner-sku-evidence-20261012/source-audit-probes.json)と[probe-retention](../../benchmarks/gliner-sku-evidence-20261012/probe-retention.json)へ保存。AIによる目視監査で、人手独立goldではない。支持候補・矛盾候補・不明・別範囲を区別し、元ラベルは上書きしていない。

## 導入判断・次

全文を捨てる証拠圧縮器や最終SKU判定器としては採用しない。根拠候補を追加表示する実験用途に限り、原文と候補行の全条件を維持して再検証できる。特に、付属品の名前だけの抽出と、実際の同梱、選択サイズの証明を分離する必要がある。実用機能は完成していないためdraft PRは作らず研究ブランチのみ保存する。

次に必要なのは追加大規模推論よりも、(1) v10入力の正確な共有、(2) machine引用の見出し・重複・範囲を分けた参照形式、(3) 原文のサイズ表見出しやanchor境界を保持した候補証拠と候補行の結合検証。独立goldをユーザーに作成要求せず、自動検査可能な失敗と未確定意味を分ける。現状データから人手独立精度を確定することはできない。

## 再現・checks

[実行コードと手順](../../experiments/gliner-sku-evidence/README.md)。入出力SHAは[SHA256.json](../../benchmarks/gliner-sku-evidence-20261012/SHA256.json)、モデルrevision・重みhashは[model-manifest](../../benchmarks/gliner-sku-evidence-20261012/model-manifest.json)。

- 固定input SHA256: `5a509f26b3d5a76ffd0d6c3069dc40ae6062c29c88a39e75c4e27ed4a5cb028a`
- 候補output SHA256: `eb780d359242ca0059d9abf74af7cecf402416d3b2b82c6547d9513e197b2dc7`
- 数値evaluation SHA256: `e6e27c747915b31a6c69d614a4ab4678cfdb5a2eadd1fa945cca7e376888697f`
- 新規artifact検算 **6/6成功**（input不変、全文被覆、原本offset、未評価状態保持、split分母、集計再計算）。pip check成功。
- 必須`test_lab*.py`は360件、**1 failure・12 error・20 skip**。既存の未同梱`.lab-output` fixture群と`robots-parser`不足。今回の新規コードを実行する失敗ではなく、既存コードは変更していない。全suite成功とは報告しない。[ログ](../../benchmarks/gliner-sku-evidence-20261012/lab-tests.log)
- 原本・既存ラベル不変、推論は12商品のみ。有料API・Kaggle upload・merge・deployなし。
