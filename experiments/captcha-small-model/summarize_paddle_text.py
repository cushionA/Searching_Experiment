#!/usr/bin/env python3
"""Verify every OCR prediction against a manifest and save a portable audit record."""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
from pathlib import Path

from evaluate_public_text import load_and_check_manifest, levenshtein, timing
from evaluate_paddle_text import MODELS, sha256_file, verify_resume_rows

NAMES = {
    'common-old': 'common_old (ddddocr 1.5.6)',
    'ppocr-v6-tiny': 'PP-OCRv6 Tiny',
    'ppocr-v6-small': 'PP-OCRv6 Small',
    'ppocr-v6-medium': 'PP-OCRv6 Medium',
    'ppocr-v5-en-mobile': 'en_PP-OCRv5 Mobile',
    'ppocr-v5-server': 'PP-OCRv5 Server',
}
SOURCE_NAMES = {'project_sloth_captcha_images_test': 'Project Sloth test',
                'kaggle_fournierp_captcha_version_2': 'Kaggle CAPTCHA Images v2'}


def verify_rows(path: Path, samples: list[dict]) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    if len(rows) != len(samples):
        raise ValueError(f'{path.name}: incomplete predictions')
    for row, sample in zip(rows, samples, strict=True):
        for field in ('id', 'path', 'source', 'sha256', 'label'):
            if row.get(field) != sample[field]:
                raise ValueError(f'{path.name}/{sample["id"]}: {field} mismatch')
        answer = row.get('answer')
        if not isinstance(answer, str):
            raise ValueError('prediction must be a raw string')
        checks = {'exact': answer == sample['label'],
                  'case_insensitive_exact': answer.casefold() == sample['label'].casefold(),
                  'distance': levenshtein(answer, sample['label']), 'characters': len(sample['label'])}
        for field, value in checks.items():
            if row.get(field) != value or type(row.get(field)) is not type(value):
                raise ValueError(f'{path.name}/{sample["id"]}: invalid {field}')
        latency = row.get('latency_ms')
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise ValueError('invalid prediction latency')
    return rows


def aggregate(rows: list[dict]) -> dict:
    chars = sum(r['characters'] for r in rows)
    return {'samples': len(rows), 'literal_exact_count': sum(r['exact'] for r in rows),
            'literal_exact_match': sum(r['exact'] for r in rows)/len(rows),
            'case_insensitive_exact_count': sum(r['case_insensitive_exact'] for r in rows),
            'case_insensitive_exact_match': sum(r['case_insensitive_exact'] for r in rows)/len(rows),
            'edit_distance_total': sum(r['distance'] for r in rows), 'reference_characters': chars,
            'character_error_rate': sum(r['distance'] for r in rows)/chars,
            'latency': timing([r['latency_ms'] for r in rows]),
            'failed_samples': sum(r.get('error') is not None for r in rows),
            'empty_answers': sum(r['answer'] == '' for r in rows)}


def save_compressed(path: Path, data: bytes) -> dict:
    path.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    return {'file': path.name, 'bytes': path.stat().st_size,
            'sha256': sha256_file(path), 'uncompressed_sha256': hashlib.sha256(data).hexdigest()}


