---
base_model:
- cross-encoder/ettin-reranker-17m-v1
language:
- en
library_name: sentence-transformers
tags:
- modernbert
- typed-decisions
- classification
- scoring
- custom-code
---

<p align="center">
  <img src="https://storage.googleapis.com/secons-site-images/other/huggingface/bekko/bekko-system-one-logo.webp" alt="bekko-system-one" width="520">
</p>

# bekko-system-one-v0-17m

**bekko-system-one-v0-17m** is the **17M** variant of Bekko System One v0: a family of compact English encoder models for **Choice, Noul, and Score** decisions. Supply instructions, application state, and candidate definitions; `model.predict()` returns candidate probabilities, a selected option, or a numeric rubric score.

Bekko System One is an **experimental project exploring whether ultra-small models can become capable System One Decision Models**, in the same category as [TypeSafe AI’s Jev](https://docs.typesafe.ai/concepts/system-one). The v0 family spans **17M, 68M, and 400M** parameters. It builds on Ettin rerankers with a shared-prefix encoder and task-specific heads, evaluating supplied alternatives in parallel without generating text.

> [!NOTE]
> **Why v0?** These models can perform very well on some tasks, but **fall far behind Jev 1.13 on S1MB’s benchmarks designed to measure generalization**. Their training data also includes dataset families represented in the evaluation, so strong results on those tasks do not establish broad generalization. The **v0** designation reflects this limited generalization.

[Release article](https://huggingface.co/blog/hotchpotch/bekko-system-one-v0-release/) · [Model collection](https://huggingface.co/collections/hotchpotch/bekko-system-one-v0-6abc47eab9c4fe4ec83d9e30) · [Browser demo](https://huggingface.co/spaces/hotchpotch/bekko-system-one-in-browser) · [Training and inference code](https://github.com/hotchpotch/bekko-system-one) · [Training dataset](https://huggingface.co/datasets/hotchpotch/bekko-system-one-dataset-v0) · [S1MB leaderboard](https://huggingface.co/spaces/hotchpotch/S1MB-leaderboard)

## ✨ Highlights

- **Typed outputs:** Choice selects among named alternatives; Noul estimates the probability of an authored binary condition; Score returns the expected value of a numeric rubric.
- **Compact models:** the 17M variant has 16.80M total parameters and 3.90M excluding lookup-only embeddings. Its browser ONNX model file is about 29 MB.
- **Shared computation:** instructions and state are encoded once per unique tokenized prefix within a microbatch and reused across candidates.
- **Standalone inference:** CPU or CUDA execution through the exported `BekkoSentenceTransformer`, with optional FlashAttention 2 and `torch.compile()`.
- **Inspectable evaluation:** S1MB reports both specialized-task results and a separate view of six synthetic instruction-and-context benchmarks.

## Model family and browser inference

| Variant | Total params | Active params (AP) | Browser ONNX file |
| --- | ---: | ---: | ---: |
| [17M](https://huggingface.co/hotchpotch/bekko-system-one-v0-17m) | 17M | 4M | 29 MB |
| [68M](https://huggingface.co/hotchpotch/bekko-system-one-v0-68m) | 68M | 42M | 196 MB |
| [400M](https://huggingface.co/hotchpotch/bekko-system-one-v0-400m) | 395M | 343M | 1.43 GB |

Parameter counts are rounded; **M** means million. **AP** excludes lookup-only token embeddings; it is not a memory estimate. File sizes use decimal MB/GB and cover only `onnx_browser/model.onnx`, excluding tokenizer, runtime, and working memory.

The [browser demo](https://huggingface.co/spaces/hotchpotch/bekko-system-one-in-browser) offers 17M and 68M with CPU/WebGPU execution. Browser exports quantize the token lookup table to row-wise INT8; transformer blocks and task heads remain FP32. These are not fully INT8 models. Demo examples illustrate selected tasks and should not be used as evidence of broad generalization.

## 🚀 Quickstart

Install the runtime dependencies:

```bash
pip install 'torch>=2.10,<2.11' 'transformers==5.17.0' 'sentence-transformers==6.1.0' 'safetensors>=0.7' 'tqdm>=4.67'
```

Use a CUDA-compatible PyTorch installation for GPU inference. Load the model and its inference class directly from the Hub:

```python
import json
from transformers.dynamic_module_utils import get_class_from_dynamic_module

repo = "hotchpotch/bekko-system-one-v0-17m"
model = get_class_from_dynamic_module(
    "inference_v0.BekkoSentenceTransformer", repo,
)(repo, trust_remote_code=True, device="cpu")  # Or "cuda".

inputs = {
    "state_json": json.dumps({
        "message": "I was charged twice for the same order. Please refund the duplicate payment."
    }),
    "decisions": [{
        "id": "department",
        "kind": "judgment",
        "type": "choice",
        "instructions_json": json.dumps("Which department should handle this request?"),
        "system_prompt": "",
        "criteria": [
            {
                "id": "billing",
                "description_json": json.dumps("Payments, charges, and refunds"),
                "value": None,
            },
            {
                "id": "technical",
                "description_json": json.dumps("Technical failures and configuration"),
                "value": None,
            },
        ],
        "documents": [],
        "scoring": None,
    }],
}

result = model.predict(inputs)
print(result["department"]["selected_id"])
print(result["department"]["probabilities"])
```

The output contains the selected candidate ID and the distribution over candidate IDs. Predictions depend on the checkpoint and input; no example probabilities are asserted here.

> [!TIP]
> This loads `BekkoSentenceTransformer`, which exposes `model.predict()` directly for both single input objects and batches. For reproducible loading, pass the same `revision="FULL_COMMIT_HASH"` to both calls above. Raw-text `encode()` is not the typed-decision interface.

## Decision types

| Type | Input candidates | Output |
| --- | --- | --- |
| **Choice** | Named alternatives with descriptions | `selected_id`, `probabilities` |
| **Noul** | Authored yes/no meanings, using IDs `true`/`false` or `yes`/`no` | `probability_yes`, `probabilities` |
| **Score** | A rubric with at least two distinct, explicit numeric values | `score`, `normalized_score`, `probabilities`, `values` |

An input object can contain several decisions sharing the same state. Decision IDs must be unique within an input object; they may repeat across input objects in a batch. Pass only the input object, without training targets or dataset metadata.

For Noul, define what both outcomes mean. For example:

```python
inputs["decisions"].append({
    "id": "refund",
    "kind": "judgment",
    "type": "noul",
    "instructions_json": json.dumps("Is the customer requesting a refund?"),
    "system_prompt": "",
    "criteria": [
        {"id": "false", "description_json": json.dumps("No refund is requested."), "value": None},
        {"id": "true", "description_json": json.dumps("The customer asks for money back."), "value": None},
    ],
    "documents": [],
    "scoring": None,
})

result = model.predict(inputs)
print(result["refund"]["probability_yes"])
```

For Score, each criterion supplies a `value` alongside its `id` and `description_json`. The numeric score is the probability-weighted expectation of these values. A rubric with values `0`, `2`, and `4` produces a score on the 0–4 scale; `normalized_score` maps that expectation to 0–1 using the minimum and maximum criterion values. Numbers are not inferred from labels or descriptions.

The runtime also supports relative document ranking with `kind="ranking"`, `type=None`, `scoring="relative"`, an empty `criteria` list, and `documents` containing `id` and `content_json`. It returns `probabilities` and an `order` of document IDs. Ranking probabilities are relative to the supplied candidate set; they are not numeric rubric scores. Runtime support alone does not establish ranking quality for this checkpoint.

## 📊 Evaluation: S1MB

[S1MB — System One Mosaic Benchmark](https://github.com/hotchpotch/S1MB) combines **137 typed benchmarks**: 59 Noul, 57 Choice, and 21 Score. The active evaluation release contains **106 subsets, 14,009 cases, and 26,269 judgments**. One case may contain several judgments. It combines existing dataset-derived tasks with synthetic tasks; it is not a collection consisting exclusively of original official test splits.

The tables below reproduce the **two-decimal comparison snapshot collected on September 30, 2026**. They show the compared models below 500M reported total parameters, plus Jev 1.13 as a reference. Jev's parameter count is not reported here. All listed models have coverage of 137 benchmarks in the full evaluation and six in the synthetic view.

### Full evaluation

| Model | Task Avg | Noul | Choice | Score | Total params | AP |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Jev 1.13](https://docs.typesafe.ai/models) | 59.59 | 64.63 | 67.22 | 46.92 | — | — |
| [bekko-system-one-v0-400m](https://huggingface.co/hotchpotch/bekko-system-one-v0-400m) | 50.60 | 51.24 | 61.32 | 39.25 | 395M | 343M |
| [bekko-system-one-v0-68m](https://huggingface.co/hotchpotch/bekko-system-one-v0-68m) | 40.46 | 42.91 | 51.62 | 26.85 | 68M | 42M |
| [bekko-system-one-v0-17m](https://huggingface.co/hotchpotch/bekko-system-one-v0-17m) | 27.57 | 31.43 | 35.57 | 15.70 | 17M | 4M |
| [von](https://huggingface.co/wfzyx/von) | 16.21 | 20.15 | 23.99 | 4.48 | 395M | 343M |
| [laya-typed-decisions](https://huggingface.co/convaiinnovations/laya-typed-decisions) | 15.00 | 20.06 | 18.93 | 5.99 | 421M | 370M |
| [laya](https://huggingface.co/convaiinnovations/laya) | 13.36 | 20.19 | 16.31 | 3.58 | 421M | 370M |
| [laya-multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) | 9.28 | 14.01 | 13.03 | 0.79 | 322M | 125M |

> [!NOTE]
> **Task Avg** is the equal-weight mean of Noul, Choice, and Score. Each task first averages its benchmark scores after baseline adjustment and clipping to 0–100. Higher is better; these values are **not raw accuracy**. Noul uses adjusted balanced accuracy, Choice compares selected-target mass against trivial answer policies, and Score compares normalized expected-value MAE against a constant prediction.

These tables are sorted by Task Avg. The leaderboard defaults to a different measure, **Borda Score**: rank points averaged equally across benchmarks. Borda depends on the comparison-model cohort and gives more weight to tasks with more benchmarks. See the [scoring specification](https://github.com/hotchpotch/S1MB/blob/main/evaluator/SCORING.md).

### Synthetic instruction-and-context benchmarks

The six benchmarks contain **600 English cases** authored and self-reviewed using GPT-6-Astra: Diverse and Contextual sets of 100 cases for each decision type. Contextual sets contain 20 families of five cases. Choice and Score vary state while retaining the question and criteria; Noul varies state and the authored binary criteria.

| Model | Task Avg | General Noul | General Choice | General Score |
| --- | ---: | ---: | ---: | ---: |
| [Jev 1.13](https://docs.typesafe.ai/models) | 96.27 | 99.00 | 98.68 | 91.14 |
| [bekko-system-one-v0-400m](https://huggingface.co/hotchpotch/bekko-system-one-v0-400m) | 54.48 | 52.00 | 80.30 | 31.14 |
| [von](https://huggingface.co/wfzyx/von) | 43.19 | 41.00 | 65.89 | 22.67 |
| [bekko-system-one-v0-68m](https://huggingface.co/hotchpotch/bekko-system-one-v0-68m) | 32.48 | 31.00 | 57.52 | 8.90 |
| [laya-typed-decisions](https://huggingface.co/convaiinnovations/laya-typed-decisions) | 31.82 | 22.00 | 58.80 | 14.67 |
| [laya](https://huggingface.co/convaiinnovations/laya) | 26.50 | 9.00 | 54.83 | 15.67 |
| [bekko-system-one-v0-17m](https://huggingface.co/hotchpotch/bekko-system-one-v0-17m) | 18.96 | 15.00 | 39.11 | 2.76 |
| [laya-multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) | 14.47 | 6.00 | 36.67 | 0.75 |

Bekko falls far behind Jev 1.13 on these S1MB benchmarks designed to measure generalization, especially for the smaller variants. This is the main limitation behind the **v0** designation. The labels are author-intended, not independently human-validated gold. The benchmarks probe adaptation to supplied instructions and context; they do not establish unseen-task generalization or training-data non-overlap.

### Evaluation conditions and interpretation

Bekko was trained on related dataset families. The recorded training and evaluation manifests have **77 identically named subsets** in common; this indicates task-family exposure, not a count of leaked test examples. High scores on the full collection should be read alongside the synthetic-task results.

The displayed tables are a rounded reporting snapshot, not a new evaluation. Model folders can contain results from different recorded dataset revisions. Use the per-benchmark model revision, dataset SHA, adapter settings, and input hashes in the [results repository](https://huggingface.co/datasets/hotchpotch/s1mb-result) for reproducibility. Bekko v0 uses native adaptive input budgeting and truncation, so its input handling should not be assumed identical to full-input adapters. Small score differences are not accompanied by confidence intervals.

[Evaluation dataset](https://huggingface.co/datasets/hotchpotch/s1mb-dataset) · [Leaderboard](https://huggingface.co/spaces/hotchpotch/S1MB-leaderboard) · [Run an evaluation](https://github.com/hotchpotch/S1MB/blob/main/docs/evaluation.md) · [Submit results](https://github.com/hotchpotch/S1MB/blob/main/docs/contributing_results.md)

## Architecture and input limits

| Item | Value |
| --- | --- |
| Model | `hotchpotch/bekko-system-one-v0-17m` |
| Base model | [`cross-encoder/ettin-reranker-17m-v1`](https://huggingface.co/cross-encoder/ettin-reranker-17m-v1) |
| Architecture | ModernBERT-compatible shared-prefix encoder |
| Decision heads | Choice, Noul, Score |
| Candidate representation | Mean pooling over candidate branch tokens |
| Probabilities | Softmax over candidates within each decision |
| Inference API | `BekkoSentenceTransformer.predict()` |
| Native inference limits | Context 7,999; query cap 7,997; candidate cap 3,800 tokens, with adaptive allocation |
| Training / browser branch limits | Query 4,096; candidate 2,048 tokens |

The prefix combines instructions and state and is encoded bidirectionally. At each layer, candidates attend to that shared prefix and to their own tokens. The prefix does not attend to candidates, and candidate branches do not attend to each other. This lets the model reuse prefix K/V across candidates instead of repeatedly encoding the same state.

Candidate representations incorporate the prefix through attention before pooling. This changes the attention pattern compared with a conventional cross-encoder; it is not an exact acceleration of unrestricted cross-attention. State and instructions are encoded together, so changing the instructions requires recomputing the prefix. Reuse is within an inference microbatch, not a persistent state-only cache.

Native query and candidate caps are not independently available at their maxima: the runtime shares the context budget and lends unused capacity between branches. Browser exports use their own recorded limits. Neither a configured token limit nor support for a backend establishes accuracy or latency at that limit.

## Attention backend

`BekkoSentenceTransformer` defaults to `attn_implementation="auto"`. On a CUDA GPU
with compute capability 8.0 or later, it prefers FlashAttention 2 when a compatible
`flash_attn` native library is available. Otherwise it uses PyTorch SDPA. CPU
inference uses SDPA. Select the backend explicitly when loading:

```python
Model = get_class_from_dynamic_module(
    "inference_v0.BekkoSentenceTransformer", repo,
)
model = Model(
    repo, trust_remote_code=True, device="cuda",
    attn_implementation="flash_attention_2",  # Or "sdpa" or "auto".
)
print(model[0].attn_implementation)  # The backend actually selected.
```

For reproducibility, pin the same model/code revision in both calls, as in the
quickstart. `model_kwargs={"attn_implementation": "flash_attention_2"}` is also
accepted by this class. Conflicting top-level and nested options raise an error.

| Selection | Behavior |
| --- | --- |
| `auto` (default) | Prefer compatible FA2 on a supported CUDA GPU; otherwise use SDPA |
| `sdpa` | Always use SDPA; do not import the external FA2 library |
| `flash_attention_2` | Require FA2; fail during model loading if the GPU is unsupported or the library is missing/binary-incompatible |

FA2 is optional and is not installed by the minimal dependency command above.
Install a `flash-attn` wheel matching your Python, PyTorch and CUDA versions.
When using the Bekko training repository, `uv sync --locked --extra fa2` installs
its pinned optional wheel. An explicit FA2 request never silently falls back.
Selection happens at model loading; reload with the desired device/backend when
changing devices. Use `BekkoSentenceTransformer` for backend selection, rather
than plain `SentenceTransformer`.

As a sizing guideline, **17M models generally have little speed difference
between SDPA and FA2**, making SDPA a practical choice without an extra dependency.
For **68M and larger models, prefer FA2 for throughput**, especially on long
inputs. This is not a speed guarantee for every checkpoint, GPU or workload;
measure representative inputs after warmup and exclude model loading time.

The optimized SDPA path reuses attention masks and rotary-position tensors within
a forward pass and computes local prefix attention in blocks. FA2 keeps valid
tokens packed through attention and feed-forward layers. Both preserve the native
rendering, adaptive input budgets, task heads and candidate order. BF16 rounding
can change individual probabilities and occasionally the selected candidate;
do not assume bitwise-equivalent predictions when switching backends.

The standalone CLI also accepts `--attn-implementation auto`, `sdpa`, or
`flash_attention_2`.

## Inference optimization: compilation and batching

**Compiled SDPA gave the lowest single-request median for all three model sizes
in our short-input RTX 5090 measurements:** 1.38 ms for 17M, 2.69 ms for 68M and
5.03 ms for 400M on a four-option Choice request. A batch of 32 distinct requests
finished in 3.24 ms with 17M, about 9,900 questions/second.

For similar short requests, start with SDPA and compilation, then measure your
own inputs after warmup. Use batching when throughput matters. These results do
not establish the fastest backend for long contexts or larger workloads.

### Compile and warm up the model

Use the `inputs` object from Quickstart and load the model with `device="cuda"`
and `attn_implementation="sdpa"`, as in the attention-backend example above.
Load it once, then compile and warm up before serving requests:

```python
model.compile_inference(mode="reduce-overhead", dynamic=True)

# This benchmark used 32 warmup calls; your workload may need a different count.
for _ in range(32):
    model.predict(inputs, batch_size=1, show_progress_bar=False)

result = model.predict(inputs, batch_size=1, show_progress_bar=False)
```

Warm up representative input lengths and candidate counts. The first calls include
compilation, and new shapes can require more preparation even with `dynamic=True`.
Call `model.disable_compile()` to restore eager execution.

<details>
<summary>Compilation behavior and runtime requirements</summary>

`compile_inference()` without arguments uses `mode="default"`, `dynamic=True`,
and the Inductor backend. `reduce-overhead` additionally uses CUDA Graphs where
supported to reduce CPU dispatch overhead, which is useful for small batches.
It reuses execution machinery, not answers: each request is evaluated again.
Only the encoder is compiled; rendering, tokenization, task heads and typed output
construction remain eager.

CUDA Graphs can
also retain additional GPU working memory. Synchronize CUDA before and after a
timed call when benchmarking. See the [PyTorch compilation modes](https://docs.pytorch.org/docs/stable/generated/torch.compile).

CUDA inference uses BF16 autocast; CPU inference uses FP32. The runtime does not require PEFT, datasets, or W&B. SDPA needs no external attention package; FA2 requires the compatible optional library described above.

</details>

### Batch independent requests

Pass a list of input objects to batch across cases:

```python
batch_inputs = [inputs, inputs]  # Replace with your application inputs.
results = model.predict(
    batch_inputs,
    batch_size=128,
    token_budget=64000,
    show_progress_bar=True,
)
```

If compilation is enabled, warm up representative batches too; single-request
warmup does not cover every batch shape.

Results follow the original input order. A single input dict returns a result dict; a list returns a list. Progress is enabled by default, counts completed input objects, and is written to stderr.

<details>
<summary>Batch options and input-length limits</summary>

| Option | Default | Meaning |
| --- | --- | --- |
| `batch_size` | `128` | Maximum cases rendered per window and decisions tokenized per window |
| `token_budget` | `64000` | Approximate padded query-plus-document work per microbatch |
| `context_length` | Backbone positional capacity | Shared query/candidate token budget per decision |
| `query_length` | Exported cap; fresh exports use context minus 2 | Query token cap, including special tokens |
| `document_length` | Fresh exports use `min(3800, context - 3)` | Per-candidate token cap, including special tokens |
| `show_progress_bar` | `True` | Display input progress |
| `prefix_layout` | Exported checkpoint setting | `instruction_state` or `state_instruction` |

Length bucketing reduces padding within each window. Complete decisions stay together; a decision exceeding `token_budget` runs alone. The budget is a work estimate, not a strict memory cap. Lower it if a batch exceeds available memory.

Length overrides apply only to the current call. Adaptive allocation shares the context equally between query and candidate, then lends unused capacity subject to each branch's cap; candidates receive the odd token. `context_length` cannot exceed the backbone's positional capacity. Queries follow the checkpoint's truncation policy; the v0 recipe uses balanced truncation. Candidates are right-truncated. Increasing a limit does not establish model quality at that length.

</details>

### Measured performance on RTX 5090

Measured October 1, 2026 through the pinned public Hub inference API. Each request
contains one question; single-request calls use `batch_size=1`. Compiled results
use `compile_inference(mode="reduce-overhead", dynamic=True)` with Inductor.
Times include text preparation, tokenization, GPU inference and output construction;
they exclude loading, compilation/warmup, network and queueing time. p50 is the
median; p95 is the 95th percentile. These synthetic short-input probes measure
speed, not quality or representative production performance.

| Model | Attention | Single p50: eager → compiled | Compiled single p95 | Compiled 32-request batch p50 | Batch questions/s |
| --- | --- | ---: | ---: | ---: | ---: |
| 17M | SDPA | 4.60 → **1.38 ms** | 1.54 ms | **3.24 ms** | **9,875** |
| 17M | FA2 | 4.61 → 1.71 ms | 2.00 ms | 3.60 ms | 8,885 |
| 68M | SDPA | 9.56 → **2.69 ms** | 3.02 ms | **6.17 ms** | **5,189** |
| 68M | FA2 | 9.01 → 3.00 ms | 3.42 ms | 6.47 ms | 4,948 |
| 400M | SDPA | 12.83 → **5.03 ms** | 5.44 ms | 17.84 ms | 1,794 |
| 400M | FA2 | 12.95 → 5.32 ms | 5.77 ms | **17.44 ms** | **1,835** |

**Batch throughput is different from response latency.** The 17M batch figure
amortizes to about 0.10 ms/question, but the entire batch returns after 3.24 ms;
it does not make an individual request return in 0.10 ms. Questions/second is
`32 / median_batch_seconds`, with no network or queueing time. Waiting to collect
a batch adds latency in a live service.

For these short inputs, compiled SDPA gave the lowest single-request median for
all three sizes. FA2 slightly improved the 400M batch median. Select the backend
explicitly at loading time to reproduce a row: `attn_implementation="sdpa"` or
`"flash_attention_2"`. This small-input comparison does not establish the fastest
backend for long contexts or larger workloads.

<details>
<summary>Noul and Score timings</summary>

Short Noul and Score requests were measured as well. With compiled SDPA, their
single-request medians were:

| Model | Noul, 2 alternatives | Score, 3 levels |
| --- | ---: | ---: |
| 17M | 1.38 ms | 1.34 ms |
| 68M | 2.76 ms | 2.65 ms |
| 400M | 5.11 ms | 4.84 ms |

</details>

<details>
<summary>Measurement protocol, environment and pinned revisions</summary>

#### Workload and timing boundaries

The Choice input is `{"message": "Please refund a duplicate charge.", "ticket": 1000}`
with the instruction "Which department should respond?" and four alternatives:
Payments and refunds, Technical failures, Product sales, and Account access.
Tickets vary from 1000 to 1031, giving 32 distinct tokenized query prefixes.
Native rendering produces **30–31 query tokens and 8–11 tokens per candidate**,
including special/task tokens. Templates and candidate descriptions repeat;
the runtime can deduplicate candidate tokenization within each batch.

The Noul probe asks whether the customer requests a refund, with explicit true
and false definitions; it uses 55–56 query tokens and 12–14 tokens per candidate.
The Score probe asks how urgent the request is, with Not urgent / Somewhat urgent /
Very urgent mapped to 0 / 1 / 2; it uses 31–32 query tokens and 8–9 per candidate.
All inputs fit without truncation. These are synthetic speed probes, not quality
measurements or a representative production traffic distribution.

Each model/backend pair runs sequentially in a fresh process. For each execution
mode, 32 warmup calls per task and batch size precede timing; single calls rotate
through the 32 inputs. Each table entry uses 200 single-request calls or 100
32-request batches. Wall-clock timing synchronizes CUDA before and after every
call. Loading, compilation/warmup, HTTP, queueing and post-call validation are
excluded; native input preparation and output construction are included. p50 is
the median and p95 uses the nearest-rank convention. These are local observations
on a shared host, not latency guarantees; in particular, 400M is not consistently
below 5 ms.

All timed outputs passed finite-probability and normalization checks. Across these
probes, the largest absolute probability difference from the same backend's eager
batch reference was about 0.0153, with no top-candidate changes.
This comparison includes batching and compilation effects; it does not imply
bitwise parity or unchanged predictions on other inputs.

#### Measured environment and model revisions

- **GPU:** one NVIDIA GeForce RTX 5090, physical GPU 1 (`CUDA_VISIBLE_DEVICES=1`),
  idle before the experiment; model/backend runs execute sequentially.
- **Host:** AMD Ryzen 9 9950X, Linux 6.8.0-139-generic, four PyTorch CPU threads.
- **CUDA stack:** NVIDIA driver 580.126.09, PyTorch 2.10.0+cu130 (CUDA 13.0),
  Triton 3.6.0; native BF16 CUDA autocast with FP32 heads, no precision overrides.
- **Python/runtime:** Python 3.12.12, Transformers 5.17.0, Sentence Transformers 6.1.0.
- **Attention:** FlashAttention 2.8.3 installed and explicitly selected for FA2
  rows; SDPA rows use PyTorch attention. Installing FA2 alone does not make a
  forced SDPA run use it. Use a compatible FA2 wheel for your PyTorch/CUDA build.
- **Batch settings:** one decision per request, window size 1 or 32,
  `token_budget=64000`, default exported input limits, `show_progress_bar=False`.

Code and weights were pinned to the same revision for each model:
[17M `2147c3d`](https://huggingface.co/hotchpotch/bekko-system-one-v0-17m/tree/2147c3d9d00559bf972616c2589eee967e266f3b),
[68M `6eb1bae`](https://huggingface.co/hotchpotch/bekko-system-one-v0-68m/tree/6eb1bae2d35066b0d634fabaf8c79beafc6fd9f1),
and [400M `4aeb85b`](https://huggingface.co/hotchpotch/bekko-system-one-v0-400m/tree/4aeb85b9d4042d75d8b8adf6ff7ba9e4629510ba).
Pass the full SHA as `revision` to both the remote class lookup and the model
constructor when reproducing these measurements.

</details>

<details>
<summary>Historical full-benchmark timing</summary>

For a historical RTX 5090 run, the 17M model evaluated 26,269 judgments across 137 S1MB benchmarks in **13.33 seconds summed inside the evaluator timer**. The run used BF16 autocast, FlashAttention 2, batch size 128, token budget 64,000, native adaptive truncation, and no compilation. This includes evaluation work; it is neither GPU-kernel-only time nor single-request latency. Model loading and outer orchestration are outside that timer. The measured model revision was [`c3a8277`](https://huggingface.co/hotchpotch/bekko-system-one-v0-17m/tree/c3a8277bc94db411568ff95da9343b0c84e35124); this historical run used locally flattened inference code and does not measure the current Hub packaging or browser export.

</details>

## Training

The models use full-parameter fine-tuning of [Ettin rerankers](https://huggingface.co/cross-encoder/ettin-reranker-17m-v1), without LoRA, on [bekko-system-one-dataset-v0](https://huggingface.co/datasets/hotchpotch/bekko-system-one-dataset-v0). The recorded release contains **153 training subsets, 6,589,190 cases, and 8,421,789 judgments**, converted into the three decision types from NLP and retrieval datasets, including synthetic sources.

The recorded runs use a cap of 300,000 judgments per dataset, uniform sampling, a full capped-pool training budget, and equal instruction-first/state-first layout weights. Each run reports **8,421,789 trained judgments and 16,517 updates**, with seed 42. The training data revision is [`c6a49c4`](https://huggingface.co/datasets/hotchpotch/bekko-system-one-dataset-v0/tree/c6a49c450fe88b40bd96c54d9cc19fafd9aa5591).

| Setting | 17M | 68M | 400M |
| --- | ---: | ---: | ---: |
| Optimizer | AdamW | AdamW | AdamW |
| Batch size | 512 | 512 | 512 |
| Encoder learning rate | 1e-4 | 3e-5 | 1e-5 |
| Head learning rate | 5e-4 | 2e-4 | 2e-4 |
| Weight decay | 0.01 | 0.01 | 0.01 |
| Warmup ratio | 10% | 10% | 10% |
| Schedule | Cosine | Cosine | Cosine |
| Recorded training-loop time | 2.10 h | 5.57 h | 25.34 h |

Durations are training-loop receipts, not complete process wall times. This recipe was selected from experiments; it is not established as optimal. Training code is available in [bekko-system-one](https://github.com/hotchpotch/bekko-system-one).

## Intended use and limitations

Use Bekko for English routing, binary judgments, and rubric-based assessment where you can define alternatives and evaluate performance on representative application data.

- **Limited generalization:** strong performance on a familiar task does not imply comparable performance on arbitrary instructions or new domains. Inspect the synthetic-task results above.
- **Probabilities:** outputs are not guaranteed to be calibrated. Changing candidate definitions or the candidate set can change the distribution; validate thresholds for your application.
- **Input truncation:** adaptive budgets can remove relevant evidence. Review the effective input limits for your runtime and workload.
- **Language:** this release targets English. Multilingual results from the Bekko Embedding family do not carry over to these models.
- **Evaluation scope:** S1MB shares dataset families with training, and its synthetic labels have not received independent human validation. Neither view proves absence of training overlap.

## License

The released-weight and bundled inference-code license declaration remains to be finalized. This card does not assign a license. Training and evaluation sources retain their own licenses and usage terms; those terms are not replaced by a model license. Consult the [training dataset source records](https://huggingface.co/datasets/hotchpotch/bekko-system-one-dataset-v0/blob/main/SOURCES.md) and each upstream source.

## Author

Yuichi Tateno — [@hotchpotch](https://github.com/hotchpotch)

## What is Bekko?

*Bekko* (/ˈbek.koː/) is a coined name inspired by two Japanese traditions:

- **Akabeko (赤べこ):** the red ox cherished as a protective charm against illness and misfortune.
- **Bekko-iro (鼈甲色):** a traditional Japanese color with a warm, translucent, amber-like hue.

The name brings together the red ox's protective spirit and the beauty of that amber color.

## 🔎 Also check out Bekko Embedding

I also build **[bekko-embedding](https://huggingface.co/collections/hotchpotch/bekko-embedding-v1-6a67fe8b50d2656663555e21)**, a family of ultra-small, high-performance multilingual embedding models for semantic search. If you are interested in compact models for multilingual retrieval, take a look at the collection for models, benchmarks, and usage examples.
