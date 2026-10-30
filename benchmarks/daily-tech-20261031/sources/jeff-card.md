---
license: apache-2.0
base_model: Qwen/Qwen3.5-0.8B
base_model_relation: finetune
library_name: transformers
pipeline_tag: zero-shot-classification
language:
- en
tags:
- jeff
- decision-model
- calibration
- system-1
- local
---

# jeff-base

**Jeff v1.3: the base model for Jeff's LoRA adapters.** Jeff is a small decision model, a fine-tune of
[Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B). You describe a situation (the *state*) and list the options
in plain words; Jeff returns a calibrated probability for each option from one forward pass, with no generated text to
parse. This repository is versioned by tag: use revision **`v1.3`**.

Catalogue, results and docs: **[jeffhub.ai](https://jeffhub.ai)**.

**Jeff v1.3 is a change in direction. Zero-shot on everything is no longer the goal: the base is always meant to be used
with an adapter.** The base is the foundation the adapters are trained on, and an adapter is where Jeff becomes good at
a task. If you want zero-shot use without an adapter, use Jeff v1.2
([mstrasser/Jeff-Qwen3.5-0.8B](https://huggingface.co/mstrasser/Jeff-Qwen3.5-0.8B), revision `v1.2`).

## The base: live-last

The v1.3 base is trained on the same data as v1.2, with one change: the **live-last** prompt layout. The fixed part of
the prompt (instructions and options) comes first and the changing input comes last, so prefix caching works and
repeated decisions over the same options get faster.

The price: the model now reads all the options before it sees the input, and a 0.8B model is much worse at going back
over a long option list than at reading the options with the input already in mind. On the general panel (4,599
questions) the two bases are level: 78.6% against 78.8%, calibration error 0.028 against 0.024. On unfamiliar tasks
with long option lists, the base on its own falls apart:

| Task (no adapter) | Options | v1.2 base | v1.3 base |
|---|--:|--:|--:|
| [support-intents](https://jeffhub.ai/adapters/support-intents) | 7–64 | 85.1% | 24.2% |
| [legal-clauses](https://jeffhub.ai/adapters/legal-clauses) | 100 | 66.0% | 7.4% |
| [tools](https://jeffhub.ai/adapters/tools) | 4–136 | 57.8% | 30.2% |
| [triage](https://jeffhub.ai/adapters/triage) | 2–36 | 67.1% | 47.6% |

With the right adapter, almost all of it comes back: v1.3 + adapter is within −0.7 to +0.3 points of v1.2 + adapter on
every task except legal-clauses (83.6% against 85.7%). Once the adapter has learned the options, putting them first
costs almost nothing, and the fixed part of the prompt can be cached. We decided the speed-up was worth it.

## What each adapter adds

Each adapter is scored on its full held-out test set, which it never trained on, three ways on the same rows: the
untrained Qwen3.5-0.8B that Jeff is built from, the Jeff v1.3 base alone, and the v1.3 base with the adapter. Across the
13 measured adapters, mean accuracy goes from 30.4% untrained to 38.6% for the base alone and 92.8% with the adapter.

| Adapter | Test rows | Qwen3.5-0.8B untrained | Jeff base v1.3 alone | Jeff base v1.3 + adapter |
|---|--:|--:|--:|--:|
| [aml](https://huggingface.co/mstrasser/jeff-adapter-aml) | 5,120 | 36.0% · 0.022 | 40.5% · 0.103 | 95.0% · 0.012 |
| [emotion](https://huggingface.co/mstrasser/jeff-adapter-emotion) | 5,408 | 12.6% · 0.045 | 25.3% · 0.080 | 60.5% · 0.018 |
| [ground](https://huggingface.co/mstrasser/jeff-adapter-ground) | 4,160 | 28.9% · 0.061 | 50.9% · 0.079 | 96.6% · 0.007 |
| [guard](https://huggingface.co/mstrasser/jeff-adapter-guard) | 6,552 | 43.8% · 0.064 | 49.4% · 0.158 | 98.2% · 0.004 |
| [legal-clauses](https://huggingface.co/mstrasser/jeff-adapter-legal-clauses) | 9,895 | 12.5% · 0.094 | 7.4% · 0.039 | 83.6% · 0.011 |
| [nav](https://huggingface.co/mstrasser/jeff-adapter-nav) | 3,300 | 12.6% · 0.038 | 13.6% · 0.156 | 97.3% · 0.006 |
| [sanctions](https://huggingface.co/mstrasser/jeff-adapter-sanctions) | 4,909 | 34.4% · 0.010 | 68.3% · 0.080 | 100.0% · 0.001 |
| [soc](https://huggingface.co/mstrasser/jeff-adapter-soc) | 4,929 | 22.1% · 0.011 | 33.8% · 0.032 | 94.1% · 0.014 |
| [spam](https://huggingface.co/mstrasser/jeff-adapter-spam) | 3,897 | 58.1% · 0.047 | 69.8% · 0.046 | 98.1% · 0.006 |
| [support-intents](https://huggingface.co/mstrasser/jeff-adapter-support-intents) | 5,577 | 33.9% · 0.164 | 24.2% · 0.095 | 96.3% · 0.003 |
| [tools](https://huggingface.co/mstrasser/jeff-adapter-tools) | 5,157 | 17.9% · 0.063 | 30.2% · 0.016 | 97.2% · 0.007 |
| [trading-desk](https://huggingface.co/mstrasser/jeff-adapter-trading-desk) | 5,000 | 38.6% · 0.015 | 41.1% · 0.115 | 98.1% · 0.010 |
| [triage](https://huggingface.co/mstrasser/jeff-adapter-triage) | 7,256 | 44.1% · 0.098 | 47.6% · 0.059 | 91.5% · 0.015 |
| [code](https://huggingface.co/mstrasser/jeff-adapter-code) | not measured yet | | | |
| [code-router](https://huggingface.co/mstrasser/jeff-adapter-code-router) | not measured yet | | | |

Each cell: accuracy · calibration error (ECE, 15 bins, after each model's own fitted temperature; lower is better, 0
is perfect). Calibration error measures how far the stated confidence is from the real hit rate: 0.01 means the stated
confidence is, on average, about 1 percentage point away from how often those answers are right.

**Jeff-Code.** The [code](https://huggingface.co/mstrasser/jeff-adapter-code) and
[code-router](https://huggingface.co/mstrasser/jeff-adapter-code-router) adapters make two decisions for Qwen3.8-27B in
the Jeff-Code coding agent ([github.com/firelex/jeff-code](https://github.com/firelex/jeff-code)). With Jeff's thinking
threshold at 0.6 (step threshold 0.40), Jeff-Code matches Qwen3.8-27B's pass rate on five benchmarks: pooled 53.4%
against 53.1% over 493 tasks, run side by side in paired blocks. It needs about 20% less time per session on average on
the big benchmarks, SWE-rebench and Terminal-Bench 2.0; the typical session is 30–45% faster. These numbers were
supplied by the maintainers; the detailed result files are still to come. [The details](https://jeffhub.ai/adapters/code).

### v1.3 + adapter against v1.2 + adapter

Each pair is measured on exactly the same test rows. Where an adapter's v1.3 test set changed, the comparison uses its
copy of the v1.2 test (named in brackets).

| Adapter test set | Test rows | v1.2 + adapter | v1.3 + adapter | Change (points) |
|---|--:|--:|--:|--:|
| emotion | 5,408 | 60.6% | 60.5% | −0.1 |
| ground | 4,160 | 97.0% | 96.6% | −0.4 |
| guard | 6,552 | 98.4% | 98.2% | −0.2 |
| legal-clauses | 9,895 | 85.7% | 83.6% | −2.1 |
| nav (heldout_synthetic-v12) | 3,300 | 97.0% | 97.3% | +0.3 |
| spam (test-v12) | 3,603 | 98.4% | 98.1% | −0.3 |
| support-intents | 5,577 | 96.8% | 96.3% | −0.5 |
| tools | 5,157 | 97.9% | 97.2% | −0.7 |
| triage | 7,256 | 91.8% | 91.5% | −0.3 |

All results and their sources: [jeffhub.ai/results](https://jeffhub.ai/results), and in one file,
[jeffhub-v1.3.json](https://jeffhub.ai/data/jeffhub-v1.3.json).

## The adapters

Adapters are not merged into the base. You load one base and all the adapters you need, and pick one per request. Each
adapter records the exact base it was trained on, and the server refuses an adapter trained on a different one, so a
v1.2 adapter does not load on v1.3. All nine v1.2 adapters are retrained on v1.3, with about 10% of the base model's own
training data mixed in as a precaution (its effect has not been measured).

| Adapter | Repository | LoRA GGUF |
|---|---|---|
| aml | [mstrasser/jeff-adapter-aml](https://huggingface.co/mstrasser/jeff-adapter-aml) | [jeff-adapter-aml-gguf](https://huggingface.co/mstrasser/jeff-adapter-aml-gguf) |
| code | [mstrasser/jeff-adapter-code](https://huggingface.co/mstrasser/jeff-adapter-code) | [jeff-adapter-code-gguf](https://huggingface.co/mstrasser/jeff-adapter-code-gguf) |
| code-router | [mstrasser/jeff-adapter-code-router](https://huggingface.co/mstrasser/jeff-adapter-code-router) | [jeff-adapter-code-router-gguf](https://huggingface.co/mstrasser/jeff-adapter-code-router-gguf) (Q8_0 base only) |
| emotion | [mstrasser/jeff-adapter-emotion](https://huggingface.co/mstrasser/jeff-adapter-emotion) | [jeff-adapter-emotion-gguf](https://huggingface.co/mstrasser/jeff-adapter-emotion-gguf) |
| ground | [mstrasser/jeff-adapter-ground](https://huggingface.co/mstrasser/jeff-adapter-ground) | [jeff-adapter-ground-gguf](https://huggingface.co/mstrasser/jeff-adapter-ground-gguf) |
| guard | [mstrasser/jeff-adapter-guard](https://huggingface.co/mstrasser/jeff-adapter-guard) | [jeff-adapter-guard-gguf](https://huggingface.co/mstrasser/jeff-adapter-guard-gguf) |
| legal-clauses | [mstrasser/jeff-adapter-legal-clauses](https://huggingface.co/mstrasser/jeff-adapter-legal-clauses) | [jeff-adapter-legal-clauses-gguf](https://huggingface.co/mstrasser/jeff-adapter-legal-clauses-gguf) |
| nav | [mstrasser/jeff-adapter-nav](https://huggingface.co/mstrasser/jeff-adapter-nav) | [jeff-adapter-nav-gguf](https://huggingface.co/mstrasser/jeff-adapter-nav-gguf) |
| sanctions (CC BY-NC 4.0) | [mstrasser/jeff-adapter-sanctions](https://huggingface.co/mstrasser/jeff-adapter-sanctions) | [jeff-adapter-sanctions-gguf](https://huggingface.co/mstrasser/jeff-adapter-sanctions-gguf) |
| soc (CC BY-NC 4.0) | [mstrasser/jeff-adapter-soc](https://huggingface.co/mstrasser/jeff-adapter-soc) | [jeff-adapter-soc-gguf](https://huggingface.co/mstrasser/jeff-adapter-soc-gguf) |
| spam | [mstrasser/jeff-adapter-spam](https://huggingface.co/mstrasser/jeff-adapter-spam) | [jeff-adapter-spam-gguf](https://huggingface.co/mstrasser/jeff-adapter-spam-gguf) |
| support-intents | [mstrasser/jeff-adapter-support-intents](https://huggingface.co/mstrasser/jeff-adapter-support-intents) | [jeff-adapter-support-intents-gguf](https://huggingface.co/mstrasser/jeff-adapter-support-intents-gguf) |
| tools | [mstrasser/jeff-adapter-tools](https://huggingface.co/mstrasser/jeff-adapter-tools) | [jeff-adapter-tools-gguf](https://huggingface.co/mstrasser/jeff-adapter-tools-gguf) |
| trading-desk | [mstrasser/jeff-adapter-trading-desk](https://huggingface.co/mstrasser/jeff-adapter-trading-desk) | [jeff-adapter-trading-desk-gguf](https://huggingface.co/mstrasser/jeff-adapter-trading-desk-gguf) |
| triage | [mstrasser/jeff-adapter-triage](https://huggingface.co/mstrasser/jeff-adapter-triage) | [jeff-adapter-triage-gguf](https://huggingface.co/mstrasser/jeff-adapter-triage-gguf) |

In a request, an adapter is named by its short name (`"model": "soc"`, `"model": "code"`): on a Jeff server, names
starting with "jeff" mean the base model.

## How to use it

Adapter serving arrives with the next Jeff release; until then, these commands need the `feat/lora` branch of
[firelex/jeff](https://github.com/firelex/jeff).

```bash
git clone https://github.com/firelex/jeff && cd jeff
uv sync --no-default-groups --extra lora                # CPU; add --extra cuda (NVIDIA GPU) or --extra mac (Apple silicon)
uv run --no-default-groups hf download mstrasser/jeff-base --revision v1.3 --local-dir checkpoints/jeff-base
uv run --no-default-groups hf download mstrasser/jeff-adapter-support-intents --revision v1.3 \
  --local-dir adapters/support-intents
JEFF_CHECKPOINT=checkpoints/jeff-base JEFF_ADAPTERS=adapters/ PORT=8765 \
  uv run --no-default-groups jeff-serve                 # on a Mac, add JEFF_BACKEND=mlx
```

Each adapter lives in its own folder inside one adapters folder; the folder's name is the name you use in requests.
Name the adapter as the model:

```bash
curl -s localhost:8765/v1/systemone -H 'content-type: application/json' -d '{
  "model": "support-intents",
  "state": {"service": "Customer support chat of an online shop",
            "message": "I sent the jacket back two weeks ago and still have not seen the money."},
  "questions": {
    "intent": {"type": "choice",
               "instructions": "What does the customer want? Choose the request that best matches what the customer is asking for in their message.",
               "criteria": {"track_refund": "Check the status of a refund they are expecting.",
                            "get_refund": "Get their money back for a purchase.",
                            "track_order": "Find out where their order is or its current status."}}
  }
}'
```

Guides: [getting started](https://jeffhub.ai/docs/getting-started), [request format](https://jeffhub.ai/docs/request-format),
[serving adapters](https://jeffhub.ai/docs/serving-adapters).

**llama.cpp.** v1.3 also ships as GGUF, in Q8_0 and Q4_K_M:
[mstrasser/jeff-base-gguf](https://huggingface.co/mstrasser/jeff-base-gguf), plus one small LoRA GGUF per adapter. See
[Running Jeff with llama.cpp](https://jeffhub.ai/docs/llama-cpp).

## What stays fixed for the life of v1.3

- The [request format](https://jeffhub.ai/docs/request-format): state, questions and instructions, with the changing
  state field last.
- The option rules: named options, keys never bare numbers.
- The answer format: a probability for every option.

A data set written to the [data guidelines](https://jeffhub.ai/docs/data-guidelines) trains on v1.3 as it is.

## Files

| File | What it is |
|---|---|
| `model.safetensors` | The weights (sha256 `d324dd6c9bb61b30af65564135b33f6892c30a9b2bd22667b2e09b9c8118cf77`; every v1.3 adapter checks it) |
| `readout.safetensors` | Jeff's readout over the answer codes |
| `decision_config.json` | Answer codes and their token ids, temperature and prompt layout (`live-last`) |
| `config.json`, `tokenizer.json`, `tokenizer_config.json`, `chat_template.jinja`, `processor_config.json` | The Qwen3.5 configuration and tokenizer |

## Training

| | |
|---|---|
| Built on | Qwen/Qwen3.5-0.8B |
| Data | The v1.2 base training data, unchanged |
| Change from v1.2 | The live-last prompt layout |
| Checkpoint | `ll-v12-final` (run `ll-v12`, step 1113) |

## Not measured yet

Nothing on JeffHub is estimated. These are still to come for v1.3:

- code and code-router: test scores (untrained, v1.3 base, v1.3 + adapter);
- jeff-serve speed and GPU memory with the v1.3 base and adapters, with prompt reuse;
- calibration charts, commonest confusions and accuracy per answer for each v1.3 adapter;
- example responses recorded from the v1.3 adapters;
- Jeff-Code: the detailed result files behind the maintainer-supplied numbers;
- Jeff's memory on a Mac.

## Limitations

- Use it with an adapter. Without one, the v1.3 base is weak on long, unfamiliar option lists (table above). For
  zero-shot use, use v1.2.
- Small models don't reason. Expect fast, calibrated choices between the options you describe, not multi-step
  reasoning, and no generated text.
- English and text only.

## Licence and data

Weights: **Apache-2.0**, as for v1.2. The model is a fine-tune of Qwen3.5-0.8B by the Qwen team (Alibaba Cloud).

Training data: the same data as the v1.2 base, which mixes public data sets under various licences, some of them
share-alike (CC BY-SA), with code-built and synthetic questions. The training data is not released. The v1.2 data
sources and their licences are listed in
[docs/data-sources.md](https://github.com/firelex/jeff/blob/main/docs/data-sources.md) in the Jeff repository.

**To confirm:** JeffHub does not yet list the base model's data sources and their licences for v1.3; the list above is
the v1.2 one, which the v1.3 base reuses unchanged.

Each adapter's own data sources and licences, including non-commercial restrictions (sanctions and soc are
CC BY-NC 4.0), are on its model card and its [JeffHub page](https://jeffhub.ai).

## Links

- Release notes: [Jeff v1.3: what changed](https://jeffhub.ai/docs/release-notes-v1-3)
- Results: [jeffhub.ai/results](https://jeffhub.ai/results)
- Code and server: [github.com/firelex/jeff](https://github.com/firelex/jeff)
- GGUF: [mstrasser/jeff-base-gguf](https://huggingface.co/mstrasser/jeff-base-gguf)
- Jeff v1.2 (zero-shot): [mstrasser/Jeff-Qwen3.5-0.8B](https://huggingface.co/mstrasser/Jeff-Qwen3.5-0.8B), revision `v1.2`

Jeff is an independent project. It uses the same request format as Jev but is not affiliated with or endorsed by
TypeSafe, the makers of Jev.
