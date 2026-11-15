import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "lab-runs" / "structure-routing-all-candidates-20261115" / "jevforge"
MODEL = ROOT / ".deps" / "structure-routing-models" / "JevForge-0.8B"
SOURCE = ROOT / ".deps" / "jevforge-source"
REVISION = "5ca7c0861a0d9b72629a7bc40130a8f154c4840f"
WEIGHT_SHA256 = "74753ad36736c454fac84e5b8d1616542ea93b504efdacab2c9c5d27fb789c52"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def candidate_groups(candidates):
    groups = defaultdict(list)
    for item in candidates:
        groups[item["group_key"]].append(item)
    result = []
    criteria = {}
    for index, (key, members) in enumerate(groups.items()):
        candidate_id = f"g{index:03d}"
        selector = members[0]["group_selector"]
        sample = members[:3]
        details = " | ".join(
            f"anchor text={item['text'][:90]!r}, href={item['href'][:120]!r}"
            for item in sample
        )
        criteria[candidate_id] = (
            f"Repeated link group selector: {selector!r}; "
            f"anchors={len(members)}; repeated_item_count={members[0]['repeated_item_count']}; "
            f"examples: {details}"
        )
        result.append({
            "candidate_id": candidate_id,
            "group_key": key,
            "group_selector": selector,
            "rule": f"{selector} a[href]",
            "member_count": len(members),
            "examples": [{"text": item["text"], "href": item["href"]} for item in sample],
        })
    return result, criteria


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("01", "08"), default="01")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    sys.path.insert(0, str(SOURCE))
    from jevforge.predict import Predictor

    input_dir = RUN.parent / "common-inputs"
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    case = next(item for item in manifest["cases"] if item["id"] == args.case)
    seed = case["seeds"][args.seed - 1]
    candidates_path = ROOT / seed["candidates"]
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    groups, criteria = candidate_groups(payload["candidates"])
    state = (
        f"Task: {case['task']}\n"
        f"Page URL: {seed['url']}\n"
        "Page state is represented by anchor candidates grouped by a shared CSS ancestry. "
        "Choose the group whose repeated anchors best match the requested product detail links."
    )
    question = {
        "type": "choice",
        "instructions": "Which repeated link group should be saved as the reusable product-detail link selector?",
        "criteria": criteria,
    }
    import torch
    torch.set_num_threads(4)
    load_started = time.perf_counter()
    predictor = Predictor(MODEL, device_name=args.device, precision="fp32")
    load_seconds = time.perf_counter() - load_started
    tokenized = predictor.tokenizer(
        "<|oj:state|>\n" + state + "\n<|oj:question|>\nchoice: " + question["instructions"] + "\n" +
        "\n".join(f"[{key}] {value}\n<|oj:answer|>" for key, value in criteria.items()),
        add_special_tokens=False,
    )["input_ids"]
    inference_started = time.perf_counter()
    prediction = predictor.decide(state, {"group_selector": question})["group_selector"]
    inference_seconds = time.perf_counter() - inference_started
    selected_id = prediction["choice"]
    selected = next(item for item in groups if item["candidate_id"] == selected_id)
    result = {
        "model": "AndeyTait/JevForge-0.8B",
        "revision": REVISION,
        "weight_sha256": WEIGHT_SHA256,
        "input": {
            "case_id": args.case,
            "seed": args.seed,
            "candidate_file": seed["candidates"],
            "candidate_sha256": sha256(candidates_path),
            "schema": "state + one choice question; each candidate is one group_selector with candidate anchors/count",
            "html_candidate_count": len(payload["candidates"]),
            "group_candidate_count": len(groups),
            "input_tokens": len(tokenized),
        },
        "output_contract": {"kind": "selector", "selector": selected["rule"], "attribute": "href"},
        "selected_candidate": selected,
        "probabilities": prediction["probabilities"],
        "confidence": prediction["confidence"],
        "runtime": {
            "device": str(predictor.device),
            "precision": predictor.precision,
            "torch_threads": torch.get_num_threads(),
            "model_load_seconds": load_seconds,
            "inference_seconds": inference_seconds,
            "predictor_calls": predictor.calls,
            "question_count": 1,
            "candidate_path_forwards": len(groups),
            "trust_remote_code": False,
        },
        "interpretation": "Candidate ranking only; the model does not generate CSS or rules. The selector is selected from observed group selectors.",
    }
    RUN.mkdir(parents=True, exist_ok=True)
    destination = RUN / f"{args.case}-seed-{args.seed}.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), "selector": selected["rule"], "groups": len(groups),
                      "tokens": len(tokenized), "load_seconds": load_seconds,
                      "inference_seconds": inference_seconds}, ensure_ascii=False))


if __name__ == "__main__":
    main()
