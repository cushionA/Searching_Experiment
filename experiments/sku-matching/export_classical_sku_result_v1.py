#!/usr/bin/env python3
"""Export the CPU classical experiments, source inputs, and reproducible code."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT/'.lab-output/sku-classical-parser-20261015-v1'
EXP = ROOT/'experiments/sku-matching'
RESULT = EXP/'results/20261015-sku-classical-result-v1'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')


def main():
    whole = LAB/'axis-alias-population-v1'
    small = LAB/'axis-alias-subset40-v1'
    full_grade = json.loads((whole/'grade.json').read_text())
    small_grade = json.loads((small/'grade.json').read_text())
    freeze = json.loads((whole/'OUTPUT_FREEZE.json').read_text())
    for run in (whole,small):
        frozen = json.loads((run/'OUTPUT_FREEZE.json').read_text())
        for name,pin in frozen['output_pins'].items():
            if sha(run/name)!=pin:raise ValueError('frozen output changed')
        for name,pin in frozen['code_pins'].items():
            if sha(EXP/name)!=pin:raise ValueError('final executed code changed')
    reqs = [json.loads(s) for s in (LAB/'population-inputs/requests.jsonl').read_text().splitlines()]
    keys = ('direction','axis_name','option_values','target_document_id','target_selected_axes')
    contexts = {json.dumps({k:r[k] for k in keys},ensure_ascii=False,sort_keys=True) for r in reqs}
    queue = [json.loads(s) for s in (whole/'residual-context-queue.jsonl').read_text().splitlines()]
    old_options = sum(len(json.loads(c)['option_values']) for c in contexts)
    new_options = sum(len(r['context']['option_values']) for r in queue)
    summary = {'schema':'classical-sku-result-v1','date_jst':'2026-10-15',
        'branch':'codex-sku-generic-model-gates-20261010',
        'base_commit':'ebf04aed9b5a747f5db8961b9c96fef7be81adef',
        'independent_test':False,'reference_kind':full_grade['reference_kind'],
        'full_dev':full_grade,'same_model_dev40':small_grade,
        'quantity_conversion_real_data_gain_proved':False,
        'typed_gain_description':'7 additional cases from whitespace/parenthesis-width normalization; no real cross-unit-conversion gain observed',
        'axis_alias_gain':0,'runtime':freeze['runtime'],'three_variant_elapsed_seconds':freeze['elapsed_seconds'],
        'pipeline_model_inference_count':0,'luna_advisory':{'transport':'delegated gpt-6-luna pseudo-API batch',
            'batch_count':1,'family_item_count':8,'used_for_final_decisions':False,
            'self_report_is_model_attestation':False,'token_or_billing_usage_available':False},
        'fallback_workload':{'original_context_count':len(contexts),'residual_context_count':len(queue),
            'baseline':'naive all-context AI dispatch, not the existing literal+Luna hybrid',
            'context_reduction_fraction':1-len(queue)/len(contexts),
            'original_all_option_count':old_options,'residual_all_option_count':new_options,
            'option_reduction_fraction':1-new_options/old_options,
            'is_estimate_of_eligible_workload_not_measured_ai_tokens_or_cost':True},
        'source_au_row_count':32325,'all_original_conditions':132136,
        'scope':{'prices_used':False,'titles_or_descriptions_used_for_final_decisions':False,
            'unknown_conditions_dropped':False,'competitor_unknown_can_certify_exclusive_match':False,
            'production_adoption_allowed':False,'new_bulk_luna_accuracy_run':False},
        'tests':{'dedicated_venv_classical':{'passed':37,'failed':0},
            'required_default_python':{'total':1525,'failures':5,'errors':49,'skips':79,
                'failures_and_errors_unchanged_from_pre_classical_baseline':True}}}
    write(RESULT.with_suffix('.json'),summary)
    text = '''古典的SKU解析 実験結果 2026-10-15 JST

既存ライブラリを優先し、jaconv/Pint/RapidFuzzの照合、Quantulum3/Sudachi/spaCyの直接抽出を検証。
SKU URL固定。価格・在庫は判定に未使用。元AU行32,325行と双方向条件132,136件を保持。
部分一致で残余を捨てず、一致/矛盾/不明を記録。全familyの一意な対応を求め、
一意の対応行以外は全て矛盾でなければ確定しない。不明は保留する。

全DEV 1,370ケース、107商品。参照はClaude Haiku5.5機械ラベル。
明確な参照1,130ケース（対応904、非対応226）、不明240。独立TESTは未実施。
方式                    確定全件   明確参照の正しい確定   誤突合   正解行の誤除外
完全一致                 248       246                  0        0
数量型付き＋表記正規化    255       253                  0        0
上記＋3種の軸名辞書       255       253                  0        0
数量型付きの対応ケース再現率は253/904=28.0%。全体の確定率255/1370=18.6%。
確定255件中2件は参照不明で正否未検証。保留1,115件。全否定の確定は0件。
旧binary採点のTN226は保留をdropと数えた値であり、非対応の証明成功ではない。
増加7件は主に括弧・空白の正規化。単位換算による実データの増加は観測されなかった。
軸名辞書は色/カラー/color/colour、サイズ/size、素材/材質/materialのみ。追加効果0。

同じDEV40（明確な対応36、不明4、非対応なし）でも18件確定、18件全て参照一致。
誤突合0、正解行の誤除外0、保留22。3方式で同結果。
旧単体NLIの0/36より対応を取り出せるが、旧literal+Lunaの35/36には届かない。
旧Lunaは単体ではなく、literalとfull-choice NLIを組み合わせた結果。
今回のSKU配列のみの処理と、商品本文を読む旧処理の設計差も残る。

CPUで3方式の全DEV照合は約36.7秒（入力ロード以後の集計）。全569値のライブラリ観測は約1.7秒。
Quantulum3(classifier無効、en_US)、SudachiPy、spaCy blank(ja)は569値全てAPI成功。
Quantulumは266数量、うちdimensionless165。spaCy Matcherは81span/71値。
これらは抽出件数であり、意味の精度ではない。既存抽出器でも靴サイズ6Wをワットと認識する。
そこで単独W/V/A/L/lを確定物理単位から外し、文字として保持した。3L=3000ml等は不明。
100cm=1m=1000mm、250g×2袋と500g×1袋の区別、寸法順序、数値の厳密比較をテスト済み。
Sudachiの附属→付属等は補助候補。①→1、色の類似語、RapidFuzzは確定根拠にしない。
Recognizers-Text日本語Dimensionは.NETが必要で、Python/JSでは同じ対応がない。
.NET用プロジェクトを準備したが、速度を優先しランタイム追加と実行は今回見送った。

最終SKU判定はLLM呼び出し0。Luna代理経路は8familyを1batchで候補提案だけ確認し、
確定・除外には一切採用していない。大規模なLuna追加精度検証は引き続き休止。
未対応文脈は4,778→2,462（48.5%減）、全候補比較数は30,284→18,037（40.4%減）。
これは今後AIへ送る候補量の試算であり、実測tokenや料金の削減率ではない。
同じDEV40の未対応文脈は543→234、全候補3,785→2,166。
旧literal+Lunaでは既に238文脈/2,174候補まで減らしていたため、既存hybridから大幅削減した結果ではない。
未対応をAIへ渡す際は、既存の商品仕様表優先処理と引用・適用範囲の確認が必要。

専用venvの新規37テスト成功。必須全体チェックは1,525件、既存の5失敗/49error継続、79skip。
新規37件のdefault Pythonでのskipは専用依存未導入によるもの。専用venvでは全件実行済み。
git diff --check成功。成果物ZIPにソース入力、全行結果、依存版、コード、失敗履歴とテストlogを収録。
branch/commitは既存のまま。新規ファイルは未commit。過去のraw・モデル・アーカイブは変更なし。
'''
    with RESULT.with_suffix('.txt').open('x',encoding='utf-8') as f:f.write(text)
    handoff = EXP/'session-handoff-20261015-classical-v1.txt'
    with handoff.open('x',encoding='utf-8') as f:
        f.write('branch codex-sku-generic-model-gates-20261010 / HEAD ebf04ae (unchanged)\n')
        f.write('Current checkpoint: experiments/sku-matching/results/20261015-sku-classical-result-v1.zip\n')
        f.write('Report: experiments/sku-matching/results/20261015-sku-classical-result-v1.txt/json\n')
        f.write('Final runs: .lab-output/sku-classical-parser-20261015-v1/axis-alias-{population,subset40}-v1\n')
        f.write('New Python files remain untracked; ZIP holds exact files. Dedicated env: .deps/sku-classical-venv.\n')
        f.write('Reinstall: python3 -m venv .deps/sku-classical-venv; install requirements-classical-v1.txt under permitted network.\n')
        f.write('Reproduce: run_classical_axis_alias_v1.py run --source population-inputs --output NEW_DIR.\n')
        f.write('Grade only after output freeze; grade_classical_population_v1.py verifies original DEV identity and frozen outputs.\n')
        f.write('Private original reference ZIPs remain in original archives; export does not add sealed TEST annotations.\n')
        f.write('Whole DEV retains every 32,325 row and 132,136 directional condition. Final gate uses no text/price/stock.\n')
        f.write('Final 253 known-correct accepts, 0 known wrong, 2 accepted reference-unknown, 1115 review.\n')
        f.write('Typed gain7 = formatting, cross-unit gain unobserved; axis alias gain0. No test-set tuning.\n')
        f.write('37 new tests pass. Required suite:1525 / fail5/error49/skip79 (existing errors unchanged).\n')
        f.write('Latest preference: installed external libraries first; speed, generality, maintainability; no case-specific rules.\n')
        f.write('One Luna advisory pseudo-API batch8 only, never adopted; bulk Luna evaluation remains paused.\n')
        f.write('Future: source-scoped specification-table facts for residual conditions, then limited AI, no unknown deletion.\n')
    files = set()
    # Walk only this new experiment tree; do not scan historical raw/venvs.
    for path in LAB.rglob('*'):
        if path.is_file() and 'untrusted-v7-trace' not in path.parts:
            files.add(path)
    for name in ('sku_classical_quantity_v1.py','sku_classical_matcher_v1.py','run_classical_sku_v1.py',
                 'run_classical_axis_alias_v1.py','grade_classical_population_v1.py','prepare_classical_population_v1.py',
                 'probe_classical_libraries_v1.py','probe_classical_real_values_v1.py','requirements-classical-v1.txt',
                 'export_classical_sku_result_v1.py','session-handoff-20261015-classical-v1.txt'):
        files.add(EXP/name)
    files.update((ROOT/'tests').glob('test_lab_sku_classical*.py'))
    subset = ROOT/'.lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8'
    for name in ('manifest.json','requests.jsonl','documents.jsonl','tasks.jsonl','row-plans.jsonl',
                 'case-plans.jsonl','membership.jsonl','selection-plan.json'):
        files.add(subset/name)
    files.update((RESULT.with_suffix('.json'),RESULT.with_suffix('.txt')))
    inventory = {'schema':'classical-sku-checkpoint-inventory-v1','files':
        {p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(files)},
        'untrusted_v7_trace_included':False,'sealed_test_annotations_included':False}
    manifest = LAB/'EXPORT_MANIFEST.json'
    write(manifest,inventory);files.add(manifest)
    archive = RESULT.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(files):z.write(p,p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or len(z.namelist())!=len(files):raise ValueError('archive verification failed')
    digest = sha(archive)
    with archive.with_suffix('.zip.sha256').open('x') as f:f.write(digest+'  '+archive.name+'\n')
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest,
                      'files':len(files),'report':str(RESULT.with_suffix('.txt'))},ensure_ascii=False))


if __name__=='__main__':main()
