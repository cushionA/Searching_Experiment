# 2026-10-27 JST 朝 技術調査・成果物レビュー

## 結論

**新規候補 PageSift 0.73.1：B（条件付き）。A0 / B1系列 / C0。候補実試行0件、推論0件、自作オフライン静的監査1回。** ページ本文の軽量抽出、サイトマップと種ページのリンクからのarchive不要URL列挙、leaf seedからのscope拡張を持つ。試す方向は前処理時間とサイト内取得漏れ補完であり、rerankingではない。

有用な新発見は、**「1億公网页面・42,124 pages/s」の評価を、1億の独立ページ発見・抽出成功と解釈できない**こと。固定ソースでは50種類のパスをseed domainへ循環付加し、HTTP要求前にtask数を増やす。作者が公開する200成功数から再計算すると132.34件/秒、100 bytes以上のMarkdownは84.01件/秒。これらも作者集計の算術検算であり、当方の実測ではない。

## 概要・現在の進捗

main `4d2984a454fc766137784fd6a51579e10b6fafd6`、前朝 `5ed6ad1ef2bfa20f181addf6664afb8fd99a8be2`、夕canonical `9a051ad18a16325dc3420f4067036e9649c47456` を照合。mainは前朝レビュー時と同一。open PR6件のheadを確認し、機能の更新として扱う差はない。mainから独立branch `research/2026-10-27-morning` を作成した。朝10/10〜26とcanonical夕方報告の計35ファイルを候補名で照合し、PageSiftの記載なし。[履歴確認](../../benchmarks/daily-tech-20261027/progress-review.json)。全歴史の意味的重複不在を保証するものではない。

AGENTS.md、関連skillの適用条件を確認。今回は一次資料の静的レビューであり、`jse.lab` grounding探索やbot-blockingシナリオ実験ではない。SKU、Moli、PR19、夕方pending 9,384件、weekly state、SMTP/cron/automationsは変更なし。10/26夕のbackend UNKNOWN失敗は再開していない。

## 方法

Python 3.12.14標準ライブラリだけで、公式GitHub・PyPI・npm・crates.ioの32資料、1,072,142 bytesを取得。URL・UTC取得時刻・SHA256を[manifest](../../benchmarks/daily-tech-20261027/source-manifest.json)へ記録。資料取得10.9466秒、オフライン監査0.008508秒。全調査時間や候補実行時間ではない。失敗応答0、追加依存0、candidate download/install/inference 0、候補CPU 0秒、料金0円、修復0回。

[コード・再現手順](../../experiments/pagesift-source-audit/README.md)、[静的結果](../../benchmarks/daily-tech-20261027/static-audit.json)を保存。候補のソース・テストはテキストとして読み、import/build/runしない。仮の1M seed計算は実入力CSVの検証ではない。

## 結果：正確なID・版・配布条件

