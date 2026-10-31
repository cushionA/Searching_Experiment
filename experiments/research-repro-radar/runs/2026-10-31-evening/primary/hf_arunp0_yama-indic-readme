---
license: apache-2.0
library_name: thx01
pipeline_tag: zero-shot-classification
base_model: FacebookAI/xlm-roberta-large
language: [en, hi, bn, ta, te, mr, gu, kn, ml, pa, ur, or, az, kk, uz, ka, tr, fa, ru, de, fr, es, ar, zh, ja, ko, multilingual]
datasets:
  - arunp0/laya-indic-synthetic
  - ai4bharat/sangraha
  - arunp0/laya-indic-real-text
  - arunp0/yama-indic-gemma
  - AmazonScience/massive
  - PolyAI/banking77
  - heegyu/news-category-dataset
  - MoritzLaurer/synthetic_zeroshot_mixtral_v0.1
  - nvidia/HelpSteer2
  - brighter-dataset/BRIGHTER-emotion-categories
  - google/civil_comments
  - google-research-datasets/go_emotions
  - nvidia/Nemotron-Safety-Guard-Dataset-v3
  - nvidia/Aegis-AI-Content-Safety-Dataset-2.0
  - coastalcph/lex_glue
  - RicardoRei/wmt-da-human-evaluation
  - ucberkeley-dlab/measuring-hate-speech
  - ibm-research/argument_quality_ranking_30k
  - CohereLabs/aya_redteaming
  - ucirvine/sms_spam
metrics: [accuracy]
tags:
  - zero-shot-classification
  - text-classification
  - intent-classification
  - sentiment-analysis
  - topic-classification
  - decision-model
  - calibration
  - routing
  - multilingual
  - indic
  - indian-languages
  - hinglish
  - code-mixed
  - hindi
  - bengali
  - tamil
  - telugu
  - marathi
  - gujarati
  - kannada
  - malayalam
  - punjabi
  - urdu
  - odia
  - xlm-roberta
  - rlcd
---

# Yama: zero-shot text classifier for Indian languages and Hinglish

**Yama (`yama-indic`) is an open-weights, Apache-2.0 zero-shot text classifier for Hindi, Hinglish, 10 other Indian
languages and English.** You pass a text and plain-English questions with your own labels; it returns a calibrated
probability for every label in one forward pass, without fine-tuning. It is a 586M-parameter encoder (XLM-RoBERTa-large),
not a generative LLM, so answers are fast, deterministic and cheap to run.

| On tasks Yama never trained on (33 tasks, 19 languages) | Accuracy |
|---|---|
| **Yama v3** | **0.814** |
| MoritzLaurer/bge-m3-zeroshot-v2.0 | 0.770 |
| joeddav/xlm-roberta-large-xnli | 0.560 |
| MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 | 0.478 |
| facebook/bart-large-mnli | 0.374 |

