"""Verify returned GPU evidence before any machine-reference scoring."""
import argparse
from collections import Counter
import json
from pathlib import Path
import zipfile

from trial_full_scoped_condition_choices_v1 import JEV, NLI, load_inputs
from trial_scoped_condition_gliner_v1 import read, sha, write_json
from integrate_claude_description_v1 import verify_quote


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--package-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    receipt = json.loads(args.receipt.read_text())
    if sha(args.archive) != receipt['artifact_sha256'] or args.archive.stat().st_size != receipt['artifact_bytes']:
        raise ValueError('result archive SHA/size differs')
    with zipfile.ZipFile(args.archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or z.testzip() is not None:
            raise ValueError('duplicate names or ZIP CRC failure')
        for name in names:
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('unsafe result member')
        completion = json.loads(z.read('COMPLETION.json'))
        if set(names) != set(completion['files']) | {'COMPLETION.json'}:
            raise ValueError('result payload inventory differs')
        import hashlib
        for name, metadata in completion['files'].items():
            content = z.read(name)
            if len(content) != metadata['size_bytes'] or hashlib.sha256(content).hexdigest() != metadata['sha256']:
                raise ValueError('result payload SHA/size differs: ' + name)
        z.extractall(args.output_dir)
    bundle = args.package_dir / 'payload'
    if completion['input_bundle_manifest_sha256'] != sha(bundle / 'BUNDLE_MANIFEST.json'):
        raise ValueError('returned input manifest SHA differs')
    checks = {}
    for tag in ['full', 'novel']:
        input_dir, run = bundle / 'input' / tag, args.output_dir / tag
        requests, documents, bindings, manifest, _ = load_inputs(input_dir)
        request_map = {r['request_id']: r for r in requests}
        summary = json.loads((run / 'summary.json').read_text())
        if summary['request_count'] != len(requests) or summary['device'] != 'cuda' or summary['precision'] != 'fp32':
            raise ValueError('GPU request/device/precision differs')
        if summary['labels_read'] is not False or summary['synthetic_data'] is not False or summary['body_model_input'] is not False:
            raise ValueError('input/task provenance differs')
        for kind, module, expected_files in [('jev', JEV, JEV.JEV_FILE_SHA256), ('nli', NLI, NLI.NLI_FILE_SHA256)]:
            pins = summary['verified_' + kind + '_pin']
            if pins is None:
                if kind != 'nli' or summary['nli_pair_count_after_skipping'] != 0:
                    raise ValueError('GPU model pin is missing')
                continue
            if (pins['model_id'], pins['revision'], pins['parameter_count'], pins['device'], pins['precision']) != (
                    module.MODEL_ID, module.MODEL_REVISION, module.FP32_PARAMETER_COUNT, 'cuda', 'fp32'):
                raise ValueError('GPU model identity differs')
            if {name: value['sha256'] for name, value in pins['files'].items()} != expected_files:
                raise ValueError('GPU model file hashes differ')
        for name, checksum in summary['output_sha256'].items():
            if sha(run / name) != checksum:
                raise ValueError('run output SHA differs')
        for name, checksum in summary['input_sha256'].items():
            if sha(input_dir / name) != checksum:
                raise ValueError('GPU source input differs')
        for name, checksum in summary['code_sha256'].items():
            if sha(bundle / 'code' / name) != checksum:
                raise ValueError('GPU source code differs')
        states = {}
        for filename in ['jev-predictions.jsonl', 'nli-predictions.jsonl', 'fusion-predictions.jsonl', 'predictions.jsonl']:
            predictions = read(run / filename)
            if len(predictions) != len(requests) or {x['request_id'] for x in predictions} != set(request_map):
                raise ValueError('GPU predictions miss/duplicate/add a request')
            for prediction in predictions:
                request = request_map[prediction['request_id']]
                if prediction['source_request'] != request or prediction['request_sha256'] != request['request_sha256']:
                    raise ValueError('GPU source condition/context binding differs')
                if prediction['source_task_bindings'] != bindings[request['request_id']]:
                    raise ValueError('GPU source task binding differs')
                if prediction['production_adoption_allowed'] is not False or prediction['state'] not in ['support', 'unknown']:
                    raise ValueError('GPU prediction state or diagnostic flag differs')
                if prediction['state'] == 'support':
                    if prediction['winning_value'] != request['selected_value'] or not prediction['quote_refs']:
                        raise ValueError('supported condition missing selected winner/quotes')
                    if not all(verify_quote(q, request, documents) for q in prediction['quote_refs']):
                        raise ValueError('GPU support citation is not literal current target text')
            states[filename] = dict(Counter(x['state'] for x in predictions))
        if (run / 'predictions.jsonl').read_bytes() != (run / 'fusion-predictions.jsonl').read_bytes():
            raise ValueError('fusion alias bytes differ')
        gpu = summary['gpu_runtime']
        if not gpu['gpu_name'] or gpu['autocast'] or gpu['cuda_matmul_tf32'] or gpu['cudnn_tf32']:
            raise ValueError('GPU allocation or fixed FP32 arithmetic differs')
        checks[tag] = {'case_count': manifest['case_count'], 'request_count': len(requests),
                       'gpu': gpu['gpu_name'], 'states': states,
                       'jev_inference_seconds': summary['jev_runtime'].get('inference_seconds'),
                       'nli_inference_seconds': summary['nli_runtime'].get('inference_seconds'),
                       'sha_and_id_checks_passed': True}
    write_json(args.output_dir / 'verification.json', {
        'all_checks_passed': True, 'labels_read': False, 'machine_scores_not_yet_read': True,
        'archive_sha256': sha(args.archive), 'bundle_manifest_sha256': completion['input_bundle_manifest_sha256'],
        'cohorts': checks, 'production_adoption_allowed': False})
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    main()
