import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path


MODEL_REPO = "McGill-NLP/MiniLM-L6-dmr"
MODEL_REVISION = "e9b9dd566fa1a146eb00b8f3230b87870144084a"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / ".deps" / "structure-routing-models" / "MiniLM-L6-dmr"
DEFAULT_TASK = "catalogからproduct detailへ"
MAX_TEXT_LENGTH = 200
MAX_ATTRIBUTE_LENGTH = 100
MAX_CHILDREN = 2
PINNED_FILE_HASHES = {
    "1_Pooling/config.json": "4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23",
    "config.json": "6aafb69a540b010d432088e3da1107e9032eed1ab21fede681bb37658b43fed2",
    "modules.json": "8f4b264b80206c830bebbdcae377e137925650a433b689343a63bdc9b3145460",
    "pytorch_model.bin": "49230f8ec5764fcd0d1c3d83ea87387d602e3c0343efbbb2d96452702ed651dd",
    "tokenizer.json": "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_manifest(model_dir):
    files = {}
    for path in sorted(p for p in model_dir.rglob("*") if p.is_file() and ".cache" not in p.parts):
        files[path.relative_to(model_dir).as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    for name, expected_hash in PINNED_FILE_HASHES.items():
        actual_hash = files.get(name, {}).get("sha256")
        if actual_hash != expected_hash:
            raise ValueError(f"Pinned model file integrity check failed: {name}")
    readme = (model_dir / "README.md").read_text(encoding="utf-8") if (model_dir / "README.md").exists() else ""
    license_match = re.search(r"(?im)^license\s*:\s*([^\r\n]+)", readme)
    return {
        "repo_id": MODEL_REPO,
        "revision": MODEL_REVISION,
        "license": license_match.group(1).strip() if license_match else "not_declared_at_pinned_revision",
        "files": files,
    }


def _shorten(value, limit):
    value = str(value or "")
    if len(value) <= limit:
        return value
    if limit < 3:
        return value[:limit]
    half = limit // 2
    return value[:half] + "..." + value[-half:]


def _format_attrs(attrs):
    ordered = sorted(attrs.items(), key=lambda item: len(str(item[1])))
    return " ".join(f"{key}={str(value)!r}" for key, value in ordered)


def _format_element(element, tree):
    attrs = {key: _shorten(value, MAX_ATTRIBUTE_LENGTH) for key, value in element.attrib.items()}
    attrs = dict(sorted(attrs.items(), key=lambda item: len(item[1])))
    children = [child.tag for child in list(element)[:MAX_CHILDREN] if isinstance(child.tag, str)]
    text = _shorten(element.text, MAX_TEXT_LENGTH)
    bbox = "x=0.0 y=0.0 width=0.0 height=0.0"
    parts = (
        ("tag", element.tag),
        ("xpath", tree.getpath(element)),
        ("text", text),
        ("bbox", bbox),
        ("attributes", _format_attrs(attrs)),
        ("children", " ".join(children)),
    )
    return "\n".join(f"[[{name}]] {value}" for name, value in parts)


def _css_token(value):
    token = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    return token or "_"


def _selector_part(element):
    classes = sorted(set(element.get("class", "").split()))
    selector = str(element.tag)
    if element.get("id"):
        selector += "#" + _css_token(element.get("id"))
    return selector + "".join("." + _css_token(name) for name in classes)


def _same_signature(left, right):
    return left.tag == right.tag and set(left.get("class", "").split()) == set(right.get("class", "").split())


def _repeated_item(anchor):
    parent = anchor.getparent()
    while parent is not None and isinstance(parent.tag, str) and parent.tag.lower() not in {"body", "html"}:
        siblings = parent.getparent()
        if siblings is not None:
            repeated = [child for child in siblings if isinstance(child.tag, str) and _same_signature(parent, child)]
            if len(repeated) >= 2:
                return parent, len(repeated)
        parent = parent.getparent()
    return None, 0


def _group_selector(item):
    chain = []
    current = item
    for _ in range(4):
        if current is None or not isinstance(current.tag, str):
            break
        chain.append(_selector_part(current))
        if current.get("id"):
            chain[-1] += "#" + _css_token(current.get("id"))
            break
        current = current.getparent()
    return " > ".join(reversed(chain))


def extract_candidates(html):
    from lxml import html as lxml_html

    root = lxml_html.fromstring(html)
    tree = root.getroottree()
    candidates = []
    groups = defaultdict(list)
    for anchor in root.xpath("//a[@href]"):
        href = anchor.get("href")
        if href is None or not href.strip():
            continue
        item, repeated_count = _repeated_item(anchor)
        selector = _group_selector(item) if item is not None else _selector_part(anchor.getparent())
        group_key = selector if item is not None else "single:" + tree.getpath(anchor)
        candidate = {
            "href": href,
            "text": " ".join(" ".join(anchor.itertext()).split()),
            "xpath": tree.getpath(anchor),
            "group_key": group_key,
            "group_selector": selector,
            "repeated_item_count": repeated_count,
            "doc": _format_element(anchor, tree),
            "bbox_missing": True,
        }
        candidates.append(candidate)
        groups[group_key].append(candidate)
    return candidates, groups


def query_text(task):
    return f"Viewport(height=900, width=1365) ---- Instructor Utterances: {task} ---- Previous Turns:"


def _run_model(model_dir, task, candidates, batch_size, device):
    import torch
    from sentence_transformers import SentenceTransformer

    load_started = time.perf_counter()
    model = SentenceTransformer(
        str(model_dir),
        device=device,
        trust_remote_code=False,
        local_files_only=True,
    )
    model.eval()
    model_load_seconds = time.perf_counter() - load_started
    texts = [query_text(task), *(candidate["doc"] for candidate in candidates)]
    tokenizer = model.tokenizer
    max_length = int(model.max_seq_length)
    raw_lengths = [
        len(tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"])
        for text in texts
    ]
    inference_started = time.perf_counter()
    embeddings = []
    actual_lengths = []
    forward_calls = 0
    for offset in range(0, len(texts), batch_size):
        features = model.tokenize(texts[offset:offset + batch_size])
        actual_lengths.extend(int(value) for value in features["attention_mask"].sum(dim=1).tolist())
        with torch.inference_mode():
            output = model(features)["sentence_embedding"]
            output = torch.nn.functional.normalize(output.float(), p=2, dim=1)
        embeddings.append(output.cpu())
        forward_calls += 1
    inference_seconds = time.perf_counter() - inference_started
    all_embeddings = torch.cat(embeddings, dim=0)
    scores = (all_embeddings[1:] @ all_embeddings[0]).tolist()
    return scores, {
        "device": str(device),
        "model_load_seconds": model_load_seconds,
        "inference_seconds": inference_seconds,
        "max_sequence_length": max_length,
        "batch_size": batch_size,
        "batches": math.ceil(len(texts) / batch_size),
        "forward_calls": forward_calls,
        "tokenized_inputs": len(texts),
        "input_token_total": sum(actual_lengths),
        "input_token_max": max(actual_lengths, default=0),
        "truncation_count": sum(length > max_length for length in raw_lengths),
        "bbox_missing_candidates": sum(bool(candidate["bbox_missing"]) for candidate in candidates),
    }


def rank_html(html, task=DEFAULT_TASK, model_dir=DEFAULT_MODEL_DIR, batch_size=32, device="cpu"):
    candidates, groups = extract_candidates(html)
    if not candidates:
        return {
            "status": "no_candidates",
            "task": task,
            "candidate_count": 0,
            "group_count": 0,
            "ranking": [],
            "groups": [],
        }
    manifest = model_manifest(model_dir)
    scores, metrics = _run_model(model_dir, task, candidates, batch_size, device)
    for candidate, score in zip(candidates, scores):
        candidate["score"] = float(score)
    ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
    group_results = []
    for key, members in groups.items():
        group_results.append({
            "group_key": key,
            "selector": members[0]["group_selector"] + " a[href]",
            "member_count": len(members),
            "repeated_item_count": members[0]["repeated_item_count"],
            "mean_score": sum(item["score"] for item in members) / len(members),
            "max_score": max(item["score"] for item in members),
            "hrefs": [item["href"] for item in members],
        })
    group_results.sort(key=lambda item: item["mean_score"], reverse=True)
    selected_group = next(item for item in group_results if item["group_key"] == ranked[0]["group_key"])
    return {
        "status": "ok",
        "task": task,
        "query": query_text(task),
        "candidate_count": len(candidates),
        "group_count": len(group_results),
        "metrics": metrics,
        "model": manifest,
        "ranking": [
            {
                "rank": index + 1,
                "href": candidate["href"],
                "text": candidate["text"],
                "xpath": candidate["xpath"],
                "group_key": candidate["group_key"],
                "score": candidate["score"],
            }
            for index, candidate in enumerate(ranked)
        ],
        "groups": group_results,
        "frozen_rule": {
            "selector": selected_group["selector"],
            "score_aggregation": "top_candidate_model_score",
            "group_key": selected_group["group_key"],
        },
        "heuristics_added": False,
        "external_api_calls": 0,
        "representative_dom_rankings": 1,
        "future_page_model_calls": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    try:
        html = Path(args.html).read_text(encoding="utf-8")
        result = rank_html(html, args.task, args.model_dir, args.batch_size, args.device)
        if result["status"] == "ok":
            manifest_path = args.model_dir.parent / (args.model_dir.name + ".manifest.json")
            manifest_path.write_text(json.dumps(result["model"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as error:
        result = {
            "status": "error",
            "error_type": type(error).__name__,
            "error": str(error),
            "model_repo": MODEL_REPO,
            "model_revision": MODEL_REVISION,
            "external_api_calls": 0,
            "page_model_calls": 0,
        }
        print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
