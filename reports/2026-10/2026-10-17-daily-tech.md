# 2026-10-17 JST 朝の実用技術調査

## 結論

**YOLO26nの公式ONNX配布を確認し、隔離CPU PoCを試行したが、ファイル権限エラーが修復1回後も残り停止。推論0件、性能改善の確認0件、実用機能PRなし。** 環境切断通知後も同じ環境は応答していた。auto-review拒否ではなくコンテナ内の権限問題であり、別環境への切替や追加修復はしていない。

判定は **A（今試す価値がある候補、今回の試行は未完了）**。採用は保留。実画像2枚で仮に動いても精度や検索coverage改善の証明にはならない。今回の有用な追加事実は、公式配布ONNXと依存が小さく、PyTorch・未知のソースコード・pickle重みを実行せずに検証を設計できること。速度・RAM適合そのものは未確認。

## 概要・最新状態

開始2026-10-16 22時台UTC＝10/17 07時台JST。main `7460493cc3f7242748c95f711e71a59537ef34ea` から独立branch `research/2026-10-17-daily-tech`。

remote heads、open PR6件、open issue #24とそのcomments（空）、研究PR19/23のcomments/reviewsを確認。PR19の停止は維持。SKU各branchのremote SHAは提供された継続状態と一致し、SKU実行・取得・枝変更なし。夕方canonical `1fd340259b665e953f174ac3e3706ff6f42cf582`、朝 `1adb4a0bdded0d37901146d739b398002bf8d047` の報告を読み、LightOnパーサー再試験、Squeez、BitNetの再開、夕方radar/state変更を行っていない。既報Crawl4AI/Docling/GLiNER/RapidOCR/Scraplingを新規扱いしない。

AGENTS、docs/cloud-lab.md、bot-blocking skillを確認。今回bot診断やjse.lab grounding探索は実施しない。Kaggle不要とし、認証・残quota確認やジョブ作成もなし。独立した一次資料照合をgpt-6-luna指定agentに委譲（実provider modelの独立検証なし）。agentが公式ONNXを確認できなかった点は親が公式assets APIとファイルdigestで補完した。

## トリガー・一次資料

トリガーは10/16夕方のA・未実行候補への新しい測定。任意の穴埋めやreranking比較ではない。軽量画像検出はOCR/画像前処理の補助候補だが、日本語文書layoutやSKU用途に適合するとは確認していない。今回は広範囲radarの再実行をせず、この一件に集中した。

