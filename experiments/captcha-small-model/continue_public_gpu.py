"""Resume the saved Kaggle version, recover outputs, and verify all four models."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--job-dir', type=Path, help='Saved job directory; defaults to RUN/gpu-eval/job-001')
    parser.add_argument('--previous-job-dir', type=Path, help='Retain three successful earlier models and merge a MoE-only repair')
    parser.add_argument('--wait-seconds', type=int, default=1800)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    from dotenv import load_dotenv
    load_dotenv(root / '.env', override=False, interpolate=False)
    path = root / '.agents/skills/kaggle-ops/scripts/kaggle_ops.py'
    spec = importlib.util.spec_from_file_location('saved_kaggle_ops', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    run = args.run.resolve()
    job_dir = args.job_dir.resolve() if args.job_dir else run / 'gpu-eval/job-001'
    record_dir = job_dir if args.job_dir else run / 'gpu-eval'
    job = helper.validate_job(helper.load(job_dir / 'job.json'))
    client = helper.api()
    result = helper.wait(client, job_dir / 'job.json', job_dir,
                         wait_seconds=args.wait_seconds)
    helper.save(record_dir / 'continuation-worker-result.json', result)
    if result['status'] not in ('complete', 'error'):
        print(json.dumps({'status': result['status'], 'ref': job['ref'],
                          'version': job['version'], 'verified': False}), flush=True)
        return 3
    if not result.get('ready_for_verification'):
        # A notebook may fail after saving partial or complete model outputs.
        result['files'] = helper.outputs(client, job, job_dir, download=True)
        helper.save(record_dir / 'terminal-recovery.json', result)
    from summarize_public_eval import summarize
    artifacts = job_dir / 'artifacts'
    if args.previous_job_dir:
        from merge_public_gpu import merge_jobs
        artifacts = merge_jobs(run, args.previous_job_dir.resolve(), job_dir)
    output = run / 'gpu-eval/summary-verified'
    if output.exists():
        raise FileExistsError(output)
    verified = summarize(run, artifacts, output)
    record = {'ref': job['ref'], 'version': job['version'],
              'kaggle_terminal_status': result['status'],
              'model_outputs_integrity': verified['integrity'],
              'verified_model_count': verified['verified_model_count'],
              'report': str(output / 'REPORT.md')}
    helper.save(record_dir / 'model-output-verification.json', record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
