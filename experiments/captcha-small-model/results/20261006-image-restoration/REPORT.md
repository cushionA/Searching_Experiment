# 公開画像入力の復元と学習用分割（2026-10-06）

過去評価で指定された2つのGit commitから、分類画像4,068枚・保存ボード1,000問を復元した。固定Git tree内のblob SHA-1、元アーカイブSHA-256、manifestで参照する全15,434画像のSHA-256を照合した。元collectorのラベル・正解選択・prompt・順序・decoded RGBの重複検出をそのまま使った。

| ソース | 固定commit | 件数 |
|---|---|---:|
| orlov-ai/hcaptcha-dataset | `a1b180f9091719517d8890c33ab8b4d5df38ac10` | 分類4,068枚 |
| ssivakorn/reCAPTCHA-study | `efb3595c33780abf9d326c729973e72d165366c2` | ボード1,000問 |

Type Aは662問、Type Bは338問。decoded画像の重複14件、注釈競合2件も過去集計と一致する。ただし旧画像単位manifestと生予測がないため、過去の各画像・順序の完全な同一性は証明できない。競合する注釈は変更せず残した。

復元manifestのSHA-256は `0d1a8999f091dbb574ef7c8d730b78a8eba37fbd645b436d64b1c7037dfc3c65`。`public_full.json.gz`に全画像のsource ID、ラベル、raw/decoded画像SHAと全ボードの正解選択を保存した。固定tree snapshots、出典のcommit/archive SHA、GPU用bundleの検証記録も同フォルダーに保存した。

## 学習前に固定したsplit

seedは20261006、目標比率はtrain 70% / validation 10% / test 20%。同じボードの全tileを同じfoldに置き、raw bytesまたはdecoded RGBが一致する別ボード・画像も推移的に連結した。5,066 components、最大componentは2行で、巨大な連結成分はなかった。

| fold | 分類画像 | ボード | 含まれないボードtarget |
|---|---:|---:|---|
| train | 2,847 | 700 | なし |
| validation | 406 | 97 | bridge / taxi / tractor |
| test | 815 | 203 | bridge |

稀なtargetとTypeの組合せはすべてのfoldに配れない。存在しないtargetの精度は未評価として扱う。詳しい割当は`grouped-splits.json.gz`、targetのcoverageと全件照合結果は`coverage_audit.json`を参照する。

全IDが1つのfoldだけに属すること、ラベル・正解選択が元manifestと同じこと、同じraw/decoded画像がfoldを跨がないことを独立に照合した。潜在的に同じ撮影シーンの異なる切り出しは、ソースに共通IDがない場合は識別できない。

この画像集合は既にモデル選定に繰り返し使ったものである。今回のsplitは学習と比較のための内部holdoutであり、新しい未使用の最終テスト集合として扱わない。

## 復元と梱包

外部取得した固定tree JSONとarchive receiptsを指定して、ネットワークを使わず復元する。

```bash
python -B experiments/captcha-small-model/restore_public_image_inputs.py \
  --source-metadata /path/to/pinned-source-metadata --output /path/to/new-eval-data
python -B experiments/captcha-small-model/prepare_public_image_split.py \
  --manifest /path/to/new-eval-data/public_full.json
python -B experiments/captcha-small-model/prepare_image_finetune_bundle.py \
  --manifest /path/to/new-eval-data/public_full.json --output /path/to/new-evaluation_bundle.bin
```

source-metadataには`hcaptcha-tree.json`、`recaptcha-tree.json`と、同じslugの`*-archive.json`が必要。receiptはrepo、commit_sha、固定codeload URL、取得archiveのpath・bytes・sha256・completeを記録する。SHAと固定commit/treeが一致しない入力は拒否する。新しい出力先だけを使用し、過去データは上書きしない。

今回のGPU用ZIP bundleは548,785,283 bytes、SHA-256 `794f53417dab8685f66330990c6fe937320213208edd1c1b5bdeada042b03069`。参照画像、manifest、frozen splits、公開source provenance、元README/LICENSEだけを含む。元796MBアーカイブとfull-board screenshotsは含めていない。

hCaptcha repositoryのコードlicenseはMITだが画像利用権は別途未確認。reCAPTCHA-studyはCC BY-NC 4.0。これは公開注釈によるオフライン評価で、実サービスでの認証通過を測らない。
