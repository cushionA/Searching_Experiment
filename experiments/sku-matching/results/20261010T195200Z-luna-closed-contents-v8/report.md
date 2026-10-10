# V8 独立監査報告

監査対象は `20261010T195200Z-luna-closed-contents-v8` の固定済み成果です。原goldはcheckout外の評価ディレクトリで `grade.py` により再採点し、ラベル内容は監査記録へ出力していません。保存済みgradingと独立再実行の比較ファイル5点（比較表、V8/cumulative各summary・case results）はすべて完全一致しました。freezeの全ソースSHAも一致しています。

V8は同一62ケース中60件正答（96.77%）、28採用・32除外・2保留でした。採用行precisionは28/28、row-match recallは28/30です。累計200ケースのうち既知193ケースでは173件正答（89.64%）でした。累計は過去ケースを再利用する比較であり、新規holdoutではありません。`human_verified=false` の機械採点結果です。

手順系譜は新規closed-contents facet 2タスクと既存2回答の再利用、source選択によるmatching修復7タスク、次元署名16新規＋V7からの合格署名14件再利用です。署名計画30件、最終レビュー62件のうち33件が新規レビュー、29件が入力・文脈・予測・署名条件の一致による再利用です。dimension側は28/30通過、2件はunknownとして扱われています。

採用28リンクのcontents proof sidecarは、bounded contents rowと可視コンテキストの一致、component line/context binding、選択lace条件、matching回答・review・linkのhash参照を全件で満たしました。negative判定ではS30見出しに限らず、完全なcontents行に結び付いたcomponent contextを根拠アンカーとして扱う設計です。原source IDとquoteは変更されていません。累計リンク62件はRakuten SKU 60件に対応し、同一Rakuten SKUへの複数AUリンクが2組あります。

レビューdispatchには5件receipt欠損がありました。監査では既存answerファイルのSHAとfile mtimeをdispatch記録と照合し、保存済み出力の存在だけを確認しました。5件とも `finished_at` はnullのままで、mtimeからprovider完了時刻を推定していません。requested `gpt-6-luna` は要求値であり、actual modelVersionとusageはnullです。したがってproviderの実モデルID・usage・routingは検証済みとは主張できず、dispatch時刻は記録値、mtimeはproxyです。

全体テスト記録は583件、skip 37、error 17、failure 1で、V7と同じ失敗ID群としてrun内 `tests.json` に記録されています。これはV8採点自体の完全一致とは別の既知テスト状態です。人手による意味検証済みとは扱いません。詳細なhash・件別検査は同階層の `independent-audit.json` を参照してください。
