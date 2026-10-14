"""Evening-only fail-fast gate. No collection, state writes or fallback."""
import json
import os
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[2] / '.agents/skills/research-repro-radar/scripts'
sys.path.insert(0, str(SCRIPTS))
import llm


def require_judge():
    if os.environ.get('RADAR_LLM', '').casefold() == 'native':
        import native_judge
        request = os.environ.get('RADAR_NATIVE_PROBE_REQUEST')
        response = os.environ.get('RADAR_NATIVE_PROBE_RESPONSE')
        if not request or not response:
            raise SystemExit('native requires a completed host probe handoff')
        req = native_judge.load_json_file(request)
        res = native_judge.load_json_file(response)
        result = native_judge.validate_response(req, res)
        if req.get('purpose') != 'probe' or result['verdict'] is None:
            raise SystemExit('native probe validation failed')
        return
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
