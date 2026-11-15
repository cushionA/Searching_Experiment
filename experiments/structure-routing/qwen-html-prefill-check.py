import copy
import json
import time
from pathlib import Path

import torch
from transformers import AutoConfig, AutoModelForCausalLM, DynamicCache


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / ".deps/structure-routing-models/Qwen-HTML"
OUT = ROOT / "lab-runs/structure-routing-all-candidates-20261115/qwen-html/cache-prefill-check.json"


def prefill(model, input_ids, chunk_size):
    device = next(model.parameters()).device
    input_ids = input_ids.to(device)
    mask = torch.ones_like(input_ids)
    cache = DynamicCache(config=model.config)
    logits = None
    pieces = []
    for start in range(0, input_ids.shape[-1], chunk_size):
        end = min(start + chunk_size, input_ids.shape[-1])
        positions = torch.arange(start, end, dtype=torch.long, device=device)
        with torch.inference_mode():
            result = model(
                input_ids=input_ids[:, start:end],
                attention_mask=mask[:, :end],
                past_key_values=cache,
                cache_position=positions,
                use_cache=True,
            )
        cache = result.past_key_values
        logits = result.logits[:, -1:, :]
        pieces.append(result.logits.detach().cpu())
    return cache, logits, torch.cat(pieces, dim=1)


def main():
    torch.manual_seed(10)
    torch.set_num_threads(1)
    config = copy.deepcopy(AutoConfig.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False))
    config.vocab_size = 257
    config.hidden_size = 64
    config.intermediate_size = 128
    config.num_hidden_layers = 2
    config.num_attention_heads = 4
    config.num_key_value_heads = 2
    config.head_dim = 16
    config.layer_types = ["full_attention", "full_attention"]
    config.max_window_layers = 2
    config.max_position_embeddings = 512
    config.dtype = torch.float32
    config.pad_token_id = 0
    config.bos_token_id = 1
    config.eos_token_id = 2
    model = AutoModelForCausalLM.from_config(config, dtype=torch.float32)
    model.eval()
    input_ids = torch.tensor(
        [[1, 71, 18, 22, 3, 4, 9, 12, 45, 31, 87, 6, 11, 5, 30, 91, 2, 23, 17, 44, 56, 7, 19, 33, 8, 9, 10, 12, 14, 16, 20]],
        dtype=torch.long,
    )
    mask = torch.ones_like(input_ids)
    started = time.perf_counter()
    with torch.inference_mode():
        full = model(input_ids=input_ids, attention_mask=mask, use_cache=False).logits
    full_initial = full.detach().cpu()
    full_seconds = round(time.perf_counter() - started, 4)
    chunked_cache, chunked_last_logits, chunked = prefill(model, input_ids, 7)
    diff = (full - chunked).abs()
    greedy_full = []
    greedy_chunked = []
    full_context = input_ids.clone()
    mask_chunked = torch.ones((1, input_ids.shape[1]), dtype=torch.long)
    next_logits = chunked_last_logits
    for index in range(5):
        token_full = full[:, -1, :].argmax(dim=-1, keepdim=True)
        token_chunked = next_logits[:, -1, :].argmax(dim=-1, keepdim=True)
        greedy_full.append(int(token_full.item()))
        greedy_chunked.append(int(token_chunked.item()))
        full_context = torch.cat([full_context, token_full], dim=1)
        mask_full = torch.ones_like(full_context)
        with torch.inference_mode():
            full = model(input_ids=full_context, attention_mask=mask_full, use_cache=False).logits
        if index < 4:
            position = torch.tensor([input_ids.shape[1] + index], dtype=torch.long)
            mask_chunked = torch.cat([mask_chunked, torch.ones((1, 1), dtype=torch.long)], dim=1)
            with torch.inference_mode():
                result = model(
                    input_ids=token_chunked,
                    attention_mask=mask_chunked,
                    past_key_values=chunked_cache,
                    cache_position=position,
                    use_cache=True,
                )
            chunked_cache = result.past_key_values
            next_logits = result.logits[:, -1:, :]
    output = {
        "architecture": "Qwen3ForCausalLM with a tiny randomized config",
        "model_weights_loaded": False,
        "input_tokens": int(input_ids.shape[1]),
        "chunk_size": 7,
        "chunk_count": 5,
        "full_forward_seconds": full_seconds,
        "max_abs_logit_difference": float(diff.max().item()),
        "mean_abs_logit_difference": float(diff.mean().item()),
        "logits_close": bool(torch.allclose(full_initial, chunked, rtol=1e-4, atol=1e-5)),
        "greedy_tokens_full": greedy_full,
        "greedy_tokens_chunked": greedy_chunked,
        "greedy_tokens_equal": greedy_full == greedy_chunked,
        "dynamic_cache_length": int(chunked_cache.get_seq_length()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output))


if __name__ == "__main__":
    main()
