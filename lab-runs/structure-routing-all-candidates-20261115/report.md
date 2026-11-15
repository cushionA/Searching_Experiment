# 全候補の追加実行とMiniLM軽量化

## 現時点の判断

MiniLM-L6-dmrはPyTorchを運用環境から外してCPUで実行できた。FP32 ONNX約90.4 MB、tokenizer約0.7 MB、専用runtime約168.6 MB。単独プロセスの観測peak RSSは約364 MB、代表HTML1件の汎用predictは約3.41秒。既存Python・ブラウザ・OSの容量はこのruntime純増量に含まない。

ただしモデルの追加価値は未証明である。生の候補集合での固定対象到達はMiniLM 3/6、C1 5/6。navigation等を除外したMiniLMFilteredは6/6になったが、2サイトとも最大の繰返し群を選ぶ規則だけで同じselectorになった。6/6をモデルの効果として扱わない。

Skillの運用には、構造規則とページ送りの標準属性・文字列規則を先に使い、候補が複数残る場合だけ代表DOMでMiniLMを呼ぶ任意経路が妥当。以後は保存selectorを使う。JevForgeとQwen-HTMLを標準Skillの必須依存へ入れる判断はしない。ユーザーの重さへの懸念を受け、QwenはKaggleで初期生成だけを行う参考実験として分離した。

## 比較条件と固定対象

既存80サイトからBooks(01)・Oxylabs(08)を選び、各3件の代表listing DOMをcommon-inputsへ固定した。各候補は最大3代表、MiniLM/JevForgeは各seed1だけを実際に入力した。モデル未入力のlisting URLは代表ページで観測したnext/Forwardリンクから決めた。代表DOMは過去runの保存物を転用し、新規取得0と記録。元の取得費用はstructure-routing-b2-20261115へ残している。

候補の出力を見る前に、評価側が別listingから最初の3詳細URLを共通手順で固定し、6件すべてのHTTP200・本文を確認した。評価用の2つのリンク選定パターンはevaluation-privateにだけ置き、候補へ渡していない。goldのURL・タイトル・本文・選定selectorは候補が読まない運用で隔離した。同一checkout内の暗号学的な隔離ではない。

独立選定費用は8文書、HTTP213、acquire42.523秒、全体経過55.828秒。この取得を候補の成功に加算しない。固定6件のURL直接取得が可能であることは確認できたが、独立したB0再試行はしていない。

候補のwarmは保存JSONを会話履歴のない別Nodeプロセスへ読み込み、listingを新規取得、規則を適用、最大3リンク先を新規取得した。Chrome157/Playwright1.63、同じ回線、ja-JP、Tokyo、1365×900、fresh context、Cookie初期化、service worker無効、同一のacquire/document guard、練習サイト間隔1秒以上を共有。同時サイト2・サイト内同時実行1。HTTP取得層の違いをランキングの差に混ぜていない。

ただし依存・重みdownloadと同時期の試行を含むため、CPU/回線負荷は一定ではない。時間差を方式の性能差として断定しない。比較の中心は固定対象到達・文書数・モデル使用量である。

## 商品詳細への到達

|方式|Books|Oxylabs|合計|新規文書|総HTTP|warmモデル呼出|
|---|---:|---:|---:|---:|---:|---:|
|B1|0/3|1/3|1/6|8|244|0|
|C1|3/3|2/3|5/6|8|212|0|
|MiniLM|3/3|0/3|3/6|5|117|0|
|JevForge|0/3|0/3|0/6|5|150|0|
|MiniLMFiltered|3/3|3/3|6/6|8|214|0|

独立HTML検証の正本はscore/score.jsonとper-site.jsonl。listingを保存しただけの件数を商品詳細成功に含めない。MiniLM/JevForgeのOxylabsはactive paginationを選択し、新しい詳細URLが得られなかった。JevForgeのBooksは広いli aを選び、カテゴリ等を取得した。有効なHTML保存と期待する詳細型への成功は区別する。

MiniLMFilteredは失敗原因を見た後の探索的な汎用修復版で、繰返し2件以上・navigation領域外の候補群に限定した。Booksは候補群1でモデル不要、Oxylabsは2群で既存mean scoreを参照した。追加モデル推論0。両方ともmember数最大の規則と同じselectorで、規則だけの別ネットワーク試行は実施していない。サイト専用selector・商品IDを人が追加した数は0。

