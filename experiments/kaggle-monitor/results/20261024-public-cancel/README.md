# CPU APIキャンセルの実測

ユーザーの「公開にして終わったら消す」許可に基づく合成テスト。`superbigzabuton/issue24-public-cancel-20261024` version 1、CPU、GPU/TPU無効、Internet無効、上限180秒。公開したソースと回収成果物に認証情報や実データを含めない。

公開frontendの固定version照会からsession ID `361701960`、kernel ID `140536360` を取得し、owner/slug/versionとSDK kernel IDを照合した。`status-before-cancel.json` のrunningに対し、保存したsession IDへAPI停止要求を1回送信して受理された。`job/job.json` にrequest_acceptedと最終cancel_acknowledged、理由user_requestedを記録。API受付と後続の停止確認を分けており、時間上限停止として扱っていない。因果関係の帰属は記録上の照合であり暗号学的証明ではない。

`job/before-final-reconcile-*` は最初の保存記録、`job/job.json` とcontinuationは修正後helperで再照会した最終記録。新たな停止要求は送っていない。成果物public-cancel-proof.json（137 bytes、SHA-256 `d9e791cd0bff4f0f78eb1260c10648f90fc4781fd24a087c72c944fc0a11bc31`）とログを回収して保存した。

その後、同じrefの現在版・ソース・公開設定・停止状態・回収済みファイルを再確認し、DeleteKernelを1回実行した。`cleanup-result.json` は受付後のGetKernel=403、完全な所有Notebook一覧23件に対象なし、公開GetKernelViewModel=404を記録する。対象Notebookの削除を確認済み。他のNotebookは削除していない。

これは停止操作と証拠回収の検証であり、実験結果の品質やSKU精度の合格判定ではない。端末への通知受信は未確認。
