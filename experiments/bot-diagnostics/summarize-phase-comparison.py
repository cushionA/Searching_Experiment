"""Summarize paired fixture phases; repeated pages are not independent samples."""
import json
import statistics
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())
median = statistics.median

def stats(values):
    return {'n': len(values), 'median_ms': median(values), 'min_ms': min(values), 'max_ms': max(values)}

summary = {'arms': {}, 'paired_block_differences': {}, 'note': '4 browser sessions per arm; 2 warm pages/session. No p-values or population claims.'}
for arm in dict.fromkeys(row['arm'] for row in rows):
    selected = [r for r in rows if r['arm'] == arm]
    warm = [p for r in selected for p in r['pages'] if p['phase'] == 'warm']
    result = {'startup': stats([r['open_ms'] for r in selected]),
              'cold': stats([r['pages'][0]['observation_ms'] for r in selected]),
              'warm': stats([p['observation_ms'] for p in warm]),
              'prepare_tab': stats([p['prepare_tab_ms'] for p in warm])}
    for phase in ('goto', 'content', 'stop'):
        result[phase] = stats([v['ms'] for p in warm for v in p['phases'] if v['phase'] == phase])
    for mode in ('separate', 'batch'):
        result[mode] = stats([v['ms'] for p in warm for v in p['extraction'] if v['mode'] == mode])
    result['extraction_savings_by_block_ms'] = [median(
        median(v['ms'] for v in p['extraction'] if v['mode'] == 'separate') -
        median(v['ms'] for v in p['extraction'] if v['mode'] == 'batch') for p in r['pages'][1:]) for r in selected]
    result['startup_phases'] = {key: stats([r['startup'][key] for r in selected]) for key in selected[0]['startup']}
    summary['arms'][arm] = result
for first, second in [('hybrid', 'playwright-reuse'), ('playwright-fresh', 'playwright-reuse')]:
    differences = []
    for block in range(1, 5):
        values = {r['arm']: median(p['observation_ms'] for p in r['pages'][1:]) for r in rows if r['block'] == block}
        differences.append(values[first] - values[second])
    summary['paired_block_differences'][first + '_minus_' + second] = {**stats(differences), 'values_ms': differences}
print(json.dumps(summary, indent=2))
