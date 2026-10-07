# ジョーシン遷移修正の証拠

[調査・修正内容](../../reports/2026-10/joshin-navigation-fix.md) と [全条件の集計](summary.json)。成功、403、Cloudプロキシ由来503を最初のURL・測定時刻・実装の版ごとに分けている。

[evidence-checkpoint.zip](evidence-checkpoint.zip) は8,013,423 bytes。34試行の台帳、本文・DOM・ソースblob、条件、fingerprint、fixture、最終コード、検証ログ、実行レシピを含む。重複blobを共有領域に集約している。

ZIPを展開して次を実行すると、各Evidenceセルの通常の `blobs/` 配置を再構成できる。出力先には新しいディレクトリを指定する。

```sh
python3 restore_checkpoint.py EXTRACTED_CHECKPOINT NEW_OUTPUT_DIRECTORY
```

復元後の34セルすべてを再検証し、4056個のblob参照とZIP内のmanifestハッシュを確認した。[復元検証](restore-verification.json)、[ZIPのSHA256](evidence-checkpoint.zip.sha256)。fixtureに混ざった一時WebSocket認証パスは共有コピーで伏せている。元のローカル観測、過去の調査checkpointは保持した。

`sources.json` はセル開始時の外側のcheckoutを記録するため、読み込み済みmodule・bridgeの実行版と一致する保証はない。最終bridgeのinstalled sourceは `environment/` に保存した。過去のPOCは `tab_open` 内で空タブから目的URLへ遷移しており、再現には当時のbase commitのnative controllerを使う。最終helperの結果と混同しない。
