"""Offline, metadata-only report. Never passes independent targets to candidates."""
import collections
import csv
import datetime
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'lab-runs/structure-routing-20261115'

def rows(name):
    p = RUN / name
    return [json.loads(line) for line in p.read_text().splitlines() if line] if p.exists() else []

def read(name):
    return json.loads((RUN / name).read_text())

def cost(row):
    return max(row.get('document_requests', 0), row.get('document_responses', 0))


def main():
    raw = rows('results.jsonl')
    scored = rows('score-cloud-outage/scores.jsonl')
    sites = rows('score-cloud-outage/site-status.jsonl')
    targets = rows('targets.jsonl')
    summary = read('score-cloud-outage/summary.json')
    interruption = read('interrupted-executions.json')
    diagnostic = read('cloud-network-diagnostic.json')
    methods = {x['method']: dict(x) for x in summary['methods']}
    for method, item in methods.items():
        scores = [x for x in scored if x['method'] == method]
        raw_method = [x for x in raw if x['method'] == method]
        pages = [p for x in scores for p in x.get('pages', [])]
        item.update({
            'exact_url_duplicates': sum(x.get('exact_url_duplicates', 0) for x in scores),
            'canonical_url_duplicates': sum(x.get('canonical_url_duplicates', 0) for x in scores),
            'unsaved_response_html': sum(not p['response_html_exists'] for p in pages),
            'status200_saved_html_no_navigation_error': sum(p['status'] == 200 and p['response_html_exists'] and not p['navigation_error'] for p in pages),
            'sites_with_gold_success': sum(x.get('gold_targets_reached_unique', 0) > 0 for x in sites if x['method'] == method),
            'captured_503_pages': sum(p['status'] == 503 for p in pages),
            'navigation_error_counts': dict(collections.Counter(
                ('saved_path_link_missing' if 'saved_path_link_missing' in p.get('navigation_error', '') else
                 'click_timeout' if 'locator.click: Timeout' in p.get('navigation_error', '') else
                 'document_budget' if 'document_budget' in p.get('navigation_error', '') else 'other')
                for x in raw_method for p in x.get('pages', []) if p.get('navigation_error'))),
            'G_success_on_verified_sites': len({x['id'] for x in scores if x['mode'] == 'G' and x['gold_status'] == 'verified' and x.get('mechanism_success')}),
            'R_success_on_verified_sites': len({x['id'] for x in scores if x['mode'] == 'R' and x['gold_status'] == 'verified' and x.get('mechanism_success')}),
        })
    stage_ledgers = [json.loads(p.read_text()) for p in RUN.glob('stage-c-selection-ledger*.json')]
    stage_known_requests = sum(s.get('network', {}).get('requests', 0) for x in stage_ledgers for s in x.get('sites', []))
    stage_documents = sum(x.get('total_documents', 0) for x in stage_ledgers) + interruption['stage_c']['unlogged_document_reservation']
    total_main = sum(x.get('requests', 0) for x in rows('evaluator-results.preflight-v1.jsonl')) + sum(x.get('document_attempts', 0) for x in rows('evaluator-results-v2.jsonl')) + sum(map(cost, raw)) + sum(map(cost, rows('challenge-results.jsonl'))) + sum(map(cost, rows('b0-results.jsonl'))) + interruption['main_unlogged_reserved_documents']
    known_http = sum(x.get('http_requests', 0) for x in raw) + sum(x.get('requests', 0) for x in rows('evaluator-results.preflight-v1.jsonl')) + sum(x.get('requests', 0) for x in rows('evaluator-results-v2.jsonl')) + sum(x.get('requests', 0) for x in rows('challenge-results.jsonl')) + len(diagnostic['requests']) + stage_known_requests
    matrix = []
    for target in targets:
        site = target['id']
        status = next(x for x in sites if x['id'] == site and x['method'] == 'B1')
        entry = {'id': site, 'domain': target['domain'], 'gold_status': status['gold_status']}
        for method in ['B1', 'C1']:
            r = [x for x in raw if x['id'] == site and x['method'] == method]
            s = [x for x in scored if x['id'] == site and x['method'] == method]
            entry[f'{method}_phase_rows'] = len(r)
            entry[f'{method}_G'] = next((x.get('mechanism_success') for x in s if x['mode'] == 'G'), None)
            entry[f'{method}_R'] = next((x.get('mechanism_success') for x in s if x['mode'] == 'R'), None)
            entry[f'{method}_target_hits'] = sum(x.get('gold_targets_reached', 0) for x in s)
            entry[f'{method}_503_page_records'] = sum(p['status'] == 503 for x in s for p in x.get('pages', []))
        matrix.append(entry)
    with (RUN / 'cloud-all-80-sites.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(matrix[0])); writer.writeheader(); writer.writerows(matrix)
    payload = {'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'complete': False,
        'all_sites_denominator': 80, 'verified_sites_denominator': 47, 'verified_target_denominator': 88,
        'candidate_rows': len(raw), 'complete_site_matrices': sum(x['B1_phase_rows'] == 3 and x['C1_phase_rows'] == 3 for x in matrix),
        'pending_matrix_site_ids': [x['id'] for x in matrix if x['B1_phase_rows'] < 3 or x['C1_phase_rows'] < 3],
        'methods': methods, 'main_document_charged': total_main, 'main_remaining': 3500-total_main,
        'stage_c_document_charged': stage_documents, 'stage_c_remaining': 400-stage_documents,
        'http_known_lower_bound': known_http, 'http_exact_total': None,
        'unknown_costs': ['v1 exact document attempts/XHR', 'aborted main and stage-C exact network/time', 'native model design/research/report usage'],
        'model_decision': 'Defer C2: observed upstream outage and UI/path errors cannot be repaired by ranking models; isolate validated 200-page selection failures before adding a model.',
        'adoption': 'Do not integrate current C1 as proven production Skill; final comparative decision pending B0, public dynamic tasks, and continuous Stage C.'}
    (RUN / 'cloud-failure-analysis.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
    failure_lines = ['# クラウド途中解析', '', '上流503をサイト側拒否・アルゴリズム失敗へ割り当てない。status=completedは実行終了であり、到達・有効HTML保存の成功とは別。', '']
    for method, item in methods.items():
        failure_lines += [f"- {method}: navigation error分類 {item['navigation_error_counts']}。503ページ記録 {item['captured_503_pages']}、raw HTML未保存 {item['unsaved_response_html']}。"]
    failure_lines += ['', 'C2追加は保留。通信障害・UIクリック失敗・経路上のリンク消失をモデルが直すとは仮定しない。候補選択だけの改善余地を、200かつ有効HTMLの観測へ限定して検証してから追加する。', '', '最小Skill統合案: 既存parser_flowのAST結果は起点・候補・遷移型のヒントとして保持し、予算付き非LLM実行器、凍結rules/graph/path、raw HTML/DOM、状態・費用台帳、独立G/R採点を別部品として接続する。追加ページを毎回LLMへ送らない。元Skillのデプロイ命令は実行しない。']
    (RUN / 'cloud-failure-analysis.md').write_text('\n'.join(failure_lines)+'\n')
    with zipfile.ZipFile(ROOT / 'lab-runs/structure-routing-20261115-checkpoint.zip') as z:
        original = z.read('lab-runs/structure-routing-20261115/report.md')
    if not (RUN / 'report-before-cloud.md').exists():
        (RUN / 'report-before-cloud.md').write_bytes(original)
    table = []
    for label, key in [('固定gold到達・期待型HTML保存 / 88対象', 'gold_targets_reached_unique'), ('固定gold到達サイト / 47検証可能サイト（全80も維持）', 'sites_with_gold_success'), ('G成功サイト / 実行64サイト（全80も維持）', 'mechanism_successful_sites_G'), ('R成功サイト / 実行64サイト（全80も維持）', 'mechanism_successful_sites_R'), ('G成功ページ / 試行ページ', None), ('R成功ページ / 試行ページ', None), ('URLだけなら成功に見える型・題名等不一致', 'false_successes'), ('同一URL重複 / canonical重複', None), ('raw HTML未保存のページ記録', 'unsaved_response_html'), ('文書予約 / 文書応答', None), ('記録済みHTTP requests / XHR-fetch', None)]:
        values=[]
        for m in ['B1','C1']:
            x=methods[m]
            if key: value=str(x[key])
            elif label.startswith('G成功ページ'): value=f"{x['mechanism_page_successes_G']}/{x['mechanism_page_attempts_G']}"
            elif label.startswith('R成功ページ'): value=f"{x['mechanism_page_successes_R']}/{x['mechanism_page_attempts_R']}"
            elif label.startswith('同一'): value=f"{x['exact_url_duplicates']}/{x['canonical_url_duplicates']}"
            elif label.startswith('文書'): value=f"{x['document_requests']}/{x['document_responses']}"
            else: value=f"{x['http_requests']}/{x['xhr_fetch_requests']}"
            values.append(value)
        table.append(f"| {label} | {values[0]} | {values[1]} |")
    text = f'''# サイト構造解析・巡回経路比較：クラウド継続記録（未完了）

2026-11-15 JST。**Cloudの上流接続が複数サイト・curl・GitHubで503となり停止。全80サイトの最終比較は完了していない。** 現版C1の本番Skill採用は見送る。完全比較による最終採否はB0・公開動的課題・段階C後まで保留する。

## 到達した区切りと80サイト分母

元commit `25f7f11e450623deb106c48219cb90686574bc92` のZIPをSHA256照合し展開、inventory 1,277ファイル全件一致。元60行は意味的に変更していない。独立評価v2は80サイト、verified47（88対象）、robots制限12、ブロック8、未検証13を維持した。未検証13を取得不能と断定しない。

B1/C1は計390行（各195）。64サイトで6行の処理記録、65〜67はcoldのみ、68〜80は未実行。ここで「処理記録」にはアクセス停止・中断予算予約・上流503も含み、成功64サイトという意味ではない。新規上流503はB1 50/C1 47ページ記録。全80の台帳は `cloud-all-80-sites.csv`、詳細採点は `score-cloud-outage/`。完了済み01〜10の通信を再実行していない。

## 保存証拠に対する途中採点

| 指標 | B1 | C1 |
|---|---:|---:|
{chr(10).join(table)}

全80サイト分母ではG成功11/80ずつ、R成功6/80対7/80。gold検証可能47サイトに限定したG成功は7/47ずつ、R成功5/47対6/47。未実施や環境障害を分母から消さない一方、混在した環境層の差を方式固有の性能差とは断定しない。Gは既観測グラフ再生、Rはcold本文未観測ページへの凍結規則適用。URL・200・グラフ生成だけでは成功にしない。型・題名・navigation errorなし・非空raw HTML保存を独立評価で照合した。

## 予算不具合・修正・試験

元07/C1の40予約対41応答、10/C1の20対21は元 `deviations.json` に保持。Cloudでは13/B1のcold 3対5、G 1対3、R 2対4を `cloud-deviations.json` に追加し、既存値を修正して隠していない。終了時に保留Fetchが解放される経路をlocalhostで再現。CDP文書送信前予約、非同期callback後再確認、別プロセス/入れ子iframeへの再帰制御、Network有効化とcache無効化、context終了前 `guard.seal()` によるoffline化を導入した。HTTP301/307/308の反復、並行iframe、JS遷移、入れ子、終了時保留を含む5試験が成功し、サーバ実受信数・応答数が予約内と確認。主比較/B0/連続処理のfixture8件成功、動的課題のlocalhost試験成功。既存lab suiteは81 tests/0 failures/2 skipped。

停止中に安全に差し替えられない3サイト18/20/21の途中実行を終了し、記録不能部分は各方式の残枠を全て保守的予約（計77文書）として維持した。該当方式の残工程は `interrupted_budget_reserved` で追加通信なし。正確なHTTP/文書/時間費用はunknown。段階C v2途中の証跡も保持し60文書を予約した。

## 費用と環境層

主比較・独立選定込みの文書課金/保守的予約は **{total_main}/3500、残{3500-total_main}**。内訳はv1予約742、v2 attempts189、候補max(予約,応答)548、中断未記録予約77。誤challenges preflightは文書0/robots8 requestsを保持し、本番20課題行へ数えない。既知HTTPは少なくとも{known_http:,}（診断・段階C選定を含む）。中断通信等の欠測があり正確な総数はunknown、0へ置換しない。方式別既知経過時間はB1 {methods['B1']['elapsed_ms']/1000:.3f}秒/C1 {methods['C1']['elapsed_ms']/1000:.3f}秒で、ブラウザ・待機・I/O込み。金額やCPU時間ではなく、中断分の時間も欠測。

元Windows Chrome157/Playwright1.63.0とCloud Linux Chromium151.0.7922.173を分離。Cloud内でも初期直接通信層、継承プロキシ明示層、終了前seal修正層を結果に保存。ja-JP、Asia/Tokyo、1365×900、headless、新規context/Cookie、cache/service worker無効を方式間で共通化した。Cloud表示はconnected/unrestrictedでも、実通信はrobots2サイト・GitHubで上流503となった。原因はunknown。個々のサイトのアクセス拒否へ読み替えない。

## 段階C・追加課題・モデル・楽天/au PAY

段階Cはv1選定0文書/2 requestsで接続失敗、v2途中は保存HTML/DOMを保持し60文書予約、v3は上流503の2文書で対象0。合計保守的予約 **{stage_documents}/400、残{400-stage_documents}**。100対象を固定した連続取得は未実施。10→30→100の累積・増分は全て未測定で、既存HTML読込やlocalhost100リンクを公開100件成功に数えない。次の独立選定は旧revisionを保存した別revisionで、候補bulk前に固定する。

B0の独立固定URL専用実行器は実装・ローカル試験済み、公開未実施。公開動的課題も未実施。B1/C1の主比較とは別費用に逃がさず、同一サイト/方式の残文書・操作・時間と全体3500を共有する。サイトマップB2へ比較を置き換えていない。

主比較のページ判断LLM・外部API/埋め込み・ローカル推論は0。既存Autopager監査fixtureの推論は別記録。native設計・調査・レポート作成のモデル使用量はunknown。Luna指定の調査委譲はしたが実モデルを独立検証しておらず `model_runtime_verified=false`。C2追加は保留。上流503・UIクリックtimeout・保存経路リンク消失をランキングモデルが直すとは仮定しない。候補選択の失敗だけを200/有効HTML観測から分離して、改善が見込める時のみ実施する。

楽天61とau PAY62はこのCloud層では上流503。元独立選定で商品を検証した事実をC1の経路生成成功としない。商品リンク観測からの別店舗・別カテゴリ・query同一性・空SPA検出の候補検証は未成立。固定商品IDや手書き正解経路は追加していない。

## 最小Skill統合案と判断の限界

C1は自作汎用構造ルール方式で、pinchtab-webgraph製品の実績ではない。公開候補8件のrevision・依存・配布条件・導入試験は既存 `candidate-audit` を再利用し、未実測を採用実績としない。元Skillのparser_flowは既存パーサーの静的ASTから代表経路を出す機能である。最小統合は、その出力をヒントにする非LLM実行器、凍結graph/rules/pathと出所、raw HTML/DOM・state・費用保存、独立G/R採点の接続。追加ページ毎にHTMLをLLMへ送る方式へ変更しない。元Skill内のデプロイ指示は資料として扱い実行していない。

全80の最終運用比較とB0・公開動的課題・連続100対象が欠けるため、本版を完成済みの本番Skillとして採用しない。環境障害で性能差を断定できない部分は残す。`verify.py --allow-incomplete` は証拠整合性を検査するもので、全工程完了の証明ではない。停止理由・残工程・再開コマンドはHANDOFFのCloud追記を参照。
'''
    (RUN / 'report.md').write_text(text)
    print(json.dumps({'candidate_rows': len(raw), 'main_charged': total_main, 'main_remaining':3500-total_main,'stage_c_charged':stage_documents,'http_known_lower_bound':known_http,'complete':False}))

if __name__ == '__main__':
    main()
