# 2026-10-22 朝: 公開者登録型発見方式の配布監査

`python3 -B experiments/daily-tech-20261022/collect.py` は固定版論文・公開配布metadataのみ取得。
`python3 -B experiments/daily-tech-20261022/audit.py` は取得hash/bytes、tree、manifest、ID、helper ASTを標準ライブラリで監査する。
外部コードのimport/compile/実行、依存導入、原文コーパス取得、モデル実行なし。

`sources/loong--rebuild_docs.py` は読解用原本。実行しない。
監査時間を候補の検索・前処理性能と扱わない。全文資料は出典と版の照合用。
原資料の権利は各出典に留保。AISTRAL配布のLICENSE/NOTICEも保存している。
このcheckoutで探索実験・正解採点を行っていない。
