import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    rows = [json.loads(line) for line in (run / 'results.jsonl').read_text(encoding='utf-8').splitlines() if line]
    keys = [(row['id'], row['method'], row['phase']) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate_result_tuple')
    destination = run / 'score'
    destination.mkdir(exist_ok=True)
    converted = []
    for original in rows:
        row = json.loads(json.dumps(original))
        cost = row['cost']
        cost['http_requests'] = cost['html_http_requests']
        cost['browser_ms'] = cost['acquire_ms']
        converted.append(row)
    scoring_input = destination / 'normalized-results.jsonl'
    scoring_input.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in converted), encoding='utf-8')
    subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('sitemap-score.py')), str(scoring_input),
                    '--run-root', str(run), '--evaluation-private', str(run / 'evaluation-private'), '--out-dir', str(destination)], check=True)
    gold = json.loads((run / 'evaluation-private/frozen-gold.json').read_text(encoding='utf-8'))
    methods = list(dict.fromkeys(row['method'] for row in rows))
    summary = {}
    for method in methods:
        items = [row for row in rows if row['method'] == method]
        reached = set()
        for row in items:
            target_urls = {target['url'] for target in gold['sites'][row['id']]['targets'] if target['candidate_valid']}
            for obs in row['observations'][1:]:
                if obs.get('page_valid') and obs.get('requested_url') in target_urls:
                    reached.add((row['id'], obs['requested_url']))
        summary[method] = {'fixed_target_reached_candidate_flag': len(reached), 'fixed_target_denominator': 6,
                           'sites_executed': len(items), 'document_requests': sum(row['cost']['document_requests'] for row in items),
                           'all_http_requests': sum(row['cost']['all_http_requests'] for row in items),
                           'xhr_fetch': sum(row['cost']['xhr_fetch'] for row in items),
                           'warm_local_model_calls': sum(row['local_inference']['calls'] for row in items),
                           'warm_external_llm_calls': sum(row['external_llm_calls'] for row in items)}
    output = {'results_sha256': hashlib.sha256((run / 'results.jsonl').read_bytes()).hexdigest(),
              'candidate_flags_are_not_independent_html_validation': True, 'independent_html_scores': 'score/score.json',
              'methods': summary, 'native_design_report_model_usage': None}
    (run / 'comparison.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
