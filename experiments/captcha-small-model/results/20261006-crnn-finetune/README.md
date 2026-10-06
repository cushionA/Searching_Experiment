# 記録内容

- `REPORT.md`: 結果、比較、制約、再検証方法。
- `record.json`: Kaggle job/version、quota snapshot、実行環境、validationによる選択、checkpointとONNXのSHA。
- `data/`: 3,070件manifestとfold割当の可逆圧縮、出典・ライセンス記録。
- `trials/`: pilot、失敗したguard試行、修正版runのconfig/result、validation/test予測、環境・ログとKaggleのstatus receipt。
- `notebooks/`: Kaggleへ提出した3版のnotebook。
- `source/`: 両training script、固定helper、portable evaluator、notebook builder/runnerのgzip snapshot。
- `predictions/`: 同一614件に絞った4種の既存OCR予測、GPU/CPU予測。
- `cpu-cross-runtime/`, `onnx-runtime/`, `audit/`: 再現性、runtime互換性と検証記録。ONNX runtime CLIと予測は保存し、14 MBのgraph本体はGitに含めない。
- `artifact-catalog.json`: 個々のファイルのSHA-256/bytes。`MANIFEST.sha256`はmanifest自身以外の全ファイルをhash化します。

すべての`.gz`はlosslessで、元データと解凍SHA-256をartifact catalogに記録します。元画像・重み・optimizer stateはこのGit記録に含めません。選択checkpointはprivate Kaggle version 2の`crnn-finetune-result/best.safetensors`（SHA-256 `cce95d7cd320d332fecb606b39ccdcf269eb9794093bea7e2c0a4e4b30c11691`）を参照してください。
