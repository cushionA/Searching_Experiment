# 2026-10-26 JST 朝 技術調査・追加検証

## 結論

**B：Indra OCR 1.1系とppu-indra-ocr 0.2.1の追加検証。A0 / B1系列 / C0。候補実試行0件、推論0件、自作のオフライン静的監査1回（5構成）。** 10/20既報のport 0.1.2 / Python 0.3.1と異なり、CJK等の専用認識器・言語モデルが既定構成に加わった。旧報の「約18MB」を現在の標準構成へ引き継げない。日本語前処理の候補は改善したが、当方の日本語品質・速度は未測定で採用は保留する。

## 概要・進捗

着手時のremote mainは `4d2984a454fc766137784fd6a51579e10b6fafd6`。独立branch `research/2026-10-26-morning` をmainから作成。前朝 `c0dd13d`、前夕 `9a051ad`、open PR一覧を確認した。mainに朝以後の新成果物は確認できず、朝10/10〜24の報告、10/25朝、夕10/12〜25を対象に名称重複を点検。LightOnOCR-3は10/16既報なので除外。Indra portは10/20既報だが、その後の現在版・既定モデル変更と配布容量の検証に限定した。全歴史の意味的重複がないという保証ではない。

夕方state 9,384件、SKU、PR19、Moli/Lightpanda、CI運用・自動化は変更しない。Seek/CADE-QSや前夕候補も再紹介しない。これは一次資料の静的技術調査で、`jse.lab`のgrounding探索や外部サイト巡回実験ではない。AGENTS.mdとbot-blocking skillを読んだが、本件にサイト別block検証は適用しない。

## 方法

公式GitHub・HF・PyPI/npmのmetadata、固定commitソース、changelog、licenseを読む。29資料、706,107 bytesをURL・取得日時・SHA256付きで保存。資料取得24.1825秒、オフライン監査0.005335秒（全調査の所要時間ではない）。Python 3.12.14、追加依存なし。候補codeはテキストとして解析し実行しない。候補weight DL 0、候補CPU 0秒、料金0円、修復0回。

再現手順は[README](../../experiments/indra-source-audit/README.md)、[script](../../experiments/indra-source-audit/audit.py)、入力は[source-manifest](../../benchmarks/daily-tech-20261026/source-manifest.json)、出力は[static-audit.json](../../benchmarks/daily-tech-20261026/static-audit.json)。5構成の算術確認を5件のOCR PoCと数えない。

## 結果

### 正確な系列・版と開発状況

