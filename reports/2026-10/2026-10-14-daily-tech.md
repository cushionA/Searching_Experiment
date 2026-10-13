# 2026-10-14 JST 朝の日次技術調査

## 結論

**A 1 / B 3 / C 1（候補群単位）。実用機能PR・推論PoCは0。** 新しい有用な確認は次の3点。

1. **RapidOCR 3.10.0の日本語OCRはPP-OCRv6 `small`以上を選ぶ。** `tiny`は検出・認識とも日本語を対応集合に含めず、`ja`/`jp`も`japan`へ正規化して拒否する設計。小ささだけでtinyへ置換すると動かない。公式YAML、resolver、付属テストを読み、24通りの設定表を静的検算した。smallで日本語画像の独立評価をする価値がありA。ただし推論成功や精度向上は未確認。
2. **Playwright MCP `browser_find`は全体snapshot生成後に絞り込む。** 出力・コンテキスト量の削減と、ブラウザ前処理の削減は別。現行MCP 0.0.83の依存先をnpm `gitHead`まで追って確認した。検索前処理停滞の解決策として単独PR化する根拠はない。WebMCP直接呼出しは別の有望経路だが、対応ページが必要。
3. **LFM2.5系列は350Mだけでなく230Mと新しい24B-A2Bも存在。** 日本語を明記する小型生成モデルとして230M/350MはB。10/12作成・10/13更新の24B-A2Bは総24B、仕様欄のactive 2.45Bで、2B級のメモリ要件と同一視しない。商用はLFM Open Licenseの年商条件を確認する必要があり、一律Apache扱いにしない。

推論用の隔離probeは `bwrap: setting up uid map: Read-only file system`、exit 1。候補を通常workspaceで動かす代替や権限変更はせず、一次資料と静的検算を完了した。これは新候補の品質失敗ではない。

## 概要・既往成果物の確認

観測開始は2026-10-13 22:03 UTC頃（10/14 07:03 JST）。個々の取得時刻はmanifestに保存。この朝の観測範囲であり、10/14全日の更新網羅ではない。

- `AGENTS.md`、`docs/cloud-lab.md`、関連bot-blocking skillを読んだ。今回はサイト診断・grounding探索を実行せず、同skillの実行手順は適用していない。個人文脈skillの適用条件も確認したが、継承内容で既往指示は足りるため追加検索なし。
- base/main `7460493cc3f7242748c95f711e71a59537ef34ea`、独立branch `research/2026-10-14-daily-tech`。昨日の `bf99e311e9e9111905e3c8aa3a7304b477ce0686` の報告全文とopen PR5件を確認。
- PR26 head `e614a3f`、PR25 head `62daa3e`。直近報告以後の新headなし。SKU記録 `d37cf3b8112ca9db818f844101dab7daa72c9536`、夕方 `a4f02d484addcd13be05671cd02f06ad49f32a25` とremoteを照合。別担当の収集・推論・stateは操作しない。夕方のnative成功を朝のモデル推論成功と数えない。
- PR19検索追加停止、PR23の既往分類結果を維持。10/10 OCR selector、10/11 Shadow DOM、10/12 Docling.rs、10/13 Xberg/Docling templateの反復なし。
- 調査2件を指定 `gpt-6-luna` へ委譲。親が一次API/ソースで補正・検証。ホスト指定は確認したが、サービス側実モデルIDの独立検証はしていない。

## 方法

GitHub公開APIのmetadata/releases/readme、固定版のソース/YAML/テスト、PyPI metadata、npm metadata、HF APIとrevision固定カード・licenseを取得。モデル重み・候補依存のinstallなし。HF系列一覧は`author=LiquidAI&search=LFM`の更新順最大100件であり、全モデル網羅ではない。作成日、更新日、release日を区別。

[取得コード・再現手順](../../experiments/daily-tech-20261014/README.md)、[36一次資料のURL・SHA-256・bytes・取得時間](../../benchmarks/daily-tech-20261014/manifest.json)、[検算結果](../../benchmarks/daily-tech-20261014/audit.json)を保存。

自作Pythonで公式YAMLをデータとして解析し、det/rec × tiny/small/medium × japan/ja/jp/enの24組を確認。RapidOCRのresolver自体・付属pytestは実行していない。PlaywrightはMCP 0.0.83 → core `1.64.0-alpha-1790635538000` → npm gitHead `e8149b8257d32dcf8f72573ecc43e72439da7080` → `find.ts`の順で版を対応づけた。

## 結果・比較

