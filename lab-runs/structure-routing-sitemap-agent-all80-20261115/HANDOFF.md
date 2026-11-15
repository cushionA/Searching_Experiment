# 引き継ぎ

## 内容

- `report.md` は全80サイトを分母とし、冷/温の実測・停止・入力なし・未実行を区別する。
- `operational-results.jsonl` は80サイト×2 phaseの160行。評価行のsource、raw phaseの参照、strict/saved HTML件数、scroll情報を保持する。
- `summary.json` は採用済み最新phase（repairがある場合はrepairを優先）と、cold既観測URLを除いたwarm新規URL集計を格納する。
- 元の `results.jsonl` はappend-onlyのまま。HTML本文やpageHTMLは集計入力に使っていない。
- `evaluation-policy-frozen.json` と `evaluation-policy-ref.json`、`execution-source-before-cold.json` は評価凍結・実行開始点の参照。正解集合の再生成や評価条件の変更をしない。
- `cost-audit.json`、`checks.json`、`validation.json`、`model-usage.json` は費用上限、完了/重複監査、検証、モデル利用の記録。
- `dependency-locks/` は取得完了後に同梱したbot-diagnosticsとstructure-routing-sitemapsのlockfile。

## 再集計

リポジトリのルートから実行する。

```sh
python3 -B lab-runs/structure-routing-sitemap-agent-all80-20261115/summarize.py \
  --repair lab-runs/structure-routing-sitemap-agent-all80-20261115/repair-run
```

これは保存済みJSON/JSONLだけを読む。再取得やモデル呼出しは行わない。実装の入口は `experiments/structure-routing/sitemap-discovery.mjs`、`sitemap-agent-routing.mjs`、`sitemap-agent-browser.mjs`。

## 検証

`validation.json` に記録済み。対象コンポーネントはparser 15、routing controller 11、browser 8、frozen evaluator 20の計54件が成功。プロジェクトlab suiteは86件中79成功、5 skip、2 failureで、記録上はWindows Camoufox path fixtureとNO_PROXY環境期待値の既知の無関係failure。

## 継続・搬送

checkpointは `lab-runs/structure-routing-sitemap-agent-all80-20261115-checkpoint.zip`。run全体、修正コード、選択request/answer、保存HTML/DOM、取得台帳、凍結policy、lockfilesを含む。書出し時にZIPのCRCと全inventory SHA256を照合する。書出し結果・ZIP SHA256はアーカイブ外の `SITEMAP-AGENT-CHECKPOINT.json` に保存する。

クラウドでは `codex-structure-routing-20261115` を取得し、ZIPをリポジトリルートへ展開する。Gitの差分とチェックポイントを一緒に使う。この実験は独立したstructure-routing runで、grounding用のstate.jsonは持たない。選択・取得の再開に必要なファイルはsites/、agent-batches/、artifacts/、repair-run/に入る。

```sh
git checkout codex-structure-routing-20261115
git pull --ff-only origin codex-structure-routing-20261115
python3 -m zipfile -e lab-runs/structure-routing-sitemap-agent-all80-20261115-checkpoint.zip .
python3 -B lab-runs/structure-routing-sitemap-agent-all80-20261115/summarize.py --repair lab-runs/structure-routing-sitemap-agent-all80-20261115/repair-run
```

取得は完了しているため、継続時にexecute --liveを実行する必要はない。実行器の検証を行う場合は、同梱のdependency-locksの各package.json/package-lock.jsonを対応する `.deps/bot-diagnostics/` と `.deps/structure-routing-sitemaps/` に置き、各ディレクトリでnpm ciする。通常のプロジェクトPython環境を使い、親runとrepair-runへそれぞれ `python3 -B -m jse.lab structure-routing --run RUN --action verify` を実行する。

新しい取得実験では別runを作り、LinuxのChromeパスを新しいprotocolへ設定する。過去runのChromeパス、対象、凍結評価、結果は書き換えない。

次の比較で新しい対象や上限を追加するときは、既存runを変更せず新しい設定・runを作成する。現在の試験から固定gold URL recall、graph search G、B1/C1に対する優位性は主張できない。
