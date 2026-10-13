"""Read-only checks for the 2026-10-13 reinstall; never rank or complete candidates."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'experiments/research-repro-radar/runs/2026-10-13-reinstall-smoke'
STATE = ROOT / 'experiments/research-repro-radar/evening-state'
SKILL = ROOT / '.agents/skills/research-repro-radar'
sys.path.insert(0, str(SKILL / 'scripts'))
import state


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ref', required=True, help='Fetched commit to restore without credentials or mutation')
    parser.add_argument('--run', default=RUN.name)
    args = parser.parse_args()
    run = RUN.parent / args.run
    installation = json.loads((run / 'installation.json').read_text())
    expected = installation['canonical_state_sha256_before']
    actual = {p.name: digest(p.read_bytes()) for p in STATE.glob('*.json')}
    assert actual == expected
    for item in installation['files']:
        assert digest((SKILL / item['path']).read_bytes()) == item['source_sha256']
    with tempfile.TemporaryDirectory(prefix='radar-restore-') as directory:
        restored = Path(directory)
        for name, sha in expected.items():
            data = subprocess.check_output(['git', 'show', f'{args.ref}:experiments/research-repro-radar/evening-state/{name}'], cwd=ROOT)
            assert digest(data) == sha, name
            (restored / name).write_bytes(data)
        candidates = json.loads((restored / 'queue.json').read_text())['candidates']
        assert len(candidates) == len({state.candidate_key(c) for c in candidates}) == 1057
        loaded = state.load_queue(restored, dt.date(2026, 10, 13), 28)
        assert len(loaded) == 1057
        assert state.load_seen(restored) == state.load_seen(STATE)
        assert state.load_mail(restored) == state.load_mail(STATE)
    # Preserve historical manifests; inspect immutable versions rather than changing expected hashes.
    historical = json.loads((ROOT / 'experiments/research-repro-radar/runs/2026-10-13-evening/SHA256.json').read_text())
    drift = []
    for name, sha in historical.items():
        data = subprocess.check_output(['git', 'show', f'39f7dfb238565361683f240634c41f42acc107a0:{name}'], cwd=ROOT)
        assert digest(data) == sha, name
        if digest((ROOT / name).read_bytes()) != sha:
            drift.append(name)
    print(json.dumps({'result': 'pass', 'restored_ref': args.ref, 'pending': 1057,
                      'state_sha256': actual, 'source_files_matched': len(installation['files']),
                      'historical_hashes_verified_at_original_commit': len(historical),
                      'historical_paths_changed_since_original_commit': drift}, indent=2))


if __name__ == '__main__':
    main()