- 公式IDは [YOLOW92/pagesift](https://github.com/YOLOW92/pagesift)、最新stable [v0.73.1](https://github.com/YOLOW92/pagesift/releases/tag/v0.73.1)、2026-10-26 13:13:21 UTC公開（JST 22:13）。commit `6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5`。repo作成10/20、push10/26、99 stars / 16 forks、contributors APIはYOLOW92 1名・245 contributions。短期に多くの版が公開されているが、stars・commit数は品質保証ではない。
- README表題は0.73.0のまま。Cargo workspace、PyPI `pagesift`、npm `@pagesift/node`、crates `pagesift`、release/tagは0.73.1で一致し、npm gitHeadも一致。別後継製品は今回の公式資料内に確認せず。CLI・Python・Node bindingを別モデルの候補数にしない。
- [0.73.1変更](https://github.com/YOLOW92/pagesift/blob/6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5/docs/releases/0.73.1.md)はMCP HTTPのTCP_NODELAY、共有cache、警告、audit log、shutdown。作者の約10倍はローカルMCP keep-aliveの遅延改善で、一般Web crawlや抽出CPUが10倍という意味ではない。`serve` throughputは同資料でも不変とされる。
- `pip install pagesift==0.73.1`（Python>=3.9）、`npm install @pagesift/node@0.73.1`（Node>=18）、`cargo install pagesift --version 0.73.1 --locked`（Rust>=1.89）が導入候補。今回は実行しない。Linux x86_64 wheelはmanylinux_2_28、12,009,210 bytes。CLIの同arch tar.gzは13,624,611 bytes。圧縮配布物の容量であり、依存・browser込み総DL/peak RSSではない。CPU経路にGPUは必須でないが、source buildやbrowser並列実行が8GB/2GB上限内とは未検証。
- [LICENSE](https://github.com/YOLOW92/pagesift/blob/6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5/LICENSE)とregistryはAGPL-3.0-only。[作者の商用案内](https://github.com/YOLOW92/pagesift/blob/6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5/COMMERCIAL.md)は無改変の内部pipeline利用を無料側に置き、公開された改変サービスのソース提供義務を説明し、非公開製品向けcommercial licenseを別途提示する。商用一律禁止でも無条件許可でもない。今回は有償契約・問い合わせなし。

## 結果：coverageと評価の分母

### archive不要のURL列挙とscope

[固定crawlソース](https://github.com/YOLOW92/pagesift/blob/6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5/crates/pagesift/src/crawl/mod.rs)では、`map`はsitemapとseedページのリンクを結合する。seed取得は`engine=http`固定で、再帰的なJSサイト全体探索ではない。`search`引数は発見済URLの語一致順ソートなので、その部分単独を検索機能PRにしない。sitemap上限は深さ3・ファイル50・単一展開50MiB。網羅性の上限がある。

`crawl --scope auto`はseedの本文文字数>=200、子リンク<3、同じhostの兄弟リンク>=3を手掛かりに最寄り祖先へ広げ、警告を返す。leaf seedしかない際の取りこぼし補完という具体的仮説になる。ただし既知host内のheuristicであり、未知domain発見や日本Web全体のcoverage改善ではない。対象範囲の自動拡張は当repoの許可範囲を広げる根拠にならない。検証時は同じ許可済みsite内に固定する。

### 100M評価を再解釈

[作者報告](https://github.com/YOLOW92/pagesift/blob/6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5/docs/benchmarks/0.60_p4_wild_x_2_showdown.md)と[runner](https://github.com/YOLOW92/pagesift/blob/6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5/bench/p4/src/wild_x2_runner.rs)を照合。

|分母・判定|作者値からの再計算|解釈|
|---|---:|---|
|task 100,000,000 / 2,373.9秒|42,124.77件/秒|失敗を含む投入task rate。作者丸め値42,124.1とわずかに差|
|HTTP 200 314,170 / 同秒数|132.34件/秒、task比0.31417%|unique URL・正しい本文を保証しない|
|Markdown 199,422 / 同秒数|84.01件/秒、task比0.199422%|runnerはtrim後byte長>=100で判定。独立した品質採点ではない|
|仮にdistinct seed 1,000,000 × 固定50パス|最大50,000,000 URL|100M taskなら2巡。実seedリストの件数・一意性は未確認|

runnerは`domain_idx=global_idx % n_seeds`、`path_idx=(global_idx / n_seeds) % 50`でURL生成する。リンクを辿って1億URLを新規発見する実装ではない。この旧0.60のstress結果は現0.73.1のサイト発見性能の証拠にもならない。高負荷実験を当方で再現していない。

## 比較・その他の調査範囲

Crawlee、Playwright、Crawl4AI、PaddleOCR、Trafilatura、Page Agent、Browsertrixの公式release一覧も保存した。主に既報版で、新規PoCを増やさない。Browsertrix 1.15.1（10/23）はbrowser/dependency/doc更新で、archive不要発見の新方式ではない。Indra、LightOnOCR、既報SLM/NLP、前夕7候補の再比較はしていない。今回の新しい採用候補はPageSiftだけで、全領域から無理に1件ずつ選んでいない。

既存Impit/adaptiveと「HTTPを先に試す」だけを比較するのは冗長。比較するなら独立した保存HTMLでの本文欠落率と前処理CPU/RSS、またはleaf seed起点で同じ許可済URL集合の回収率。作者の各版・各機材・synthetic throughputを混在させて優劣を作らない。

## 問題・チェック

- 外部候補code実行のcredential/personal-file隔離承認は**既存pendingのまま、今回not attempted**。PageSift install/build/benchmark、Moli追加依存、夕方backend再試行は未実施。新たなdenial・結果不明の書込みなし。Kaggleを必要とするcompute不足は示されず、job/auth/quota操作なし。
- **ローカル既存基盤テスト：151件、failure 1、skip 15、15.817秒。** `python3 -B -m unittest discover -s tests -p 'test_lab*.py'` の `test_offline_diagnostics_guards` が `.deps/bot-diagnostics` の`robots-parser`不在（MODULE_NOT_FOUND）で失敗。[ログ](../../benchmarks/daily-tech-20261027/local-lab-tests.log)。候補性能の失敗ではない。既存依存の修復・CI保守は今回対象外としてinstallしない。前朝の42件とは基準main/テスト集合が異なり、改善退行の件数比較をしない。
- 自作静的監査はhash・5配布経路の版・tag/npm SHA・固定パス数・constantsの確認が成功。候補test suite実行成功とは言わない。
- push後remote SHAとexact-SHA CIを別途確認し、親への返答でrun IDと状態を報告。CI成功で候補crawl性能が実証されたとは扱わない。

## 採用・次の判断

**B**。PoC成功をAの必須条件にはしない。本件の条件は、既存経路と異なる価値を測る独立入力・scope範囲の確定と、既存隔離判断である。まず品質を落とさず前処理時間を短縮するか、site内leaf seedの取りこぼしを補完するかに検証を限定する。1億task速度を採用理由にしない。利用条件・8GB/2GB内の依存込み適合はその時点で確認する。

今回は調査・静的監査のみで実用機能の実装なし、draft PRなし。merge/deployなし。追加承認質問を繰り返さない。