- [公式YOLO26説明](https://docs.ultralytics.com/models/yolo26/)はDFL-free、小型head、end-to-end経路を説明。NMS-freeは選択経路であり、通常の全利用形態がNMS不要とはしない。配布ONNXの出力shapeとmetadataを確認する計画だったが推論前に停止したため、このファイルの経路は未確認。
- [公式assets release v8.4.0](https://github.com/ultralytics/assets/releases/tag/v8.4.0) ID `276461319` の公開は2026-01-13、更新は09-17。model世代の初回公開日と同一視しない。`yolo26n.onnx` asset ID `398736502` は04-17作成・更新、9,941,957bytes、API download count 15,722。`yolo11n.onnx` ID `340060855` は01-13作成・更新、10,930,182bytes、download count 7,545。取得bytes/SHA256がAPI size/digestと2/2一致。後日追加されたファイルとrelease作成日を区別する。
- [コードrepo](https://github.com/ultralytics/ultralytics)は62,571 stars / 11,867 forks、10/16 18:56 UTC push、最新release `v8.4.178` は10/16 00:09 UTC。これは系列全体の人気・開発信号でありYOLO26採用数ではない。[専用repo](https://github.com/ultralytics/yolo26)は132 stars /16 forks、09/16 push。独立した品質再現やcommunity導入件数は未確認。
- [後継YOLO27](https://docs.ultralytics.com/models/yolo27/)は公式にR&D中・コード/weights未公開、確定launch日なし。新しい名称だけを実行可能候補にしない。
- code/modelは公式表示AGPL-3.0、Enterprise選択肢あり。[商用方針](https://www.ultralytics.com/license)との用途適合は別判断。AGPLは商用を一律禁止する意味ではないが、非公開製品への組込み条件を満たすか未検証であり統合しない。公開デモ画像の商用再配布権は個別に確認していないため画像自体をcommitしない。

[取得manifest](../../benchmarks/daily-tech-20261017/manifest.json)にURL・時刻・hash。公式code参照commitは `f413c7b2a603469bfcd17cf01671baba3b030eb4`。公開bus.jpg/zidane.jpgもこのcommitに固定。モデルはrelease asset digestで固定した。公式38.9msや最大43%改善はvendor値で、今回の観測値ではない。

## 方法・導入・資源

[コード](../../experiments/daily-tech-20261017/)と[数値・エラー](../../benchmarks/daily-tech-20261017/)を保存。Python3.12公式image digest、ONNX Runtime1.23.2、NumPy2.3.4、Pillow12.0.0。通常の`pip install ultralytics`/PyTorch経路は使わず、reputable registryのbinary wheelのみを取得し、依存全wheelのversion/bytes/hashも固定。未知repo codeやremote custom opsは実行しない。

同じ公開画像2枚を640×640、Pillow bilinear letterbox、FP32 RGB/255へ変換。CPUExecutionProvider・thread1、各画像warmup5＋測定30回、shape確認・finite確認・confidence0.25、必要時だけclass-aware NMS IoU0.7を計画。モデル別独立process、load/preprocess/inference/postprocessとRSSを分ける。正解annotationがないので検出件数を精度にしない。単純な旧世代比較は今回の夕方候補のCPU前処理適合性を測るためだけで、定期的な比較ではない。

ホストはXeon Platinum8573C。コンテナは1CPU・RAM/swap1GiB、timeout180秒/モデル、UID10001、networkなし、read-only、cap-drop ALL、no-new-privileges、pids32、tmpfs16MiB、host mountなし。認証・private filesをコピーしていない。候補CPU20分/8GiB上限より厳しい実行設定。実RAM値と最低必要RAMは未知。

公開資料＋画像＋モデル22,466,611bytes、依存wheel48,532,324bytes、base image amd64 compressed layers43,259,288bytes、合計114,258,223bytes。2GB枠内の取得物payloadであり、Git fetch・HTTP再送・manifest等を含む全network wire bytesの厳密実測ではない。候補モデル2本は20,872,139bytes。GPU、Kaggle、課金、学習なし。

## 結果・比較・想定との差

|項目|結果|
|---|---|
|公式モデルsize/digest照合|2/2一致|
|依存取得・image build|成功。外部ソースビルドなし|
|初回model process|exit2、`/app/probe.py` Permission denied、推論前停止|
|修復|1回。COPYに`--chmod=644`を追加|
|修復後process|exit2、同じエラー。推論0件|
|読み取り診断|UID10001、`/app`が`drw-r--r--`。directoryにsearch/execute権限がない|
|YOLO11n baseline|先行失敗で未実行|
|速度・RSS・精度・期待改善率|全て未測定。数値ゼロで補完しない|

修復時のCOPY chmodがdirectoryにも適用され、search permissionがなくなったことを確認。これはモデル非対応・ライブラリ非対応・実メモリ不足ではない。修復上限1回を守り、directory権限を再変更して推論を再試行することはしていない。保存Dockerfileは失敗時の再現資料であり、動作確認済みの導入レシピではない。

想定deltaは旧nano検出器に比べたCPU前処理時間低下。今回確認できた差は配布ONNXのbytesが約9.0%小さいことだけで、速度差やRAM差を意味しない。正解付き実用途画像がないため、仮に推論成功しても採用品質基準を満たしたとは言えない。

## 採用・次

実用機能なし、draft PRなし。新規通知が必要な改善実績なし。成果物を独立研究branchにpushしexact remote SHAとそのSHAのCIを確認する。merge/deploy/email/cron変更、無関係CI修理なし。

このrunは**修復上限到達で終了**。同じ朝runや別環境で再開しない。将来あらためてユーザーが継続を許可した場合に限り、directory権限の修正案を明示したうえで、同じhashの公開ONNXと正解付き用途画像による測定を再計画する。商用用途のlicense適合も未解決。新しい検索網羅性・archive-free discoveryの改善結果は今回なし。