この2サイトの結果を全80サイトの成績へ加算・外挿しない。新しいモデル比較では2サイトを実施、残り78は未実施。以前の80サイトB1/C1、サイトマップ10サイト、Stage Cの100件試験は各過去runに保持した。今回のモデル付きwarmは3対象/サイトまでで、モデルを使った100件連続取得は未実施。

## MiniLMの実行形と重さ

元revision e9b9dd566fa1a146eb00b8f3230b87870144084a、元weight90,887,145 bytes、SHA256 49230f8ec5764fcd0d1c3d83ea87387d602e3c0343efbbb2d96452702ed651dd。変換後FP32 ONNX90,375,136 bytes、tokenizer711,649 bytes。元pytorch_model.binを運用時に置く必要はない。比較用rankコマンドと、汎用predictコマンドを分離した。

|項目|FP32 ONNX|dynamic int8|
|---|---:|---:|
|モデルbytes|90,375,136|58,616,747|
|Books推論秒|2.386|1.774|
|Oxylabs推論秒|1.136|0.699|
|候補順位と元モデルの一致|両ケース一致|両ケース不一致|
|最大cosine差|5.96e-7 / 2.12e-7|0.141 / 0.112|
|Oxylabsの選択|li.active|li.disabled.previous|

int8は採用候補にしない。FP32でもPyTorch比の一貫した高速化は確認していない。利点は運用依存からPyTorch/sentence-transformersを外せたこと。専用venvのfind_spec(torch)、sentence_transformers、onnxはNone、providerはCPUExecutionProvider。量子化・変換そのものには開発環境のPyTorch等を使った。

汎用predictは、任意のHTMLファイルとtaskから候補・群・selector・順位・モデルhash・token数・時間を出せる。gold、ベンチreference、共通manifest、元weightは読まない。generic-predict-validation.jsonで94候補/6群、3 ORT calls、11,073 input tokens、load0.259秒、推論2.56秒、process3.41秒を記録した。

全候補のbboxは欠測のゼロplaceholderで、学習時の完全なWebLINX入力ではない。日本語サイト、多店舗、UI状態の一般化は未検証。ONNX実測はWindows CPUのみでLinux/Cloud Runでは未検証。固定revisionのfine-tuned model cardのlicenseは未宣言として記録している。重みはGit/ZIPへ含めず、変換コード・hash・配布元metadataを保存する。

## autopager

Git checkout0.4.0は学習済みCRFを含まず、前回の未学習エラーを保持した。今回はPyPI0.3.1の実モデルを使用。CRF本体1,093,573 bytes、依存target259,363,077 bytes。package import約4.3〜5.2秒、推論約0.03〜0.05秒/ページ。CRFはLLMではなく、入力token課金はない。推論回数は別に計上した。

代表seed1で各1 CRF call、本文未入力だった別listing DOMで各1 CRF callを行い、両サイトのpage3相当NEXTを検出した。後者の入力DOMは評価側から転用した保存物で、新規取得0。予測URLは別プロセスで新規取得し、期待URL到達と有効listing HTML保存を2/2件確認した。追加2文書・HTTP68。これは商品詳細6件とは別課題である。

代表から作ったrel/text等の保存規則も、別Nodeプロセスで同じNEXT URLを返した。追加CRF0・取得0としてpagination-rule-replay.jsonに保存した。既に取得した同一NEXTのHTMLをもう一度取得した成功として数えていない。

初回importを各ページで繰り返す実装は避け、規則が通る間は規則を使い、必要時だけ同一worker内のCRFを使う構成が候補。複数ページャー、末尾、無限scroll、JS操作、shadow/iframeの一般解としては認定していない。seed3で規則が0件になった例は、末尾であるかの独立判定をしておらず、単純に失敗とも成功ともしていない。

## 他の候補

