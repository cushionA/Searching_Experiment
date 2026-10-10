"""CPU/GPU trial: can small general models replace the gate's language rules?

build      write a fixed question set: Codex's source-bound presence tasks and controls, their
           negated forms, row-scoped variants (3段カバー付き for a 2段 SKU), and value-pair
           questions sampled from the v10 rule gate (support -> same, conflict -> different).
run        ask every question in plain words and take the model's short generated answer as is.
summarize  answers per kind and model, two-model agreement, and input tokens per second.

Expected answers are references, not gold: Codex structural controls, the v10 rule gate (whose
29-pair adoptions agree with the Luna machine labels), and Claude's literal reading of the
constructed scope questions. Prices are not decided here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import platform
import random
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = {"qwen3.5-0.8b": ("Qwen/Qwen3.5-0.8B", "2fc06364715b967f1860aea9cf38778875588b17"),
          "qwen3.5-2b": ("Qwen/Qwen3.5-2B", "15852e8c16360a2fea060d615a32b45270f8a8fc")}
PROMPTS = {
    "relation": ("「{premise}」\nこの文から「{hypothesis}」と言えますか。\n"
                 "「正しい」「間違い」「書いていない」のどれか1つだけで答えてください。"),
    "value_pair": ("楽天の選択肢「{premise}」と、auの選択肢「{hypothesis}」は、同じものを指していますか。\n"
                   "「同じ」「違う」「わからない」のどれか1つだけで答えてください。"),
}
ANSWERS = {"relation": {"書いていない": "unknown", "正しい": "support", "間違い": "conflict"},
           "value_pair": {"わからない": "unknown", "同じ": "support", "違う": "conflict"}}
# The v10 gate reads XS as S and XL as L, so its references for such pairs are wrong.
X_SIZE = re.compile(r"(?<![A-Za-z])\d?X+[SL](?![A-Za-z])")
FLIP = {"support": "conflict", "conflict": "support", "unknown": "unknown"}


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).open(encoding="utf-8") if line.strip()]


def write_new(path: Path, text: str):
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def negate(hypothesis: str) -> str | None:
    if hypothesis.endswith("付いている。"):
        return hypothesis[:-len("付いている。")] + "付いていない。"
    if hypothesis.endswith("付いていない。"):
        return hypothesis[:-len("付いていない。")] + "付いている。"
    return None


def presence_questions(tasks_dir: Path):
    tasks = {t["task_id"]: t for t in read_jsonl(tasks_dir / "tasks.jsonl")}
    controls = {c["task_id"]: c["reference_relation"] for c in read_jsonl(tasks_dir / "structural-controls.jsonl")}
    out = []
    for task_id, t in sorted(tasks.items()):
        quote, hyp = t["evidence"]["quote"], t["hypothesis"]
        if task_id in controls:
            expected, ref = controls[task_id], "codex_structural_control"
        else:
            # Literal reading of a fixed AU line; なし negates the component the line names.
            expected = "conflict" if "なし" in quote else "support"
            ref = "claude_literal_reading"
        out.append({"kind": "presence", "premise": quote, "hypothesis": hyp, "expected": expected, "reference": ref,
                    "source": task_id, "pair": task_id})
        neg = negate(hyp)
        if neg:
            out.append({"kind": "presence_negated", "premise": quote, "hypothesis": neg, "expected": FLIP[expected],
                        "reference": ref, "source": task_id, "pair": task_id})
    # A line about another tier of the same series does not decide this SKU's tier.
    for quote, expected in (("2段カバー付き", "support"), ("2段カバーなし", "conflict"),
                            ("3段カバー付き", "unknown"), ("3段カバーなし", "unknown")):
        out.append({"kind": "row_scope", "premise": quote, "hypothesis": "この2段の商品にはカバーが付いている。",
                    "expected": expected, "reference": "claude_literal_reading", "source": "au:712539756",
                    "pair": "scope:" + quote})
    return out


def value_pair_questions(runs, per_class: int, seed: int):
    # Whole option values on both sides, so the model replaces value decomposition rather than
    # judging fragments. Only Rakuten values that are a single atom keep the gate's atom-level
    # relation equal to a value-level reference.
    pools = {"support": {}, "conflict": {}}
    for run in runs:
        axes = {row["row_key"]: row["axes"] for c in read_jsonl(ROOT / run / "inputs" / "products.jsonl")
                for row in c["au_rows"]}
        for r in read_jsonl(ROOT / run / "predictions" / "A-full_spec_notice.jsonl"):
            reqs = {q["requirement_id"]: q for q in r["requirements"]}
            for row in r["rows"]:
                for ar in row.get("atom_results", []):
                    req = reqs[ar["requirement_id"]]
                    if req["quote"] != req["axis_value_quote"]:
                        continue
                    for ev in ar.get("evidence", []):
                        rel = ev.get("relation")
                        if ev.get("source") != "au_row" or rel not in pools or ar["status"] != rel:
                            continue
                        path = (ev.get("span") or {}).get("locator", {}).get("json_path")
                        axis = next((a for a in axes[row["row_key"]] if a.get("value_span")
                                     and a["value_span"]["locator"].get("json_path") == path), None)
                        if axis is None or bool(X_SIZE.search(req["axis_value_quote"])) != bool(X_SIZE.search(axis["value"])):
                            continue
                        key = (req["axis_label"], req["axis_value_quote"], f"{axis['axis_name']}: {axis['value']}")
                        pools[rel].setdefault(key, {"type": req["type"], "run": run, "case_id": r["case_id"],
                                                    "row_key": row["row_key"], "note": ar.get("note")})
    rng = random.Random(seed)
    out = []
    for rel, pool in pools.items():
        keys = sorted(pool)
        rng.shuffle(keys)
        # Spread the sample over value types so colours do not crowd out sizes and variants.
        by_type = defaultdict(list)
        for k in keys:
            by_type[pool[k]["type"]].append(k)
        chosen = []
        while len(chosen) < per_class and any(by_type.values()):
            for t in sorted(by_type):
                if by_type[t] and len(chosen) < per_class:
                    chosen.append(by_type[t].pop())
        for axis, rak_value, au_value in chosen:
            meta = pool[(axis, rak_value, au_value)]
            out.append({"kind": "value_pair", "premise": f"{axis}: {rak_value}", "hypothesis": au_value,
                        "expected": rel, "reference": "v10_rule_gate", "source": meta["case_id"],
                        "pair": f"{rak_value}|{au_value}", "value_type": meta["type"], "gate_note": meta["note"]})
    return out


def build(args):
    questions = presence_questions(Path(args.codex_tasks)) + value_pair_questions(args.gate_runs, args.per_class, args.seed)
    for i, q in enumerate(questions):
        q["question_id"] = f"q{i:03d}"
    out = ROOT / args.out
    write_new(out / "questions.jsonl", "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))
    print(dict(Counter(q["kind"] for q in questions)))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def kind_of(q):
    return "value_pair" if q["kind"] == "value_pair" else "relation"


def parse(kind: str, text: str) -> str:
    """The first offered answer word in the reply; anything else is invalid."""
    found = [(text.find(word), label) for word, label in ANSWERS[kind].items() if word in text]
    return min(found)[1] if found else "invalid"


def run(args):
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForCausalLM, AutoTokenizer
    torch.set_num_threads(args.threads)
    cuda = args.device == "cuda"
    repo, revision = MODELS[args.model]
    local = Path(snapshot_download(repo, revision=revision))
    out = ROOT / args.out
    questions = read_jsonl(out / "questions.jsonl")
    tok = AutoTokenizer.from_pretrained(local)
    t0 = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(local, dtype=getattr(torch, args.dtype)).to(args.device).eval()
    if args.quant == "int8-dynamic":
        model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
    load_seconds = time.perf_counter() - t0
    rows = []
    with torch.inference_mode():
        for q in questions:
            text = tok.apply_chat_template([{"role": "user", "content": PROMPTS[kind_of(q)].format(**q)}],
                                           tokenize=False, add_generation_prompt=True, enable_thinking=False)
            enc = tok(text, return_tensors="pt").to(args.device)
            start = time.perf_counter()
            gen = model.generate(**enc, max_new_tokens=args.max_new_tokens, do_sample=False)
            if cuda:
                torch.cuda.synchronize()
            seconds = time.perf_counter() - start
            reply = tok.decode(gen[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
            rows.append({"question_id": q["question_id"], "reply": reply, "answer": parse(kind_of(q), reply),
                         "input_tokens": int(enc["input_ids"].shape[1]),
                         "output_tokens": int(gen.shape[1] - enc["input_ids"].shape[1]), "seconds": seconds})
    variant = args.device + ("-int8" if args.quant == "int8-dynamic" else "")
    name = f"{args.model}-{variant}"
    weights = sorted(local.glob("*.safetensors"))
    meta = {"model": args.model, "repo": repo, "revision": revision, "device": args.device, "variant": variant,
            "gpu": torch.cuda.get_device_name(0) if cuda else None, "dtype": args.dtype, "quant": args.quant,
            "threads": args.threads, "max_new_tokens": args.max_new_tokens, "decoding": "greedy",
            "load_seconds": load_seconds, "weights_sha256": {p.name: sha256(p) for p in weights},
            "torch": torch.__version__, "python": platform.python_version(), "cpu": platform.processor(),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(), "prompts": PROMPTS, "answers": ANSWERS,
            "questions_sha256": sha256(out / "questions.jsonl")}
    write_new(out / f"predictions-{name}.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    write_new(out / f"run-{name}.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    tokens, seconds = sum(r["input_tokens"] for r in rows), sum(r["seconds"] for r in rows)
    print(name, f"{len(rows)} questions, {tokens} input tokens, {seconds:.1f}s, {seconds / len(rows):.2f}s/question")


def tally(questions, answers):
    by_kind = defaultdict(Counter)
    for qid, q in questions.items():
        a, c = answers[qid], by_kind[q["kind"]]
        c["n"] += 1
        c["correct"] += a == q["expected"]
        c["answer_" + a] += 1
        # Deciding (support/conflict) against the reference is the error that can adopt or drop a SKU.
        if a in ("support", "conflict"):
            c["decided"] += 1
            c["decided_wrong"] += a != q["expected"]
    return {k: dict(v) for k, v in by_kind.items()}


def summarize(args):
    out = ROOT / args.out
    questions = {q["question_id"]: q for q in read_jsonl(out / "questions.jsonl")}
    preds = {p.stem[len("predictions-"):]: {r["question_id"]: r for r in read_jsonl(p)}
             for p in sorted(out.glob("predictions-*.jsonl"))}
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
               "references": "Codex structural controls, v10 rule gate, Claude literal reading; not gold",
               "runs": {}, "two_model_agreement": {}}
    for name, p in preds.items():
        meta = json.loads((out / f"run-{name}.json").read_text(encoding="utf-8"))
        seconds = sum(r["seconds"] for r in p.values())
        summary["runs"][name] = {"by_kind": tally(questions, {k: r["answer"] for k, r in p.items()}),
                                 "input_tokens": sum(r["input_tokens"] for r in p.values()),
                                 "output_tokens": sum(r["output_tokens"] for r in p.values()), "seconds": seconds,
                                 "seconds_per_question": seconds / len(p), "device": meta["device"],
                                 "gpu": meta["gpu"], "threads": meta["threads"], "dtype": meta["dtype"],
                                 "quant": meta["quant"], "load_seconds": meta["load_seconds"]}
    variants = {json.loads((out / f"run-{n}.json").read_text(encoding="utf-8"))["variant"] for n in preds}
    for variant in sorted(variants):
        pair = [preds.get(f"{m}-{variant}") for m in MODELS]
        if None in pair:
            continue
        # Two models must give the same decisive answer; any disagreement stays unknown.
        agreed = {qid: (pair[0][qid]["answer"] if pair[0][qid]["answer"] == pair[1][qid]["answer"] else "unknown")
                  for qid in questions}
        summary["two_model_agreement"][variant] = tally(questions, agreed)
    write_new(out / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["build", "run", "summarize"])
    parser.add_argument("--out", default=".lab-output/sku-qwen35-cpu-relations-20261010-v2")
    parser.add_argument("--codex-tasks", default="../SearchEngine-codex-review/.lab-output/sku-cpu-requirement-tasks-20261010-v3")
    parser.add_argument("--gate-runs", nargs="+", default=[".lab-output/sku-gate-tasks-20261010-v10",
                                                          ".lab-output/sku-gate-raw-family-20261010-v10"])
    parser.add_argument("--per-class", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--model", choices=sorted(MODELS))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--dtype", default="float32", choices=["float32", "bfloat16", "float16"])
    parser.add_argument("--quant", default="none", choices=["none", "int8-dynamic"])
    parser.add_argument("--max-new-tokens", type=int, default=8)
    args = parser.parse_args()
    {"build": build, "run": run, "summarize": summarize}[args.step](args)


if __name__ == "__main__":
    main()
