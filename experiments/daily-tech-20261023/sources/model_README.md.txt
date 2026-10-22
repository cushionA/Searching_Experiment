---
license: apache-2.0
base_model: Qwen/Qwen3.5-0.8B
library_name: peft
language:
- zh
- en
tags:
- lora
- searchjev
- search-agent
- system-1
- calibration
- decision-model
datasets:
- SearchJev/search_jev_unified_v2_release_20260928
---

# SearchJev-0.8B

SearchJev is a fast and calibrated **System-1 model for search agents**. Given a search state and a decision
schema (e.g. *is this document relevant?*, *is the evidence sufficient?*, *where should the next query go?*),
it returns a calibrated probability distribution over the legal values of every field in a single forward pass,
without autoregressive decoding. In a dual-system search agent it takes the short decisions around a System-2 LLM
and hands low-confidence ones back to it.

This repository holds the LoRA adapter of **SearchJev-0.8B** on
[Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B), together with its per-question-type
temperatures and option-label alphabet.

- Code (training and inference): <https://github.com/EvoScientist/Search_Jev>
- Training data: [SearchDecision-Bench](https://huggingface.co/datasets/SearchJev/search_jev_unified_v2_release_20260928)
- Training logs: [W&B report](https://forge.coreweave.com/wandb/R-DTeam/Search_JEV/reports/Search_Jev-Report--VmlldzoxODAzMTUwMw)
- Paper: *SearchJev: A Fast and Calibrated System-1 Model for Search Agents*

## Usage

```bash
git clone https://github.com/EvoScientist/Search_Jev && cd Search_Jev
pip install -r requirements.txt          # a CUDA GPU is required (Qwen3.5 linear-attention kernels)
```

```python
from huggingface_hub import snapshot_download
from training.inference import JEVPredictor

predictor = JEVPredictor(snapshot_download("SearchJev/SearchJev-0.8B"), device="cuda", merge_lora=True)
result = predictor.decide(
    state={"question": "When was the Eiffel Tower completed?",
           "documents": ["The Eiffel Tower was completed in March 1889 for the World's Fair."]},
    question="Is the evidence sufficient to answer the question now?",
    question_type="noul",                      # noul (yes/no) | choice | score
)
print(result.choice, result.confidence, result.probabilities)
```

The calibrated temperatures in `calibration.json` are applied automatically
(choice 1.11, yes/no 0.88, ordinal score 0.96).

## Results

In-distribution test sets of SearchDecision-Bench (the decision-quality table of the paper).
Relevance: NDCG@10 / ECE; other families: accuracy / ECE (all x100); latency: p50 ms per decision at batch size 1
on one H200. AR: the same backbone before training, emitting JSON under constrained decoding.

| Model | Relevance | Sufficiency | Routing | Navigation | Rewriting | Verification | Latency (ms) |
|---|---|---|---|---|---|---|---|
| Qwen3.5-0.8B AR (JSON) | 85.7 / 15.7 | 49.9 / 24.2 | 24.7 / 24.0 | 42.0 / 19.7 | 40.6 / 32.1 | 56.8 / 9.5 | 144.2 |
| **SearchJev-0.8B** | 98.6 / 5.8 | 89.4 / 5.1 | 83.2 / 5.0 | 56.8 / 4.6 | 84.0 / 2.3 | 91.7 / 9.3 | 27.6 |

## Training

LoRA (rank 16, alpha 32, dropout 0.05) on the attention, Gated DeltaNet, and MLP projections; one epoch over the
683,130 training decisions of SearchDecision-Bench (5,176 steps, effective batch 132 on 6 H200 GPUs); AdamW,
learning rate 5e-5, cosine schedule with 5% warm-up, bf16; loss = soft-label cross-entropy + 0.5 x Brier score,
with half weight for LLM-assigned labels, demonstrations, and constructed rewrites; one temperature per question
type fitted on 5,000 validation decisions. `config.resolved.yaml` is the exact configuration of the run.

## License

Apache-2.0, like the Qwen3.5 base model.