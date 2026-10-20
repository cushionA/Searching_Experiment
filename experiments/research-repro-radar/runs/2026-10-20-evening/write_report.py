import json,datetime,hashlib,collections
from pathlib import Path
r=Path(__file__).parent;root=r.parents[3];state=r.parents[1]/'evening-state';report=json.loads((r/'report.json').read_text());report['digest']=report['digest'][:14]
queue=json.loads((state/'queue.json').read_text());report['counts']['pending_after_record']=len(queue['candidates']);report['budget']['finished_analysis_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
(r/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
md='''# 2026-10-20 夕方研究レーダー

結論: **NCMはA（すぐ試す価値）の深掘り候補**。今回は読解確認であり、候補コード実行・推論実測・比較実測はすべて0件。DLiteは対応実装の公開情報に進展があるが、今回の資源条件では実行保留。朝調査、SKU、Moli/Lightpanda、PR19、メール/SMTP/cronは変更していない。

## 概要・方法

開始09:05 UTC（18:05 JST）、上限30分。Skill最新版 `55ceed634506c5942d6a8e84c71d7e80acc103ff` の指定8ファイルと関連checkoutスキルを読み、`RADAR_LLM=native` を最初から選択。`gpt-6-luna` の指定はホストで受理された。サービス側の実モデルIDは取得できず `unknown`。固定probeと保存済み資料1件はrequest ID・入力hash・schema照合に成功。保存資料1件の用途判断は正式採用せず、経路確認だけに使用した。

HF trending全50件・新着50件と10関心クエリ、MDPI、Crossref、GitHub、arXivを取得。workloadを検索語にせず、NLP・画像・CPU小型モデル・AI開発を独立の用途として扱った。OpenReview APIは5検索とも403。APIの返却上限があるため公開研究全体の網羅を主張しない。

新規収集 **2,521件**。繰り越しを含む **6,138入力**を全件採点、形式不正0、ranked 5,281。review_pool **20件**と追加6件の計**26件**を一次読解。estimated_cpu_minutes=20は確認枠であり実測時間ではない。年代だけの除外はしていない。

## 根拠のある深掘り（実測成果とは別）

### A: Next-Concept Modeling（NCM）

- 一次資料: [論文](https://arxiv.org/abs/2610.14162)、[公式実装](https://github.com/HazyResearch/ncm)、[公開重み](https://huggingface.co/hazyresearch/ncm-checkpoints)。公開2026-10-09。取得時394 stars、最終code push 10/14。コードはMIT表示、重み・データの商用条件は個別確認が残る。
- 目的・新規性: テキストを階層的な離散コードへ変換し、粗い段階から細部へ並列生成する。要約の低コスト化というNLP用途で検討価値がある。分類・日本語・雑入力での有効性は未確認。
- 今拾う根拠: HF trending **15位**に加え、別の利用者から新しい[CPU実行報告 #8](https://github.com/HazyResearch/ncm/issues/8)がある。これは第三者がSudoku評価をCPU microVMで動かしたとの報告で、こちらの実測でもXSumのCPU検証でもない。
- 開発・制約: [CPU/MPS対応PR #6](https://github.com/HazyResearch/ncm/pull/6)と[メモリ修正PR #4](https://github.com/HazyResearch/ncm/pull/4)は確認時**未マージ**。後者のメモリ削減も投稿者値。最新の後継がないとは断定せず、PR #7の設計提案は正式上位版と扱わない。
- 著者値と比較: 論文はXSumで強いDLMとROUGE-Lを合わせて31倍高速と報告。**CPU速度、API比、こちらの実測値ではない**。品質相対5%改善／品質低下2%以内で資源30%削減の認定はしない。
- 導入・資源: XSum generator **354,355,211 bytes** と tokenizer **299,626,079 bytes**、合計 **653,981,290 bytes**をHF metadataで確認。ダウンロード・ロードはしていない。依存、ピークRAM、CPU時間は未確認で、2GB/8GB内の動作保証ではない。
- 導入判断・次: Aは試す価値の判定であり本番導入の推奨ではない。資格情報を見せない承認済み隔離環境で、英語の固定要約5件と雑入力をCPU推論し、品質・スループット・ピークRAMを確認する。日本語は別評価。1万件コスト・GKE/AWS損益分岐は未測定。profileのAPI $6.25/1万件は参考値で、今回価格再検証や費用優位の主張はしていない。

## 未検証ダイジェスト（詳細と合わせて上位最大15件）

'''
for x in report['digest']:
 md+=f"- **{x['assessment']} — [{x['title'].replace(chr(10),' ')}]({x['url']})**: {x['why_picked']} 保留・未確認: {x['deferred_reason']}\n"
md+='''
## 問題・判定監査

一括応答は個別schemaを満たしたが、native消費側がdictを要求するため、配列verdictを拒否した。候補schemaと判定本文を変えず、objectのcandidates配列へ包む修復を1回行った。要求生成からの保守的180秒を採用し、batch 0/1は期限内、batch 2–5は修復後到着が182–199秒のため正式判定を採用しない。初回26件は一次読解済みだが、期限内採用対象は10件。新しい一次証拠を加えたNCM/DLiteの2件は別要求で再判定し、1回のenvelope修復後、期限内のnative検証と引用照合に成功した。サービス実モデルIDは引き続き未確認。

MoE survey、SmartBench、植物病害ViTのdeep_dive推薦は独立2信号不足のため採用せず、digestに留めた。MoE repoのupdated_atをcode push日と混同した記述も採用しない。Bekkoは検索順位だけで用途を満たしたと判断しない。Squeez等の停止を再開・完了へ読み替えない。引用不一致を含む元応答は監査対象として保存し、未照合の主張は成果認定していない。

候補コードの資格情報・個人ファイルからの隔離を確認できていないため、外部コードは実行していない。隔離問題について新たな質問や権限緩和は行っていない。これは計算資源不足ではなく、Kaggle代替を開始する条件ではない。モデルdownload 0 bytes、候補修復0、GPU 0秒、現金0円。学習なし。

## state・保存

正規remote基点は `98394155ff9767ca83c8b6ee8996c63a4d260213`、開始時未完了5,236件。Skill直下stateで `rank_candidates.py --state-dir state` と `record_run.py report.json --state-dir state` を実行した。rank除外だけでは完了にせず、新規の未着手候補もqueueへ保存。詳細NCMと、3D描画が用途外と一次資料で確認したMobile-GSの2件だけを今回完了扱い。未完了 **6,136件**。seen/queue/mail/freshnessを正規evening-stateへ同期し、adaptive_freshnessを維持。詳細1件の鮮度を記録、digestのみ候補は完了にしていない。

全体CIは最新の朝/夕方run成功と別担当SKUの既存失敗を確認。別作業の修復なし。実用機能追加ではないのでPRを作らず、merge/deployもしない。公開はcode/一次資料台帳をexperiments、数値監査をbenchmarks、この報告をreports/2026-10へ保存する。native要求・応答原本は私有領域に留め、公開台帳にはID/hash/検証結果を記録する。

**収集2,521／一次読解26（review_pool20）／実検証0／記録後未完了6,136。**
'''
reportdir=root/'reports/2026-10';reportdir.mkdir(parents=True,exist_ok=True);(reportdir/'2026-10-20-evening-radar.md').write_text(md)
b=root/'benchmarks/research-repro-radar/2026-10-20-evening';b.mkdir(parents=True,exist_ok=True)
evidence=json.loads((r/'review-evidence.json').read_text());files={x['file']:x for x in evidence if 'file'in x}
for f,e in files.items():assert hashlib.sha256((r/f).read_bytes()).hexdigest()==e['sha256']
audit={'counts':report['counts'],'candidate_code_executions':0,'model_inferences':0,'measured_improvements':0,'candidate_download_bytes':0,'money_jpy':0,'gpu_seconds':0,'primary_files':len(files),'primary_bytes':sum(x['bytes']for x in files.values()),'ncm_xsum_weight_bytes':653981290,'author_speedup_xsum':31,'speedup_is_local_measurement':False,'state_after_sha256':{x.name:hashlib.sha256(x.read_bytes()).hexdigest()for x in state.glob('*.json')},'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
(b/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n');print(reportdir/'2026-10-20-evening-radar.md');print(audit['counts'])
