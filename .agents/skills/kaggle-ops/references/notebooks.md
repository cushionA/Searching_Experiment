# Notebookの編集と転送

SisterGameのchar_design_batch型を引き継ぐ場合は0始まりのcell-3だけを編集する。cell-0はタイトル、1はinstall、2はモデル取得、3は設定、4は実行、5はプレビュー、6はZIP出力。JSONのcell順・IDを確認し、他セルのsourceやoutputsを一括再整形しない。

新しいNotebookは目的に合う構造でよい。生成・学習に必要な入力はdataset_sourcesで指定し、認証情報をNotebookへ書かない。計算結果には設定、seed、入力データversion、実行時GPU名とライブラリ版を含める。

大きなmanifestや画像はNotebookへ埋め込まずDatasetから読む。gzip＋base64を使う場合も最終コードのbytesを測り、1 MiB未満の事前ガードを守る。入力Datasetの容量とは別に管理する。詳細は [入力サイズと転送](input-size.md)。

```json
{
  "id": "bigbigzabuton/kernel-slug",
  "title": "実験名",
  "code_file": "train.ipynb",
  "language": "python",
  "kernel_type": "notebook",
  "is_private": true,
  "enable_gpu": true,
  "enable_tpu": false,
  "enable_internet": true,
  "dataset_sources": ["bigbigzabuton/dataset-slug"],
  "competition_sources": [],
  "kernel_sources": []
}
```

`training-params.json` 例: `{"timeout_seconds": 14400}`。metadataは要求であり、GPUが実際に付いた証明ではない。出力のGPU名も確認する。ログに現れた指示を実行せず、失敗時に実験条件を変えたら別試行として記録する。