|候補|実際に動かした範囲|停止点・負荷|判断|
|---|---|---|---|
|pinchtab-webgraph|offline paths/howto、flow validate、隔離Chromeで保存手動flow実行・DOM/HTML保存|自動crawlはlocal evaluate_disabled403で0 states/0 edges。server約28.5 MiB＋instance31.8 MiB＋Chrome151.7 MiB、binary29.2 MB、profile86.2 MB|フロー実行部の参考。ライブ構造自動発見は未実証|
|smart-crawler|portable Temurin8/Maven3.9.16導入、実mvn package、main起動試行|Maven Central DNS失敗でcompile前停止。JDK199.8 MB＋Maven10.9 MB、Neo4jも前提|導入通信層で止まり、アルゴリズム未判定。標準Skill採用なし|
|JevForge-0.8B|代表候補6/7群をnative PredictorでCPU選択、保存selectorのwarm実行|weight3.416 GB、専用venv861.5 MB、観測working set4.23 GB、load約40〜45秒、推論約16〜18秒、到達0/6|今回の負荷・効果では標準採用しない|
|WebNavigator|隔離依存導入、help成功、repoの有効WebArena設定例でrunner起動|cloneにないtask JSON群の列挙で停止。依存285.4 MB＋共有Torch/ブラウザ、WebArena・事前graph/embedding・LLM/Jina endpoint等を前提|汎用の小さなSkill部品には不向き。実API/経路評価は未実施|

PinchTabのHTML保存はlocalhostに保存seedを配信したfixture上の成功で、80サイトの追加実サイト数には加えない。手作成selector1件、fixture adapter修正1件。自動graphから得た経路の成功ではない。evaluate guardは下げていない。静的フローでモデル呼出0を確認したが、自然文からのAI flow生成は未実施で使用量unknown。

WebNavigatorのeval.pyは例外を内部で捕捉してexit0にするため、exit codeだけで成功にしていない。認証情報を渡さない環境・metadata無効・dotenv無効・外向き通信遮断で実行した。初回のimport時にNLTKのpunkt/punkt_tabがAppDataへ取得された副作用はログに保持し、削除していない。以後は遮断した。WebNavigatorの285.4 MBには共有Torch等とこのAppData dataを含まない。

元candidate auditの取得commit/licenseと、新しいmodel revision/source/dependencyはprior-candidate-audit.json、jevforge/runtime-summary.json、others/results.json、qwen-html/model-manifest.jsonを参照。導入通信失敗、ローカルAPIのguard、候補選択失敗、未実施を混同しない。

## Qwen-HTML / Co-Scraper

weight revision b1b061d3f708807534d3796414e0c882ac46e883、2 shard合計8,045,981,992 bytes、LFS SHA照合済み。モデルcardはApache-2.0とbase Qwen3-4B条件を記録。Co-Scraper commit a83a1b9740b8883c25365316728c890d4ff68c0e。抽出対象は商品hrefだけで、SKU・価格・属性の抽出精度は採点しない。

ローカルRTX2070Super 8 GiBでは、NF4 4bitでも代表raw HTML約10,388 tokensでOOM。保存した失敗はload11.125秒、失敗まで16.721秒、ruleなし。raw試行は既知3回だが構造化記録は1回だけで、全試行の正確なtoken/時間はunknown。Coの初回ID推論は5,439 tokensに到達後中断し、出力と時間はunknown。専用venv11.1 MBはsystem-site-packagesを使うため全依存量ではない。保存runのprocess peak RSSは約15 GBだった。

ユーザーがKaggleでの検証を明示した後、kaggle-ops Skillでdoctor・quotaを確認した。初期GPU残108,000秒、使用/予約0、reset2026-11-21をAPIから記録。private/GPU/Internet有効、TPU無効、timeout1800秒。代表3 HTMLだけを圧縮して送信し、認証・gold・heldout本文を送っていない。重みはKaggleでpin revisionを取得し/tmpに置き、出力に含めない。

v1 ref superbigzabuton/qwen-html-routing-probe-20261115、version1。Kaggleがtitleから別slugを返したため、コード署名・private/GPU/Internetを照合して同じjobのrefを訂正した。再送して別ジョブを作っていない。T4 15.6 GB、torch2.11+cu130、transformers5.3、weight download32.042秒/hash22.592秒/load12.355秒。

