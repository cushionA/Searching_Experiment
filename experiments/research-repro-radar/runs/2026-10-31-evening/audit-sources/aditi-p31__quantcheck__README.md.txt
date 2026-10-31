# quantcheck

Functional acceptance testing for quantized LLM artifacts (GGUF files served
through Ollama), and the full audit dataset behind the paper:

> **Broken on Arrival: Non-Functional Quantized LLM Artifacts in Public Model
> Registries and How to Catch Them.** Aditi Patodiya, 2026.
> [arXiv:2609.05881](https://arxiv.org/abs/2609.05881)

Release `v3.4` adds a template test of the two TheBloke deepseek-coder-6.7B
community files on one CUDA pod (`rerun-2026-10/results-tpl/`), which shows
that their low census scores come from a missing chat template, not from the
backend, and corrects the documentation that described the census template
setup for community files (see [`RELEASE_NOTES_v3.4.md`](RELEASE_NOTES_v3.4.md)
and the finding below). Release `v3.3` accompanies the revised paper, which
replaces version 1 of the preprint as arXiv version 2. It adds the per-task
results of the 24 screen-tuning configurations, so that no analysis value is
copied from a committed file any more; what still rests on inputs not in the
release is read from released extractions or marked in `PROVENANCE.md` (see
[`RELEASE_NOTES_v3.3.md`](RELEASE_NOTES_v3.3.md)); `v3.2.1` differed from
`v3.2` in documentation only ([`RELEASE_NOTES_v3.2.1.md`](RELEASE_NOTES_v3.2.1.md)). Version 1 had a
different title ("Broken on Arrival: Silently Defective LLM Artifacts in
Public Model Registries and How to Catch Them") and a defect diagnosis that
the revision corrects; see the corrections under [Findings](#findings).

Release `v3.2` keeps the data and analysis of v3.1 and adds the population
bounds of the miss audit, the evaluation of the proposed acceptance gate on
the census, and corrected descriptions of the first-version audit sample and
of the repetition statistic (see [`RELEASE_NOTES_v3.2.md`](RELEASE_NOTES_v3.2.md)).
Release `v3.1` kept the v3.0 data and changed the analysis in one respect:
stage-two baselines are grouped by model variant (the registry tag without
its quantization suffix) instead of by an alias-normalized model and size.
It also added miss-audit certificates, the per-artifact file digests of the
census, a provenance map ([`PROVENANCE.md`](PROVENANCE.md)), and analysis
scripts that run from a clean checkout. See
[Reproduce the paper's numbers](#reproduce-the-papers-numbers).

## Findings

- **Census.** 327 code-capable artifacts were executed (313 quantized): all
  305 tags at or under 8 GB in 15 model lines of the official Ollama library
  (291 quantized, 14 fp16, of 35 model variants), and 22 popular
  Hugging Face community files. Each artifact ran a 15-task HumanEval+ screen.
- **Seven non-functional official tags.** Seven tags (2.3%, Wilson 95% CI
  1.1–4.7%) are suspects on the screen and are confirmed on all 164
  HumanEval+ tasks: six solve none and `llama3.2:1b-instruct-q3_K_S` solves
  6. All seven are q2_K to q3_K_L tags of models with at most 4B parameters:
  four `qwen2.5-coder:3b-instruct` tags (q2_K, q3_K_S, q3_K_M, q3_K_L),
  `phi3.5:3.8b-mini-instruct-q2_K`, and two `llama3.2:1b-instruct` tags
  (q2_K, q3_K_S).
- **No file defects.** Five of the seven were rebuilt from the original
  checkpoints with llama.cpp. The four Qwen2.5-Coder-3B tags match our
  uncalibrated rebuild in all 434 tensors; the phi3.5 tag matches in all 197
  tensors once its 32 `attn_qkv` tensors use `Q2_K` (an older llama.cpp type
  mix). Each of the five files is an exact, uncalibrated llama.cpp
  quantization of the released weights. The llama3.2-1B checkpoint is
  licence-gated and was not rebuilt, so its two tags are non-functional but
  unattributed.
- **Calibration rescues most of them.** Adding only an importance matrix
  (imatrix) to the same weights, toolchain and tensor mix takes Qwen q2_K
  from 0 to 106–113 of 164 tasks, q3_K_M to 76–89 and q3_K_L to 80–86. Qwen
  q3_K_S stays weak under every calibration (13–30). For phi3.5, llama.cpp's
  current tensor mix alone lifts the uncalibrated build from 0 to 21, and an
  imatrix to 37; a third-party imatrix build at the tag's own mix scores 34.
- **The recipe alone does not predict failure.** The library's q2_K to
  q3_K_L tags of Qwen2.5-Coder-1.5B are also tensor-identical to our
  uncalibrated rebuild (all 338 tensors) and stay functional (13–15 of 15).
- **Two community files fail for want of a chat template (v3.4).** TheBloke's
  deepseek-coder-6.7B files at Q3_K_M and Q4_K_M score 5 and 6 of 15 in the
  census, where Hugging Face community files ran with no `TEMPLATE` line
  (`harness/hf_audit.py` copies a template only for the families in its donor
  table, and the community entries' family is `mixed`; `template_donor` is null
  on all 22). The files carry no `tokenizer.chat_template`; Ollama then selects
  no template (`ollama show --template` prints `{{ .Prompt }}`) and its bundled
  llama-server formats each request with its default ChatML template, which
  differs from the model's documented instruction format: 28 of their 30
  answers run on into invented chat turns. On one RTX 4090 pod with the same
  Ollama release (`rerun-2026-10/results-tpl/`), the same files with no
  template reproduce the census screen answers (5 and 6 of 15; all 30 answers
  identical) and solve 16 and 18 of 164; with the deepseek-coder instruction
  template they score 13 and 15 of 15 and 98 and 101 of 164, as they do on
  Metal (13 and 15, where
  `harness/mac_night.py` supplied that template). The paper's arXiv v2 text
  called the pair backend-dependent; this release corrects the census template
  documentation (see `RELEASE_NOTES_v3.4.md`), and the paper's next version (the
  workshop submission and arXiv v3) carries the template-dependent verdict.
  `analysis/findings.json` (superseded, v1) still lists the pair as
  `BACKEND_DEPENDENT`.
- **Seven is a lower bound.** All 12 deepseek-coder-1.3B tags, fp16
  included, score 1 to 6 of 15, so a group baseline cannot judge them; the
  group is not attributed.
- **The screen.** None of 66 sampled tags it passed is non-functional: 64 solve at least 17
  distinct HumanEval+ tasks on one file and backend (49-task audit, plus the 15 screen tasks where the
  file digest matches), and the other two solve 65 and 44 of 164 in a rerun on the census files
  (`rerun-2026-10/miss_audit/`, `rerun-2026-10/results-v1cases/`). Screening costs about 30 s of
  generation per tag (median, one RTX 4090), excluding download and scoring. A WikiText-2 perplexity
  threshold, set after the fact, flags the five failures we measured.
- **The v1 defect diagnosis is corrected.** Version 1 reported the four
  Qwen2.5-Coder-3B tags and the phi3.5 q2_K tag as "silently defective"
  conversions. Its referees were chosen by distributor, not by recipe: they
  were importance-matrix builds from bartowski, and for q3_K_S and q3_K_L
  the nearest available level (Q3_K_M) rather than the same one.
  Recipe-matched rebuilds show the five files are exact, uncalibrated
  quantizations. They are still non-functional, but they are not defective
  files.
- **The September 2026 correction was also wrong in part.** On 2026-09-10 the
  phi3.5 claim was withdrawn on ollama/ollama#18252 because a no-imatrix
  upload from lm-kit also failed. That upload carries the library file's
  tensors, so it was not an independent test. The same comment kept the four
  Qwen claims, using Qwen's own GGUF files as no-imatrix referees; those
  files are labeled `Instruct-AWQ` in their metadata, which indicates
  AWQ-scaled weights, so they are not uncalibrated referees either. The v3
  verdicts rest on our own rebuilds.
- **A scoring fault is corrected.** The census scorer marked HumanEval/15 as
  failed for every artifact, although most generations contain the standard
  solution: its strengthened tests can exceed the time limit when scoring runs
  in parallel. Every failed task of every result set was re-evaluated
  serially, one evaluation at a time with the same EvalPlus version and its
  default limits, together with a 3% sample of passes (none changed).
  HumanEval/15 then passes for 275 of the 305 official artifacts, one of which
  also gains HumanEval/23; no pass turns into a fail. Stage-one flags fall from
  50 to 39 and the seven suspects are unchanged; with the v3.1 baseline groups
  (below), one non-suspect tag, `yi-coder:1.5b-chat-q2_K`, moves from degraded
  to healthy (8 to 9 of 15). The original score files are left as they were.
  `rerun-2026-10/census_rescore.jsonl` is an earlier parallel rescore; the
  serial scores the paper reports are in `rerun-2026-10/serial/`.
- **Baseline groups are corrected in v3.1.** Stage two compares a tag with
  the median score of its group's q6_K, q8_0 and fp16 tags. Up to v3.0 the
  group was `size_key` in `analysis/analyze.py` (alias-normalized model family
  and size), and 5 of its 28 groups pooled different registry checkpoints:
  granite 8B (`granite-code:8b-instruct`, its 128k variant and
  `granite3.1-dense:8b-instruct`), the granite-code 3B and 20B context
  variants, codegemma 7B v1.0 and v1.1, and mistral 7B unversioned, v0.2 and
  v0.3. From v3.1 the group is the model variant, the registry tag without its
  quantization suffix (35 groups, the columns of the census figure). The
  classes become 271 healthy, 2 degraded, 6 low baseline, 7 suspect and 19 no
  baseline (v3.0: 271/3/6/7/18): `mistral:7b-instruct-v0.2-q8_0` moves from
  degraded to healthy (6 of 15 against its own baseline of 6.5) and
  `granite-code:8b-instruct-128k-q4_0` from healthy to no baseline. The seven
  suspects, the 39 stage-one flags and every suite-size (A1) value the paper
  quotes are unchanged. Three of 35 model variants contain a non-functional
  tag (8.6%, Wilson 3.0–22.4%); counted by registry model and size it is 3 of
  29 (10.3%, 3.6–26.4%), as before.
- **Superseded v1 outputs are kept for the record.** `analysis/findings.json`
  (written by `analysis/analyze.py`) still lists five `confirmed_defects`;
  `analysis/classified.json` (from the older ratio rule in
  `analysis/classify.py`) labels five tags `DEFECTIVE`; and the `verdict`
  field in `results*/audit_scored.jsonl` is `"DEFECTIVE"` for every
  artifact below 9 of 15. That field is the raw stage-one flag, not a
  finding. The paper's numbers are in `rerun-2026-10/serial/numbers_regroup.json`
  (`rerun-2026-10/serial/numbers_serial.json` is the v3.0 version with the
  older baseline groups, and `rerun-2026-10/analysis/numbers_v3.json` the
  earlier parallel-rescore version). `findings.json`'s
  `backend_dependent` entries are superseded by the v3.4 template-dependent
  verdict.
- **Upstream reports.** `disclosure/DISCLOSURES.md` records the original
  report, the withdrawals and their dates.

## What is in this repository

| Path | Contents |
|---|---|
| `harness/` | The census pipeline: pull an artifact, run the smoke suite, record output statistics, score with EvalPlus, delete the model to bound disk use |
| `smoke_suite_v1.json` | The 15 smoke tasks (HumanEval+ task ids) and the tuning record |
| `heldout_49.json` | The 49 other HumanEval+ tasks used for the miss audit |
| `inventory_candidates.json` | The census inventory (327 artifacts) |
| `results/` | Census dataset: per-artifact generation transcripts (361 files), raw results with output statistics, original scores, and the full 164-task runs of the suspects |
| `results-mac/`, `results-metalrep/` | Metal-backend replication runs |
| `results-native/` | Control runs of the two TheBloke community files with no `TEMPLATE` line, the census condition (Ollama selects no template; `native_template` records `{{ .Prompt }}`); the script's docstring calls them native-template runs. Run on 13 August 2026 on an unrecorded CUDA machine with all 164 tasks in one pass, in numeric task order; the answers differ from the census on 28 of the 30 screen tasks, and the files solve 22 and 16 of 164 (`serial/native_serial.jsonl`) |
| `results-sensitivity/` | Threshold sensitivity runs (v1) |
| `analysis/` | v1 analysis code and outputs (superseded; see [Findings](#findings)) |
| `metal_replication.json`, `referee_round2.json`, `sensitivity_sample.json` | v1 adjudication and robustness inputs; `sensitivity_sample.json` lists the 10 first-version miss-audit tags (how they were drawn is not recorded; see [Miss-audit population bound](#miss-audit-population-bound)) |
| `muse_case_study.json`, `muse-glimmer-tracking/` | Day-zero case study of community conversions of a newly released model (v1) |
| `rerun-2026-10/` | The October 2026 rerun behind the v3 findings: rebuilds, calibration, tensor comparisons, perplexity, robustness and miss-audit runs, the dated run plan, logs, and the v3 analysis scripts (below) |
| `disclosure/DISCLOSURES.md` | Upstream report log |
| `PROVENANCE.md` | Every provenance statement of the paper (file identity, tensor identity, re-downloads, storefront copies, checkpoint and referee revisions, runtime and toolchain versions, calibration texts, serial scoring) mapped to the file and record that supports it, or marked as not in this release |

### The 2026-10 rerun (`rerun-2026-10/`)

The rerun used one RTX 4090 pod (the October pod) with Ollama 0.32.6 (the
census version) for generation and llama.cpp v0.7.0 (`620d222`) on CPU for
conversion, quantization, importance matrices and perplexity. A third RTX 4090
pod, after the census pod and the October pod, ran the miss-audit closure
(`results-v1cases/`) on 22 October 2026 with the same Ollama release. A fourth
RTX 4090 pod ran the template test (`results-tpl/`) on 28 October 2026, again
with the same release archive.
`RUN_PLAN.md` is the dated local run plan. The decision rule and the
first-round runs were written before any run. Its log records the later
additions, most of them before they ran and four with a fixed expected reading;
the positive control and the order-independent tensor comparison were logged
with their results. The serial re-scoring (`serial/SERIAL_REPORT.md`) and the
closure run are not in it; `scripts/v1cases.sh` states the closure run's
purpose and the digests it had to load.

| Path | Contents |
|---|---|
| `RUN_PLAN.md` | Dated run plan and run log |
| `fn_sample_30.json`, `fn_sample_b.json` | The two pre-drawn samples of unflagged artifacts for the miss audit (seeds 20261021, 20261022) |
| `results/` | R1, 15 tasks: the five library suspects, their 4-bit siblings, our plain (uncalibrated) rebuilds, third-party imatrix (bartowski) and Qwen's `Instruct-AWQ`-labeled files, and the Q4_K_M/Q6_K positive controls |
| `results-r2/` | R2, generation settings: context 4,096 and 8,192, 2,048-token cap, temperature 0.2 with seeds 1–3, for ten builds |
| `results-r3/`, `results-r3b/` | Miss audit: 29 + 27 unflagged census artifacts on the 49 held-out tasks |
| `results-r5/` | R5, 15 tasks: our own WikiText-2 imatrix builds |
| `results-r6/` | R6, all 164 tasks: calibrated Qwen2.5-Coder-3B builds and controls |
| `results-p0/` | 15 tasks: phi3.5 rebuilt with the library's tensor mix, and code-imatrix builds |
| `results-p0c/` | All 164 tasks: phi3.5 builds, the remaining third-party Qwen builds, code-imatrix Qwen builds |
| `results-g1/` | Qwen2.5-Coder 0.5B and 1.5B at q2_K–q3_K_L: library tag, plain rebuild, own imatrix |
| `results-v1cases/` | Miss-audit closure (third pod, 22 October 2026): `granite-code:3b-instruct-q3_K_M` and `mistral:7b-instruct-q3_K_S` on all 164 tasks from the census files. `runs.jsonl` (with each run's `blob`), `transcripts/`, `scored_serial.jsonl` (serial scores), the pod's Ollama server log `serve_v1cases.log.xz`, `env_v1cases.txt` (date, GPU and driver, Ollama version), `ollama_tarball_v1cases.sha256` and the run log `v1cases.log` |
| `results-tpl/` | Template test (fourth pod, 28 October 2026, v3.4): the two TheBloke deepseek-coder-6.7B files on CUDA under three Modelfiles (no template; the Metal run's instruction template; the `deepseek-coder:6.7b-instruct-q4_K_M` Modelfile as `run_entries.donor_modelfile` copies it), 15 screen tasks and all 164. `runs.jsonl` (Modelfile text and hash, the template Ollama adopted, file and tensor hashes, per-generation metrics), `transcripts/`, `scored_serial.jsonl`, `env_tpl.txt`, `pip_freeze_tpl.txt`, `file_sha256.txt` (both files equal the census digests), `ollama_tarball_tpl.sha256`, `donor_modelfile.txt`, `donor_template.txt`, the server log `serve_tpl.log.xz`, the run log `tpl.log` and the records of an aborted first launch (`aborted_first_attempt/`, no generation) |
| `census_rescore.jsonl` | Corrected per-task results for the 15 suite tasks of all 334 archived census smoke transcripts |
| `p0b_tensor_compare.jsonl` | Library tag vs plain rebuild, per tensor: Qwen-3B q2_K–q3_K_L and q4_K_M, phi3.5 q2_K |
| `p0f_compare.jsonl` | phi3.5 q2_K library tag vs our rebuild with `attn_qkv` at `Q2_K` |
| `g1_tensor_compare.jsonl` | Qwen 0.5B and 1.5B library tags vs plain rebuilds |
| `local_referee_hashes.jsonl` | File and tensor hashes of locally held third-party files: QuantFactory and lm-kit phi3.5 Q2_K, Qwen's q2_k and q3_k_m |
| `ppl/` | `llama-perplexity` logs (WikiText-2 test, 20 chunks of 512 tokens) |
| `quants-imat/`, `quants-imat-code/`, `quants-g1/` | Importance-matrix files (WikiText-2 train; Python code; WikiText-2 train for 0.5B/1.5B). These are activation statistics in llama.cpp's GGUF imatrix format, not model files |
| `source_commits.jsonl` | Hugging Face revisions of the four source checkpoints |
| `env.txt`, `pip_freeze.txt`, `ollama_tarball.sha256` | Pod environment, Python packages, hash of the Ollama 0.32.6 release archive |
| `calib_sha256.txt`, `calib_code_sha256.txt` | SHA-256 of the two calibration texts (the texts are not included; see below) |
| `logs.tar.xz` | Run logs: Ollama server log, conversion, quantization, imatrix and queue logs (84 MB unpacked) |
| `scripts/` | The pod scripts and run-entry files (see "Rebuild, calibrate and compare"), including `v1cases.sh` (the miss-audit closure run) and `score_v1cases_serial.py` (its serial scoring, one EvalPlus worker); `tpl_test.sh`, `run_template_test.py` and `tpl_entries.json` (the template test, v3.4) and `score_template_serial.py` (its serial scoring) |
| `a1/` | Suite-size analysis: `a1_analysis.py` and `a1_results.json` on the original scores, `A1_REPORT.md`, an independent check in `verify/`, and `a1_rescored.py`/`.json` on the corrected scores |
| `analysis/` | `numbers_v3.py`, `numbers_v3.json` and `figures_v3.py` on the earlier parallel rescore |
| `serial/` | The serial re-scoring the paper reports: `serial_rescore.py`, per-set `*_serial.jsonl` scores (`tpl_serial.jsonl`, v3.4, holds the template test's), `flips.jsonl`, `instances.jsonl` (one line per serial evaluation of every set except the two tuning sets and the template test, with EvalPlus reasons and timings; the tuning sets' re-evaluations are summarized in `serial_summary.json` and `SERIAL_REPORT.md`), `tuning_p1_humaneval_serial.jsonl` and `tuning_p1_mbpp_serial.jsonl` (the 24 screen-tuning configurations' per-task results, v3.3), `numbers_regroup.py`/`.json` (the paper's numbers apart from the population bound and the gate evaluation, model-variant baselines; v3.1), `a1_regroup.py`/`.json` (suite-size analysis, model-variant baselines; v3.1), `figures_serial.py`, `census_digests.py`/`.json` (per-artifact file digests of the census, extracted from the census server log; v3.1), `tuning_configs.json` (names of the 24 screen-tuning configurations), the v3.0 versions `numbers_serial.py`/`.json` and `a1_serial.py`/`.json` (older baseline groups), and the reports `SERIAL_REPORT.md`, `PAPER_DIFF.md`, `VERIFY_REPORT.md` |
| `miss_audit/` | `certificates.csv`, `certificates.json`: per-artifact evidence for the 66 artifacts of the miss audit (v3.1; see "Miss-audit certificates"). `bounds.py`, `bounds.csv`, `bounds.json`, `BOUNDS.md`: population bounds with their assumptions, and `bounds_check.py`, an exact-fraction check; `v1_seed_search.py`/`.log`: the seed search for the first-version draw (v3.2; see "Miss-audit population bound") |
| `miss_audit/closure_v1cases.json` | Closure record of the two first-version audit tags: census digest, digest match, and 164-task serial score of each (65 and 44 of 164) |
| `ds13/` | deepseek-coder-1.3B diagnosis scripts and outputs |
| `gate/` | `facts_b.py`, `facts_b.json`, `FACTS_B.md`: the tags each output-statistic rule flags and their full-run status, the proposed acceptance gate applied to the census, persistence of the suspects' files, the repetition score, and every non-suspect tag with a full 164-task run (v3.2; see [Flags, gate and full runs](#flags-gate-and-full-runs)) |

Every results directory has `runs.jsonl` (one record per run: the entry, the
Ollama options, per-task output statistics and, for rebuilds and downloaded
files, a `hash` record with the whole-file SHA-256, the tensor-payload
SHA-256, the tensor count, key metadata and any imatrix keys),
`scored.jsonl` (EvalPlus per-task results; a task passes only when both the
base and the plus tests pass), and `transcripts/<id>__<setting>.jsonl`.
`results-v1cases/` and `results-tpl/` have `scored_serial.jsonl` instead of `scored.jsonl`: they
were scored only serially, by `scripts/score_v1cases_serial.py` and `scripts/score_template_serial.py`. To
rescore the template test, run `EVALPLUS_BIN=.venv/bin .venv/bin/python
rerun-2026-10/scripts/score_template_serial.py COPY` on a copy of `results-tpl/`
(the script rewrites `scored_serial.jsonl` in the directory it is given). Runs
of census tags and already hashed builds were made with `--no-hash`. Some
`(id, setting)` pairs in `scored.jsonl` were scored twice by overlapping
scorer passes with identical results; the analysis uses the last row. Two R1
entries (`plain-qwen3b-F16`, `plain-qwen3b-Q8_0`) have an `error`: Ollama
ran out of local disk while importing them.

The quantized GGUF files themselves (our rebuilds and the downloaded
third-party files) are not distributed; their hashes are in the `runs.jsonl`
`hash` records. Paths inside the records are pod paths: `/workspace/p2rerun/`
corresponds to `rerun-2026-10/`, and `/root/p2/run/` to
`rerun-2026-10/scripts/` together with `harness/`, `smoke_suite_v1.json` and
`heldout_49.json` at the repository root and the two `fn_sample_*.json` files
in `rerun-2026-10/`.
The `transcript` fields of `serial/*_serial.jsonl` (all but the two tuning
files, which carry none, and `tpl_serial.jsonl`, whose field is a file name under
`results-tpl/transcripts/`; the tuning study's generations are not released) are
paths in the author's working copy; their file names identify the transcripts, which are
in `results/transcripts/` (census), `rerun-2026-10/results*/transcripts/`
(rerun) and `results-mac/`, `results-metalrep/`, `results-native/` and
`results-sensitivity/` (`transcripts/` in each).

## Reproduce the paper's numbers

Requirements: Python 3.9 or later. The numbers use only the standard
library; the figures also need matplotlib and numpy. From the repository
root:

```
python3 rerun-2026-10/serial/numbers_regroup.py   # the paper's numbers (classes, flags, attribution, settings, ...)
python3 rerun-2026-10/serial/a1_regroup.py        # suite-size analysis (RQ4, "Suite choice")
python3 rerun-2026-10/serial/figures_serial.py    # census and calibration figures
python3 rerun-2026-10/miss_audit/bounds.py        # miss-audit population bounds (RQ4, "Population")
python3 rerun-2026-10/gate/facts_b.py             # RQ3 flag status, gate on the census, persistence, full runs
```

- `numbers_regroup.py` reads the serial scores (`rerun-2026-10/serial/*_serial.jsonl`,
  since v3.4 also `tpl_serial.jsonl` with `results-tpl/runs.jsonl` and its transcripts for the
  `template_test` block),
  the census files at the repository root (`results/audit_scored.jsonl`,
  `results/audit_results.jsonl`, `smoke_suite_v1.json`, `heldout_49.json`, and
  `analysis/analyze.py` for its helpers) and the rerun files (`ppl/`, the
  tensor comparisons, `a1/a1_results.json`), and rewrites
  `rerun-2026-10/serial/numbers_regroup.json`. It groups baselines by model
  variant; it also asserts that with the v3.0 grouping it reproduces the
  class counts, suspects and stage-one flags of `numbers_serial.json` and
  `analysis/numbers_v3.json`. Every value that does not depend on the
  grouping (the 164-task runs, the attribution and settings tables,
  perplexity, tensor identity, the miss audit, the screen cost) equals its
  value in `numbers_serial.json`.
- `a1_regroup.py` reads `numbers_regroup.json`, so run it second. It first
  reproduces every per-k value of the released `a1/a1_results.json` from the
  original scores, then evaluates all 32,647 subsets of 3 to 15 suite tasks
  on the serial scores (about 10 s) and compares the result, metric by
  metric, with `serial/a1_serial.json`. It rewrites
  `rerun-2026-10/serial/a1_regroup.json`.
- `figures_serial.py` writes `rerun-2026-10/serial/figs/`. It reads only values
  that do not depend on the baseline groups (census counts, flags, suspects,
  164-task runs) from `numbers_serial.json`, and checks the layout of each
  figure after drawing it.
- `bounds.py` (v3.2) rebuilds the audit frame, its ten strata and the three
  samples from the census scores, the inventory and the sampling manifests,
  regenerates both seeded draws from their seeds, and rewrites
  `rerun-2026-10/miss_audit/bounds.csv` and `bounds.json`.
  `bounds_check.py` repeats rows 1 to 4 in exact fractions, and
  `v1_seed_search.py` (about 16 minutes) repeats the seed search in
  `v1_seed_search.log`.
- `facts_b.py` (v3.2) recomputes the output statistics from the
  per-generation metrics and rewrites `rerun-2026-10/gate/facts_b.json`.

**Screen-tuning data, and inputs that are not in this release.**

- *Screen tuning.* The screen was tuned on 24 Qwen2.5-Coder configurations
  from a separate study. Their names are in `serial/tuning_configs.json` and,
  since v3.3, their serially re-scored per-task results are in
  `serial/tuning_p1_humaneval_serial.jsonl` and
  `serial/tuning_p1_mbpp_serial.jsonl`: one row per configuration with the
  `per_task` verdicts, `n_pass`, the pool-scored `old_n_pass`, the tasks that
  were re-evaluated and the `flips` whose verdict changed. From them
  `numbers_regroup.py` recomputes the `tuning_serial` block (the 39-task pool,
  the 0 of 15 of the known failure and the MBPP+ range of the healthy
  configurations, 38.6–70.1%), `a1_regroup.py` the `calibration_94pct_check`
  block that `headline.calibration_94pct` repeats (the "94% of random
  15-of-164 suites" figure), and `facts_b.py` the
  `tuning_full164_separate_study` summary. What is not released is the tuning
  study's original, pool-scored EvalPlus result files
  (`QUANTCHECK_P1_RESULTS`); the serial rows carry the tasks whose verdict
  changed (`flips`), from which `a1_regroup.py` rebuilds the pool-scored
  verdicts and the serial-versus-pool differences. If the serial tuning files are removed, each script falls back to the
  committed values and marks them `"from_committed_file": true`.
- *Census server log.* The census Ollama `serve.log` (93 MB) is not released.
  `serial/census_digests.json`, written from it by `serial/census_digests.py`,
  records the server version and, for every census artifact, the SHA-256 of
  the file it ran on and the log line it comes from. `numbers_regroup.py`
  takes its file-identity check from it, or from the log itself when
  `QUANTCHECK_CENSUS_SERVELOG` points at the log, in which case it also
  asserts that the two agree. `gate/facts_b.py` does the same.

After a clean run, `git diff` shows nothing, apart from `meta.python` in
`a1_regroup.json` if your Python version differs. `bounds.py` and
`facts_b.py` reproduce their committed outputs
byte for byte; `v1_seed_search.py` reproduces its log except for the
elapsed seconds. With matplotlib 3.9.4 and numpy 2.0.2 the figures match the
paper's: the PNG files byte for byte, the PDF files except for their
creation date.

**Earlier versions of the analysis**, kept for comparison:

```
python3 rerun-2026-10/serial/numbers_serial.py    # v3.0 numbers, size_key baseline groups
python3 rerun-2026-10/analysis/numbers_v3.py      # earlier parallel rescore
python3 rerun-2026-10/analysis/figures_v3.py      # figures on the parallel rescore, writes rerun-2026-10/figs/
python3 analysis/analyze.py                       # v1 pipeline, withdrawn v1 rule, rewrites analysis/findings.json
```

`numbers_v3.py`, `analyze.py` and `numbers_serial.py` reproduce their
committed outputs byte for byte. The two earlier suite-size scripts need the
tuning study's original EvalPlus result files for their last step:
`a1/a1_rescored.py` stops with an `AssertionError` in `part2_3` and writes
nothing, and `serial/a1_serial.py` stops with a message and writes nothing.
Their census parts are covered by `a1_regroup.py`, which reproduces
`a1_results.json`, compares itself with `a1_serial.json` and performs the
calibration check from the released serial files.

**The deepseek-coder-1.3B diagnosis** (RQ1, `rerun-2026-10/ds13/`):

```
python3 rerun-2026-10/ds13/ds13_census_parse.py   # unparseable outputs: 59.4% for the group, 2.4% for 286 other tags
```

runs from the release. The other three scripts need inputs that are not in
it: `ds13_servelog.py` the census `serve.log` (`QUANTCHECK_CENSUS_SERVELOG`;
its output, `servelog_loads.json`, is the released form of that evidence);
`ds13_outputs.py` EvalPlus 0.3.1 and the census transcripts it was run on in
its working layout (`QUANTCHECK_DS13_DIR`; the file names are the keys of
`outputs_summary.json`, and all are in `results/transcripts/`);
`ds13_tokcheck.py` EvalPlus, the `tokenizers` package and the
`tokenizer.json` of deepseek-coder-1.3b-instruct
(`QUANTCHECK_DS13_TOKENIZER`, default the Hugging Face cache). They write
next to themselves. Given those inputs, each reproduces its committed JSON
byte for byte.

**Paths.** All defaults follow the repository layout, and each can be
overridden with an environment variable. No script reads an absolute or
home-relative data path; the EvalPlus executables (`EVALPLUS_BIN`) and the
Hugging Face cache are tool locations.

| Variable | Default | Used by |
|---|---|---|
| `QUANTCHECK_ROOT` | repository root | `numbers_regroup.py`, `a1_regroup.py`, `numbers_serial.py`, `figures_serial.py`, `serial_rescore.py`, `a1_analysis.py`, `a1/verify/*.py`, `bounds.py`, `v1_seed_search.py`, `facts_b.py` |
| `QUANTCHECK_RERUN_DIR` | `rerun-2026-10/` | `numbers_regroup.py`, `numbers_serial.py`, `numbers_v3.py`, `figures_v3.py`, `a1_rescored.py`, `serial_rescore.py`, `bounds.py`, `facts_b.py` |
| `QUANTCHECK_ANALYSIS_DIR` | `rerun-2026-10/analysis/` | `numbers_regroup.py`, `numbers_serial.py` |
| `QUANTCHECK_SERIAL_DIR` | `rerun-2026-10/serial/` | `numbers_regroup.py`, `a1_regroup.py`, `bounds.py`, `facts_b.py` |
| `QUANTCHECK_A1_DIR` | `rerun-2026-10/a1/` | `numbers_regroup.py`, `a1_regroup.py`, `numbers_serial.py`, `a1_serial.py`, `numbers_v3.py`, `a1_rescored.py` |
| `QUANTCHECK_OUT_DIR` | next to the script | `numbers_regroup.py`, `a1_regroup.py`, `ds13/*.py` (except `ds13_census_parse.py`, which only prints), `bounds.py`, `facts_b.py` |
| `QUANTCHECK_TUNING_DIR` | `rerun-2026-10/serial/` | `numbers_regroup.py`, `a1_regroup.py`, `facts_b.py` |
| `QUANTCHECK_P1_RESULTS` | none in this repository | `a1_regroup.py`, `a1_serial.py`, `a1_analysis.py`, `a1_rescored.py`, `a1/verify/*.py`, `serial_rescore.py` |
| `QUANTCHECK_CENSUS_SERVELOG` | not in this repository | `numbers_regroup.py`, `ds13_servelog.py`, `facts_b.py` |
| `QUANTCHECK_CENSUS_TRANSCRIPTS` | `results/transcripts/` | `ds13_census_parse.py` |
| `QUANTCHECK_DS13_DIR`, `QUANTCHECK_DS13_TOKENIZER` | see above | `ds13_outputs.py`, `ds13_tokcheck.py` |
| `QUANTCHECK_NUMBERS_JSON` | `rerun-2026-10/analysis/numbers_v3.json` | `a1_rescored.py` |

The two v3.1 scripts also accept the `QC_*` names of the author's working
copy. Only path lines were changed in the v3.0 scripts, so the SHA-256 of
`a1_analysis.py` recorded in `a1_rescored.json` refers to the file before
that edit. Absolute paths recorded inside `a1_results.json`,
`a1_rescored.json` and `a1_serial.json` were replaced by placeholders such as
`<QUANTCHECK_ROOT>`; rerunning those scripts records your own paths there.
`a1/verify/verify_extra.py` also reads the census archive
`results/audit_data_full.tgz`, which is not in this repository (the same
transcripts are in `results/transcripts/`).

## Miss-audit certificates

The paper's miss audit ran 66 artifacts that the screen passed (10 from the
first version in `results-sensitivity/`, 56 drawn with recorded seeds in
`rerun-2026-10/results-r3/` and `results-r3b/`) on 49 held-out tasks. `rerun-2026-10/miss_audit/certificates.csv`
lists, for each of them, the evidence that it is not non-functional, that
is, that it solves at least 17 of the 164 HumanEval+ tasks: the distinct
successful task ids, the backend and the file digest of every run, and the
record each digest comes from. Successes are combined only within one file
digest and one backend. `certificates.json` holds the same rows and a
summary (status counts, the file-identity checks, the sampling frame).

| Status | v1 sample (10) | new sample (56) | All |
|---|---:|---:|---:|
| Certified on the audit run alone | 8 | 42 | 50 |
| Certified by census screen plus audit, same digest and backend | 0 | 14 | 14 |
| Certified by a full 164-task rerun on the census file (CUDA) | 2 | 0 | 2 |

The last two are the v1-audit tags `granite-code:3b-instruct-q3_K_M` (12 of
49 in the audit, 14 of 15 on the screen) and `mistral:7b-instruct-q3_K_S`
(11 of 49, 10 of 15). The v1 audit run recorded neither its backend nor its
file digest, so its successes cannot be combined with the screen's, and the
audit alone falls short of 17. Both tags were therefore rerun on all 164
HumanEval+ tasks on 22 October 2026, on a third RTX 4090 pod with Ollama
0.32.6 and the census options. Each run loaded the census file (blobs
`9c66e1aa` and `067ecc27`, equal to the census digests in
`serial/census_digests.json`), and, scored serially, they solve 65 and 44
of 164. The runs are in `rerun-2026-10/results-v1cases/` and the closure record
is `rerun-2026-10/miss_audit/closure_v1cases.json`. The rerun does not repeat
the census answers byte for byte: run in a different task order on another
machine, 2 of granite's 15 screen answers and 3 of mistral's differ from the
census transcripts, and one verdict changes (granite, HumanEval/107, pass to
fail); their 49 held-out answers match the v1 audit transcripts on 41 and
40 of 49 tasks. In `certificates.csv` and `certificates.json` the two rows keep their
pre-rerun `status` (`not certified`), `certificate_basis` and `reason`. The
`closure` column, added after the rerun, records the rerun result for these
two rows and is empty for the other 64; `summary.closure` in
`certificates.json` gives the status counts after the closure. No audited
artifact scores 2 of 49 or less. `gemma3:4b-it-q8_0` was re-published
between the census and the audit, so its certificate covers the October
file only. The census digests come from the census server log (see
`serial/census_digests.json`), the October digests from the `blob` field of
`runs.jsonl` and `rerun-2026-10/logs.tar.xz:logs/serve.log`, and the v1 Mac
pull digests from a Mac server log that is not released. The closure digests
come from the `blob` field of `results-v1cases/runs.jsonl` and
`results-v1cases/serve_v1cases.log.xz`. The script that built the table
reads those unreleased logs and is not included; the source labels in the
table name the released files where they exist.

## Miss-audit population bound

The 56 October tags were drawn at random within ten model-family strata, in
two rounds with recorded seeds (`rerun-2026-10/fn_sample_30.json`, seed
20261021, frame 223; `fn_sample_b.json`, seed 20261022, frame 194);
`rerun-2026-10/miss_audit/bounds.py` regenerates both lists from the seeds.
How the 10 first-version tags were drawn is not recorded: no seed, script or
frame exists, a seed search does not recover the draw
(`v1_seed_search.py`, `.log`), and all 10 files are at most 3.9 GB, which
suggests a draw restricted to small files. The bound the paper prints
therefore covers only the 223 tags outside the first-version sample:

| Population | Draws used | August file of the re-published `gemma3:4b-it-q8_0` | Exact stratified 95% upper bound |
|---|---|---|---:|
| 223 unflagged official tags outside the tuning set and the first-version sample, as census files | 56 seeded draws, 0 non-functional | counted as failed | 22 (9.9%) |

"Non-functional" means the tag's August census file solves at most 16 of the
164 HumanEval+ tasks. The bound inverts the product of per-stratum
hypergeometric probabilities and assumes only simple random sampling within
each stratum. The full table in
[`rerun-2026-10/miss_audit/BOUNDS.md`](rerun-2026-10/miss_audit/BOUNDS.md)
(section 4; per row in `bounds.csv`) adds tighter bounds under further
assumptions (15 to 20 tags when the first-version 10 are treated as random
draws or the October gemma file stands in for the August one) and a variant
over all 233 tags that makes no assumption about the first-version 10 (30,
12.9%).

## Flags, gate and full runs

`rerun-2026-10/gate/facts_b.py` recomputes the facts below from the raw
records; the values are in `facts_b.json` and explained in `FACTS_B.md`.

### Output-statistic flags

The output-statistic rules of RQ3 also flag tags that are not suspects. These
extra flags are not labelled false positives: several of the flagged tags
solve at most 16 of the 164 tasks in a full run. Truncation at or above 0.2 flags 10 of
the 298 non-suspect tags: 5 solve at most 16 of 164 (five deepseek-coder-1.3B
tags), 2 solve more, and 3 have no full run. Either statistic at or above 0.3
flags 6: four deepseek-coder-1.3B tags solving 2 to 16 of 164,
`deepseek-coder-v2:16b-lite-instruct-q2_K` (25 of 164), and
`starcoder2:15b-instruct-v0.1-q3_K_S` (no full run). The repetition score at
or above 0.2 flags 4, one verified above 16 and three with no full run.
`numbers_regroup.json` (`rq3_output_rules`) keeps the older field name
`false_positives` for the same counts, and the v3.0 reports
`serial/PAPER_DIFF.md` and `serial/VERIFY_REPORT.md` keep their original
wording.

### Proposed gate on the census

The paper's proposed gate publishes a tag that solves at least 9 of the 15
screen tasks. Any other flagged tag that is not a suspect, whatever its
class, is published only if a full 164-task run on the same file and backend
reaches 17 distinct solved tasks (the run can stop there); suspects go to
attribution instead. On the census, 39 tags are flagged; 7 are suspects, and
the 32 others split as follows:

| Outcome | Tags |
|---|---:|
| Full run exists, 17 or more solved: published | 8 |
| Full run exists, at most 16 solved: withheld (seven deepseek-coder-1.3B tags and `gemma3:270m-it-q8_0`) | 8 |
| No full run yet: pending | 16 |

So of the 16 such tags with a full run, 8 would be published and 8 withheld.
The tags are listed in `facts_b.json` (`gate.routing_rule_on_census`).

### Full 164-task runs of non-suspect tags

Every non-suspect official tag with any full 164-task run on its census file,
with its serial scores (class from the serial 15-task scores and model-variant
baselines). The census full runs were not drawn at random: `harness/fullrun.py`
(lines 59 to 72) chose every official tag at most 2 of 15 on the original
scorer plus the lowest-scoring tag at 3 to 8 of 15 per model and size key,
so the runs lean toward weak tags. 16 tags are verified above 16 of 164 and
8 at or below 16.

| Tag | Class | Screen (of 15) | Full (of 164) | Run |
|---|---|---:|---:|---|
| `qwen2.5-coder:3b-instruct-q4_K_M` | healthy | 15 | 132 | October re-download (R6) |
| `phi3.5:3.8b-mini-instruct-q4_K_M` | healthy | 11 | 74 | October re-download (P0-c) |
| `granite-code:3b-instruct-q3_K_M` | healthy | 14 | 65 | miss-audit closure |
| `starcoder2:15b-instruct-v0.1-q2_K` | no baseline | 9 | 65 | census |
| `yi-coder:1.5b-chat-q2_K` | healthy | 9 | 48 | census |
| `granite-code:20b-instruct-q2_K` | no baseline | 9 | 46 | census |
| `mistral:7b-instruct-q3_K_S` | healthy | 10 | 44 | miss-audit closure |
| `mistral:7b-instruct-v0.2-q3_K_L` | healthy | 7 | 44 | census |
| `llama3.2:1b-instruct-q3_K_M` | healthy | 8 | 42 | census |
| `phi3.5:3.8b-mini-instruct-q3_K_S` | healthy | 9 | 42 | census |
| `gemma3:1b-it-q4_K_M` | healthy | 7 | 37 | census |
| `llama3.2:3b-instruct-q2_K` | degraded | 7 | 30 | census |
| `deepseek-coder-v2:16b-lite-instruct-q2_K` | no baseline | 7 | 25 | census |
| `gemma2:2b-instruct-q2_K` | healthy | 7 | 25 | census |
| `gemma3:270m-it-fp16` | healthy | 5 | 20 | census |
| `granite3.1-dense:2b-instruct-q2_K` | degraded | 5 | 17 | census |
| `gemma3:270m-it-q8_0` | healthy | 5 | 16 | census |
| `deepseek-coder:1.3b-instruct-fp16` | low baseline | 2 | 16 | census |
| `deepseek-coder:1.3b-instruct-q3_K_L` | low baseline | 1 | 10 | census |
| `deepseek-coder:1.3b-instruct-q4_0` | low baseline | 1 | 10 | census |
| `deepseek-coder:1.3b-instruct-q3_K_S` | healthy | 3 | 7 | census |
| `deepseek-coder:1.3b-instruct-q4_K_M` | low baseline | 1 | 7 | census |
| `deepseek-coder:1.3b-instruct-q3_K_M` | low baseline | 1 | 5 | census |
| `deepseek-coder:1.3b-instruct-q2_K` | low baseline | 2 | 2 | census |

Census runs: `rerun-2026-10/serial/census_full164_serial.jsonl` (the
original scores are in `results/fullrun_scores.jsonl`). Closure runs:
`rerun-2026-10/results-v1cases/scored_serial.jsonl`. October re-downloads:
`serial/rerun_r6_serial.jsonl` and `serial/rerun_p0c_serial.jsonl`, on files
equal to the census digests. The screen-tuning configurations of a separate
study also have full runs (`serial/tuning_p1_humaneval_serial.jsonl`); they
are left out of this table because their file identity with the census was
not recorded.

## Smoke-test an artifact yourself

Requirements: Python 3.9+, [Ollama](https://ollama.com) serving locally (the
study pinned v0.32.6), and [EvalPlus](https://github.com/evalplus/evalplus)
0.3.1, which supplies the task prompts as well as the scorer. A virtual
environment keeps the EvalPlus executables next to the interpreter:

```
python3 -m venv .venv && .venv/bin/pip install evalplus==0.3.1
.venv/bin/python harness/audit.py --only qwen2.5-coder:3b-instruct-q3_K_M --results-dir my-results
.venv/bin/python rerun-2026-10/scripts/rescore_census.py my-results my-results/scored.jsonl 1
```

`harness/audit.py` runs `ollama pull` for each selected inventory entry,
sends the 15 suite tasks to Ollama's `/api/chat` (temperature 0, seed 42,
context 2,048, at most 768 new tokens), and writes the generations to
`<results-dir>/transcripts/` and one record per artifact, with the per-task
output statistics (6-gram repetition score, prompt echo, whether generation hit
the token cap, presence of a function definition), to
`<results-dir>/audit_results.jsonl`. The repetition score (`rep6_share`,
averaged as `rep6_share_mean`) is six times the count of the most frequent
word 6-gram divided by the word count (`harness/audit.py` lines 40 to 51);
despite the field name it is not a share and can exceed 1. The script then
stops and removes the model (`--keep-model` keeps it). Notes:

- `--only` selects a tag from `inventory_candidates.json`. To test another
  tag, pass `--inventory FILE` with `{"artifacts": [{"tag": "..."}]}`.
- Runs are resumable: tags already in the results directory's
  `audit_results.jsonl` are skipped. Without `--results-dir` the script
  writes to this repository's `results/`, where every census tag is already
  recorded, so nothing runs.
- Entries whose tag starts with `hf:` are skipped; Hugging Face files were
  run with `harness/hf_audit.py` (pod paths) or `rerun-2026-10/scripts/run_entries.py`.
- `--task-file heldout_49.json` runs the 49 miss-audit tasks instead.
- `OLLAMA_BIN` (default `ollama`) and `OLLAMA_HOST` (a full URL, default
  `http://127.0.0.1:11434`) configure the runtime.

Scoring: `rerun-2026-10/scripts/rescore_census.py RESULTS_DIR OUT.jsonl [workers]`
scores every transcript in `RESULTS_DIR` with the EvalPlus executables found
next to the Python interpreter, and writes per-task pass/fail and `n_pass`.
`harness/score_smoke.py`, the census scorer, scores only this repository's
`results/` directory, skips tags already in `results/audit_scored.jsonl`,
and finds EvalPlus through `EVALPLUS_BIN` (default `~/Library/Python/3.9/bin`).
HumanEval/15's strengthened tests are slow and can exceed EvalPlus's time
limit when scoring runs in parallel, so use one worker; scores are lower
bounds.

Reading the score: on the tuning set every healthy configuration passes 15
of 15. The screen flags an artifact below 9 of 15 (stage one), then compares
it with its group baseline, the median score of the same model variant (the
registry tag without its quantization suffix, for example
`mistral:7b-instruct-v0.2`) at q6_K, q8_0 or fp16 (stage two). A suspect
scores at most 2 of 15 in a group whose baseline is at least 8; the census
baselines, from the serial scores, are in the `regroup.baselines` block of
`rerun-2026-10/serial/numbers_regroup.json`.
A low score says an artifact does not work, not why. Attributing a cause
needs the rebuild comparison below.

## Rebuild, calibrate and compare

`rerun-2026-10/scripts/` holds the scripts exactly as they ran on the pod.
They hardcode pod paths (`/root/p2`, `/root/p2/run`, `/workspace/p2rerun`, `/workspace/p2tpl`,
Ollama at `/root/p2/ollama/bin/ollama`); adjust these before reuse. The
underlying steps, with llama.cpp v0.7.0:

```
python llama.cpp/convert_hf_to_gguf.py CHECKPOINT_DIR --outtype f16 --outfile model-f16.gguf
llama-quantize model-f16.gguf model-Q3_K_M.gguf Q3_K_M                       # plain rebuild, no calibration
llama-imatrix -m model-f16.gguf -f wiki.train.raw -o model.imatrix --chunks 100 -c 512 -t 12
llama-quantize --imatrix model.imatrix model-f16.gguf model-imat-Q3_K_M.gguf Q3_K_M
llama-quantize --tensor-type attn_qkv=q2_k phi35-f16.gguf phi35-Q2_K-libmix.gguf Q2_K   # phi3.5 with the library's older mix
llama-perplexity -m model.gguf -f wikitext-2-raw/wiki.test.raw --chunks 20 -c 512 -t 12
```

The GGUF behind an Ollama tag is the `FROM .../blobs/...` path printed by
`ollama show --modelfile TAG`. The comparison scripts need the `gguf` Python
package (`pip install gguf`, or `llama.cpp/gguf-py`):

- `compare_tensors.py A=PATH_A B=PATH_B` compares two GGUF files tensor by
  tensor (type, shape and bytes, independent of tensor order) and lists the
  metadata keys that differ. The paper's tensor-identity results use it.
- `gguf_hash.py LABEL=PATH` prints the whole-file SHA-256 and a tensor-payload
  SHA-256 over the tensors in file order. Because it depends on tensor order,
  it first reported every plain rebuild as distinct; the order-independent
  comparison is the one reported. `gguf_tensor_hash_stdlib.py` computes the
  same hashes without the `gguf` package (used for `local_referee_hashes.jsonl`).
- `tensor_types.py LABEL=PATH` prints the tensor type mix.
- `run_entries.py ENTRIES.json --setting NAME [--options JSON] [--tasks suite|FILE] --results-dir DIR`
  runs library tags, local GGUF files and Hugging Face files through the
  census generation path, copying the chat template from a donor library tag;
  `score_runs.py DIR [workers]` scores the results. Run them with
  `PYTHONPATH=harness` so `run_entries.py` finds `harness/audit.py`, and set
  `OLLAMA_BIN`; the script writes Modelfiles to `/root` and downloads to
  `/root/hfdl`.

Run order on the pod: `setup_pod.sh`; `r1.sh` (starts Ollama with
`start_ollama.sh`, builds the plain rebuilds with `quantize_refs.sh`, runs
R1); `pc.sh` (positive controls); `r2_r3.sh` (R2 only; its R3 part was
handed to `gpu_queue.sh`); `imatrix_refs.sh` and
`p0_cpu.sh` on CPU (importance matrices, the phi3.5 library-mix rebuild and
its tensor comparison, code calibration, perplexity); `gpu_queue.sh` (R3,
R5, R6, R3b, then `gpu_queue_more.sh`: retries, P0 smoke, P0-c) with
`scorer_loop.sh` scoring alongside; `g1lite.sh`; and `rescore_census.py` for
the census rescore. `queue_tail.sh` was superseded by `gpu_queue.sh` and its
log is empty. The library-vs-plain comparison in `p0b_tensor_compare.jsonl` was
run by calling `compare_tensors.py` directly. The miss-audit closure ran later
on a third pod with `v1cases.sh`, which installs Ollama 0.32.6 and EvalPlus
itself and expects the scripts in `/root/p2/run`; `score_v1cases_serial.py`
then scored its transcripts serially on a Mac. The template test ran on a
fourth pod with `tpl_test.sh`, which installs the same Ollama archive and
EvalPlus, downloads the two TheBloke files and checks their digests, and
expects `run_template_test.py`, `tpl_entries.json`, `run_entries.py`,
`gguf_hash.py`, `all164.json`, `harness/` and `smoke_suite_v1.json` in
`/root/p2/run`; its `/workspace/p2tpl` `results/` and `logs/` are released
flat in `results-tpl/` (`serve.log.xz` as `serve_tpl.log.xz`), and
`score_template_serial.py` scored its transcripts serially on a Mac.

### Calibration texts (not included)

The calibration texts are not redistributed. To rebuild them, check each
against its recorded SHA-256:

- **WikiText-2** (Merity et al., 2017; CC BY-SA): the raw v1 archive that
  `imatrix_refs.sh` downloads from
  `https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip`.
  `wikitext-2-raw/wiki.train.raw` is the imatrix text
  (`calib_sha256.txt`); `wiki.test.raw` is the perplexity text (its hash was
  not recorded).
- **Python code**: the first 1,500,000 bytes of the `.py` files under 200 KB
  in llama.cpp v0.7.0 (`620d2225cc436ab0b001e3200e590e3b286ef9ab`),
  concatenated in sorted path order, as in `p0_cpu.sh`:
  `find llama.cpp -name "*.py" -size -200k | sort | xargs cat | head -c 1500000 > code.txt`
  (`calib_code_sha256.txt`). Sort order depends on the locale; if the hash
  differs, try `LC_ALL=C`.

## Data notes

- `inventory_candidates.json` carries `"frozen": false`. The inventory was
  nevertheless fixed on 2026-08-10 (its `generated` field) before the census
  ran on 2026-08-12 (`ts_start` in `results/audit_results.jsonl`, 00:59 to
  08:39 UTC for the official tags); the flag was never switched, and the
  file is unchanged since. Each artifact's pull time is in
  `results/audit_results.jsonl` (`pull_s`).
- Three v1 transcripts contained email addresses that a model reproduced
  from its training data (`results-native/transcripts/deepseek-coder-6.7b-instruct.Q3_K_M.native.jsonl`,
  `...Q4_K_M.native.jsonl`, and
  `results/transcripts/hf_TheBloke_deepseek-coder-6.7B-instruct-GGUF_deepseek-coder-6.7b-instruct.Q4_K_M.gguf.jsonl`).
  In v3 they are replaced by `<EMAIL_REDACTED>`. Only the contents of string
  literals changed, so scores are unaffected. The same replacement was made in
  the v3.4 template-test transcripts (`rerun-2026-10/results-tpl/transcripts/`,
  seven distinct addresses, 11 occurrences in eight answers across three
  files) before release; their serial scores were
  recomputed afterwards and are unchanged.
- In `rerun-2026-10/logs.tar.xz`, the public key that Ollama generated for
  the pod at start-up is replaced by `<OLLAMA_PUBLIC_KEY_REDACTED>`, and
  `RUN_PLAN.md` names the pod's storage volume `<RUNPOD_VOLUME>`.

## Upstream disclosure

The five tags were reported in
[ollama/ollama#18252](https://github.com/ollama/ollama/issues/18252). The
report, its corrections and the withdrawal of the v1 defect claims are
tracked in `disclosure/DISCLOSURES.md`.

## License

Code is released under the MIT License (see `LICENSE`). The audit dataset
(everything under `results*/`, the data files in `rerun-2026-10/`, and the
top-level JSON data files) is released under CC BY 4.0. The calibration
texts are not included.

## Citing

```bibtex
@article{patodiya2026brokenonarrival,
  title   = {Broken on Arrival: Non-Functional Quantized {LLM} Artifacts in
             Public Model Registries and How to Catch Them},
  author  = {Patodiya, Aditi},
  journal = {arXiv preprint arXiv:2609.05881},
  year    = {2026},
  doi     = {10.48550/arXiv.2609.05881},
  url     = {https://arxiv.org/abs/2609.05881}
}
```

GitHub's "Cite this repository" button (from `CITATION.cff`) emits the same
citation in BibTeX and APA. If quantcheck catches a broken artifact for you,
an issue report here is welcome.
