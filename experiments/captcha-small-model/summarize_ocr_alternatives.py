#!/usr/bin/env python3
"""Verify additional OCR ablations and archive sample-level evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from evaluate_public_text import load_and_check_manifest
from summarize_paddle_text import aggregate, save_compressed, sha256_file, verify_rows, SOURCE_NAMES

NAMES = {
    'common-old': 'common_old（元画像）',
    'ppocr-v6-small': 'PP-OCRv6 Small（元画像）',
    'dddd-common-beta': 'ddddocr common / beta=True',
    'parseq-tiny': 'PARSeq Tiny',
    'easyocr-en-g2': 'EasyOCR English G2 / 標準文字集合',
    'easyocr-en-g2-alnum': 'EasyOCR English G2 / 固定ASCII英数字',
    'captcha-crnn-finetuned': 'Graf-J CAPTCHA CRNN Finetuned',
    'common-old-autocontrast': 'common_old + autocontrast 1%',
    'common-old-otsu': 'common_old + Otsu二値化',
}


def agreement(left: list[dict], right: list[dict]) -> dict:
    """Accept identical raw answers; correctness never selects the answer."""
    if len(left) != len(right):
        raise ValueError('agreement inputs have different lengths')
    accepted = []
    for a, b in zip(left, right, strict=True):
        for key in ('id', 'sha256', 'label', 'source'):
            if a[key] != b[key]:
                raise ValueError(f'agreement input {key} mismatch')
        if a.get('error') is None and b.get('error') is None and a['answer'] != '' and a['answer'] == b['answer']:
            accepted.append(a)
    correct = sum(row['answer'] == row['label'] for row in accepted)
    total = len(left)
    return {'total_samples': total, 'accepted_samples': len(accepted),
            'abstained_samples': total - len(accepted), 'coverage': len(accepted)/total if total else None,
            'accepted_correct': correct, 'accepted_incorrect': len(accepted) - correct,
            'accepted_exact_match': correct/len(accepted) if accepted else None,
            'correct_accepted_fraction_of_all': correct/total if total else None}


def summarize(manifest_path: Path, results: Path, output: Path, environment: Path,
              previous: Path, standard_easyocr_source: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    manifest, samples = load_and_check_manifest(manifest_path.resolve())
    sources = dict(Counter(s['source'] for s in samples))
    if sources != {'project_sloth_captcha_images_test': 2000, 'kaggle_fournierp_captcha_version_2': 1070}:
        raise ValueError('expected the pinned 3070-image evaluation')
    manifest_sha = sha256_file(manifest_path)
    previous_result = json.loads(previous.read_text())
    if previous_result['manifest_sha256'] != manifest_sha:
        raise ValueError('baseline manifest differs')
    rows_by_model, metrics = {}, {}
    snapshots = {}
    code_paths = {
        'common-old': Path(__file__).with_name('evaluate_public_text.py'),
        'ppocr-v6-small': Path(__file__).with_name('evaluate_paddle_text.py'),
        'dddd-common-beta': Path(__file__).with_name('evaluate_dddd_beta.py'),
        'parseq-tiny': Path(__file__).with_name('evaluate_parseq_text.py'),
        'easyocr-en-g2': standard_easyocr_source,
        'easyocr-en-g2-alnum': Path(__file__).with_name('evaluate_easyocr_text.py'),
        'captcha-crnn-finetuned': Path(__file__).with_name('evaluate_crnn_text.py'),
        'common-old-autocontrast': Path(__file__).with_name('evaluate_preprocessed_text.py'),
        'common-old-otsu': Path(__file__).with_name('evaluate_preprocessed_text.py'),
    }
    for name in NAMES:
        row_path, summary_path = results/(name+'.jsonl'), results/(name+'.json')
        rows = verify_rows(row_path, samples)
        summary = json.loads(summary_path.read_text())
        if summary['completed'] != len(samples):
            raise ValueError(f'{name}: incomplete run')
        prediction_sha = sha256_file(row_path)
        if name in ('common-old', 'ppocr-v6-small'):
            if previous_result['models'][name]['prediction_sha256'] != prediction_sha:
                raise ValueError(f'{name}: baseline predictions changed')
        else:
            if summary['status'] != 'complete' or summary['manifest_sha256'] != manifest_sha:
                raise ValueError(f'{name}: wrong status or manifest')
            if summary['predictions_sha256'] != prediction_sha:
                raise ValueError(f'{name}: predictions changed')
        code = code_paths[name].read_bytes()
        code_sha = hashlib.sha256(code).hexdigest()
        identity = summary.get('evaluation_identity', {})
        expected_code_sha = summary.get('evaluator_sha256', identity.get('evaluator_sha256'))
        if expected_code_sha is not None and expected_code_sha != code_sha:
            raise ValueError(f'{name}: evaluator source changed; supply the original snapshot')
        hash_verified = expected_code_sha is not None
        snapshots[name] = {'filename': code_paths[name].name, 'sha256': code_sha, 'source': code.decode(),
                           'recorded_evaluator_hash_verified': hash_verified,
                           'provenance': 'Matches the recorded run hash' if hash_verified else
                                         'Current repository source; no evaluator hash was recorded in the run'}
        overall = aggregate(rows)
        by_source = {source: aggregate([r for r in rows if r['source'] == source]) for source in sources}
        for key in ('literal_exact_match', 'case_insensitive_exact_match', 'character_error_rate', 'latency',
                    'failed_samples', 'empty_answers'):
            if key in summary and summary[key] != overall[key]:
                raise ValueError(f'{name}: overall {key} differs from recomputed score')
        for source, computed in by_source.items():
            for key in ('samples', 'literal_exact_match', 'case_insensitive_exact_match', 'character_error_rate', 'latency'):
                if summary['by_source'][source][key] != computed[key]:
                    raise ValueError(f'{name}/{source}: {key} differs from recomputed score')
        paired = {source: {
            'new_correct_baseline_wrong': sum(r['exact'] and not b['exact'] for r, b in zip(rows, rows_by_model['common-old'], strict=True) if r['source'] == source),
            'baseline_correct_new_wrong': sum(b['exact'] and not r['exact'] for r, b in zip(rows, rows_by_model['common-old'], strict=True) if r['source'] == source),
        } for source in sources} if name != 'common-old' else None
        metrics[name] = {'display_name': NAMES[name], 'overall': overall, 'by_source': by_source,
                         'paired_vs_common_old': paired, 'prediction_sha256': prediction_sha,
                         'summary_sha256': sha256_file(summary_path), 'run_summary': summary,
                         'evaluator_sha256': code_sha, 'recorded_evaluator_hash_verified': hash_verified}
        rows_by_model[name] = rows
    consensus = {'rule': 'Accept a nonempty raw string only when common_old and PP-OCRv6 Small agree; otherwise abstain. No label input or confidence tuning.',
                 'overall': agreement(rows_by_model['common-old'], rows_by_model['ppocr-v6-small']),
                 'by_source': {source: agreement([r for r in rows_by_model['common-old'] if r['source'] == source],
                                                [r for r in rows_by_model['ppocr-v6-small'] if r['source'] == source]) for source in sources}}
    portable = []
    for index, sample in enumerate(manifest['samples']):
        predictions = {name: {key: value for key, value in rows[index].items() if key not in ('id', 'path', 'source', 'sha256', 'label')}
                       for name, rows in rows_by_model.items()}
        portable.append(json.dumps({'sample': sample, 'predictions': predictions}, ensure_ascii=False))
    for filename in ('text_ocr.py', 'evaluate_public_text.py', 'summarize_paddle_text.py', 'summarize_ocr_alternatives.py'):
        data = Path(__file__).with_name(filename).read_bytes()
        snapshots[filename] = {'filename': filename, 'sha256': hashlib.sha256(data).hexdigest(), 'source': data.decode()}
    output.mkdir(parents=True)
    audits = [save_compressed(output/'manifest.json.gz', manifest_path.read_bytes()),
              save_compressed(output/'predictions.jsonl.gz', ('\n'.join(portable)+'\n').encode()),
              save_compressed(output/'evaluator-sources.json.gz', json.dumps(snapshots, ensure_ascii=False).encode())]
    result = {'schema_version': 1, 'date': '2026-10-06', 'report_timezone': 'UTC+09:00',
              'scope': 'Exploratory offline pretrained OCR and fixed preprocessing comparison; literal whole-string exact match.',
              'samples': len(samples), 'manifest_sha256': manifest_sha, 'sources': manifest['provenance'],
              'licenses': manifest['license'], 'environment': json.loads(environment.read_text()),
              'models': metrics, 'agreement_with_abstention': consensus, 'audit_files': audits,
              'verification': {'image_hashes_verified': len(samples), 'prediction_rows_verified': len(samples)*len(NAMES),
                               'scores_recomputed': True,
                               'recorded_evaluator_hash_verified_by_model': {
                                   name: model['recorded_evaluator_hash_verified'] for name, model in metrics.items()}},
              'limitations': ['Pretraining overlap with these public evaluation sources is unknown for all pretrained models.',
                              'This is exploratory comparison on the same already-inspected evaluation data, not a new untouched holdout.',
                              'Original Kaggle split assignments and the earlier per-image manifest remain unavailable.',
                              'Single-pass CPU timings with batch size1 are reference values; no GPU or repeated speed trials.',
                              'Agreement precision applies only to accepted images; abstentions are included in coverage and the all-image denominator.']}
    (output/'metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    table = ['| モデル / 設定 | 数字 Sloth / 2,000 | 英数字 Kaggle / 1,070 | 平均CPU ms/枚 | エラー |',
             '|---|---:|---:|---:|---:|']
    for name, model in metrics.items():
        s, k = (model['by_source'][source] for source in SOURCE_NAMES)
        table.append(f"| {NAMES[name]} | {s['literal_exact_count']} ({s['literal_exact_match']:.2%}) | {k['literal_exact_count']} ({k['literal_exact_match']:.2%}) | {model['overall']['latency']['mean_ms']:.2f} | {model['overall']['failed_samples']} |")
    consensus_table = ['| データ | 採用枚数 / 全枚数 | カバー率 | 採用分の完全一致 | 採用した誤答 |', '|---|---:|---:|---:|---:|']
    for source, score in consensus['by_source'].items():
        consensus_table.append(f"| {SOURCE_NAMES[source]} | {score['accepted_samples']} / {score['total_samples']} | {score['coverage']:.2%} | {score['accepted_exact_match']:.2%} | {score['accepted_incorrect']} |")
    best = {source: max(metrics, key=lambda name: metrics[name]['by_source'][source]['literal_exact_match']) for source in sources}
    report = '\n'.join(['# 追加OCRモデルと前処理の比較（2026-10-06）', '',
        '同じ公開CAPTCHA文字画像3,070枚を使い、大小文字を区別した文字列全体の完全一致を再集計した。正解は採点にだけ使い、画像前処理・予測・一致による採用判定には渡していない。', '',
        f"今回の最高値は数字Slothで **{NAMES[best['project_sloth_captcha_images_test']]}**、英数字Kaggleで **{NAMES[best['kaggle_fournierp_captcha_version_2']]}**。この順位は今回の2ソースに対する結果であり、事前学習時の重複は未確認。", '',
        '## モデルと前処理', '', *table, '',
        '`common_old`はddddocr 1.5.6でbeta=Falseを選んだモデル名。beta=Trueは別のcommon.onnxであり、名前だけで精度の優劣は決まらない。', '',
        'CRNNはGraf-J/captcha-crnn-finetuned、固定revision `8ca7bfadc2608b007b5cafe20a7d0c29888a5cbb`。約14.3MBのsafetensorsをstrict loadし、公開モデルのCNN+BiLSTMをローカルPyTorchで再現。公開processor通りL画像150×40、PIL既定bicubic、[0,1]tensor、blank0のgreedy CTCで認識した。Transformersや未確認のremote codeは実行していない。モデルカード記載の学習元はhammer888/captcha-dataとPython Captcha Libraryによる生成画像。カードの精度値と今回の精度は分けて扱う。', '',
        'PARSeq Tinyは公式commitと重みを固定し、標準RGB32×128入力・autoregressive decode・refine1を使用。文字の正規化やcharset adapterは省いた。EasyOCRはEnglish G2認識器だけを使い、画像全体を1行として認識。標準文字集合と、全画像共通のASCII英数字62文字を許可する設定を別々に記録した。英数字制限はCTC decode前の固定文字集合制約であり、答えから記号を削る後処理ではない。低confidence時の標準コントラスト再試行も含む。', '',
        '追加前処理はcommon_oldに対して、RGB→L→PIL autocontrast(cutoff=1)と、RGB→L→OpenCV Otsu二値化の2種類。サイズを変えず、形態処理や線除去は行わない。各設定を全画像に固定して適用し、正解を見て画像ごとに結果を選んでいない。内部のリサイズ・入力tensor正規化は各モデルの標準処理のまま。前処理も含めて時間を計測した。', '',
        '## 2モデル一致時だけ採用する方法', '',
        'common_oldとPP-OCRv6 Smallの生の予測が完全一致した非空の文字列だけ採用し、それ以外は保留する固定ルール。正解は一致判定に使わない。', '', *consensus_table, '',
        '**採用分の一致率は全画像を解いた精度ではない。** 保留があるためカバー率を併記した。2つの認識器を動かす時間が必要で、採用分にも誤答が残る。', '',
        '## データ・再現記録', '',
        '- [manifest.json.gz](manifest.json.gz): 3,070枚の出典、正解、画像SHA-256、アーカイブと別名。前のPP-OCR評価と同じmanifest。',
        '- [predictions.jsonl.gz](predictions.jsonl.gz): 全画像×9モデル/設定の生予測、一致判定、編集距離、時間、エラー。',
        '- [metrics.json](metrics.json): ソース別集計、前処理による改善/悪化枚数、モデルと評価コードのSHA-256、実行条件・環境・検証結果。',
        '- [evaluator-sources.json.gz](evaluator-sources.json.gz): 記録済みの評価コードSHAと照合したスナップショット。標準EasyOCRは英数字制限を追加する前の版も保存。common_oldの元実行には評価コードSHAの記録がないため、その項目だけは現在のリポジトリ実装を収録した。', '',
        'Sloth公開test2,000枚とKaggle重複除外後1,070枚を使用。元のKaggle split割当は不明。このデータは既に比較に使っており、EasyOCRの英数字設定も標準設定の失敗例を見た後の追加比較である。独立した新しいholdoutの評価とは扱わない。CPUは2 intra-op / 1 inter-op、1画像ずつfp32、モデルごとに順番に実行。速度は単発参考値。', '',
        '入力manifest復元、固定モデル取得、実行方法は[README](../../README.md#追加ocrと前処理)を参照。入力画像や元アーカイブは変更していない。', ''])
    (output/'REPORT.md').write_text(report, encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'results', 'output', 'environment', 'previous', 'standard-easyocr-source'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    summarize(args.manifest, args.results, args.output, args.environment, args.previous, args.standard_easyocr_source)


if __name__ == '__main__':
    main()
