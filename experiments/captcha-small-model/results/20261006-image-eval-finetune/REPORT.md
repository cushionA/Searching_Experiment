# 画像候補の比較と選抜モデルのヘッド学習（2026-10-06）

**MobileCLIP2-S3の固定特徴に小さな分類ヘッドを学習し、内部testのボード完全一致が79/203問から135/203問（38.92%→66.50%）に増えた。** 分類は学習前後とも811/815枚（99.51%）。S3とS4だけを学習対象にし、画像エンコーダの重みは固定した。

## 未調整モデルの全件比較

同じ復元manifestの分類4,068枚・保存ボード1,000問を、元と同じ固定prompt・source別の許可クラス・target対その他の類似度margin > 0で採点した。

| モデル | 分類正解 / 4,068 | 分類率 | ボード完全一致 / 1,000 | ボード率 | GPU画像処理合計 |
|---|---:|---:|---:|---:|---:|
| MobileCLIP2-S2 | 4025 | 98.94% | 424 | 42.40% | 132.74秒 |
| MobileCLIP2-S3 | 4034 | 99.16% | 428 | 42.80% | 151.54秒 |
| PE-Core-S16-384 | 3798 | 93.36% | 332 | 33.20% | 163.07秒 |
| MobileCLIP2-B | 4010 | 98.57% | 419 | 41.90% | 86.78秒 |
| MobileCLIP2-S4 | 4034 | 99.16% | 421 | 42.10% | 192.38秒 |

処理時間はGPU上の1回の計測で、前処理・画像encode・類似度計算の合計。モデル取得・ロード・text埋め込み・encode warmupは含まない。環境と元protocolは保存した。

## 強い候補だけを学習

候補選定はvalidationの分類406枚・ボード97問だけで行った。正解数の多い順、同率なら固定した元checkpointのbytesが小さい順、最後に候補の固定順という規則を保存してから選んだ。

| モデル | validation分類 / 406 | validationボード / 97 |
|---|---:|---:|
| MobileCLIP2-S2 | 400 | 48 |
| MobileCLIP2-S3 | 403 | 50 |
| PE-Core-S16-384 | 379 | 28 |
| MobileCLIP2-B | 401 | 43 |
| MobileCLIP2-S4 | 403 | 51 |

分類winnerはS3（S4と同数、996,928,503 bytes対1,783,395,736 bytes）、ボードwinnerはS4。この2つの固定特徴だけに16出力のlinear headを学習した。EfficientFormerV2-S2 / V2-L / EfficientFormer-L3 / MobileOne-S4のNotebookは未提出で、精度は未測定。

ヘッド学習はCPU、seed 20261006、AdamW、lr 0.001、weight decay 0.0001、max 1,000 epochs、patience 10、各モデル180秒の上限。元の100 epochsの案は実学習開始前に`convergence_policy.json`で変更した。未知のラベルをlossから除き、分類画像ではsourceで注釈された8クラス、ボードtileでは問い合わせtargetだけにBCEを適用する。予測は分類がsourceの許可クラス内argmax、ボードがtargetのsigmoid確率 >= 0.5。正解の選択数は予測に使わない。

## 同じ内部testでの学習前後比較

学習を行わないrawモデルと、学習したheadを**同じ815分類画像・203ボード**で比較した。全件1,000問の率とは分母を分けている。

| モデル | raw分類 / 815 | head分類 / 815 | rawボード / 203 | headボード / 203 | ボード改善幅 |
|---|---:|---:|---:|---:|---:|
| MobileCLIP2-S3 | 811（99.51%） | 811（99.51%） | 79（38.92%） | 135（66.50%） | +27.59ポイント |
| MobileCLIP2-S4 | 811（99.51%） | 811（99.51%） | 80（39.41%） | 130（64.04%） | +24.63ポイント |

S3のボードは60問が改善、4問が悪化。S4は54問が改善、4問が悪化。分類は両モデルとも1枚改善・1枚悪化で正解総数は変わらなかった。

