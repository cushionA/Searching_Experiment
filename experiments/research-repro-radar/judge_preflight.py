"""Evening-only fail-fast gate. No collection, state writes or fallback."""
import json
import os
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / '.agents/skills/research-repro-radar/scripts'
sys.path.insert(0, str(SCRIPTS))
import llm


def require_judge():
    # Retain the recorded model, without assuming account availability.
    if os.environ.get('RADAR_LLM', 'codex').casefold() != 'codex':
        raise SystemExit('Evening radar requires RADAR_LLM=codex; no fallback')
    os.environ['RADAR_LLM'] = 'codex'
    result = llm.preflight(os.environ.get('RADAR_JUDGE_MODEL', 'gpt-6-luna'))
    if result['verdict'] is None:
        print(json.dumps({'status':'judge_unavailable', **result}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    require_judge()
