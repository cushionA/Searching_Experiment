# robotsを閲覧ゲートにしない経路試験

本runは完了。ユーザーの最新指示によりrobots_policy=sitemap_discovery_onlyを明示し、前回停止した02 Web Scraper・10 MagicScraperを再検証した。元runは上書きしていない。branchはcodex-structure-routing-20261115。

最初にreport.md、protocol.json、summary.json、route-verification.json、transferred-setup.jsonを読む。coldは今回条件での初回HTML取得というラベルであり、URL inventory/軽量エージェント回答は前回から転用している。完全coldの初期費用比較ではない。

初回は両サイトでスクロール後3/3の詳細HTML保存。別プロセス・別一覧のwarmは02で3/3、10で2/3。10のindex.htmlを商品詳細へ混ぜた失敗を保持している。HTML12件保存と目的型確認11件を区別し、固定URL recallは未測定。

ブラウザ巡回と今回URL選択のモデル呼出0。MagicScraper6件の型の後評価だけNative Luna1ジョブ、token/内部推論回数unknown。自動scoreは02の6件確認、MagicScraperはcanonical redirectの評価URL範囲不足で未確認のまま。posthoc評価は別資料で、候補・規則・scoreを修正していない。

robots規則で閲覧停止しないのはこのprotocolで明示した条件。今後この作業を継続する場合も、最新のユーザー指示としてこのpolicyを引き継ぐ。実HTTP401/403/429、ログイン等は別の取得結果として扱う。grounding transport/他run/AGENTSの条件を一括変更していない。新しいmetadata実行ではrobotsはSitemap参照の発見だけに使う。

前回の文書予約上限46＋今回28＝74、共通上限180。サイト別02の旧分10、10の旧分1も引き継いだ。今回の新規HTTP359、XHR58、UI45、browser_ms58.242秒。前回中断費用unknownを0扱いしない。既存inventoryを転用したことと、詳細HTMLの新規取得は区別する。

再開は元branchの本runを含むcommitをcheckoutしてcheckpointをrepository rootへ展開する。ZIP inventoryの原バイトSHAを照合する。run.lock、node_modules、venv、モデル重み、資格情報は含めない。dependenciesは前回と同じpin。Python3.12以上、Node22、lxml、既存のPlaywright/Chrome環境を使う。

```powershell
python -X utf8 -B -m jse.lab structure-routing --run lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115 --action verify
python -X utf8 -B experiments/structure-routing/sitemap-agent-score.py --run lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115 --policy lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115/evaluation-artifacts/policy.json
```

追加のlive取得は不要。完了済みphaseはskip。将来の別条件試験も元の結果を上書きせず、転用費用・残予算・実行sourceを記録する。URI末尾型が広すぎてindexを拾う問題と目的型の自動確認が、次の実装課題として残る。
