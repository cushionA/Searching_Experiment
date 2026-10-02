# テーマ探索と検索入口の保存

[全保存ZIP](search-discovery-all-20261002.zip)は、2026年10月2日に保存した調査一式。4getをかなり有力な候補として優先保持し、ブロック対策による取得可能性は検証待ちとする。最新の方針は[候補台帳](../search-candidates-20261002.json)と[調査記録](../../docs/free-search-engines.md)を参照する。

ZIPには全15候補群、5runのチェックポイント、取得資料34件の記録、発見リンク1,168件の一覧、出典付き回答、保存時点の実装・テスト・手順書を含める。取得資料は両armの重複、確認画面、書誌JSONを含む。発見リンクには未取得のURLもあり、本文を確認した資料と区別して記録している。

元の作業領域のZIPを変更せず、そのままコピーした。[SHA-256](search-discovery-all-20261002.zip.sha256)は `4749f29bf681ce80fb30682c610eb6d62fa897e98c6ebadc8689c7f521617242`。ZIP内の `MANIFEST.json` と `source.zip` 内の `MANIFEST.json` には各保存ファイルのハッシュを記録し、全5runを復元して検証済み。ZIP内のコード・文書は保存時点のものなので、リポジトリの最新ファイルと異なる場合がある。

## 復元

全保存ZIPを展開し、`checkpoints/` 内の対象runのZIPを任意のrunディレクトリへ展開する。Python 3.12以上で、このリポジトリのルートから検証する。

```bash
python3 -B -m jse.lab verify --run /absolute/path/to/restored-run
```

保存時点のコードを使う場合は `source.zip` も展開し、そのディレクトリから同じコマンドを実行する。`SAVE_INDEX.json` が5runの対応表で、元のrunパス、保存状態、HTTPとbytesの使用量を確認できる。各runは結果判断待ちの状態を保持し、保存のために判断・予算・取得履歴を変更していない。