|候補・目的/新規性|公開更新・コミュニティ|license・導入・hardware/制約|判断・検証価値|
|---|---|---|---|
|**RapidOCR 3.10.0**：言語別モデル経路をYAMLへ集約、対応言語照会、遅延初期化、headless OpenCV既定。独立画像OCR用途。|10/8 15:21 UTC release、観測時8,131 stars、複数contributor、push10/13。最新releaseは3.10.0。|本体Apache-2.0。既定small det/rec/clsは公式MODEL_LICENSESにApache-2.0とhashを明記。他モデル/フォントを一括許諾しない。Python≥3.8、OCR依存＋別runtime導入が必要。CPU ONNXが既定、GPU任意。RAM/速度未測定。|**A**。日本語smallの品質・初回/2回目時間を独立画像で確かめる具体的価値。tinyの日本語対象外を24設定で静的確認。既定もsmall。モデル品質・実推論は未確認、導入保留。|
|**Playwright MCP 0.0.83 / WebMCP**：対応ページの登録ツールをMCP toolとして直接呼ぶ。0.0.82でlist/call方式から統合toolへ。|0.0.82は9/18、最新0.0.83は9/28、38,101 stars。Microsoft、upstreamテスト/PRあり。0.0.83はstale tab/frameとChromium155+呼出しも修正。|Apache-2.0。Nodeと対応browserが必要。MCP packageのNode≥18に対し、固定依存coreは≥20なので18だけで十分としない。CPU、browser RAM未測定、GPU必須の記載なし。WebMCPはexperimental、ページ側実装とブラウザ対応が前提。|**B**。UI探索を省略できる仮説。公開実対象で同じデータが取れると確認してから。一般サイト発見/coverage増加の保証なし。ページ登録のschema/annotationを信用・実行許可と同一視しない。|
|**同MCP browser_find**：一致するsnapshot行と文脈を返す。7/9の0.0.78で追加、0.0.83はファイル保存対応。|上記と同じ系列。新releaseとは数えず、既調査Shadow DOMとは異なる適用境界を確認。|上記と同じ。本体は`ariaSnapshot`→全行split/走査→snippet構築。現在のページ内のみ。|**B（ブラウザ操作の出力量削減）**。検索前処理高速化用途では採用根拠不足。返す文字数とCPU時間を混同しない。検索結果rerankや新サイト発見ではなく、検索系PRは作らない。|
|**LFM2.5 230M/350M/24B-A2B**：日本語を含む生成・構造化抽出。230Mは350Mから蒸留、小型系列と最新上位を比較。|230M: HF作成6/24・更新8/19、308 likes/77,146 downloads。350M: 作成3/31・更新8/5、435/52,403。24B: 作成10/12・更新10/13、137/2,565。HF値はstarsの代替で品質指標ではない。|LFM Open License v1.0、年商1,000万USDを基準とする商用条件あり（license節5）。Transformers/GGUF/ONNX等。230M BF16重み概算0.46GB、350M約0.70GB、24B約48GB（重みのみの算術、実RAMではない）。24Bは常時reasoning、長contextの追加メモリ別。|**B**。日本語の分類/抽出に限定したschema遵守・誤抽出評価が先。日本語tagだけで精度を保証しない。230Mを350Mの一律上位としない。新24Bをactive約2Bだけで小型採用しない。SKUや検索比較は再開しない。|
|**Chrome DevTools MCP 1.10.1**：1.10.0のchunked trace処理/CSS取得、1.10.1でbundle修正。|最新1.10.1は9/23、53,472 stars、Google系公式repoで継続開発。初期候補の1.10.0からpatch版まで確認。|Apache-2.0。Node＋Chrome/Puppeteer、CPU、実RAM未測定。大容量trace診断向け。|**C今回**。crawl coverageや前処理停滞を解消する実対象がなく、通常診断保守を研究機能にしない。|

Crawlee最新v4.0.0-rc.1（10/6）、Scrapy2.19（9/10）、warcio.js2.4.12（9/4）、Docling2.138（10/12）は昨日と同じ。候補数を埋めるため再検証しない。WebArchive不要の新サイト発見を実測で示す新しい候補は今回の範囲では得ていない。NLPにこの検索用途制限を適用していない。

### 一次資料

