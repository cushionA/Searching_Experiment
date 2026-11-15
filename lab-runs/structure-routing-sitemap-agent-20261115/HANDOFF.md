# URL群選択→一覧→スクロール→詳細HTMLの引き継ぎ

作業branchはcodex-structure-routing-20261115。report.md、STATUS.json、summary.json、route-verification.jsonを読む。初期の別方式の試験は過去runに保持している。

本pilotは完了。対象9サイト、URL群を得た7サイト、ブラウザ初回7サイト、warm2サイト。Scraping Sandboxで/infinite-scrollからスクロール後の商品3件を取得し、保存recipeを別Nodeプロセスで使って/data-tableから未観測の商品3件を取得した。HTMLキャッシュから読んだ件数0、巡回LLM0。詳細型は後評価を含むため、自動型検証の完成と取り違えない。

URL選択はNative gpt-6-luna指定2ジョブ（初回入力切断＋1回修復）、本文要約の評価は別のLuna2ジョブ。全Nativeトークン・内部推論回数はunknown、実モデルのprovider IDは独立検証なし。URL選択側にHTMLや評価資料を渡していない。評価結果は取得へフィードバックしていない。

score.jsonは凍結した自動評価で6件確認。route-verification.jsonは、それに別の後評価9件を区別して記録したもの。fixed URL recallや80サイトベンチマークのスコアはnull。template-validation.jsonの厳密DOM構造照合は3件とも不一致。成功へ合わせて直さない。

Web Scraper/MagicScraperの詳細はrobotsで停止し、warm未実施。拒否パスの再試行、関連ホスト追加による迂回、ログイン等はしない。ScrapingCourse/Automation Exerciseは今回URL取得不可。NASAは検索結果への誤到達を成功から除外。TypeScriptは期待型のリンク群を得られなかった。

最初のWeb Scraper試行を停止し、途中DOMをartifacts/02/sitemap-agent-browser/aborted-001に保存した。厳密なHTTP/時間はunknown、文書上限9をattempts.jsonlに別計上。修復後は読取期限、進捗・文書予約台帳、部分結果保存がある。中断を理由に新runで予算をリセットしない。

Chrome/依存/モデル重みはcheckpointに含めない。Node22、Python3.12以上、lxmlが必要。sitemap用package.json/lockを.deps/structure-routing-sitemapsへ配置してnpm ci、ブラウザ用manifestはdependencies/browser-package.jsonでplaywright/robots-parserのpinを確認する。今回新しいruntime依存は導入していない。

新しいラボCLI入口を含むため、このbranchの最終commitをcheckoutし、checkpointのrepository-relative pathsをrepository rootへ展開する。ZIP inventoryの原バイトSHAを照合する。Gitの行末変換を受けたJSONのhashと混同しない。evaluation-artifactsは取得終了後に回収した後評価資料で、候補選択へ再入力しない。

```powershell
python -X utf8 -B -m jse.lab structure-routing --run lab-runs/structure-routing-sitemap-agent-20261115 --action verify
python -X utf8 -B experiments/structure-routing/sitemap-agent-score.py --run lab-runs/structure-routing-sitemap-agent-20261115 --policy lab-runs/structure-routing-sitemap-agent-20261115/evaluation-artifacts/policy.json
node --test experiments/structure-routing/sitemap-agent-routing.test.mjs
python -X utf8 -B experiments/structure-routing/sitemap-agent-score.test.py
```

discover/executeは--liveが必要。完了したサイト/phaseはskipし、phase resultがあるのにresults.jsonlへ未反映なら復元する。中断phaseの台帳は保存して予約上限を持ち越す。state.jsonを持つgrounding runへこの別ベンチマークを混ぜることは拒否される。

Windows以外の既存ブラウザではprotocol内の実行ファイルパスが合わないため、現runの条件を黙って変更せず、検証済み環境との差を記録する。verify/scoreはブラウザを開かない。

追加チェックは26件通過（URL/再開6、browser7、評価8、CLI5）。test_labは86件中2失敗・5skip、前回と同じCamoufox Windows path/proxy NO_PROXY。今回未変更領域で修正していない。

次の必要作業は目的型の自動検証、DOM fallbackが検索リンクを拾う問題、一覧候補の選び直し。今回の完了はこの方式の小試験であり、全80サイトへの展開やSkillへの本組込みは未実施。
