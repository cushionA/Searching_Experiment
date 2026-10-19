# 2026-10-20 morning source audit

Public text sources only. No fetched code is imported, installed, or executed.
`collect.py` captures bounded responses (2 MB per response), saves SHA256/bytes/time/errors,
and preserves existing records. Run it once in a fresh copy for fresh observations;
existing manifests intentionally do not refresh. `audit.py` is offline and verifies saved
payloads and selects the Python 3.12 Linux x86_64 wheel metadata without downloading it.

```sh
python3 -B experiments/daily-tech-20261020/collect.py
python3 -B experiments/daily-tech-20261020/audit.py
```

`source/selectolax-readme.md` was initially requested as README.rst and returned 404;
`selectolax-readme-fixed.md` is the corrected README.md. This is a source-path correction,
not a candidate execution repair. Source files retain upstream licenses; selectolax MIT,
Indra code Apache-2.0 and weights modified OpenRAIL-M (license text only, no weights).
MiniCPM README and Trafilatura release metadata are screening evidence, not new trials.
No candidate PoC runner is supplied. Reports distinguish upstream tests/claims from our audit.