Same test samples and harness for every model; details under [Comparison with other zero-shot classifiers](#comparison-with-other-zero-shot-classifiers).

**Use it for:** intent detection and routing, support-ticket triage, sentiment, topic and news classification, yes/no
checks ("is this a complaint?") and 1–N ratings ("how urgent?"), on text in Hindi (Devanagari or romanised), Hinglish,
Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Urdu, Odia and English. Labels, questions and
the number of options are defined at inference time, so one model handles many tasks.

## How it works

Yama is a **typed-decision model**. You give it a text (a message, ticket, review, headline or document) and one or more
questions written in plain English. It answers every question in a single forward pass, with a calibrated probability
for each option. Question schemas are defined at inference time, so it handles new labels and new tasks without
retraining.

**The name.** In Indian tradition Yama is also *Dharmaraja*, the judge who weighs what is placed before him and gives a
verdict. This model does the same with the options you give it.

**Successor to `laya-indic`.** Yama is v3 of [`arunp0/laya-indic`](https://huggingface.co/arunp0/laya-indic), released
as a separate model because the encoder changed. The v1 and v2 (mmBERT-base) weights stay in that repository.

Yama is an independent personal release. It is not affiliated with or endorsed by HAL-X AI. It uses the THX-01 decision
architecture and the open-source [`thx01`](https://pypi.org/project/thx01/) library (Apache-2.0, HAL-X AI), but it is
**built on the [`FacebookAI/xlm-roberta-large`](https://huggingface.co/FacebookAI/xlm-roberta-large) encoder (MIT)**,
not on THX-01's weights. All training data is public and permissively licensed, plus labels generated and verified by
open-weights models (Qwen3, Qwen3.6, Gemma 4).
As far as can be verified, no proprietary-model output was used. The one exception that can't be fully checked is NVIDIA's Nemotron Safety Guard v3, which is partly machine-translated and synthetic and doesn't name the models used; see `LICENSES.md`.

| | |
|---|---|
| Parameters | about 586M (XLM-R-large encoder plus a 2-layer decision head) |
| Input | up to 512 tokens per question (question, options and text) |
| Question types | `choice`, `noul` (yes/no), `score` (ordinal) |
| Training | 3 epochs over 591,217 decisions: 285,078 from 17 public datasets, 173,226 verified Qwen3 synthetic questions, 117,646 verified questions about real Indian-language text and 15,267 verified cause/effect and passage yes/no questions |
| Languages | trained on 30+ languages; strongest on English, Hindi, Hinglish and 10 other Indian languages |
| Licence | Apache-2.0 |

## What's new in v3

- **A larger encoder.** v3 is built on XLM-R-large (560M) instead of mmBERT-base. Overall accuracy rises from 0.800 to
  0.862 and mean calibration error falls from 0.091 to 0.070. The biggest gains are in Indian languages: SIB-200 Indic
  topic 0.670 → 0.844, IndicSentiment 0.884 → 0.950, MASSIVE Indic 0.849 → 0.893.
- **Causal reasoning is no longer at chance.** IndicCOPA rises from 0.513 to 0.624. Telugu and Gujarati, which had no
  cause/effect training data at all, improved as much as the other languages (0.528 → 0.634, 0.529 → 0.632).
- **More real Indian-language text.** 54,198 new questions about real Sangraha text, written by Gemma 4 and verified by
  Qwen3.6, with extra weight on Odia and Urdu, which v2 barely covered.
- **Trade-offs.** About twice the compute per decision of v2, and the input limit is 512 tokens instead of 1,024.
  Banking77 (0.819 → 0.791) and SMS spam (0.964 → 0.944) are lower than v2. BoolQ-hi is unchanged (0.618 → 0.616).

## Question types

| type | returns | criteria |
|---|---|---|
| `choice` | one of N options, with a probability for each | `{"key": "description", ...}` (descriptions may be `null`) |
| `noul` | P(yes) | none |
| `score` | an ordinal level and its distribution | an ordered list of level descriptions |

THX-01 also supports `number`, `excerpt` and `"cite": true`. **This model was not trained for those** and does not
support them.

## Quickstart

```python
import thx01   # pip install thx01

agent = thx01.load("arunp0/yama-indic")          # or a local directory; GPU if available
# v2 (previous model): thx01.load("arunp0/laya-indic")

text = "Kal se mera UPI payment fail ho raha hai, paise kat gaye par merchant ko nahi mile"
out = agent.system_one(text, {
    "topic":  {"type": "choice", "instructions": "What is the customer writing about?",
               "criteria": {"failed_payment": "payment failed or money debited but not received",
                            "refund": "asking for a refund", "account": "login or account access", "other": None}},
    "urgent": {"type": "noul", "instructions": "Does the customer need help urgently?"},
    "anger":  {"type": "score", "instructions": "How frustrated is the customer?",
               "criteria": ["calm", "mildly annoyed", "clearly frustrated", "furious"]},
})
# topic  -> failed_payment (0.96)
# urgent -> P(yes) 0.74
# anger  -> "clearly frustrated" 0.88
```

The response's `model` field says `"THX-01"`. That is the `thx01` library's default label; the weights are this model's.

For many texts, use `agent.predict_batch(texts, questions, batch_size=64)`. `questions` can be one schema shared by every
text or a list with one schema per text.

Write questions in English and leave the text in its own language and script. That is how the model was trained.

## Benchmarks

Zero-shot accuracy on 67 tasks, compared with [THX-01](https://huggingface.co/doofz/THX-01) (322M, same architecture,
roughly 2.1M training decisions) and with v2 ([`laya-indic`](https://huggingface.co/arunp0/laya-indic) v2). All models were scored with the same harness, the same
prompts and the same test samples. The harness is adapted from THX-01's open benchmark code (`bench/tasks.py`).

| Task family | Tasks | THX-01 | v2 | **v3** |
|---|---|---|---|---|
| Overall (all tasks) | 67 | 0.758 | 0.800 | 0.862 |
| English general (AG News, Emotion, Banking77, SMS, MASSIVE en) | 7 | 0.814 | 0.851 | **0.853** |
| MASSIVE scenario, THX's 14 languages | 14 | 0.871 | 0.878 | **0.901** |
| MASSIVE intent@20 + yes/no (az, hi) | 4 | 0.916 | 0.919 | **0.931** |
| SIB-200 topic, THX's 10 languages | 10 | 0.768 | 0.779 | **0.844** |
| SIB-200 topic, Indic (hi + 9) | 10 | 0.630 | 0.670 | **0.844** |
| MASSIVE scenario, Indic (bn ta te kn ml ur) | 6 | 0.669 | 0.849 | **0.893** |
| IndicSentiment (6 languages) | 6 | 0.878 | 0.884 | **0.950** |
| IndicCOPA (6 languages) | 6 | 0.524 | 0.513 | **0.624** |
| Hinglish-TOP (domain, intent@20) | 2 | 0.789 | 0.940 | **0.942** |
| Synthetic held-out schemas (Qwen3) | 4 | 0.718 | 0.872 | **0.924** |

Per task: v3 is ahead of THX-01 on 63 of 67 tasks, behind on 3, tied on 1, and ahead of v2 on 60, behind on 6, tied on 1.

### Comparison with other zero-shot classifiers

Four widely used zero-shot classifiers (the Hub's most-downloaded one, bart-large-mnli, which is English-only, and
three multilingual ones), scored on the same test samples with the same harness. Only `choice` tasks are compared (60 tasks), because NLI models have no native yes/no-question or
rating mode. For the NLI models each option becomes the hypothesis "This text is about {option description}." (COPA
alternatives are used as-is), and probabilities are a softmax over entailment scores, as in the Hugging Face
`zero-shot-classification` pipeline.

| Task family | Tasks | **Yama v3** | bart-large-mnli | mDeBERTa-v3-base-xnli | xlm-roberta-large-xnli | bge-m3-zeroshot-v2.0 |
|---|---|---|---|---|---|---|
| **Not in Yama's training data** | 33 | **0.814** | 0.374 | 0.478 | 0.560 | 0.770 |
| SIB-200 topic (19 languages) | 19 | **0.843** | 0.273 | 0.321 | 0.504 | 0.788 |
| IndicSentiment (6 languages) | 6 | **0.950** | 0.502 | 0.857 | 0.768 | 0.888 |
| IndicCOPA (6 languages) | 6 | **0.624** | 0.479 | 0.575 | 0.553 | 0.586 |
| AG News | 1 | 0.878 | 0.785 | 0.774 | 0.709 | **0.910** † |
| DAIR Emotion | 1 | 0.535 | 0.491 | 0.316 | 0.279 | **0.693** † |
| Indian-language subset of the above | 22 | **0.813** | 0.347 | 0.532 | 0.586 | 0.753 |
| *Train split in Yama's data (not zero-shot for Yama)* | 27 | *0.902* | *0.200* | *0.304* | *0.405* | *0.669* |

Full model ids: `facebook/bart-large-mnli`, `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`,
`joeddav/xlm-roberta-large-xnli`, `MoritzLaurer/bge-m3-zeroshot-v2.0`.

- On the 33 tasks not in its training data, Yama is ahead of bart-large-mnli, mDeBERTa-xnli and xlm-roberta-large-xnli
  on every task, and ahead of bge-m3-zeroshot-v2.0 on 29, behind on 3 (AG News, DAIR Emotion, IndicCOPA Marathi) and
  tied on 1. Mean calibration error on these tasks is 0.092 for Yama and for bge-m3-zeroshot-v2.0, 0.100 for
  bart-large-mnli, 0.140 for xlm-roberta-large-xnli and 0.223 for mDeBERTa-xnli.
- The bottom row (MASSIVE, Banking77, Hinglish-TOP) is shown for completeness only: Yama was trained on those
  datasets' training splits and the NLI models were not.
- Sanity check: our harness gives the NLI models scores at or above their own published results (for example
  bart-large-mnli on AG News: 0.785 accuracy here, 0.703 macro-F1 on the bge-m3-zeroshot card), so the prompt wording
  is not handicapping them.
- † The bge-m3-zeroshot-v2.0 card reports for AG News and DAIR Emotion only the score of a run trained on up to 500
  examples per class of those datasets (0.892 and 0.697 F1), and our scores match those, so its AG News and Emotion
  results are probably not zero-shot.
- Yama's own synthetic training data includes generic topic, sentiment and emotion questions (not these datasets), so
  "not in training data" means the dataset, not the task type.

**Read these numbers with the training data in mind.** None of the models saw these test sets. Some tasks, however, have
their *training splits* or similar task formats in one model's data and not in the other's:

| Eval family | This model trained on | THX-01 |
|---|---|---|
| MASSIVE | train split, 20 languages (incl. hi bn ta te kn ml ur, az ka tr fa) | train splits ("public benchmarks' training splits") |
| Hinglish-TOP | train + validation splits | not stated |
| Banking77, SMS spam | Banking77 train; SMS messages outside the 1,000-message test sample | not stated |
| AG News | **nothing** (licence unclear, so excluded) | not stated individually |
| SIB-200 | **nothing** (CC-BY-SA, so excluded); synthetic topic sentences and Qwen3/Gemma-labelled topics on real Sangraha text | not stated |
| IndicCOPA | **no IndicCOPA data.** New in v3: 7,782 cause/effect questions in the COPA format, written by Gemma 4 from real Sangraha text in 9 languages. **Telugu and Gujarati were deliberately left out** to measure transfer | not stated |
| BoolQ-hi | **no BoolQ data.** New in v3: 7,485 passage yes/no questions on real Sangraha text in 6 languages. **Hindi was deliberately left out** | not stated |
| IndicSentiment, DAIR Emotion | **nothing**; synthetic reviews and emotion datasets instead | not stated |

All training texts, including the Sangraha corpus the real-text questions were cut from (line by line), were
deduplicated against every evaluation set, using normalised exact match.

<details>
<summary>Per-task results (accuracy and expected calibration error)</summary>

| Task | n | THX-01 acc | v2 acc | v3 acc | THX-01 ECE | v3 ECE |
|---|---|---|---|---|---|---|
| AG News (4) | 1000 | 0.931 | 0.864 | 0.878 | 0.009 | 0.043 |
| DAIR Emotion (6) | 1025 | 0.427 | 0.492 | 0.535 | 0.261 | 0.284 |
| Banking77 (77) | 1001 | 0.658 | 0.819 | 0.791 | 0.047 | 0.086 |
| SMS spam (yes/no) | 1000 | 0.883 | 0.964 | 0.944 | 0.101 | 0.035 |
| MASSIVE-intent@20 (en) | 1000 | 0.929 | 0.925 | 0.933 | 0.065 | 0.052 |
| Intent yes/no (en) | 1000 | 0.961 | 0.967 | 0.962 | 0.035 | 0.030 |
| MASSIVE-intent@20 (az) | 1000 | 0.909 | 0.881 | 0.905 | 0.051 | 0.059 |
| Intent yes/no (az) | 1000 | 0.948 | 0.945 | 0.948 | 0.028 | 0.025 |
| MASSIVE-scenario (en) | 500 | 0.906 | 0.926 | 0.930 | 0.089 | 0.054 |
| MASSIVE-scenario (az) | 500 | 0.878 | 0.862 | 0.900 | 0.061 | 0.051 |
| MASSIVE-scenario (ru) | 500 | 0.896 | 0.900 | 0.908 | 0.067 | 0.045 |
| MASSIVE-scenario (tr) | 500 | 0.874 | 0.866 | 0.886 | 0.084 | 0.067 |
| MASSIVE-scenario (de) | 500 | 0.884 | 0.902 | 0.918 | 0.068 | 0.048 |
| MASSIVE-scenario (fr) | 500 | 0.894 | 0.902 | 0.912 | 0.066 | 0.042 |
| MASSIVE-scenario (es) | 500 | 0.868 | 0.902 | 0.892 | 0.070 | 0.056 |
| MASSIVE-scenario (ar) | 500 | 0.830 | 0.844 | 0.838 | 0.067 | 0.053 |
| MASSIVE-scenario (hi) | 500 | 0.856 | 0.892 | 0.900 | 0.051 | 0.050 |
| MASSIVE-scenario (zh-CN) | 500 | 0.872 | 0.898 | 0.910 | 0.072 | 0.057 |
| MASSIVE-scenario (fa) | 500 | 0.894 | 0.890 | 0.920 | 0.075 | 0.053 |
| MASSIVE-scenario (ka) | 500 | 0.762 | 0.728 | 0.870 | 0.055 | 0.064 |
| MASSIVE-scenario (ja) | 500 | 0.908 | 0.922 | 0.926 | 0.066 | 0.052 |
| MASSIVE-scenario (ko) | 500 | 0.868 | 0.856 | 0.910 | 0.053 | 0.045 |
| SIB-200 (az) | 204 | 0.779 | 0.770 | 0.853 | 0.091 | 0.062 |
| SIB-200 (en) | 204 | 0.833 | 0.819 | 0.828 | 0.068 | 0.077 |
| SIB-200 (ru) | 204 | 0.779 | 0.848 | 0.848 | 0.076 | 0.058 |
| SIB-200 (tr) | 204 | 0.779 | 0.745 | 0.853 | 0.101 | 0.052 |
| SIB-200 (de) | 204 | 0.819 | 0.819 | 0.848 | 0.086 | 0.070 |
| SIB-200 (ar) | 204 | 0.774 | 0.779 | 0.828 | 0.069 | 0.068 |
| SIB-200 (hi) | 204 | 0.721 | 0.730 | 0.868 | 0.132 | 0.047 |
| SIB-200 (zh) | 204 | 0.794 | 0.828 | 0.843 | 0.066 | 0.072 |
| SIB-200 (kk) | 204 | 0.701 | 0.760 | 0.843 | 0.126 | 0.062 |
| SIB-200 (uz) | 204 | 0.701 | 0.691 | 0.824 | 0.127 | 0.083 |
| Hinglish-TOP domain | 1000 | 0.887 | 0.969 | 0.976 | 0.057 | 0.043 |
| Hinglish-TOP intent@20 | 1000 | 0.691 | 0.910 | 0.907 | 0.050 | 0.058 |
| MASSIVE-intent@20 (hi) | 1000 | 0.877 | 0.904 | 0.911 | 0.033 | 0.044 |
| Intent yes/no (hi) | 1000 | 0.932 | 0.945 | 0.960 | 0.028 | 0.021 |
| BoolQ-hi (yes/no) ‡ | 1000 | 0.647 | 0.618 | 0.616 | 0.186 | 0.236 |
| MASSIVE-scenario (bn) | 500 | 0.698 | 0.846 | 0.884 | 0.077 | 0.045 |
| MASSIVE-scenario (ta) | 500 | 0.700 | 0.848 | 0.870 | 0.077 | 0.043 |
| MASSIVE-scenario (te) | 500 | 0.622 | 0.838 | 0.900 | 0.082 | 0.050 |
| MASSIVE-scenario (kn) | 500 | 0.614 | 0.854 | 0.906 | 0.066 | 0.051 |
| MASSIVE-scenario (ml) | 500 | 0.652 | 0.846 | 0.908 | 0.056 | 0.077 |
| MASSIVE-scenario (ur) | 500 | 0.728 | 0.862 | 0.892 | 0.059 | 0.055 |
| SIB-200 (mr) | 204 | 0.647 | 0.726 | 0.863 | 0.180 | 0.046 |
| SIB-200 (bn) | 204 | 0.623 | 0.662 | 0.819 | 0.168 | 0.061 |
| SIB-200 (ta) | 204 | 0.691 | 0.696 | 0.833 | 0.111 | 0.060 |
| SIB-200 (te) | 204 | 0.632 | 0.657 | 0.858 | 0.131 | 0.042 |
| SIB-200 (gu) | 204 | 0.618 | 0.672 | 0.833 | 0.135 | 0.065 |
| SIB-200 (kn) | 204 | 0.608 | 0.662 | 0.843 | 0.181 | 0.050 |
| SIB-200 (ml) | 204 | 0.529 | 0.598 | 0.853 | 0.248 | 0.073 |
| SIB-200 (pa) | 204 | 0.564 | 0.603 | 0.819 | 0.171 | 0.062 |
| SIB-200 (ur) | 204 | 0.672 | 0.691 | 0.853 | 0.138 | 0.055 |
| IndicSentiment (hi) | 998 | 0.916 | 0.927 | 0.957 | 0.099 | 0.019 |
| IndicSentiment (bn) | 998 | 0.868 | 0.878 | 0.949 | 0.065 | 0.022 |
| IndicSentiment (mr) | 998 | 0.894 | 0.908 | 0.949 | 0.080 | 0.017 |
| IndicSentiment (ta) | 998 | 0.869 | 0.847 | 0.951 | 0.063 | 0.019 |
| IndicSentiment (te) | 998 | 0.859 | 0.868 | 0.943 | 0.059 | 0.021 |
| IndicSentiment (gu) | 998 | 0.861 | 0.877 | 0.953 | 0.060 | 0.016 |
| IndicCOPA (hi) | 449 | 0.550 | 0.501 | 0.617 | 0.168 | 0.249 |
| IndicCOPA (mr) | 449 | 0.510 | 0.514 | 0.604 | 0.184 | 0.258 |
| IndicCOPA (bn) | 500 | 0.522 | 0.518 | 0.626 | 0.186 | 0.233 |
| IndicCOPA (ta) | 500 | 0.540 | 0.490 | 0.634 | 0.153 | 0.228 |
| IndicCOPA (te) | 500 | 0.532 | 0.528 | 0.634 | 0.175 | 0.232 |
| IndicCOPA (gu) | 448 | 0.491 | 0.529 | 0.632 | 0.219 | 0.216 |
| Qwen3-synth held-out schemas (choice) | 1008 | 0.726 | 0.845 | 0.911 | 0.119 | 0.053 |
| Qwen3-synth held-out (Hinglish) | 719 | 0.729 | 0.890 | 0.940 | 0.128 | 0.035 |
| Qwen3-synth held-out schemas (noul) | 1342 | 0.752 | 0.930 | 0.961 | 0.116 | 0.034 |
| Qwen3-synth held-out schemas (score) | 650 | 0.663 | 0.823 | 0.886 | 0.109 | 0.045 |

ECE is expected calibration error with 15 equal-width bins; lower is better. Mean ECE over the 67 tasks is 0.070 for v3, 0.091 for v2 and 0.097 for THX-01.

‡ BoolQ-hi is left out of every mean, as in earlier versions. Our harness had a label-parsing bug in the first runs of this
benchmark; all rows here were scored after the fix.

</details>

**Where v3 is stronger than THX-01 and v2:**
- Indian-language topic classification (SIB-200 Indic +0.21 over THX-01, +0.17 over v2) and sentiment (+0.07 over THX-01).
- Low-resource languages THX-01 also covers: Georgian, Kazakh, Uzbek, Azerbaijani, Turkish.
- Causal reasoning in Indian languages (IndicCOPA +0.10), including the two languages it had no data for.
- New decision schemas it has never seen (synthetic held-out +0.21 over THX-01).

**Where it is weaker:**
- AG News (0.878 vs 0.931 for THX-01). THX-01 reports training on public benchmarks' training splits; AG News is excluded here.
- Banking77 and SMS spam are a little lower than v2.
- BoolQ-hi (0.616) is below THX-01 (0.647); passage yes/no questions remain hard.
- IndicCOPA (0.624) is well above chance but far from strong.

## Training

**Method.** RLCD (Reinforcement Learning for Calibrated Decisions), as introduced by THX-01 and implemented in `thx01`:
- **Policy-gradient term:** sampling from noisy logits, rewarded by a strictly proper scoring rule.
- **Soft cross-entropy term:** on label-smoothed targets.
- **Calibration:** a temperature for each question type, fitted after training on held-out data.

Decision training starts from `FacebookAI/xlm-roberta-large` with a freshly initialised decision head. (Continued
pretraining on Sangraha, which helped v2, gave no measurable gain on XLM-R-large, so v3 does not use it.)

| | |
|---|---|
| Epochs | 2, then a third epoch at one fifth of the learning rate |
| Batch | 16 × 2 gradient accumulation = 32 decisions per update; 36,950 updates per 2 epochs |
| Learning rates | encoder 1e-5, head 2e-4 (epoch 3: 2e-6 / 4e-5); AdamW, weight decay 0.01, warmup, cosine decay |
| Precision | bf16 autocast, with a loss-spike guard (roll back and halve the learning rate if the running loss jumps); it never fired |
| Hardware / time | one NVIDIA H100: about 2.6 hours for epochs 1–2 and 1.3 hours for epoch 3 |
| Question mix | 268k choice, 221k yes/no, 102k score |

**Public data** (285,078 decisions; unchanged from v2; every source is CC0, CC-BY, Apache-2.0 or MIT; full attribution
in `LICENSES.md`):

| Source | Decisions | Licence | Used for |
|---|---|---|---|
| MoritzLaurer/synthetic_zeroshot_mixtral_v0.1 | 59,998 | Apache-2.0 | general zero-shot choice / yes-no over diverse tasks |
| MASSIVE (20 languages, train) | 54,875 | CC-BY-4.0 | intent and domain choice, intent yes/no |
| HuffPost News Category | 28,552 | CC-BY-4.0 | news-topic choice, coarse and fine |
| HelpSteer2 | 20,000 | CC-BY-4.0 | response-quality score questions |
| BRIGHTER emotion | 19,344 | CC-BY-4.0 | emotion yes/no, human-labelled in-language (Hindi and Marathi in full, plus 8 other languages) |
| Civil Comments | 12,999 | CC0-1.0 | toxicity yes/no and score |
| GoEmotions | 11,998 | Apache-2.0 | fine-grained emotion |
| Nemotron Safety Guard v3 | 11,000 | CC-BY-4.0 | multilingual safety categories |
| Aegis 2.0 | 10,999 | CC-BY-4.0 | safety categories |
| LEDGAR (LexGLUE) | 10,000 | CC-BY-4.0 | contract-clause type |
| Banking77 (train) | 9,964 | CC-BY-4.0 | 77-way intent |
| WMT DA human evaluation | 9,000 | Apache-2.0 | translation-quality score |
| Measuring Hate Speech | 8,000 | CC-BY-4.0 | hate-speech facets |
| IBM Argument Quality 30k | 8,000 | CC-BY-3.0 | argument-quality score |
| Hinglish-TOP (train + validation) | 6,936 | Apache-2.0 | Hinglish domain and intent |
| Aya Red-teaming | 2,685 | Apache-2.0 | multilingual harm categories |
| SMS Spam Collection | 728 | CC-BY-4.0 | spam vs ham |

**Model-generated data** (306,139 verified questions). Every question was answered blind by a second, different
open-weights model and kept only if the two agreed.

| Part | Texts | Questions | Generator → verifier | New in |
|---|---|---|---|---|
| Label-first synthetic decisions | 59,310 | 173,226 | Qwen3-235B-A22B-Instruct-2507 → Qwen3-30B-A3B-Instruct-2507 | v1 |
| Questions on real Sangraha text | 22,791 | 63,448 | Qwen3-235B-A22B-Instruct-2507 → Qwen3-30B-A3B-Instruct-2507 | v2 |
| Questions on real Sangraha text | 18,699 | 54,198 | Gemma-4-26B-A4B-it → Qwen3.6-35B-A3B | v3 |
| Cause/effect questions (COPA format) from real Sangraha text | 7,782 | 7,782 | Gemma-4-26B-A4B-it → Qwen3.6-35B-A3B | v3 |
| Passage yes/no questions on real Sangraha text | 3,192 | 7,485 | Gemma-4-26B-A4B-it → Qwen3.6-35B-A3B | v3 |

All five models are open-weights and Apache-2.0. The v3 labels were generated on our own GPU with vLLM. The three v3
parts are published as [`arunp0/yama-indic-gemma`](https://huggingface.co/datasets/arunp0/yama-indic-gemma) (configs
`real_text`, `copa`, `boolq`, with held-out test splits).

- **Synthetic decisions** (v1): the generator proposes a schema, gold answers are sampled first, then it writes a text
  that makes them true, in a chosen language, script and register. About 68% of texts are in Indian languages or
  Hinglish. Published as [`arunp0/laya-indic-synthetic`](https://huggingface.co/datasets/arunp0/laya-indic-synthetic);
  see `SYNTHETIC_DATA.md`.
- **Real-text questions** (v2 and v3): 1–3 sentence snippets cut from Sangraha "verified" documents (AI4Bharat,
  CC-BY-4.0). The generator picks a topic from a fixed 16-topic list and writes 2–3 snippet-specific questions; topic
  options are assembled in code, and in 7% of cases the correct topic is removed so "none of the options" is the answer.
  The v2 part is published as [`arunp0/laya-indic-real-text`](https://huggingface.co/datasets/arunp0/laya-indic-real-text).
  The v3 part covers all 11 languages roughly equally, with extra Odia (2,324 texts) and Urdu (2,212).
- **Cause/effect questions** (v3): Gemma reads a real snippet and writes a premise, a "what caused this?" or "what
  happened next?" question and two alternatives, in the snippet's language. Hindi, Marathi, Bengali, Tamil, Kannada,
  Malayalam, Punjabi, Odia and Urdu; **not Telugu or Gujarati**. Only 58% of generated questions passed verification.
- **Passage yes/no questions** (v3): a longer real snippet is the passage; Gemma writes yes/no questions about it in the
  passage language, about half answered "no". Gujarati, Tamil, Telugu, Bengali, Marathi and Kannada; **not Hindi**.

## Limitations

- **Text classification only.** It does not do `number`, `excerpt` or citation questions, open-ended generation or
  multi-step reasoning. IndicCOPA (0.62) shows commonsense causal reasoning is still weak.
- **Instructions in English.** Questions and option descriptions should be in English; the text can be in any
  supported language.
- **Long texts are truncated** at 512 tokens per question (v2 allowed 1,024). XLM-R's tokenizer is 25–40% more compact
  on Indian scripts than v2's, which offsets part of this.
- **Calibration is good on average but not everywhere.** IndicCOPA and BoolQ-hi are poorly calibrated (ECE above 0.2).
  Recalibrate on your own data before using probabilities as thresholds.
- **About 52% of the training labels come from language models** (Qwen3, Gemma 4) and were checked only by another
  language model. Synthetic Indic text is cleaner and more formal than real user text. Sangraha is mostly news and web
  prose, not chat.
- **Content warning.** The safety and toxicity datasets (Aegis 2.0, Nemotron Safety Guard, Measuring Hate Speech, Civil
  Comments, Aya Red-teaming) contain offensive and harmful text. The model can classify such content but is not a
  tested content-moderation system.

## FAQ

### What is the best zero-shot classifier for Hindi and Hinglish?
In our tests Yama is the most accurate open zero-shot classifier for Indian languages. On 22 Indian-language tasks
that no model was trained on (SIB-200 topic, IndicSentiment, IndicCOPA), it scores 0.813 accuracy, against 0.753 for
bge-m3-zeroshot-v2.0, 0.586 for xlm-roberta-large-xnli, 0.532 for mDeBERTa-v3-base-xnli and 0.347 for bart-large-mnli.
On Hinglish voice-assistant requests (Hinglish-TOP) it scores 0.942, but it was trained on that dataset's training split.
Test it on a sample of your own data before relying on it.

### Which languages does Yama support?
Instructions are written in English; the text can be in Hindi (Devanagari or romanised), Hinglish, Bengali, Tamil,
Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Urdu, Odia or English. It was also trained on Russian, Turkish,
Azerbaijani, Kazakh, Uzbek, Georgian, Persian, Arabic, German, French, Spanish, Chinese, Japanese and Korean, and
inherits XLM-RoBERTa's coverage of about 100 languages, but it is tested most on the languages above.

### How is Yama different from bart-large-mnli or other NLI zero-shot models?
NLI models score each label separately as "does this text entail *this text is about X*?". Yama reads the question and
all the options together and was trained directly on typed decisions, so it also answers yes/no questions and ordinal
ratings, not just pick-one-label, and its probabilities are calibrated. See the comparison table under **Benchmarks**.

### How is it different from asking an LLM?
Yama does not generate text. One forward pass of a 586M encoder returns probabilities for every option, so there is
no output parsing, no sampling and no prompt-format drift. A single decision takes about 17 ms on an NVIDIA H100 and about 120 ms on 8 CPU threads (AMD EPYC); batched, it handles about 1,400 decisions per second on one H100. It is not a replacement for an LLM on open-ended
or multi-step reasoning tasks.

### Can I use it commercially?
Yes. The weights are Apache-2.0. All training data is CC0, CC-BY, Apache-2.0 or MIT, with no ShareAlike or
NonCommercial sources. No proprietary-model output was used as far as can be verified; Nemotron Safety Guard v3
doesn't name the models used to generate it. See `LICENSES.md` for attributions.

### How do I make it more accurate on my task?
Write clear option descriptions (they matter more than the keys), keep questions in English, and recalibrate the
probabilities on a few hundred of your own labelled examples before using them as thresholds.

### What happened to laya-indic?
It is still available at [`arunp0/laya-indic`](https://huggingface.co/arunp0/laya-indic) with the v1 and v2 weights
(mmBERT-base encoder). Yama is its successor: v3 moved to a larger encoder (XLM-RoBERTa-large), so it was released as a
separate model. Use Yama for new work; it scores 0.862 against 0.800 for v2 on the same 67 tasks.

## Acknowledgements and licence

- **Architecture, training objective and inference library:** THX-01 / `thx01` by HAL-X AI (Apache-2.0).
- **Benchmark harness:** THX-01's `bench/tasks.py`, including its MASSIVE scenario descriptions and label humaniser,
  which were also used in some training instructions.
- **Encoder:** XLM-RoBERTa large, by Facebook AI (MIT). v1 and v2 used mmBERT-base, by JHU CLSP (MIT).
- **Indian-language corpus:** Sangraha, by AI4Bharat (CC-BY-4.0), source of the real-text questions.
- **Label models:** Qwen3 and Qwen3.6 (Alibaba Qwen team) and Gemma 4 (Google DeepMind), all Apache-2.0.

Copyright 2026 Arun Prabhakaran. The model is released under the Apache License 2.0. See `NOTICE` for the required
attributions and `LICENSES.md` for every dataset's authors and licence.

## Citation

```bibtex
@misc{prabhakaran2026yama,
  title  = {Yama: a zero-shot typed-decision model for Indian languages and Hinglish},
  author = {Prabhakaran, Arun},
  year   = {2026},
  url    = {https://huggingface.co/arunp0/yama-indic}
}
```
