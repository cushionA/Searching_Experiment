---
license: apache-2.0
base_model: manjunathshiva/opendecider-nano
base_model_relation: quantized
library_name: onnx
pipeline_tag: zero-shot-classification
language:
- en
tags: [opendecider, decision-model, onnx, onnxruntime-web, webgpu, wasm, browser, on-device, system-one, calibrated-decisions, typed-decisions, classification, routing, guardrails, commercial-use]
---

<p align="center">
  <img src="https://raw.githubusercontent.com/manjunathshiva/opendecider/main/assets/logo-lockup.png" alt="OpenDecider" width="360" />
</p>

# OpenDecider-nano ONNX

[opendecider-nano](https://huggingface.co/manjunathshiva/opendecider-nano), the ~400M calibrated decision model, as ONNX
for the browser (**WebGPU** or **WebAssembly**), Node, Bun and Deno. Give it a state (text or JSON) and typed questions
(`choice`, `score`, `noul`); it returns a probability for every option, **on the user's device: nothing is sent
anywhere**. These are the files [`@opendecider/web`](https://www.npmjs.com/package/@opendecider/web) loads, and the
files [OpenDecider Focus](https://chromewebstore.google.com/detail/opendecider-focus/npjgckljljojppjgikkiiaifoojkoebj) runs to filter a YouTube feed (a Chrome extension on the
Chrome Web Store).

**[Try it in your browser](https://manjunathshiva.github.io/opendecider/demo/)** ·
[Guide](https://manjunathshiva.github.io/opendecider/guides/browser/) ·
[GitHub](https://github.com/manjunathshiva/opendecider)

```bash
npm install @opendecider/web
```

```ts
import { loadNano, choice, noul, Guard } from "@opendecider/web";

const model = await loadNano(); // these files, at the revision the package pins, SHA-256 checked, then cached
const r = await model.systemOne("I was charged twice for order 1182. Please fix this today.", {
  team: choice("Which team should handle this?", { billing: "charges, refunds", tech: "bugs, outages" }),
  urgent: noul("Does this need a reply today?"),
});
r.answers.team; // { type: "choice", choice: "billing", probabilities: { billing: 0.92, tech: 0.08 }, ... }

const guard = new Guard({ model }); // the prompt guard, on the device
```

## Files

| file | build | size | SHA-256 |
|---|---|---|---|
| `onnx/model_q8f16.onnx` | 8-bit weights, float16 elsewhere: WebGPU's default | 469,218,222 bytes (450 MiB) | `80436dbed1a284548b5e6c3975bb26df81fb493fc4e64629e5a6ef490bf0e0ae` |
| `onnx/model_q8.onnx` | 8-bit weights, float32 elsewhere: WebAssembly's default | 594,006,972 bytes (569 MiB) | `3920323e41bb107f28566be7b45e6cb55a2bdc1951b27a6e62625857e22542c4` |
| `onnx/model_fp16.onnx` | float16 throughout: for many questions at once on WebGPU (`dtype: "fp16"`) | 791,886,899 bytes (755 MiB) | `ce3b92ed327c394570123867d48a73383e748b015c08ca5b735670d6601ab44c` |
| `tokenizer.web.json` | opendecider-nano's tokenizer for tokenizers.js | 1,570,653 bytes | `fd4146f0082d98fd1fc03bf41349f4d1eed478987ce9a64addc1654dfcad14ee` |

One graph each: `input_ids` and `attention_mask` (int64, [batch, tokens]) in, `logits` (float32, [batch, tokens]) out,
one score per token; the answer is the softmax over the scores at the `[MASK]` markers, one before each option
(`opendecider.json` has the details). In q8 and q8f16 the weights are 8-bit (ONNX Runtime's MatMulNBits, block 32):
weight-only, so the browser's WebAssembly gives the same logits as native ONNX Runtime to 1e-6 (fp16: within 0.01).

## Same answers as the PyTorch model

Scored with native ONNX Runtime against opendecider-nano's committed answers, on every benchmark the model card reports:

| build | typed-decisions (2,000) | general (200) | Laya's battery (10 × 400) | answers as PyTorch |
|---|---|---|---|---|
| opendecider-nano (PyTorch) | 0.796 | 0.680 | 0.656 | – |
| **q8f16** | 0.798 | 0.680 | 0.656 | 99.5–99.8% |
| **q8** | 0.795 | 0.680 | 0.656 | 99.6–99.8% |
| **fp16** | 0.796 | 0.680 | 0.655 | 99.9–100% |

On WebGPU, which computes in float16 (Chrome, Apple M4 Max, all 2,400 typed-decisions and general questions), fp16 gave
PyTorch's answer on 99.45–100% of them and q8f16 on 99.3–99.5%.

As a guard against jailbreaks and prompt injection (2,438 prompts, each build at its own train-split threshold, which
`@opendecider/web`'s `Guard` uses):

| build | threshold | accuracy (95% CI) | attacks caught | benign flagged |
|---|---|---|---|---|
| opendecider-nano (PyTorch) | 0.387 | 0.831 (0.816–0.845) | 0.914 | 0.214 |
| q8 | 0.391 | 0.828 (0.813–0.843) | 0.914 | 0.218 |
| q8f16 | 0.390 | 0.828 (0.813–0.842) | 0.914 | 0.218 |
| fp16 | 0.388 | 0.830 (0.815–0.845) | 0.913 | 0.215 |

4-bit builds were not released: they changed 2.5–4.3% of the answers. Every number:
[Benchmarks](https://manjunathshiva.github.io/opendecider/benchmarks/#in-the-browser).

## Will it run on my device?

| | WebGPU, q8f16 | WebGPU, fp16 | WebAssembly, q8 |
|---|---|---|---|
| download (once, then the browser's cache) | 450 MiB | 755 MiB | 569 MiB |
| memory, with a 2,048-token question | about 2.2 GiB | about 0.6 GiB more than q8f16 | about 2.8 GiB |
| one question, Chrome on an Apple M4 Max | 47 ms | about the same | 125 ms (8 threads), 830 ms (1 thread) |
| 40 questions of about 180 tokens at once | 7.4 s | 1.1 s | about 15 s (8 threads) |

ONNX Runtime's 8-bit WebGPU kernels are tuned for one question at a time, so for many at once (a feed, a batch) use
fp16 on WebGPU. On WebAssembly fp16 is no faster than q8 and needs about 4.8 GiB: keep q8 there.

A browser with WebGPU uses it; otherwise WebAssembly. More WebAssembly threads need a cross-origin-isolated page (see the
guide). Phones with little memory may not load it.

## Without JavaScript

The graph runs in any ONNX Runtime. In Python, with the same prompt as the opendecider package (0.7.0 or later):

```python
import numpy as np, onnxruntime as ort
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer
from opendecider.prompt import nano_ids

tok = AutoTokenizer.from_pretrained("manjunathshiva/opendecider-nano")
sess = ort.InferenceSession(hf_hub_download("manjunathshiva/opendecider-nano-ONNX", "onnx/model_q8.onnx"))
options = {"billing": "charges, refunds", "tech": "bugs, outages"}
ids, truncated = nano_ids(tok, "I was charged twice for order 1182.", "Which team should handle this?", options, 2048)
x = np.array([ids])
logits = sess.run(["logits"], {"input_ids": x, "attention_mask": np.ones_like(x)})[0][0]
z = logits[x[0] == tok.mask_token_id].astype(np.float64)
p = np.exp(z - z.max()); p /= p.sum()
dict(zip(options, p.round(4)))   # {'billing': 0.9476, 'tech': 0.0524}
```

## Check the files yourself

The files rebuild byte for byte from the repository's `packaging/onnx/` (torch 2.14.1, transformers 5.18.0, onnx
1.23.1, onnxscript 0.7.2, onnxruntime 1.30.0), from opendecider-nano at revision
`beeeb640f3333aec4ef78ed3b7bd0605f973a590`:

```bash
python packaging/onnx/export_nano.py --out build/ --revision beeeb640f3333aec4ef78ed3b7bd0605f973a590
python packaging/onnx/quantize.py build/model.onnx build/onnx/
python packaging/onnx/make_web_tokenizer.py <opendecider-nano>/tokenizer.json build/tokenizer.web.json
shasum -a 256 build/onnx/*.onnx build/tokenizer.web.json
```

`@opendecider/web` pins this repository's revision and the size and SHA-256 of each file, and never uses a file that
does not match.

## Limits

- **English**, like opendecider-nano: evaluated in English only.
- **2,048 tokens** per input; a longer state is shortened first (the answer is marked `truncated`).
- **Not for edge functions**: Cloudflare Workers and similar runtimes have far less memory than the model needs.
- opendecider-nano's own limits apply; see its [model card](https://huggingface.co/manjunathshiva/opendecider-nano).

## License

Apache-2.0, as opendecider-nano. Built on [Ettin-encoder-400m](https://huggingface.co/jhu-clsp/ettin-encoder-400m) (MIT);
see NOTICE. Manjunath Janardhan.
