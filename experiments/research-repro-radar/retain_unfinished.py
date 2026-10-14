"""Preserve unfinished evening carry items excluded by today's rank cutoff.

Run after rank_candidates and before record_run. This does not change scores or
complete an item; record_run remains the only completion remover.
"""
import argparse
import json
from pathlib import Path

def key(row):
    return str(row.get('doi') or row.get('arxiv_id') or row.get('id') or row['url']).casefold()

def retain(previous, current):
    existing = {key(row) for row in current['candidates']}
    restored = []
    for row in previous['candidates']:
        identity = key(row)
        if identity not in existing:
            current['candidates'].append(dict(row))
            existing.add(identity)
            restored.append(identity)
    return restored

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous-queue', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--ledger', required=True, type=Path)
    args = parser.parse_args()
    path = args.state_dir / 'queue.json'
    previous = json.loads(args.previous_queue.read_text())
    current = json.loads(path.read_text())
    restored = retain(previous, current)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2)+'\n')
    result = {'retained_ids': restored, 'pending': len(current['candidates']),
              'reason': 'Ranking exclusion is not a completed judgment; original carry metadata preserved.'}
    args.ledger.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
