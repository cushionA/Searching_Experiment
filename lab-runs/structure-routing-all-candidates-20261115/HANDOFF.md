# 全候補・軽量MiniLM・Qwen Kaggleの引き継ぎ

最初にreport.md、RUN-STATUS.json、ALL-CANDIDATES-CHECKPOINT.jsonを読む。作業branchはcodex-structure-routing-20261115、開始commitはa0fa8aa98f58993d10f0012a7daa0db5a6466ecc。

MiniLMはCPU専用ONNX・Torch不要の汎用predictまで実行済み。FP32は元PyTorchと順位/selectorが一致、int8は不一致なので同等版扱いしない。重量・依存・RSS・tokensはminilm-onnx/runtime-summary.jsonとgeneric-predict-validation.json。元model/bin、変換後ONNX、tokenizerは.depsにありZIP/Git外。model/tokenizer SHAと変換コードはcheckpoint内にある。

本runの比較は代表DOMを過去runから移転した後の規則生成＋fresh warm取得。完全cold crawlの対照比較ではない。固定gold6件は候補に渡しておらず不変更。B1=1/6、C1=5/6、MiniLM=3/6、JevForge=0/6、MiniLMFiltered=6/6、CoScraperClassOnly=3/6（実施Booksは3/3、Oxylabs未実施）。Filteredはモデルなし最大member数規則と同じselectorになったため、MiniLMの効果とは言えない。

autopagerはCRFでNEXTを2/2検出し、予測URLのHTMLを新規2文書取得。保存ruleも別processで同じURLになった（追加モデル0/取得0）。商品詳細の6件と分母を混ぜない。PinchTabはmanual flow＋localhost HTML保存まで、自動graphはdefault evaluate guard 403。SmartCrawlerはMaven DNS、WebNavigatorは不足するWebArena task assetsで停止。詳細others/results.json。ユーザー通常browser profileを使用せず、専用profile/fixture serverは停止済み。

Qwenのlocal rawは8GiB GPUでOOM。Kaggle v1はT4でraw OOM/Co JSON解析失敗、outputs回収済み。v2はchunk prefill/出力上限/native generation configを調整した1回の追試。

- 固定ref: superbigzabuton/qwen-html-routing-probe-20261115
- v1 job: qwen-html/kaggle-job-001/job.json
- v2 job: qwen-html/kaggle-job-002/job.json
- v2 code/signature/sourceはjob.jsonとrepair-validation-1.json参照。
- v1ではtitle由来でrefが変化したが、source/private/GPU/Internet照合後に同じjobをreconcileし、再送していない。
- 両versionとも完了・成果物回収済み。v2の22 outputs・内部continuation SHAとremote source signature/private/GPU/Internet/version2を検証済み。再submit不要。

Kaggleの操作は.agents/skills/kaggle-ops/SKILL.mdに従う。親の認証をNotebookや別APIへコピーしない。現在Windows helper環境は.deps/kaggle-windows-venv/Scripts/python.exe、helperは.agents/skills/kaggle-ops/scripts/kaggle_ops.py。Cloudでは同skillの専用venvと既存の認証を使う。値は保存物へ含めない。

live log APIはHTTPErrorだったが、complete後のpersisted log/outputを回収した。v2は5 model calls、40,971 input/5,639 output tokens、推論1,206.925秒、全体1,273.186秒。raw selector armは規則なしで失敗。Co armは894 bytesのparserを生成した。付属CLIを除外する汎用AST loaderを追加し、生成クラスとXPathを変更せず消費した。これはexecutor adapter1件であり、手動selector補正0。OS sandboxではなく許可するAST形状を限定している。

CoScraperClassOnlyの保存ruleを別Node processで読み、model未入力のlistingから新規詳細HTML3/3を保存した。4文書・HTTP86・warm model0。ruleはrules/CoScraperClassOnly/01.json、生成物とclass auditはqwen-html配下。Kaggle使用時間のaccount差分は2ジョブ合計1,619.578秒、予約0。追加の大規模runや3回目Kaggle追試は予定していない。

採点と規則確認は保存物だけで実行できる。model weights・Python依存・node_modulesはZIPに含めない。Cloudで続ける際はfinal archiveをrepository rootへ展開し、inventoryの原バイトSHAを照合する。Gitの行末変換を受けたJSONのhashと取り違えない。独立goldを候補プロセスへ渡さない。

```powershell
python -B experiments/structure-routing/all-candidates-score.py --run lab-runs/structure-routing-all-candidates-20261115
node --test experiments/structure-routing/all-candidates.test.mjs experiments/structure-routing/sitemap-discovery.test.mjs
python -B experiments/structure-routing/generated-parser.test.py
node experiments/structure-routing/pagination-follow.mjs lab-runs/structure-routing-all-candidates-20261115 --rules-only
```

完了済みresult tuple、HTML、gold、過去の80サイト結果を再取得・上書きしない。userが並行編集しているbot-diagnosticsや既存の400M索引/過去rawへ触れない。取得済みDOMの解析や同じselectorの再使用を新規取得に数えない。

全体test_labはWindowsのUTF-8設定なしで22 errors、UTF-8指定後は81件中2 failures/5 skipsが残った（camoufox Windows path、proxy NO_PROXY）。今回未変更領域で、基準checkoutとの比較・修正はしていない。追加Node10件・HTML scorer9件・Kaggle helper23件・AST loader3件は通過し、各checks.logに保存した。