def summarize(manifest_path: Path, results: Path, output: Path,
              environment_path: Path, previous_path: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    manifest, samples = load_and_check_manifest(manifest_path)
    if len(samples) != 3070:
        raise ValueError('expected 3070 samples')
    expected_sources = {'project_sloth_captcha_images_test': 2000,
                        'kaggle_fournierp_captcha_version_2': 1070}
    if dict(collections.Counter(s['source'] for s in samples)) != expected_sources:
        raise ValueError('manifest sources/counts differ from the pinned evaluation')
    manifest_sha = sha256_file(manifest_path)
    environment = json.loads(environment_path.read_text())
    previous = json.loads(previous_path.read_text())['ocr']
    all_rows, metrics = {}, {}
    for name in NAMES:
        row_path, summary_path = results/(name+'.jsonl'), results/(name+'.json')
        rows = verify_rows(row_path, samples)
        summary = json.loads(summary_path.read_text())
        if summary['completed'] != len(samples):
            raise ValueError(f'{name}: summary incomplete')
        if name in MODELS:
            verify_resume_rows(rows, samples)
            if summary['status'] != 'complete' or summary['manifest_sha256'] != manifest_sha:
                raise ValueError(f'{name}: wrong status/manifest SHA')
            if summary['model_sha256'] != MODELS[name]['sha256']:
                raise ValueError(f'{name}: wrong model SHA')
            if summary['predictions_sha256'] != sha256_file(row_path):
                raise ValueError(f'{name}: changed predictions')
        overall = aggregate(rows)
        sources = {source: aggregate([r for r in rows if r['source'] == source]) for source in SOURCE_NAMES}
        if name in MODELS:
            for field in ('literal_exact_match', 'case_insensitive_exact_match', 'character_error_rate',
                          'failed_samples', 'empty_answers', 'latency'):
                if summary[field] != overall[field]:
                    raise ValueError(f'{name}: overall aggregate mismatch for {field}')
        for source, computed in sources.items():
            fields = ['samples', 'literal_exact_match', 'case_insensitive_exact_match', 'character_error_rate', 'latency']
            if name in MODELS:
                fields += ['failed_samples', 'empty_answers']
            for field in fields:
                if summary['by_source'][source][field] != computed[field]:
                    raise ValueError(f'{name}/{source}: source aggregate mismatch for {field}')
        paired = {source: {'new_correct_baseline_wrong': sum(r['exact'] and not b['exact'] for r,b in zip(rows,all_rows['common-old']) if r['source']==source),
                           'baseline_correct_new_wrong': sum(b['exact'] and not r['exact'] for r,b in zip(rows,all_rows['common-old']) if r['source']==source)}
                  for source in SOURCE_NAMES} if name != 'common-old' else None
        metrics[name] = {'display_name': NAMES[name], 'overall': overall, 'by_source': sources,
                         'paired_vs_common_old': paired, 'prediction_sha256': sha256_file(row_path),
                         'summary_sha256': sha256_file(summary_path), 'run_summary': summary}
        all_rows[name] = rows
    baseline_reproduced = all(metrics['common-old']['by_source'][s][field] == previous['by_source'][s][field]
                              for s in SOURCE_NAMES for field in ('samples','literal_exact_count','case_insensitive_exact_count','edit_distance_total','reference_characters','character_error_rate'))
    baseline_note = ('今回のcommon_oldのソース別正解数・大小文字を無視した正解数・編集距離・参照文字数・文字誤り率は、前回の集計と全て一致した。'
                     if baseline_reproduced else '今回のcommon_oldの集計は前回と一致しなかった。metrics.jsonのby_sourceと前回の結果を照合する必要がある。')
    output.mkdir(parents=True)
    audits = [save_compressed(output/'manifest.json.gz', manifest_path.read_bytes())]
    portable = []
    for sample, i in zip(manifest['samples'], range(len(samples)), strict=True):
        predictions = {name:{k:v for k,v in rows[i].items() if k not in ('id','path','source','sha256','label')}
                       for name,rows in all_rows.items()}
        portable.append(json.dumps({'sample': sample, 'predictions': predictions}, ensure_ascii=False))
    audits.append(save_compressed(output/'predictions.jsonl.gz', ('\n'.join(portable)+'\n').encode()))
    recommended = {
        source: min(metrics, key=lambda name: (-metrics[name]['by_source'][source]['literal_exact_match'],
                                               metrics[name]['by_source'][source]['latency']['mean_ms']))
        for source in SOURCE_NAMES
    }
    result = {'schema_version': 1, 'date': '2026-10-06', 'report_timezone': 'UTC+09:00',
              'scope': 'Offline single-line OCR on public source-labeled text CAPTCHA images.',
              'manifest_sha256': manifest_sha, 'samples': len(samples), 'sources': manifest['provenance'],
              'licenses': manifest['license'], 'environment': environment,
              'baseline_previous_aggregate_reproduced': baseline_reproduced,
              'previous_per_image_identity_verified': False, 'models': metrics, 'audit_files': audits,
              'recommended_by_source': recommended,
              'verification': {'image_hashes_verified': len(samples), 'prediction_rows_verified': len(samples)*len(NAMES),
                               'aggregate_scores_recomputed': True},
              'limitations': ['Earlier per-image manifest is unavailable; equal aggregate scores do not prove byte-for-byte equality to the previous evaluation.',
                              'Original Kaggle train/validation/test assignments are unavailable; only all 1070 unique images are compared.',
                              'CPU timings are single-pass reference values; baseline startup briefly overlapped smoke/unit checks, candidates ran sequentially.',
                              'No detector, orientation classifier, confidence filtering, denoising, alphabet restriction or prediction normalization.',
                              'Recognition models are pretrained RapidOCR 3.9.2 ONNX exports; pretraining overlap with evaluation sources is not verified.']}
    (output/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    table = ['| モデル | Sloth / 2,000 | Kaggle / 1,070 | 全体 / 3,070 | 全体完全一致率 | 平均CPU ms/枚 | 推論エラー |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name,m in metrics.items():
        s=m['by_source']['project_sloth_captcha_images_test'];k=m['by_source']['kaggle_fournierp_captcha_version_2'];o=m['overall']
        table.append(f"| {NAMES[name]} | {s['literal_exact_count']} ({s['literal_exact_match']:.2%}) | {k['literal_exact_count']} ({k['literal_exact_match']:.2%}) | {o['literal_exact_count']} | {o['literal_exact_match']:.2%} | {o['latency']['mean_ms']:.2f} | {o['failed_samples']} |")
    report = '\n'.join(['# PP-OCRとcommon_oldの公開文字画像比較（2026-10-06）','',
        '公開CAPTCHA文字画像3,070枚を、画像全体を1行として認識する条件で評価した。大小文字を区別し、正解文字列と全体が一致した画像だけ正解とした。', '',
        f"今回の数字6桁中心のProject Slothでは **{NAMES[recommended['project_sloth_captcha_images_test']]}**、歪んだ英数字5桁のKaggleでは **{NAMES[recommended['kaggle_fournierp_captcha_version_2']]}** が有力。完全一致率が同じモデルはCPU時間の短いものを選んだ。この結論は今回の2データセットに対する結果。", '',
        '## 完全一致と処理時間','',*table,'',
        '## データの記録','',
        'Project Slothの公開test 2,000枚と、Kaggle CAPTCHA Images v2の重複除外後1,070枚。Kaggle ZIPには2,140画像エントリがあり、同一SHA-256の複製をまとめ、ラベルの整合性も確認した。Project Slothの正解はファイル名の最初のドットまで（先頭ゼロを保持）、Kaggleは画像ファイルのstem。画像の再保存や追加学習は行っていない。','',
        '- Project Sloth revision: `eeaf2b6ec9086645f270f7e2aaa9ea90730683de`',
        '- Manifest SHA-256: `'+manifest_sha+'`',
        '- [manifest.json.gz](manifest.json.gz): 全画像のID・出典・正解・元メンバー名と別名・画像SHA-256、入力アーカイブのSHA-256・サイズ。',
        '- [predictions.jsonl.gz](predictions.jsonl.gz): 全3,070画像×6モデルの予測・一致判定・編集距離・処理時間・失敗記録。各行には元manifestのsampleも含む。',
        '- [metrics.json](metrics.json): ソース別の完全一致・大小文字を無視した一致・文字誤り率・平均/p50/p95時間、モデルSHA-256・辞書SHA-256・設定・実行環境・検証結果。','',
        '元の画像manifestはmainにないため、前回と画像単位で同一だったことは検証できない。'+baseline_note+'Kaggleの元split割当は不明のため、元test 214枚の再集計は行っていない。','',
        '## 実行条件と再利用','',
        'Python 3.12.14、RapidOCR 3.9.2、ONNX Runtime 1.30.0。AMD EPYC 9V74を使うCloud CPU環境、CPUExecutionProvider・intra-op 2・inter-op 1。PP-OCRは各モデルを順番に実行し、先頭画像1枚のwarmupを計測から除外した。入力はRGBからBGRへ変換し、RapidOCR標準の高さ48、幅320以上の動的padding。認識時間は画像decode、入力変換、モデル前処理、推論、文字列decodeを含み、画像ファイルopenとモデルロードを除く。common_oldは既存のevaluate_public_text.pyとtext_ocr.pyを使用した。','',
        '文字検出・向き分類・信頼度閾値・ノイズ除去・文字集合の制限・予測文字列の正規化は使っていない。時間は単発参考値で、common_old測定の冒頭には短時間のスモーク/単体確認が重なった。学習時のデータ重複は未確認。','',
        'gzipはPython標準ライブラリで読める。入力を復元したら、manifestの画像SHAを必ず照合する。実行例とモデル取得方法は[README](../../README.md#pp-ocrの比較)を参照。','',
        '```python','import gzip, json','manifest = json.loads(gzip.open("manifest.json.gz", "rt", encoding="utf-8").read())',
        'with gzip.open("predictions.jsonl.gz", "rt", encoding="utf-8") as stream:',
        '    rows = [json.loads(line) for line in stream]','```',''])
    (output/'REPORT.md').write_text(report,encoding='utf-8')
    return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--results-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--environment',type=Path,required=True)
    parser.add_argument('--previous-metrics',type=Path,required=True)
    a=parser.parse_args()
    r=summarize(a.manifest,a.results_dir,a.output_dir,a.environment,a.previous_metrics)
    print(json.dumps({'verified_predictions':r['verification']['prediction_rows_verified'],
                      'baseline_reproduced':r['baseline_previous_aggregate_reproduced']}))


if __name__ == '__main__':
    main()