学習後のvalidationは両モデルとも分類403/406、ボード64/97。事前と同じbytesによる同率規則で**S3のheadを推奨**する。候補選定とheadの選択にはtestの正解数を使っていない。各head内のcheckpointはvalidation masked BCEで選び、両モデルともbest epochは1,000だった。epoch上限まで改善していたため、収束済みとは主張しない。testを使う追加のparameter調整は行っていない。

## 分割・出典・検証範囲

trainは分類2,847枚 / 700ボード、validationは406枚 / 97ボード、testは815枚 / 203ボード。元ボードの全tileを同じfoldに置き、同じraw bytesまたはdecoded RGBがある別の画像・ボードも推移的にまとめた。全IDが1つのfoldだけに属し、重複画像がfoldを跨がないことを照合した。

validationにbridge / taxi / tractorのボードはなく、testにbridgeはない。bridgeのtest精度は`null`（未評価）として記録している。この画像集合は過去のモデル選びにも使っており、今回は内部holdoutによる探索的評価である。未使用の最終test集合ではない。

- [orlov-ai/hcaptcha-dataset](https://github.com/orlov-ai/hcaptcha-dataset/tree/a1b180f9091719517d8890c33ab8b4d5df38ac10)：分類4,068枚。repositoryのコードlicenseはMIT、画像利用権は別途未確認。
- [ssivakorn/reCAPTCHA-study](https://github.com/ssivakorn/reCAPTCHA-study/tree/efb3595c33780abf9d326c729973e72d165366c2)：人の注釈による保存ボード1,000問、CC BY-NC 4.0。

復元sourceのcommit/tree/blobとimage SHAを照合し、S2の全件集計も過去と一致した。ただし旧画像単位manifestと生予測がないため、過去の各画像・順序の同一性は証明できない。公開の注釈競合2問も変更せず残した。これは保存された画像選択ボードの評価で、実サービスの認証通過、動的tile更新、スライダー・回転・ジグソーを測っていない。

元GPU Notebookは古いheadコードの未定義変数でerrorになった。画像エンコーダ5種類はすべてreturncode 0で、全raw予測・特徴cacheを保存済み。元のjob/ref/version/errorと失敗コード・ログを保存し、修正版headはS3/S4だけ別のCPUプロセスで実行して成功した。

独立auditで全raw 4,068+1,000行、全15,434参照画像のSHA、15,420 unique画像のfeature indexとtensor SHAを照合した。さらに保存headとfeatureから全foldのlogits・確率・分類・ボード選択・BCEを再計算し、testの生予測3,132行×2と元注釈を全件照合した。回収44ファイルのbytes/SHAと元実行コードsnapshotも照合し、audit errorは0だった。

## 保存物

- `metrics.json`：raw全件・validation・選抜headの全foldと同一testのpaired集計。
- `raw_predictions/`：5候補すべての画像単位・ボード単位の生予測gzip。
- `learned_heads/`：S3/S4の小さいlinear head重み、学習metadata、生test予測gzip。
- `feature_indices/`：5モデルのordered画像SHA・特徴tensor SHA・cache行の来歴。特徴tensor本体約189MBはGitへ入れていない。
- `dataset/`：元manifest・固定split・group割当・source/archive SHAとcoverage記録。
- `code/`：元GPU実行code、失敗した元head、修正head、auditと復元・分割codeのsnapshot。
- `job_status/`：元Notebook error、ref/version、回収SHA一覧、元失敗ログ、成功した修正headプロセス。
- `artifact_pointers.json`：特徴cacheを回収できる固定Kaggle kernel ref/versionと各artifact SHA。所有者の認証が必要。
- `selected_models.json`・`selection_policy.json`・`convergence_policy.json`：validationだけでの候補とheadの選択条件。
- `file_manifest.json`：この成果物内のファイルのbytes/SHAとcanonicalな一覧のSHA。
