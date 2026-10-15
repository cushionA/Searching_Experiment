#!/usr/bin/env python3
"""Verify and export frozen library-only DEV trials and advisory queues."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/sku-matching'
LAB = ROOT / '.lab-output/sku-installed-library-matching-20261015-v1'
SOURCE = ROOT / '.lab-output/sku-classical-parser-20261015-v1/population-inputs'
POP = ROOT / '.lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs'
RESULT = EXP / 'results/20261015-sku-installed-library-guards-v1'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def verify():
    verified = {}

    def check(path, expected):
        observed = sha(path)
        if observed != expected:
            raise ValueError(f'Frozen input/output/code differs: {path}')
        verified[str(path.relative_to(ROOT))] = observed

    for suffix in ('', 'price-free/'):
        run = LAB / suffix
        selection = run / 'selection-v1'
        ranking = run / 'ranking-v1'
        sf = read(selection / 'OUTPUT_FREEZE.json')
        rf = read(ranking / 'OUTPUT_FREEZE.json')
        ri = read(ranking / 'INPUT_FREEZE.json')
        for name, expected in sf['input_pins'].items():
            check(SOURCE / name, expected)
        for name, expected in sf['output_pins'].items():
            check(selection / name, expected)
        check(Path(sf['view_path']), sf['view_sha256'])
        check(EXP / 'run_installed_library_selection_v1.py', sf['code_sha256'])
        check(EXP / 'replay_dev_nli_policies_v1.py', sf['gate_code_sha256'])
        for name, pin in rf['output_files'].items():
            check(ranking / name, pin['sha256'])
        ranking_code = (EXP / 'run_installed_library_ranking_v1.py' if suffix else
                        LAB / 'executed-code-initial/run_installed_library_ranking_v1.py')
        check(ranking_code, ri['source_sha256']['ranking_code'])
        check(EXP / 'installed_library_views_v1.py', ri['source_sha256']['views_code'])
        grade = read(run / 'grade.json')
        check(selection / 'OUTPUT_FREEZE.json', grade['selection_output_freeze_sha256'])
        check(ranking / 'OUTPUT_FREEZE.json', grade['ranking_output_freeze_sha256'])
        grader_code = (EXP / 'grade_installed_libraries_v1.py' if suffix else
                       LAB / 'executed-code-initial/grade_installed_libraries_v1.py')
        check(grader_code, grade['grader_code_sha256'])
    check(POP / 'cases.jsonl', '06898908758f6a6dfe3446ee63c7688c0e68d4928dde7dbfe05a821e2ef63a09')
    check(POP / 'products.jsonl', '75353ed9c40333bef064109d8c99a0baf72c707564724ca1bd9481ccae539926')
    price = read(LAB / 'views-price-free.json')
    for name, pin in price['input_code_sha256'].items():
        check(EXP / name, pin)
    text = read(LAB / 'residual-text-v2/SUMMARY.json')
    for name, pin in text['source_pins'].items():
        check(ROOT / name, pin)
    check(EXP / 'probe_library_residual_text_v1.py', text['code_sha256'])
    queue_dir = LAB / 'price-free/helper-queue-v1'
    queue = read(queue_dir / 'MANIFEST.json')
    for name, pin in queue['source_pins_sha256'].items():
        check(ROOT / name, pin)
    for name, pin in queue['outputs'].items():
        check(queue_dir / name, pin['sha256'])
    check(EXP / 'build_library_helper_queue_v1.py', queue['queue_builder_code_sha256'])
    write(LAB / 'FINAL_INTEGRITY_VERIFICATION.json', {
        'schema': 'installed-library-guards-final-integrity-v1',
        'verified_files': verified, 'reference_archives_opened': False,
        'annotations_used_to_change_predictions': False,
        'initial_executed_code_snapshots_verified': True})


def main():
    verify()
    grade = read(LAB / 'price-free/grade.json')
    helpers = read(LAB / 'price-free/HELPER_ANALYSIS.json')
    baseline = read(LAB / 'price-free/EXISTING_BASELINE_COMPARISON.json')
    sf = read(LAB / 'price-free/selection-v1/OUTPUT_FREEZE.json')
    rf = read(LAB / 'price-free/ranking-v1/OUTPUT_FREEZE.json')
    summary = {
        'schema': 'installed-library-guards-result-v1', 'date_jst': '2026-10-15',
        'branch': 'codex-sku-generic-model-gates-20261010',
        'base_commit': 'ebf04aed9b5a747f5db8961b9c96fef7be81adef',
        'independent_test': False, 'reference_kind': grade['reference_kind'],
        'population': {'cases': 1370, 'products': 107, 'known_positive': 904,
                       'known_negative': 226, 'reference_unknown': 240,
                       'original_case_row_candidates': 32325, 'directional_conditions': 132136},
        'selection': {k: {
            'cases': v['counts']['cases'], 'correct_accept_known': v['correct_accepted_known'],
            'wrong_accept_known': len(v['wrong_accepted_known_case_ids']),
            'correct_explicit_reject_known': v['correct_explicit_reject_count'],
            'wrong_explicit_reject_known': len(v['wrong_explicit_reject_case_ids']),
            'accept_reference_unknown': len(v['accepted_unknown_reference_case_ids']),
            'reject_reference_unknown': len(v['unknown_reference_explicit_reject_case_ids']),
            'matching_rows_marked_conflict': v['pruning']['matching_rows_marked_conflict']}
            for k, v in grade['selection'].items()},
        'ranking': {k: {'counts': v['counts'], 'positive_top1_hit_rate': v['top1_position_hit_rate'],
                        'positive_top3_hit_rate': v['top3_position_hit_rate']}
                    for k, v in grade['ranking'].items()},
        'helper_workloads': helpers['catalog_workloads'],
        'existing_baseline_comparison': baseline,
        'text_probe': read(LAB / 'residual-text-v2/POLICY_EFFECT_SEPARATION.json'),
        'runtime_seconds': {'four_selection_views': sf['elapsed_seconds'],
                            'nine_ranking_variants': rf['elapsed_seconds']},
        'sku_model_inference_count': 0, 'new_bulk_luna_accuracy_run': False,
        'custom_sku_parser_used': False, 'custom_alias_dictionary_used': False,
        'handwritten_phrase_patterns': False,
        'unit_conversion_adopted_for_final_decisions': False,
        'catalog_comparison_policy_gain_not_library_gain': True,
        'price_exclusion': {'native_extractor': 'quantulum3, classifier disabled',
                            'masked_japanese_yen_spans': 15,
                            'other_currency_entities_preserved': 4,
                            'universal_price_semantics_proved': False},
        'scope': {'product_url_fixed': True, 'price_or_stock_used_as_match_evidence': False,
                  'body_used_for_final_selected_decisions': False,
                  'unknown_condition_discarded': False, 'fuzzy_ranking_emits_decisions': False,
                  'top3_permanent_pruning_authorized': False, 'production_adoption_allowed': False},
        'tests': {'dedicated_new_suite': {'passed': 19, 'failed': 0},
                  'required_suite': {'total': 1544, 'failures': 5, 'errors': 49, 'skips': 91,
                                     'same_existing_failure_and_error_groups': True}},
        'external_uploaded_report': {'reproducible_case_files_received': False,
                                    'reported_80_case_numbers_verified': False,
                                    'used_as_method_reference_only': True}}
    write(RESULT.with_suffix('.json'), summary)
    text = '''既存ライブラリによるSKU突合・ガード・補助 実測結果（2026-10-15 JST）

単体で全件を処理するには保留が多い。AIの前段の矛盾候補除外と確認順の補助が有望。
RapidFuzz、jaconv、SudachiPy/SudachiDict-core、spaCyの標準APIを使用。
専用SKUパーサー、商品ごとの条件分岐、同義語辞書、学習器は今回追加・使用していない。
新規コードは入力・既存判定器・ライブラリAPIを結ぶ実験用アダプターと保存処理。
URL固定後の選択済みSKUとau行の比較であり、商品URLを探す名寄せではない。
価格・在庫・本文は最終SKU一致の根拠に使わない。SKU判定のモデル推論は0件。

対象：既に探索済みのDEV 1,370ケース、107商品。AU行32,325、双方向条件132,136を全て保持。
参照：Claude Haiku5.5機械ラベル。対応904、非対応226、参照不明240。
人手正解・未使用TESTによる検証ではない。最良方式の選択もDEV上の比較である。

選択値比較                 正しい一致確定  正しい非対応確定  参照明確の誤判定  保留
価格除外後の文字列完全一致   296             58                0                 982
jaconv文字幅正規化          297             58                0                 981
Sudachi標準正規化           297             58                0                 981
spaCy標準正規化             298             58                0                 980
各方式の自動判定には別途34件（一致10・非対応24）の参照不明が含まれ、正否未検証。
spaCyの正しい一致確定298/904=33.0%。保留は非対応の証明と数えていない。
旧方式は選択肢集合全体の一致を要求して246件確定。今回の完全一致296件への増加50件は、
選択済みの値に対応する選択肢を確認する比較設計の効果。標準正規化の上積みは最大2件。
異なる選択肢集合だけで矛盾とはしない。軸名の対応が不明、衝突、未対応の条件は残す。
最終確定は全条件を支持した一行と、全て矛盾した競合行がそろう場合のみ。

候補順位（最終一致判定ではない）：正解のある904ケースが分母。
spaCy + RapidFuzz token_set_ratio：先頭正解867/904=95.9%、先頭3行の正解保持904/904。
Sudachi + 同scorer：先頭正解865/904=95.7%、先頭3行の正解保持904/904。
jaconv + 同scorer：先頭正解861/904=95.2%、先頭3行の正解保持895/904。
spaCyの先頭が一意な正例837件は参照一致。ただし非対応226件にも候補を提示する。
非対応のうち172件にも一意の先頭候補が出るので、「一意の高スコア」だけでは確定できない。
token_set_ratioは追加条件を無視して100点になることもある。数値・否定・付属品の証明ではない。
上位3行は確認順の補助。残りの候補を保持し、未知の競合を調べずに確定しない。

ガード用キュー：保守を優先して文字列完全一致の判定を使い、spaCy/RapidFuzzで確認順を付けた。
矛盾候補21,378/32,325=66.1%を印付けし、残る10,947行を保持。参照正解904行の誤除外0。
レビュー982ケースの未解決条件を2,088文脈に集約。全条件をAIに渡す場合の4,778文脈から56.3%減。
全選択肢比較量は30,284→16,000。実測token・料金削減ではなく送信候補量の試算。
同じDEV40では残る171文脈/1,557選択肢。以前のliteral+Lunaの238文脈/2,174選択肢より少ないが、
タスク設計が変わっており、残りをAIに渡した最終精度・実料金は今回測っていない。
専用数量パーサーを使った前回の全DEVでは2,462文脈だった。今回2,088への減少も比較設計の効果を含む。

非対称条件の補助：軸が見つからない、対応する選択肢がない条件をunknownとして理由付きで取り出す。
これは候補行との未対応条件の抽出であり、文章から非対称性の意味を完全判定するものではない。
残余本文ではspaCy PhraseMatcherに元の選択肢から自動生成したパターンを渡し、語句の所在を記録。
DEV40の未対応391要求中、説明文ヒット333・タイトルヒット148（重複あり）。
語句の存在だけでは支持・矛盾・別売・適用範囲を判定できない。missも非対応の証拠にしない。
11文書は凍結済み仕様表のみ、29文書は既存方針の全文fallback。全40文書で公開文脈と原文一致を検証。
表ありでmissした4要求も表全文を保持し、本文に戻さない。fallbackのmissは54要求。
PhraseMatcherによる追加の本文削減は0。全文から132,121文字減った差は既存の仕様表選択の効果だけ。
初回v1はホスト全文を誤使用したため無効と明記し保存。修正版v2と回帰テストを収録。

価格ノイズはQuantulum3標準のJPY認識を使って15spanだけ除外。分類モデル無効。
2P/2pをpennyと誤認する4spanは除外しない。12枚・寸法・形状などは保持。
価格除外後も単体判定と正解保持率は維持。今回検出したJPYは料金・割引表記であり、
あらゆる商品で金額を価格とみなせる証明ではない。単位換算による最終確定は今回していない。

CPU所要：4方式の選択値比較は約1.8秒、9方式の候補順位は約1.9秒（入力ロード・保存以外の範囲は各freeze参照）。
小規模なライブラリ処理だけなのでKaggleジョブは追加していない。Lunaの大規模追加精度検証も休止継続。
Pintは前回の単位換算試験で使用。Microsoft Recognizers-Text日本語Dimensionは.NET依存で今回未実行。
ユーザー提供80件報告は参考にしたが、実行用CSV/ZIP未受領のため79/80などの数値は未検証。
特に高スコア方式に独自辞書・構文分解・固定仕様判定が含まれ、今回のライブラリ単体結果とは比較しない。

専用venvの新規19テスト成功（表本文漏れ、引用span、全候補保持、一致/不明/矛盾など）。
必須全体suiteは1,544件、5失敗/49error/91skip。既存の失敗・欠損成果物・依存不足が残り、全体成功ではない。
git diff --check成功。既存branch/HEAD ebf04aeを維持、新規ファイルは未commit。
ZIPは入力、全行結果、候補キュー、凍結記録、実行コード、失敗履歴、テストlogを含む。
再実行には元repositoryのebf04aeと専用venvが必要。採点archiveは従来の場所に保持しZIPには入れていない。
次はこの保留キューに既存の本文判定を接続して最終精度と実送信量を測る。専用辞書の拡張はしない。
'''
    with RESULT.with_suffix('.txt').open('x', encoding='utf-8') as f:
        f.write(text)
    handoff = EXP / 'session-handoff-20261015-library-guards-final.txt'
    with handoff.open('x', encoding='utf-8') as f:
        f.write('branch codex-sku-generic-model-gates-20261010 / HEAD ebf04ae unchanged\n')
        f.write('Latest checkpoint: experiments/sku-matching/results/20261015-sku-installed-library-guards-v1.zip\n')
        f.write('Report: same prefix .txt/.json; current run .lab-output/sku-installed-library-matching-20261015-v1\n')
        f.write('User: prioritize installed libraries, speed/generality/maintenance; no bespoke SKU parser or expanding dictionary.\n')
        f.write('Use standalone only where supported; otherwise guards/auxiliary to reduce AI. Bulk Luna accuracy still paused.\n')
        f.write('Final features: price-free/{selection-v1,ranking-v1}; helper-queue-v1 raw catalogue gate + spaCy/token_set priority.\n')
        f.write('Raw:296 known accepts+58 explicit rejects, 0 known wrong, 34 auto reference-unknown, review982.\n')
        f.write('spaCy:298 known accepts+58 explicit rejects, review980. Gain2 beyond raw; +50 raw gain is task-design change.\n')
        f.write('Guard21378/32325 conflicts, 904 matching rows kept; contexts2088/4778, estimates not actual tokens/cost.\n')
        f.write('Positive904 top3 retention904 with spaCy/Sudachi token_set; do not permanently prune rest or certify absence.\n')
        f.write('All original rows and132136 directional conditions retained. Queue has all backfill and unknown competitor checks.\n')
        f.write('Text v1 INVALID original body source; v2 uses selection.text, validates public context equality. Never body-fallback on table miss.\n')
        f.write('333/391 lexical hits only. PhraseMatcher extra text reduction0; previous table-first policy accounts for all char savings.\n')
        f.write('Model SKU inference0. No new neural model or dedicated alias dictionary/grammar. CPU native calls only.\n')
        f.write('Dedicated19 tests pass; required1544 fail5/error49/skip91 existing groups; git diff --check clean.\n')
        f.write('Prediction reproduction: run_installed_library_selection_v1.py --source POPULATION_INPUTS --views views-price-free.json --output NEW_DIR\n')
        f.write('Ranking: run_installed_library_ranking_v1.py --source ORIGINAL_CASE_PRODUCT_DIR --views views-price-free-ranking.json --view-modes width_jaconv sudachi_norm spacy_norm --output NEW_DIR\n')
        f.write('Original inputs are in ZIP at prior .lab-output paths. Gate uses repository base code; private original DEV reference archives needed to regrade.\n')
        f.write('Reinstall dedicated .deps/sku-classical-venv from requirements-classical-v1.txt only if needed; no new network work was required.\n')
        f.write('External80 report method reference only; its case CSV/ZIP not received, reported accuracy unverified.\n')
        f.write('Next: evaluate existing body AI on residual queue and actual sent tokens, then independent evaluation before deployment.\n')
    files = {p for p in LAB.rglob('*') if p.is_file()}
    for name in ('requests.jsonl', 'documents.jsonl', 'row-plans.jsonl', 'case-plans.jsonl', 'tasks.jsonl', 'manifest.json'):
        files.add(SOURCE / name)
    for name in ('cases.jsonl', 'products.jsonl'):
        files.add(POP / name)
    spec = ROOT / '.lab-output/sku-v7-small-nli-spec-first-20261014-v1'
    for name in ('requests.jsonl', 'documents.jsonl', 'case-plans.jsonl', 'spec-table-selections.jsonl', 'SPEC_TABLE_POLICY.json'):
        files.add(spec / 'host' / name)
    files.add(spec / 'public-bundle/contexts.jsonl')
    for name in ('installed_library_views_v1.py', 'prepare_library_price_free_views_v1.py',
                 'run_installed_library_selection_v1.py', 'run_installed_library_ranking_v1.py',
                 'grade_installed_libraries_v1.py', 'analyze_library_helpers_v1.py',
                 'probe_library_residual_text_v1.py', 'build_library_helper_queue_v1.py',
                 'replay_dev_nli_policies_v1.py', 'grade_final_test_subset_v1.py',
                 'grade_fresh_dev_sku_gate_v1.py', 'requirements-classical-v1.txt',
                 'export_installed_library_guards_v1.py'):
        files.add(EXP / name)
    files.update((ROOT / 'tests/test_lab_sku_installed_libraries.py', handoff,
                  RESULT.with_suffix('.txt'), RESULT.with_suffix('.json')))
    inventory = {'schema': 'installed-library-guards-export-inventory-v1',
                 'base_repository_commit_required': summary['base_commit'],
                 'sealed_test_reference_included': False,
                 'reference_archives_included': False,
                 'files': {str(p.relative_to(ROOT)): {'bytes': p.stat().st_size, 'sha256': sha(p)}
                           for p in sorted(files)}}
    inventory_path = LAB / 'EXPORT_INVENTORY.json'
    write(inventory_path, inventory)
    files.add(inventory_path)
    archive = RESULT.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in sorted(files):
            z.write(p, str(p.relative_to(ROOT)))
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise ValueError(f'Bad archive CRC: {bad}')
    archive_sha = sha(archive)
    with archive.with_suffix('.zip.sha256').open('x', encoding='utf-8') as f:
        f.write(f'{archive_sha}  {archive.name}\n')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': archive_sha, 'files': len(files), 'crc_valid': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