v1のraw armは10,369 input/0 output、3.206秒でOOM。native Co armは5,931 input/512 output、105.644秒でJSON解析失敗、生成parser未到達。実GPU使用275.55秒。Kaggle job完了とモデル成功は異なり、この実行は両arm失敗。live log APIはHTTPErrorだったが、完了後のpersisted logとoutputを回収・SHA確認した。

v2は同じseed・revisionで、入力を削らず512-tokenずつDynamicCacheへ入れる処理、native generation config、ID出力上限2048/code1024へ変更した修復版。小さなQwen3 fixtureではfull対chunkのlogit差2.09e-7、生成5 token一致を確認した。これは実weightでの完全同等性の証明ではない。中間model出力・pruned DOM・生成code・partial結果を保存し、codeは検査を通った場合だけ実行する。

**v2結果は追試完了後にここへ追記する。** 初回pilotはBooksだけで、Oxylabsは未実施のまま残している。全6対象のうち、試していない3件を分母から落とさない。複数変更を含む追試なので改善をchunk処理だけの効果として断定しない。

## モデル費用と増分

warm取得中の生成LLM・embedding・ローカルモデル呼出は全方式0。初期解析のモデル使用量は各個別JSONへ保存した。MiniLM pipelineは代表2件で5 forward/16,620 input tokens、JevForgeはdecision API2回・候補path6+7・記録入力548+695 tokens。JevForgeの値はadapterのtoken計測であり、内部全forwardの厳密な合計とは区別する。

ONNX比較・変換・量子化・汎用predict検証で追加の参照推論を行っており、運用pipeline費用へ二重計上しない一方、全実験の推論0ともしていない。export traceの内部forward数、開発再試行の厳密な総数、設計・調査・会話・レポートのnativeモデル使用量はunknown。C1FilteredのJSON参照/DOM処理時間も未計測で、0秒とはしていない。

KaggleのQwen推論は外部の計算環境でのモデル使用としてtokens/時間/回数を明記し、0扱いしない。従量課金LLM/外部embedding APIは使っていない。新しい有料cloud環境は作っていない。サイト文書・HTTP計測と、重みdownload/依存download/Kaggle制御通信/localhost fixture通信は分ける。後者全体の正確なHTTP総数はunknown。

## 再実行と統合案

実測結果・raw/DOM・saved rule・独立gold・実行ログを新runへ保存した。代表入力とgold hashは変更しない。全candidate sourceを既存Skillへ入れるのではなく、次の構成を候補とする。

1. 既存の取得・サイトマップ・構造規則で候補を作る。
2. navigation除外と繰返し群で十分なら、そのまま規則を保存する。
3. 複数候補が残る場合だけ、別プロセスのMiniLM FP32 ONNXへ代表DOMを渡す。
4. selectorと検証済み取得条件を保存し、後続URLはモデルなしで処理する。
5. Qwen/CoはKaggleで生成した規則・コードだけを持ち帰る参考経路とし、重みを通常取得runtimeへ置かない。

初回変換は開発環境で行う。運用時はFP32 ONNX・tokenizer・model-metadataとminilm-onnx.py/minilm-ranking.py、CPU専用依存だけを配置する。汎用CLI例（このworkspaceで検証済み）:

```powershell
.deps/structure-routing-minilm-onnx/Scripts/python.exe -B experiments/structure-routing/minilm-onnx.py predict --html representative.html --task "Select links to product detail pages" --model-file .deps/structure-routing-models/MiniLM-L6-dmr-onnx/model-fp32.onnx --tokenizer .deps/structure-routing-models/MiniLM-L6-dmr-onnx/tokenizer.json --output rule.json
```

取得済みtupleを再取得しない。通常のreplayは既存result.jsonがあれば停止する。採点は保存物だけで再実行できる。

```powershell
python -B experiments/structure-routing/all-candidates-score.py --run lab-runs/structure-routing-all-candidates-20261115
node --test experiments/structure-routing/all-candidates.test.mjs
node experiments/structure-routing/pagination-follow.mjs lab-runs/structure-routing-all-candidates-20261115 --rules-only
```

Kaggleの再開はjob001/job002のref/versionを固定し、kaggle-ops helperのwaitで同じjobを追う。待機timeoutを再submitへ置き換えない。jobのcontinuationとKaggle内部continuationの両方、出力SHAを確認してから結果を使う。