- RapidOCR [3.10.0](https://github.com/RapidAI/RapidOCR/releases/tag/v3.10.0)、[固定YAML](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/rapidocr/default_models.yaml)、[resolver](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/rapidocr/utils/model_resolver.py)、[付属テスト](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/tests/test_model_resolver.py)、[モデルlicense notice](https://github.com/RapidAI/RapidOCR/blob/v3.10.0/python/MODEL_LICENSES.md)。noticeのsmall det/rec hashはregistryと一致。ただし実重みをDLしてhash照合した意味ではない。
- Playwright MCP [0.0.82](https://github.com/microsoft/playwright-mcp/releases/tag/v0.0.82)、[0.0.83](https://github.com/microsoft/playwright-mcp/releases/tag/v0.0.83)、[固定package](https://github.com/microsoft/playwright-mcp/blob/v0.0.83/package.json)、[対応するfind実装](https://github.com/microsoft/playwright/blob/e8149b8257d32dcf8f72573ecc43e72439da7080/packages/playwright-core/src/tools/backend/find.ts)。
- LiquidAI [230M固定カード](https://huggingface.co/LiquidAI/LFM2.5-230M/blob/40cb2ad3b3044d5a41eee083a6103c8b523afa45/README.md)、[350M固定カード](https://huggingface.co/LiquidAI/LFM2.5-350M/blob/9e6c6ccf47cd318696e137d381a7ded8fe4df09f/README.md)、[24B固定カード](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B/blob/1769516ac588c4c558089c94f27d96b0dc67b183/README.md)、[license本文](https://huggingface.co/LiquidAI/LFM2.5-350M/blob/9e6c6ccf47cd318696e137d381a7ded8fe4df09f/LICENSE)。公式性能値は著者値、今回は再測定なし。
- DevTools MCP [1.10.1](https://github.com/ChromeDevTools/chrome-devtools-mcp/releases/tag/chrome-devtools-mcp-v1.10.1)。

## 想定との差・問題

- 「多言語なら最小tinyでも日本語」は成立しなかった。det/rec両方を確認する必要がある。言語対応集合と日本語精度も別。
- RapidOCRモデルlicenseが単に未確認なのではなく、既定3モデルについては新noticeに個別hash付き根拠がある。一般のモデル全体への外挿はしない。
- 3.9.0当時の「PP-OCRv6はONNX/OpenVINOだけ」を現行3.10へ流用しない。後続3.9.1/3.9.2にはPyTorch/Paddle/MNN/TensorRT関連の追加がある。今回は個々のエンジンを動作検証していない。
- `browser_find`の出力量減少だけで前処理が速くなるという初期仮説を固定版ソースで棄却。Nodeのトップレベル要件と依存要件の差も残した。
- 24B-A2Bカードの見出しはactive 2Bと丸めるが、仕様欄は2.45B。GPU著者ベンチマークはB300の条件であり、無料Kaggleで同じ性能が出る証拠ではない。
- 隔離probeのexit 1以外に資料取得/静的検算エラーなし。自動承認reviewによる拒否ではなく環境のuid map設定失敗。認証・権限変更、別backend、隔離省略、Kaggleへの移動なし。Kaggle残量や非公開設定の確認もしておらず、利用可能と主張しない。

## 導入判断・次

Aは「今試す価値」でありPoC完了条件ではない。RapidOCRは独立した公開/合成日本語画像でsmallを評価する価値があるが、現環境で安全に動作する隔離実行器が未確保。今回は設定監査までで終了。必要なのは隔離が実際に動く許可済み実行環境であり、ユーザーへモデル利用の再承認を求めているわけではない。

WebMCPは対応する実対象と出力契約を確認できた時にUI経路との到達データ・操作回数・総時間を比較する。任意のfixtureを作って一般サイトcoverage改善とは呼ばない。LFM小型群は日本語の独立正解集合と商用適用条件を定めてから。上位モデルの新規性だけで大きなDL/GPUを開始しない。

本体機能・既存実験・CIを変更していない。完成した実用機能がないためdraft PRなし、merge/deployなし。研究記録だけをpushする。

## 検証・成果物

Python3.12.14、[audit.json](../../benchmarks/daily-tech-20261014/audit.json)で36 source hash/bytes、24言語設定、license hash2件、snapshot生成順を確認。監査0.088335秒、peak RSS13,568KiBは自作の静的監査プロセス値で、OCR/ブラウザ/モデル性能値ではない。推論0、ブラウザ実行0、GPU0。

自作Python AST、JSON読込、ローカルリンク、whitespaceを確認する。本体実験基盤を変更していないのでlab全体テスト・通常CI保守は実施しない。原文txtはbyte保持のため専用.gitattributesを用い、改変しない。remote SHAとcheck状態はpush後の最終応答で示す。
