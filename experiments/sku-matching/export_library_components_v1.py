#!/usr/bin/env python3
"""Export native PhraseMatcher component trials, inputs, and failure history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/sku-matching'
LAB = ROOT / '.lab-output/sku-library-components-20261015-v1'
SOURCE = ROOT / '.lab-output/sku-classical-parser-20261015-v1/population-inputs'
POP = ROOT / '.lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs'
OLD = ROOT / '.lab-output/sku-installed-library-matching-20261015-v1'
RESULT = EXP / 'results/20261015-sku-library-components-v1'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, obj):
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def verify():
    verified = {}

    def check(path, pin):
        value = sha(path)
        if value != pin:
            raise ValueError(f'Frozen input/code/output changed: {path}')
        verified[str(path.relative_to(ROOT))] = value

    for version in ('run-v1', 'run-v2', 'run-v3'):
        run = LAB / version
        freeze = read(run / 'OUTPUT_FREEZE.json')
        for name, pin in freeze['input_pins'].items():
            check(ROOT / name, pin)
        for name, pin in freeze['output_pins'].items():
            check(run / name, pin)
        for name, pin in freeze['code_pins'].items():
            path = EXP / name
            if name == 'run_library_components_v1.py' and version != 'run-v3':
                snapshot = 'executed-code-initial' if version == 'run-v1' else 'executed-code-v2'
                path = run / snapshot / name
            check(path, pin)
        if version != 'run-v1':
            grade = read(run / 'grade.json')
            check(run / 'OUTPUT_FREEZE.json', grade['prediction_freeze_sha256'])
            check(EXP / 'grade_library_components_v1.py', grade['grader_code_sha256'])
    write(LAB / 'FINAL_INTEGRITY_VERIFICATION.json', {
        'schema': 'library-components-export-verification-v1',
        'files_verified': verified, 'reference_archives_opened_by_exporter': False,
        'run_v1_is_unvalidated_history_only': True})


def main():
    verify()
    run = LAB / 'run-v3'
    grade, freeze = read(run / 'grade.json'), read(run / 'OUTPUT_FREEZE.json')
    concentration = read(run / 'COVERAGE_CONCENTRATION.json')
    base, diagnostic = (grade['variants'][key] for key in ('baseline_raw', 'native_punctuation'))
    before, after = (v['workload']['residual_contexts'] for v in (base, diagnostic))
    reduced = 100 * (1 - after / before)
    summary = {
        'schema': 'library-components-result-v1', 'date_jst': '2026-10-15',
        'branch': 'codex-sku-generic-model-gates-20261010',
        'base_commit': 'ebf04aed9b5a747f5db8961b9c96fef7be81adef',
        'final_prediction_run': str(run.relative_to(ROOT)),
        'independent_test': False, 'reference_kind': grade['reference_kind'],
        'input_index': {'source': 'original request.option_values by product/side/axis',
                        'mechanically_rebuilt': True, 'human_answer_registration_required': False,
                        'handwritten_alias_dictionary': False, 'labels_used_for_updates': False,
                        'library': 'spaCy PhraseMatcher(NORM), blank Japanese tokenizer',
                        'no_claim_of_zero_integration_maintenance': True},
        'grade': grade, 'prediction_counts': freeze['counts'],
        'coverage_concentration': concentration,
        'production_gate_changed': False, 'actual_extra_ai_reduction_deployed': False,
        'strict_mode_extra_context_reduction': 0,
        'punctuation_diagnostic_context_reduction_fraction': 1 - after / before,
        'actual_ai_tokens_cost_or_calls_measured': False,
        'sku_model_inference_count': 0, 'runtime_seconds': freeze['elapsed_seconds'],
        'test_results': {'focused_component_and_previous_library_tests': {'passed': 40, 'failed': 0},
                         'required_suite': {'total': 1527, 'failures': 4, 'errors': 66, 'skips': 60,
                                            'component_test_failures_or_errors': 0,
                                            'overall_passed': False}},
        'history': {'v1': 'not graded: insufficient original task-binding validation',
                    'v2': 'graded and preserved: independent selected field unnecessarily blocked Route A',
                    'v3': 'generic component-axis subset correction from raw input/schema inspection',
                    'v2_grade_already_read_by_root_before_v3_fix': True,
                    'fix_worker_or_reviewer_read_reference_or_grade': False,
                    'new_product_specific_rules_added': False}}
    write(RESULT.with_suffix('.json'), summary)
    text = f'''商品内選択肢からの複合属性抽出 実験結果（2026-10-15 JST）

入力の選択肢から商品ごとの一時索引を自動生成し、spaCy PhraseMatcher(NORM)で照合した。
人が答え合わせして項目登録する辞書ではない。入力が変われば再生成する。
専用の色名・カテゴリ・同義語辞書、手書き単位文法、文字列の構文解析器は追加していない。
標準日本語トークナイザーと、入力・結果を保存する実験用アダプターを使用。
照合条件・入力検証・連携コードの保守は残り、「保守不要」とは言えない。

対象は既に探索済みDEV 1,370ケース、107商品。元AU行32,325、双方向条件132,136を全て保持。
参照はClaude Haiku5.5の機械注釈（対応904、非対応226、参照不明240）。人手正解・独立TESTではない。
URL固定後のSKU属性比較。モデル推論0、本文の仕様補完・意味翻訳・新しい単位換算は今回未使用。
価格部分は前回のQuantulum3で作った凍結済みprice-freeビューを再利用した。

2方向を試験：複合選択値を相手の複数軸で説明する方向と、相手の複合欄から元の属性を取り出す方向。
全選択肢で同じ属性構造が成立し、2属性が変わる2×2の実観測組合せがある場合だけ仮説を出す。
同語が複数軸に当たる、値の重なり、複数値、残る数字・型番、必要属性の欠落は未対応に残す。
別のサイズ欄などは元の条件として保持し、分解対象と無関係だからといって捨てない。
複数の候補欄が適格なら保留。全条件支持の1行があっても未対応の競合行が残れば確定しない。

全文字トークンを説明する厳格モード：追加仮説0、既存判定・AI対象量の変化0。
spaCyが句読点と判定した未照合部分だけを除く診断モード：594条件を仮適用（支持162、矛盾432）。
うち複合→複数軸324、複数軸→複合欄270。独自の括弧・区切り文字リストは用意していない。
句読点の意味保持・適用範囲は未証明。このモードを既存の本番判定へ採用していない。

方式                   正しい一致確定  正しい非対応確定  明確参照の誤判定  保留
既存の完全一致ガード   {base['correct_accept_known']}             {base['correct_explicit_reject_known']}                0                 {base['cases']['review']}
厳格な複合分解         296             58                0                 982
句読点診断の仮適用     {diagnostic['correct_accept_known']}             {diagnostic['correct_explicit_reject_known']}                0                 {diagnostic['cases']['review']}
各方式とも一致確定10件・非対応確定24件の参照不明が別にあり、正否未検証。
診断で増えた54件は全て参照一致。ただし全54件が1商品raw-0908bb952709f362に集中している。
「ブラック（フェイクファー）」などのカラー×ファーを分けた効果。54の独立商品での成功ではない。
他商品への汎化や、未注釈条件594件それぞれの意味の精度はまだ証明できない。
参照正解904行の誤除外0。既存の既知support/conflict状態は上書きしていない。

AIに渡す未解決文脈：既存{before}→診断仮適用{after}。追加{reduced:.2f}%減の試算。
全選択肢比較量は16,000→{diagnostic['workload']['residual_option_comparisons']:,}。
残る候補行は10,947→{diagnostic['workload']['nonconflict_row_candidates']:,}。
実際のAPI呼び出し・トークン・料金は測っていない。厳格モードや現在の稼働判定は追加削減0。
今回の出力は、未説明部分と複合属性候補をAIへ示す補助、および将来の仮説検証用。

CPU実行は約{freeze['elapsed_seconds']:.2f}秒（入力読込・初期化等の範囲はfreeze参照）。Kaggleジョブなし。
新規21テスト＋既存ライブラリ19テストの計40テストが成功。
必須全体suiteは1,527件、4失敗・66error・60skipで全体成功ではない。
今回のcomponentテストの失敗/errorは0。既存の欠損成果物、依存不足、引用正規化等の問題をlogに保存。

初回v1は条件とtaskの結び付け検証不足で採点対象外。出力とコードを保存した。
v2も保存・採点後、独立したサイズ欄がRoute Aを阻む集合条件を入力から発見した。
v3では「全選択軸と同じ」から「分解した属性が選択軸に含まれる」へ一般的な契約を修正。
rootはv2の採点結果を既に見ていた。修正担当とレビュー担当は採点・注釈を読まず、
入力とコードの範囲不整合を確認して修正した。商品専用例外や参照に合わせた語彙追加ではない。
この履歴もあるため、新しい独立評価の精度とは解釈しない。

branch codex-sku-generic-model-gates-20261010、HEAD ebf04aeのまま。新規ファイルは未commit。
ZIPには全入力、元のガード、全方式の出力、索引・原文span・未対応理由、コード、履歴、test logを収録。
再実行には元repositoryのebf04aeとrequirements-classical-v1.txtの専用venvが必要。
採点用の元DEV注釈archiveは従来の場所に残し、ZIPには入れていない。封印TESTの注釈は使っていない。
'''
    with RESULT.with_suffix('.txt').open('x', encoding='utf-8') as f:
        f.write(text)
    handoff = EXP / 'session-handoff-20261015-library-components-final.txt'
    with handoff.open('x', encoding='utf-8') as f:
        f.write('branch codex-sku-generic-model-gates-20261010 / HEAD ebf04ae unchanged\n')
        f.write('Latest checkpoint: experiments/sku-matching/results/20261015-sku-library-components-v1.zip\n')
        f.write('Report same prefix .txt/.json; prediction .lab-output/sku-library-components-20261015-v1/run-v3\n')
        f.write('Input-local automatic index uses native spaCy PhraseMatcher NORM; no hand-maintained SKU aliases/grammar.\n')
        f.write('No human answer-registration or gold-driven dictionary update. Libraries and glue still require maintenance.\n')
        f.write('Strict lexical full coverage gain0. Native punctuation diagnostic only:594 new hypotheses, 350 known correct accepts vs296 baseline.\n')
        f.write('Gain54 all one product raw0908 Color x Fur; 0 known wrong/falsepositive-row-prunes, human/independent validation absent.\n')
        f.write('AI context2088->1926 (7.76% additional eligible reduction); actual tokens/cost unmeasured, production gate unchanged.\n')
        f.write('Both directions: whole compound-domain coverage, stable atomic axis set, observed2x2, exact selected native atoms, no residual/overlap/collision.\n')
        f.write('Separate independent selected attributes retained and checked through original full132136 condition bindings.\n')
        f.write('Runv1 incomplete task validation kept/notgraded; v2 blocked independentSize in RouteA; v3 uses component-axis subset.\n')
        f.write('Root already gradedv2 before source-only scope fix. Worker/reviewer did notreadgrade. History/code snapshots preserve chronology.\n')
        f.write('40 focused tests passed; required1527 fail4/error66/skip60, newcomponentfailure0; logs in newLAB.\n')
        f.write('Reproduce: .deps/sku-classical-venv/bin/python -B experiments/sku-matching/run_library_components_v1.py --output NEW_DIR\n')
        f.write('Private original DEV reference archives needed to regrade; no sealedTEST labels used/included.\n')
        f.write('No SKUmodel inference/newKagglejob; bulk Luna accuracy remains paused. Preserve existing raw/models/archives.\n')
    files = {p for p in LAB.rglob('*') if p.is_file()}
    for name in ('requests.jsonl', 'documents.jsonl', 'row-plans.jsonl', 'case-plans.jsonl', 'tasks.jsonl', 'manifest.json'):
        files.add(SOURCE / name)
    for name in ('cases.jsonl', 'products.jsonl'):
        files.add(POP / name)
    files.update(p for p in (OLD / 'price-free/selection-v1').iterdir() if p.is_file())
    files.update((OLD / 'views-price-free.json', OLD / 'price-exclusion-audit.json'))
    for name in ('library_local_phrase_components_v1.py', 'run_library_components_v1.py',
                 'grade_library_components_v1.py', 'export_library_components_v1.py',
                 'run_installed_library_selection_v1.py', 'replay_dev_nli_policies_v1.py',
                 'grade_final_test_subset_v1.py', 'grade_fresh_dev_sku_gate_v1.py',
                 'requirements-classical-v1.txt'):
        files.add(EXP / name)
    files.update((ROOT / 'tests/test_lab_sku_library_components.py',
                  ROOT / 'tests/test_lab_sku_installed_libraries.py', handoff,
                  RESULT.with_suffix('.txt'), RESULT.with_suffix('.json')))
    inventory_path = LAB / 'EXPORT_INVENTORY.json'
    inventory = {'schema': 'library-components-export-inventory-v1',
                 'base_repository_commit_required': summary['base_commit'],
                 'sealed_test_reference_included': False, 'private_reference_archives_included': False,
                 'files': {str(p.relative_to(ROOT)): {'bytes': p.stat().st_size, 'sha256': sha(p)}
                           for p in sorted(files)}}
    write(inventory_path, inventory)
    files.add(inventory_path)
    archive = RESULT.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in sorted(files):
            z.write(p, str(p.relative_to(ROOT)))
    with zipfile.ZipFile(archive) as z:
        for name, pin in inventory['files'].items():
            h, size = hashlib.sha256(), 0
            with z.open(name) as f:
                for b in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(b)
                    size += len(b)
            if h.hexdigest() != pin['sha256'] or size != pin['bytes']:
                raise ValueError(f'Archive inventory mismatch: {name}')
    checksum = sha(archive)
    with archive.with_suffix('.zip.sha256').open('x', encoding='utf-8') as f:
        f.write(f'{checksum}  {archive.name}\n')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': checksum, 'files': len(files), 'inventory_verified': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