- 公式コード [datalab-to/indra](https://github.com/datalab-to/indra)、PyPI `indra-ocr==1.1.2`。releaseは2026-10-20 01:40:36 UTC、wheel upload 01:41:17 UTC。repo作成10/13、現在1,086 stars / 116 forks。最新mainは `e20355319b5aa5705a876a8d2b40aca606d7df5f`（10/22、README更新）。starsは品質の証明ではない。
- モデルIDは `datalab-to/indra-ocr`。Python 1.1.2が指定するモデルtagは **v1.1.0**。HF refsとport固定catalogueが一致するtargetは `a1283c4b42a273c09480ad50e71461daff50fcc7`。公式authorのIndra検索では別の後継IDは見つからなかった。HF作成10/10、API lastModified 10/19。コードとモデルのversion番号・更新日は別。
- community port [ppu-indra-ocr v0.2.1](https://github.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/releases/tag/v0.2.1)は10/20 04:10:51 UTC、npm latestも0.2.1。tagを解決したcommitは `f3190b32db8eaa10ac8c1d6a2da180d48b266938`、1 star、repo最終push10/20。新既定preset **v1.1**と旧 **v3.1**を持つ。番号の大小だけで新旧判定しない。Datalab公式portではない。

### 新しい有用な差と静的検証

[上流changelog](https://github.com/datalab-to/indra/blob/e20355319b5aa5705a876a8d2b40aca606d7df5f/CHANGELOG.md)は1.1.0でCJK/RTL/その他script認識器と、不確かな行を再解読するn-gram LMを追加。1.1.2ではPDF文字層の文字種と画像を比較して、legacy fontの誤った文字層をOCRへ戻す。単純な「text layerがあればOCR不要」より誤抽出を抑える設計で、検索前処理の誤った本文生成を減らす可能性がある。ただしページ発見・取得網羅性は改善しない。

|配布構成|ファイル数|metadata上のbytes|
|---|---:|---:|
|Python既定、mathなし|12|76,089,598|
|Python mathあり|17|95,963,757|
|port初期base|6|9,180,548|
|base + CJK認識器|7|19,356,566|
|base + CJK認識器 + CJK LM|8|38,311,726|

Python loaderは既定で各script/LMを取得する。portの[catalogue](https://github.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/blob/f3190b32db8eaa10ac8c1d6a2da180d48b266938/src/model-catalogue.ts)とchangelogは必要時ロードを指定。portの小さい初期baseを日本語処理全体の容量と表現できない。混在scriptやLatin fallbackならさらに増える。上表は実通信量・依存込み容量・メモリ実測ではない。

HFのsafetensors表示は1,250,566 parametersだが、[config](https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/config.json)はLatin recognizerだけの表示用重みで、runtimeは使用しないと明記。既定loaderの取得集合にも含まれないことを静的確認。「1M」はpipeline全体のparameter数ではない。全既定ネットワーク7.3Mは作者説明であり、今回ONNXを読んで計数してはいない。

## 比較

10/20のport 0.1.2は旧モデルとSROIE評価が対象。今回のv1.1は日本語scan/縦書きも対象に加えるため、その旧速度・CERを現在版に転用しない。著者は現在版の日本語kanaのOmniParseBenchを57.9（19テスト）と報告するが、少数テストで日本語文書一般の品質を保証しない。clean print向けで、複雑な表、劣化scan、手書き、複雑な縦書き読み順に制限がある。既報LightOnOCR-3の大きなVLM系と比較して配布量は小さいが、同一入力での品質比較はしていない。

## 問題・制限

1. **revision確認の落とし穴**：保存した `/api/models/.../revision/v1.1.0` は `sha=bd02167...` を返した一方、refsのtag targetとport固定値は `a1283c...`。今回は後者の明示SHAでtree/license/metaを取得し監査した。原因未確定。metadataエンドポイントのshaだけでtag固定済みと判断しない。失敗応答ではなく不一致の観測である。
2. codeはApache-2.0、weightは[modified OpenRAIL-M](https://huggingface.co/datalab-to/indra-ocr/blob/a1283c4b42a273c09480ad50e71461daff50fcc7/LICENSE)。売上/資金調達$20M超の条項にはpersonal/research例外があるが、競合製品・サービス条項には同じ例外がない。コードlicenseだけで商用無条件可としない。自身の適合は未確認。
3. 外部候補code実行のcredential/personal-file隔離承認は**既存pendingのまま、今回not attempted**。Indra Python/JSのinstall・推論は未開始。新たなdenial・不確実な書込みはない。停止の再試行、Kaggle迂回、Moliの追加依存導入もしていない。

## 採用判断

**B（条件付き）**。既報からの意味ある更新は、日本語を含む新モデル構成、必要時ロード、壊れた文字層をOCRへ戻す経路である。新技術一般をAにするにはPoC成功必須ではないが、本件は既知のweight利用条件未解決と日本語品質の不確実性が判断理由になる。

許可・利用適合が整った場合の導入候補は `pip install indra-ocr==1.1.2`（Python>=3.10、NumPy/OpenCV-headless/ONNX Runtime/pypdfium2/HF Hub）、または `npm install ppu-indra-ocr@0.2.1 onnxruntime-node`（PDF用runtime等は別）。今回は実行しない。CPU対応でGPU不要という作者仕様は確認できるが、8GB内のpeak RSS保証はない。weightは2GB内でも依存込み総DLと並列workerのメモリは未測定。Kaggleを必要とするcompute不足は示されておらず、認証・quota照会やjob開始は不要。

## 次

既存の隔離判断とweight利用適合が解決した時だけ、許諾済みの独立日本語PDFを、正常文字層・画像scan・壊れた文字層に分けてauto/OCR/textを比較する。期待本文欠落率、CER、誤fallback率、wall timeとpeak RSSを記録し、欠落を増やさずOCR回数を減らせるかを見る。現時点で新しい許可質問は出さない。

本成果は調査と静的監査で、実用機能の実装・検索reranking追加ではないためdraft PRなし。merge/deployなし。push後のremote exact SHAと当該SHAのCI run IDは親への最終返信で報告する。CIはrepository検証であり、OCR性能の再現成功ではない。
