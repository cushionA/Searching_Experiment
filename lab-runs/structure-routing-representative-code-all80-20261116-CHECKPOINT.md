# クラウド引き継ぎ用チェックポイント

ブランチ: `codex-structure-routing-20261115`

全80サイトの測定を完了。先行6サイトは再利用、新規74サイトを追加。有効HTML保存43/80サイト・204ページ、目的型確認23/80サイト・101ページ。実行時の追加LLM呼び出し0。初期nativeトークンは不明。詳細はrun内の `report.md` と `HANDOFF.md`。

ZIPは116,576,353 bytes、SHA256は `318e9108fc06124932e10cb2faf142c6c3027a5fd9d258221df78e119c283376`。Gitでは次の3分割を保存した。

- `structure-routing-representative-code-all80-20261116-checkpoint.zip.part001`
- `structure-routing-representative-code-all80-20261116-checkpoint.zip.part002`
- `structure-routing-representative-code-all80-20261116-checkpoint.zip.part003`

各部分のbytesとSHAは同じフォルダーの `structure-routing-representative-code-all80-20261116-parts.json`、元ZIPの全件検査結果は `structure-routing-representative-code-all80-20261116-export.json`。失敗した旧 `checkpoint-unverified.zip` と診断用ファイルは配布対象に含めていない。

このブランチを取得したrepository rootで実行する。

```bash
python -B lab-runs/structure-routing-representative-code-all80-20261116-restore.py --extract-to .
python -B lab-runs/structure-routing-representative-code-all80-20261116/summarize.py
python -B lab-runs/structure-routing-representative-code-all80-20261116/audit.py
python -B -m jse.lab structure-routing --method representative-code --run lab-runs/structure-routing-representative-code-all80-20261116 --action verify
```

復元スクリプトは標準Pythonだけを使い、各partと再構成したZIPのSHAを確認してから展開する。再集計にはPython 3.12以上とlxml、実行器のverifyには既存のNode依存も必要。ZIPは測定時のソースと保存HTML/DOMを含むので、Git checkoutだけの状態に頼らず展開する。

ライブ取得は完了している。初期調査、生成、cold、warmを再実行する必要はない。取得拒否・未実行を含む全160行は `operational-results.jsonl`、実行できた107 phaseの生結果は `results.jsonl`。判定器・固定候補・生成コードを結果に合わせて変更しない。

次の改善案は一覧候補の検証、目的型を保つリンク選択、API契約の事前検査、初期入力の圧縮と実usage計測。今回の結果から全面自動巡回の完成とは判断していない。
