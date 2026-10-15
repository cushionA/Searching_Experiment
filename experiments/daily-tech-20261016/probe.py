"""Deterministic contract probe. Synthetic format cases, NOT OCR accuracy."""
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import resource
import sys
import time


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == '__main__':
    start = time.perf_counter()
    g = load('grounding', 'grounding.py')
    m = load('models', 'models.py')
    protocol = json.loads(Path('protocol.json').read_text())
    rows = []
    for case in protocol['parser_cases']:
        t = time.perf_counter()
        blocks = g.parse_blocks(case['raw'])
        actual = {'blocks': len(blocks), 'markdown': g.to_markdown(blocks, g.HEADER_FOOTER if case.get('drop_header_footer') else ())}
        if case.get('pixels'):
            actual['pixels'] = [list(b.to_pixels(*case['pixels'])) for b in blocks]
        rows.append({'id': case['id'], 'group': case['group'], 'input': case['raw'], 'expected': case['expected'], 'actual': actual,
                     'pass': actual == case['expected'], 'parsed': [b.to_dict() for b in blocks], 'seconds': time.perf_counter()-t})
    modes = []
    for case in protocol['model_cases']:
        actual = dataclasses.asdict(m.profile(case['model']))
        try:
            m.check_mode(case['model'], 'grounding')
            actual['grounding_allowed'] = True
        except ValueError:
            actual['grounding_allowed'] = False
        modes.append({'model': case['model'], 'expected': case['expected'], 'actual': actual, 'pass': case['expected'] == actual})
    print(json.dumps({'python': platform.python_version(), 'source_sha256': {p: sha(p) for p in ['grounding.py', 'models.py', 'probe.py', 'protocol.json']},
                      'parser': rows, 'models': modes, 'wall_seconds': time.perf_counter()-start,
                      'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, ensure_ascii=False, indent=2))
