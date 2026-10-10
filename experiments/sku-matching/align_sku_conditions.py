"""Align the selected Rakuten SKU's conditions with every row of the fixed AU product, by model.

questions  one question per (fixed pair, Rakuten axis, selected value): which AU option, listed
           with its axis name, is the same as this Rakuten selection, or 該当なし.
ask        a local model answers each question twice, with the AU options in forward and
           reverse order. The reply is kept verbatim.
align      a mapping is kept only when both orders give the same AU option (or both 該当なし)
           and no other Rakuten value of the same axis maps to that option. Per case and AU row
           each condition is then 対応確認済み (aligned), 明示的な矛盾 (contradiction: the row has
           another option on the mapped axis) or 未対応 (unresolved). Unresolved conditions of rows
           without a contradiction go to handoff.jsonl for the description check (Codex).
           The alignment-only decision adopts a row only when every condition is aligned on it and
           every other row has a contradiction; anything else is excluded. Diff with the rule gate.
score      after predictions are saved: machine labels of the 29 confirmed pairs.

No vocabulary or value parsing: answers are compared with the listed options after NFKC and
whitespace removal only. Labels are not read before `score`.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import platform
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = {"qwen3.5-0.8b": ("Qwen/Qwen3.5-0.8B", "2fc06364715b967f1860aea9cf38778875588b17"),
          "qwen3.5-2b": ("Qwen/Qwen3.5-2B", "15852e8c16360a2fea060d615a32b45270f8a8fc"),
          "qwen3.5-4b": ("Qwen/Qwen3.5-4B", "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"),
          "modernbert-ja-310m-jev": ("argos1111/modernbert-ja-310m-jev", "07cda23579443e7a33c0f474114279fa032340d6"),
          "japanese-reranker-small-v2": ("hotchpotch/japanese-reranker-small-v2", "e8091d132c23b372e059505edbb9255f346100d3"),
          "ruri-v3-reranker-310m": ("cl-nagoya/ruri-v3-reranker-310m", "bb46934ee9ed09f850b9fcff17501b3ef7ddb2b3")}
CLASSIFIERS = ("modernbert-ja-310m-jev",)
RERANKERS = ("japanese-reranker-small-v2", "ruri-v3-reranker-310m")
MAPPED = ("mapped", "exact_string")
LABELS = ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
NONE = "該当なし"
ALIGNED, CONTRADICTION, UNRESOLVED = "aligned", "contradiction", "unresolved"


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).open(encoding="utf-8") if line.strip()]


def write_new(path: Path, rows=None, text=None):
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text if text is not None else "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(body, encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(text.split())
    return text.strip("-・「」\"'")


def au_options(context) -> list[dict]:
    seen, out = set(), []
    for row in context["au_rows"]:
        for axis in row["axes"]:
            key = (axis["axis_name"], axis["value"])
            if key not in seen:
                seen.add(key)
                out.append({"axis_name": axis["axis_name"], "value": axis["value"]})
    return out


def questions(args):
    run, out = ROOT / args.run, ROOT / args.out
    contexts = {c["dossier_id"]: c for c in read_jsonl(run / "inputs" / "products.jsonl")}
    asked = {}
    for case in read_jsonl(run / "inputs" / "cases.jsonl"):
        for axis in case["rakuten_selected"]["axes"]:
            key = (case["dossier_id"], axis["axis_key"], axis["value"])
            if key not in asked:
                asked[key] = {"dossier_id": case["dossier_id"], "axis_key": axis["axis_key"],
                              "axis_label": axis["axis_label"], "value": axis["value"],
                              "family_values": axis["family_values"],
                              "au_options": au_options(contexts[case["dossier_id"]])}
    rows = [{"question_id": f"q{i:04d}", **q} for i, q in enumerate(asked[k] for k in sorted(asked))]
    write_new(out / "questions.jsonl", rows)
    write_new(out / "questions-manifest.json", text=json.dumps({
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False, "run": args.run,
        "inputs_sha256": {n: sha256(run / "inputs" / n) for n in ("products.jsonl", "cases.jsonl")},
        "questions_sha256": sha256(out / "questions.jsonl"), "question_count": len(rows),
        "code_sha256": sha256(Path(__file__))}, ensure_ascii=False, indent=2) + "\n")
    print(len(rows), "questions")


def prompt(q, options) -> str:
    listing = "\n".join(f"- {o['axis_name']}: {o['value']}" for o in options)
    family = "、".join(q["family_values"])
    return (f"楽天の商品で「{q['axis_label']}: {q['value']}」が選ばれています。（楽天のこの項目の選択肢: {family}）\n"
            f"auの同じ商品の選択肢は次のとおりです。\n{listing}\n"
            f"楽天の選択と同じものを、上の一覧から1行そのまま答えてください。同じものが無ければ「{NONE}」と答えてください。")


def parse(reply: str, options) -> str | int:
    r = norm(reply)
    full = [i for i, o in enumerate(options) if r == norm(f"{o['axis_name']}:{o['value']}")]
    if len(full) == 1:
        return full[0]
    bare = [i for i, o in enumerate(options) if r == norm(o["value"])]
    if len(bare) == 1:
        return bare[0]
    return "none" if NONE in r else "invalid"


def jev_context(q) -> str:
    # Rendering must match the model's training format: 質問 then 状況.
    return (f"質問: 楽天の「{q['axis_label']}: {q['value']}」と同じものを指す、auの選択肢はどれですか。\n"
            f"状況: 楽天のこの項目の選択肢は{'、'.join(q['family_values'])}です。")


def ask_jev(args, out, qs, local, tok, model, torch):
    """Choice over the AU options plus 該当なし, then a true/false check of the chosen pair."""
    rows = []
    with torch.inference_mode():
        def scores(context, candidates):
            enc = tok([context] * len(candidates), candidates, padding=True, truncation="only_first",
                      max_length=512, return_tensors="pt").to(args.device)
            return model(**enc).logits[:, 0].float().tolist(), int(enc["input_ids"].numel())

        for q in qs:
            for order in ("forward", "reverse"):
                options = q["au_options"] if order == "forward" else q["au_options"][::-1]
                candidates = [f"{o['axis_name']}: {o['value']}" for o in options] + [NONE]
                start = time.perf_counter()
                choice_scores, tokens = scores(jev_context(q), candidates)
                best = max(range(len(candidates)), key=choice_scores.__getitem__)
                choice, verify = "none", None
                if best < len(options):
                    verify_scores, more = scores(
                        f"質問: 楽天の「{q['axis_label']}: {q['value']}」と、auの「{candidates[best]}」は同じものを指しますか。\n状況: 通販サイトの商品の選択肢です。",
                        ["true", "false"])
                    tokens += more
                    verify = verify_scores[0] > verify_scores[1]
                    choice = q["au_options"].index(options[best]) if verify else "none"
                if args.device == "cuda":
                    torch.cuda.synchronize()
                rows.append({"question_id": q["question_id"], "order": order, "reply": candidates[best],
                             "verified_same": verify, "choice": choice, "input_tokens": tokens, "output_tokens": 0,
                             "seconds": time.perf_counter() - start})
    return rows


def ask_reranker(args, qs, tok, model, torch):
    """Mutual best match: the AU option closest to the Rakuten value must have that value as its own
    closest among the Rakuten axis's options. Scores only rank; no threshold is applied."""
    rows = []
    with torch.inference_mode():
        def best(query, docs):
            enc = tok([query] * len(docs), docs, padding=True, truncation=True, max_length=512,
                      return_tensors="pt").to(args.device)
            logits = model(**enc).logits.float()
            scores = (logits[:, 0] if logits.shape[1] == 1 else logits[:, -1]).tolist()
            return max(range(len(docs)), key=scores.__getitem__), int(enc["input_ids"].numel())

        # The reverse search covers every option of every Rakuten axis of the product when asked, so a
        # Rakuten axis without an AU counterpart (レース あり) loses to the axis that has one.
        rakuten_options = defaultdict(dict)
        for q in qs:
            for v in q["family_values"]:
                rakuten_options[q["dossier_id"]][(q["axis_key"], v)] = f"{q['axis_label']}: {v}"
        for q in qs:
            start = time.perf_counter()
            texts = [f"{o['axis_name']}: {o['value']}" for o in q["au_options"]]
            b, tokens = best(f"{q['axis_label']}: {q['value']}", texts)
            pool = (list(rakuten_options[q["dossier_id"]].items()) if args.reverse_scope == "all"
                    else [((q["axis_key"], v), f"{q['axis_label']}: {v}") for v in q["family_values"]])
            back, more = best(texts[b], [text for _, text in pool])
            mutual = pool[back][0] == (q["axis_key"], q["value"])
            seconds = time.perf_counter() - start
            for order, choice in (("forward", b), ("reverse", b if mutual else "not_mutual")):
                rows.append({"question_id": q["question_id"], "order": order, "reply": texts[b],
                             "reverse_best": pool[back][1], "choice": choice,
                             "input_tokens": tokens + more if order == "forward" else 0, "output_tokens": 0,
                             "seconds": seconds if order == "forward" else 0.0})
    return rows


def ask(args):
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer, BitsAndBytesConfig
    torch.set_num_threads(args.threads)
    out = ROOT / args.out
    qs = read_jsonl(out / "questions.jsonl")
    repo, revision = MODELS[args.model]
    local = Path(snapshot_download(repo, revision=revision))
    tok = AutoTokenizer.from_pretrained(local)
    t0 = time.perf_counter()
    if args.model in CLASSIFIERS + RERANKERS:
        model = AutoModelForSequenceClassification.from_pretrained(local, dtype=getattr(torch, args.dtype)).to(args.device)
    elif args.quant == "int8":
        model = AutoModelForCausalLM.from_pretrained(local, quantization_config=BitsAndBytesConfig(load_in_8bit=True),
                                                     device_map=args.device)
    else:
        model = AutoModelForCausalLM.from_pretrained(local, dtype=getattr(torch, args.dtype)).to(args.device)
    model.eval()
    load_seconds = time.perf_counter() - t0
    rows = (ask_jev(args, out, qs, local, tok, model, torch) if args.model in CLASSIFIERS else
            ask_reranker(args, qs, tok, model, torch) if args.model in RERANKERS else [])
    with torch.inference_mode():
        for q in ([] if args.model in CLASSIFIERS + RERANKERS else qs):
            for order in ("forward", "reverse"):
                options = q["au_options"] if order == "forward" else q["au_options"][::-1]
                text = tok.apply_chat_template([{"role": "user", "content": prompt(q, options)}], tokenize=False,
                                               add_generation_prompt=True, enable_thinking=False)
                enc = tok(text, return_tensors="pt").to(args.device)
                start = time.perf_counter()
                gen = model.generate(**enc, max_new_tokens=args.max_new_tokens, do_sample=False)
                if args.device == "cuda":
                    torch.cuda.synchronize()
                seconds = time.perf_counter() - start
                reply = tok.decode(gen[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
                choice = parse(reply, options)
                if isinstance(choice, int):
                    choice = q["au_options"].index(options[choice])
                rows.append({"question_id": q["question_id"], "order": order, "reply": reply, "choice": choice,
                             "input_tokens": int(enc["input_ids"].shape[1]),
                             "output_tokens": int(gen.shape[1] - enc["input_ids"].shape[1]), "seconds": seconds})
    name = (f"{args.model}-{args.device}" + (f"-{args.quant}" if args.quant != "none" else "")
            + ("-reverse-all" if args.model in RERANKERS and args.reverse_scope == "all" else ""))
    write_new(out / f"answers-{name}.jsonl", rows)
    write_new(out / f"answers-{name}.json", text=json.dumps({
        "model": args.model, "repo": repo, "revision": revision, "device": args.device, "dtype": args.dtype,
        "quant": args.quant, "threads": args.threads, "decoding": "greedy", "thinking": False,
        "max_new_tokens": args.max_new_tokens, "load_seconds": load_seconds,
        "gpu": torch.cuda.get_device_name(0) if args.device == "cuda" else None,
        "weights_sha256": {p.name: sha256(p) for p in sorted(local.glob("*.safetensors"))},
        "torch": torch.__version__, "python": platform.python_version(),
        "questions_sha256": sha256(out / "questions.jsonl"), "answers_sha256": sha256(out / f"answers-{name}.jsonl"),
        "code_sha256": sha256(Path(__file__)), "finished_at_utc": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, indent=2) + "\n")
    seconds = sum(r["seconds"] for r in rows)
    print(name, f"{len(rows)} answers, {seconds:.0f}s, {seconds / len(rows):.2f}s/answer")


def mappings(qs, answers, exact_first=False, axis_checks=True):
    """(dossier, axis_key, value) -> AU option dict, NONE, or a reason it stays unresolved.

    answers=None maps only identical strings. exact_first maps an identical string before any
    model answer is read, so a model cannot move a value away from its identical AU option.
    """
    by_q = defaultdict(dict)
    for a in answers or []:
        by_q[a["question_id"]][a["order"]] = a["choice"]
    result = {}
    for q in qs:
        key = (q["dossier_id"], q["axis_key"], q["value"])
        same = [o for o in q["au_options"] if norm(o["value"]) == norm(q["value"])]
        if (exact_first or answers is None) and len(same) == 1:
            result[key] = {"status": "exact_string", "option": same[0]}
            continue
        if answers is None:
            result[key] = {"status": "no_identical_string"}
            continue
        f, r = by_q[q["question_id"]]["forward"], by_q[q["question_id"]]["reverse"]
        if r == "not_mutual":
            result[key] = {"status": "not_mutual_best"}
        elif f != r or f == "invalid":
            result[key] = {"status": "order_inconsistent" if f != r else "invalid_answer"}
        elif f == "none":
            result[key] = {"status": "no_same_au_option"}
        else:
            option = q["au_options"][f]
            a, b = norm(q["value"]), norm(option["value"])
            # One value containing the other carries extra conditions on the longer side
            # (スリム / グレー vs グレー, ホワイト vs オフホワイト), so the pair is not the same option.
            contained = a != b and (a in b or b in a)
            result[key] = {"status": "one_side_has_more" if contained else "mapped", "option": option}
    # One AU option cannot stand for two different Rakuten values of the same axis.
    targets = defaultdict(list)
    for key, m in result.items():
        if m["status"] in MAPPED:
            targets[(key[0], key[1], m["option"]["axis_name"], m["option"]["value"])].append(key)
    for keys in targets.values():
        if len(keys) > 1:
            for key in keys:
                result[key] = {"status": "not_one_to_one", "option": result[key]["option"],
                               "shared_with": [k[2] for k in keys if k != key]}
    if not axis_checks:
        return result
    # Axes correspond one to one too: a Rakuten axis whose values land on several AU axes, or an AU
    # axis reached from several Rakuten axes (サイズ 80cm -> カラー マーブルホワイト), is not trusted.
    au_axes_of, rak_axes_of = defaultdict(set), defaultdict(set)
    for (dossier, axis_key, _), m in result.items():
        if m["status"] in MAPPED:
            au_axes_of[(dossier, axis_key)].add(m["option"]["axis_name"])
            rak_axes_of[(dossier, m["option"]["axis_name"])].add(axis_key)
    for key, m in result.items():
        if m["status"] not in MAPPED:
            continue
        if len(au_axes_of[key[:2]]) > 1:
            result[key] = {"status": "axis_split", "option": m["option"]}
        elif len(rak_axes_of[(key[0], m["option"]["axis_name"])]) > 1:
            result[key] = {"status": "axis_shared", "option": m["option"]}
    return result


AXIS_EVIDENCE = MAPPED + ("one_side_has_more", "not_one_to_one")


def axis_split(maps):
    """(dossier, Rakuten axis_key) -> symmetric with one AU axis, or one-sided with a reason.

    A value whose AU counterpart was found (even when the value pair itself stays unresolved) is
    evidence that its Rakuten axis corresponds to that AU axis. An axis with no such value is
    one-sided; values landing on several AU axes, or one AU axis claimed by several Rakuten axes,
    leave the axis one-sided rather than guessing.
    """
    votes = defaultdict(Counter)
    for (dossier, axis_key, _), m in maps.items():
        votes[(dossier, axis_key)]  # every asked axis gets an entry
        if m["status"] in AXIS_EVIDENCE:
            votes[(dossier, axis_key)][m["option"]["axis_name"]] += 1
    claims = defaultdict(list)
    for key, v in votes.items():
        if len(v) == 1:
            claims[(key[0], next(iter(v)))].append(key)
    result = {}
    for key, v in votes.items():
        if not v:
            result[key] = {"kind": "one_sided", "reason": "no_value_found_an_au_counterpart"}
        elif len(v) > 1:
            result[key] = {"kind": "one_sided", "reason": "values_land_on_several_au_axes", "votes": dict(v)}
        elif len(claims[(key[0], next(iter(v)))]) > 1:
            result[key] = {"kind": "one_sided", "reason": "au_axis_claimed_by_several_rakuten_axes", "votes": dict(v)}
        else:
            result[key] = {"kind": "symmetric", "au_axis": next(iter(v)), "votes": dict(v)}
    return result


def align_axes(args, run, out, qs, answers, meta):
    """Explicit split: compare symmetric conditions here; hand one-sided ones to the description check."""
    maps = mappings(qs, answers, exact_first=args.exact_first, axis_checks=False)
    axes = axis_split(maps)
    contexts = {c["dossier_id"]: c for c in read_jsonl(run / "inputs" / "products.jsonl")}
    rule = {r["case_id"]: r for r in read_jsonl(run / "predictions" / "A-full_spec_notice.jsonl")}
    dest = out / f"align-{args.answers}-{args.tag}"
    conditions, handoff, decisions, diff = [], [], [], []
    for case in read_jsonl(run / "inputs" / "cases.jsonl"):
        ctx, sel = contexts[case["dossier_id"]], case["rakuten_selected"]
        paired = {axes[(case["dossier_id"], a["axis_key"])].get("au_axis") for a in sel["axes"]} - {None}
        au_values = defaultdict(set)
        for row in ctx["au_rows"]:
            for a in row["axes"]:
                au_values[a["axis_name"]].add(a["value"])
        au_only_axes = {n for n, v in au_values.items() if n not in paired and len(v) > 1}
        row_states = {}
        for row in ctx["au_rows"]:
            row_axes = {a["axis_name"]: a for a in row["axes"]}
            conds = []
            for axis in sel["axes"]:
                m, ax = maps[(case["dossier_id"], axis["axis_key"], axis["value"])], axes[(case["dossier_id"], axis["axis_key"])]
                base = {"axis_label": axis["axis_label"], "selected_value": axis["value"],
                        "family_values": axis["family_values"], "axis_key": axis["axis_key"],
                        "value_span": axis["value_span"], "axis": ax, "mapping": m}
                if ax["kind"] == "one_sided":
                    conds.append({**base, "status": "one_sided"})
                    continue
                au = row_axes.get(ax["au_axis"])
                option = m.get("option") if m["status"] in MAPPED + ("one_side_has_more",) else None
                if option is None or option["axis_name"] != ax["au_axis"] or au is None:
                    conds.append({**base, "status": "symmetric_unresolved"})
                elif au["value"] != option["value"]:
                    conds.append({**base, "status": CONTRADICTION, "au_value": au["value"], "au_value_span": au["value_span"]})
                elif m["status"] == "one_side_has_more":
                    conds.append({**base, "status": "extra_in_value", "au_value": au["value"], "au_value_span": au["value_span"]})
                else:
                    conds.append({**base, "status": ALIGNED, "au_value": au["value"], "au_value_span": au["value_span"]})
            au_only = [{"axis_name": n, "value": row_axes[n]["value"], "value_span": row_axes[n]["value_span"]}
                       for n in sorted(au_only_axes) if n in row_axes]
            statuses = {c["status"] for c in conds}
            if CONTRADICTION in statuses:
                state = CONTRADICTION
            elif "symmetric_unresolved" in statuses:
                state = "symmetric_unresolved"
            elif statuses <= {ALIGNED} and not au_only:
                state = ALIGNED
            else:
                state = "pending_description_check"
            row_states[row["row_key"]] = state
            conditions.append({"case_id": case["case_id"], "au_row_key": row["row_key"], "row_state": state,
                               "conditions": conds, "au_only_varying_conditions": au_only})
            if state == "pending_description_check":
                handoff.append({
                    "case_id": case["case_id"], "dossier_id": case["dossier_id"], "au_product_id": case["au_product_id"],
                    "au_row_key": row["row_key"],
                    "au_product_source": {k: ctx["au_product"][k] for k in ("raw_file", "sha256")},
                    "rakuten_sku": {k: sel[k] for k in ("source_sku_key", "sku_record_key", "variant_id", "url",
                                                        "raw_file", "sha256")},
                    "one_sided_conditions": [c for c in conds if c["status"] == "one_sided"],
                    "extra_in_value_conditions": [c for c in conds if c["status"] == "extra_in_value"],
                    "aligned_conditions": [c for c in conds if c["status"] == ALIGNED],
                    "au_only_varying_conditions": au_only,
                    "rule": "adopt only if every condition is supported on this row; a contradiction is never overridden"})
        full = [k for k, s in row_states.items() if s == ALIGNED]
        others_contradicted = all(s == CONTRADICTION for k, s in row_states.items() if k not in full)
        if len(full) == 1 and others_contradicted:
            decision, top, reason = "matched", full[0], "all_conditions_aligned_on_one_row"
        elif len(full) > 1:
            decision, top, reason = "unmatched", None, "several_rows_aligned"
        elif all(s == CONTRADICTION for s in row_states.values()):
            decision, top, reason = "unmatched", None, "contradiction_on_every_row"
        elif any(s == "pending_description_check" for s in row_states.values()):
            decision, top, reason = "unmatched", None, "pending_description_check"
        else:
            decision, top, reason = "unmatched", None, "symmetric_condition_unresolved"
        decisions.append({"case_id": case["case_id"], "dossier_id": case["dossier_id"], "decision": decision,
                          "top_row_key": top, "reason": reason, "row_states": dict(Counter(row_states.values()))})
        rb = rule[case["case_id"]]["binary"]
        if (rb["decision"], rb.get("top_row_key")) != (decision, top):
            diff.append({"case_id": case["case_id"], "rule": [rb["decision"], rb.get("top_row_key")],
                         "rule_reasons": rb["reason_codes"], "alignment": [decision, top], "alignment_reason": reason})
    axis_rows = [{"dossier_id": k[0], "axis_key": k[1], **v} for k, v in sorted(axes.items())]
    for name, rows in (("axes.jsonl", axis_rows), ("conditions.jsonl", conditions), ("handoff.jsonl", handoff),
                       ("decisions.jsonl", decisions), ("diff-vs-rule-v10.jsonl", diff)):
        write_new(dest / name, rows)
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False, "mode": "axis_split",
               "answers": args.answers, "exact_first": args.exact_first, "answers_sha256": meta["answers_sha256"],
               "code_sha256": sha256(Path(__file__)), "axes": dict(Counter(v["kind"] for v in axes.values())),
               "axis_reasons": dict(Counter(v.get("reason", "symmetric") for v in axes.values())),
               "decisions": dict(Counter(d["decision"] for d in decisions)),
               "decision_reasons": dict(Counter(d["reason"] for d in decisions)),
               "handoff_rows": len(handoff), "handoff_cases": len({h["case_id"] for h in handoff}),
               "diff_vs_rule": dict(Counter(f"{d['rule'][0]}->{d['alignment'][0]}" for d in diff)),
               "outputs_sha256": {p.name: sha256(p) for p in sorted(dest.glob("*.jsonl"))}}
    write_new(dest / "summary.json", text=json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("axes", "axis_reasons", "decisions", "decision_reasons", "handoff_rows",
                                              "diff_vs_rule")}, ensure_ascii=False, indent=1))


def align(args):
    run, out = ROOT / args.run, ROOT / args.out
    qs = read_jsonl(out / "questions.jsonl")
    if args.answers == "exact":
        answers, meta = None, {"answers_sha256": None}
    else:
        answers = read_jsonl(out / f"answers-{args.answers}.jsonl")
        meta = json.loads((out / f"answers-{args.answers}.json").read_text(encoding="utf-8"))
        if meta["answers_sha256"] != sha256(out / f"answers-{args.answers}.jsonl"):
            raise RuntimeError("Answers changed after they were saved")
    if args.axis_split:
        return align_axes(args, run, out, qs, answers, meta)
    maps = mappings(qs, answers, exact_first=args.exact_first)
    contexts = {c["dossier_id"]: c for c in read_jsonl(run / "inputs" / "products.jsonl")}
    rule = {r["case_id"]: r for r in read_jsonl(run / "predictions" / "A-full_spec_notice.jsonl")}
    dest = out / f"align-{args.answers}" if not args.tag else out / f"align-{args.answers}-{args.tag}"
    conditions, handoff, decisions, diff = [], [], [], []
    for case in read_jsonl(run / "inputs" / "cases.jsonl"):
        ctx, sel = contexts[case["dossier_id"]], case["rakuten_selected"]
        mapped_axes = set()
        cond_maps = []
        for axis in sel["axes"]:
            m = maps[(case["dossier_id"], axis["axis_key"], axis["value"])]
            cond_maps.append((axis, m))
            if m["status"] in MAPPED:
                mapped_axes.add(m["option"]["axis_name"])
        # An AU axis no Rakuten condition maps to distinguishes rows only when it has several values.
        au_values = defaultdict(set)
        for row in ctx["au_rows"]:
            for a in row["axes"]:
                au_values[a["axis_name"]].add(a["value"])
        varying_au_only = {n for n, v in au_values.items() if n not in mapped_axes and len(v) > 1}
        row_states = {}
        for row in ctx["au_rows"]:
            row_axes = {a["axis_name"]: a for a in row["axes"]}
            conds = []
            for axis, m in cond_maps:
                base = {"axis_label": axis["axis_label"], "selected_value": axis["value"],
                        "family_values": axis["family_values"], "axis_key": axis["axis_key"],
                        "value_span": axis["value_span"], "mapping": m}
                if m["status"] not in MAPPED:
                    conds.append({**base, "status": UNRESOLVED, "reason": m["status"]})
                    continue
                au = row_axes.get(m["option"]["axis_name"])
                if au is None:
                    conds.append({**base, "status": UNRESOLVED, "reason": "row_lacks_mapped_axis"})
                elif au["value"] == m["option"]["value"]:
                    conds.append({**base, "status": ALIGNED, "au_value": au["value"], "au_value_span": au["value_span"]})
                else:
                    conds.append({**base, "status": CONTRADICTION, "au_value": au["value"], "au_value_span": au["value_span"]})
            au_only = [{"axis_name": n, "value": row_axes[n]["value"], "value_span": row_axes[n]["value_span"]}
                       for n in sorted(varying_au_only) if n in row_axes]
            state = (CONTRADICTION if any(c["status"] == CONTRADICTION for c in conds) else
                     ALIGNED if all(c["status"] == ALIGNED for c in conds) and not au_only else UNRESOLVED)
            row_states[row["row_key"]] = state
            conditions.append({"case_id": case["case_id"], "au_row_key": row["row_key"], "row_state": state,
                               "conditions": conds, "au_only_varying_conditions": au_only})
            if state == UNRESOLVED:
                handoff.append({
                    "case_id": case["case_id"], "dossier_id": case["dossier_id"], "au_product_id": case["au_product_id"],
                    "au_row_key": row["row_key"],
                    "au_product_source": {k: ctx["au_product"][k] for k in ("raw_file", "sha256")},
                    "rakuten_sku": {k: sel[k] for k in ("source_sku_key", "sku_record_key", "variant_id", "url",
                                                        "raw_file", "sha256")},
                    "unresolved_conditions": [c for c in conds if c["status"] == UNRESOLVED],
                    "aligned_conditions": [c for c in conds if c["status"] == ALIGNED],
                    "au_only_varying_conditions": au_only,
                    "rule": "adopt only if every condition is supported on this row; a contradiction is never overridden"})
        full = [k for k, s in row_states.items() if s == ALIGNED]
        others_contradicted = all(s == CONTRADICTION for k, s in row_states.items() if k not in full)
        if len(full) == 1 and others_contradicted:
            decision, top, reason = "matched", full[0], "all_conditions_aligned_on_one_row"
        elif len(full) > 1:
            decision, top, reason = "unmatched", None, "several_rows_aligned"
        elif not full and all(s == CONTRADICTION for s in row_states.values()):
            decision, top, reason = "unmatched", None, "contradiction_on_every_row"
        else:
            decision, top, reason = "unmatched", None, "unresolved_conditions_pending"
        decisions.append({"case_id": case["case_id"], "dossier_id": case["dossier_id"], "decision": decision,
                          "top_row_key": top, "reason": reason, "row_states": dict(Counter(row_states.values()))})
        rb = rule[case["case_id"]]["binary"]
        if (rb["decision"], rb.get("top_row_key")) != (decision, top):
            diff.append({"case_id": case["case_id"], "rule": [rb["decision"], rb.get("top_row_key")],
                         "rule_reasons": rb["reason_codes"], "alignment": [decision, top], "alignment_reason": reason,
                         "pending_rows": sum(s == UNRESOLVED for s in row_states.values())})
    write_new(dest / "conditions.jsonl", conditions)
    write_new(dest / "handoff.jsonl", handoff)
    write_new(dest / "decisions.jsonl", decisions)
    write_new(dest / "diff-vs-rule-v10.jsonl", diff)
    map_counts = Counter(m["status"] for m in maps.values())
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
               "answers": args.answers, "exact_first": args.exact_first, "answers_sha256": meta["answers_sha256"], "code_sha256": sha256(Path(__file__)),
               "mapping_status": dict(map_counts),
               "decisions": dict(Counter(d["decision"] for d in decisions)),
               "decision_reasons": dict(Counter(d["reason"] for d in decisions)),
               "handoff_rows": len(handoff), "handoff_cases": len({h["case_id"] for h in handoff}),
               "diff_vs_rule": dict(Counter(f"{d['rule'][0]}->{d['alignment'][0]}" for d in diff)),
               "outputs_sha256": {p.name: sha256(p) for p in sorted(dest.glob("*.jsonl"))}}
    write_new(dest / "summary.json", text=json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def score(args):
    dest = ROOT / args.out / (f"align-{args.answers}" + (f"-{args.tag}" if args.tag else ""))
    summary = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
    for name, digest in summary["outputs_sha256"].items():
        if sha256(dest / name) != digest:
            raise RuntimeError(f"Prediction file changed before scoring: {name}")
    gold = {g["case_id"]: g for g in read_jsonl(ROOT / LABELS)}
    counts = Counter()
    for d in read_jsonl(dest / "decisions.jsonl"):
        g = gold.get(d["case_id"])
        if g is None:
            continue
        if d["decision"] == "matched":
            counts["accept_" + ("correct_row" if g["decision"] == "matched" and d["top_row_key"] in g["matching_au_row_keys"]
                                else "wrong_row" if g["decision"] == "matched" else
                                "on_unmatched_label" if g["decision"] == "unmatched" else "on_review_label")] += 1
        else:
            counts["exclude_on_" + g["decision"] + "_label"] += 1
    # Condition level, on cases labelled matched: what the alignment says on the labelled row and on
    # the competing rows. A contradiction on the labelled row would exclude a true match.
    rows = defaultdict(dict)
    for c in read_jsonl(dest / "conditions.jsonl"):
        rows[c["case_id"]][c["au_row_key"]] = c
    cond = Counter()
    for case_id, case_rows in rows.items():
        g = gold.get(case_id)
        if g is None or g["decision"] != "matched":
            continue
        true_rows = [k for k in case_rows if k in g["matching_au_row_keys"]]
        for k in true_rows:
            for c in case_rows[k]["conditions"]:
                cond["true_row_condition_" + c["status"]] += 1
        others = [r for k, r in case_rows.items() if k not in g["matching_au_row_keys"]]
        cond["cases_matched_label"] += 1
        cond["cases_competitors_all_contradicted"] += all(r["row_state"] == CONTRADICTION for r in others)
    result = {"labels": LABELS, "labels_sha256": sha256(ROOT / LABELS), "counts": dict(counts),
              "matched_label_conditions": dict(cond),
              "note": "Luna machine labels, human-unverified; 29 confirmed pairs used in development"}
    write_new(dest / "score-machine-labels-conditions.json", text=json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=1))


def score_axes(args):
    """Axis split against a manual axis reference, then conditions on the labelled rows by that split."""
    out = ROOT / args.out
    dest = out / f"align-{args.answers}-{args.tag}"
    summary = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
    for name, digest in summary["outputs_sha256"].items():
        if sha256(dest / name) != digest:
            raise RuntimeError(f"Prediction file changed before scoring: {name}")
    reference = json.loads((ROOT / args.axis_reference).read_text(encoding="utf-8"))["pairs"]
    labels_of = {(q["dossier_id"], q["axis_key"]): q["axis_label"] for q in read_jsonl(out / "questions.jsonl")}
    axis_counts, axis_errors = Counter(), []
    predicted = {}
    for a in read_jsonl(dest / "axes.jsonl"):
        label = labels_of[(a["dossier_id"], a["axis_key"])]
        want = reference[a["dossier_id"]][label]["au_axis"]
        got = a.get("au_axis")
        predicted[(a["dossier_id"], a["axis_key"])] = want
        kind = ("symmetric" if want else "one_sided") + "_reference_" + (
            "correct" if got == want else "predicted_one_sided" if got is None else
            "predicted_symmetric" if want is None else "wrong_au_axis")
        axis_counts[kind] += 1
        if got != want:
            axis_errors.append({"dossier_id": a["dossier_id"], "axis_label": label, "reference": want, "predicted": got,
                                "reason": a.get("reason"), "votes": a.get("votes")})
    gold = {g["case_id"]: g for g in read_jsonl(ROOT / LABELS)}
    dossier_of = {d["case_id"]: d["dossier_id"] for d in read_jsonl(dest / "decisions.jsonl")}
    cond, row_state = Counter(), Counter()
    for c in read_jsonl(dest / "conditions.jsonl"):
        g = gold.get(c["case_id"])
        if g is None or g["decision"] != "matched" or c["au_row_key"] not in g["matching_au_row_keys"]:
            continue
        row_state[c["row_state"]] += 1
        for x in c["conditions"]:
            ref_symmetric = predicted.get((dossier_of[c["case_id"]], x["axis_key"])) is not None
            cond[("symmetric" if ref_symmetric else "one_sided") + "_" + x["status"]] += 1
    result = {"axis_reference": args.axis_reference, "axis_reference_sha256": sha256(ROOT / args.axis_reference),
              "labels_sha256": sha256(ROOT / LABELS), "axes": dict(sorted(axis_counts.items())),
              "axis_errors": axis_errors, "labelled_row_conditions_by_reference_split": dict(sorted(cond.items())),
              "labelled_row_states": dict(row_state),
              "note": "axis reference is Claude's own reading (not blind); labels are Luna machine labels"}
    write_new(dest / "score-axes.json", text=json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "axis_errors"}, ensure_ascii=False, indent=1))
    for e in axis_errors:
        print("AXIS ERROR", json.dumps(e, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["questions", "ask", "align", "score", "score-axes"])
    parser.add_argument("--run", default=".lab-output/sku-gate-tasks-20261010-v10")
    parser.add_argument("--out", default=".lab-output/sku-align-20261010-v1/legacy")
    parser.add_argument("--model", choices=sorted(MODELS))
    parser.add_argument("--answers", help="answers name, e.g. qwen3.5-2b-cuda")
    parser.add_argument("--tag", default="", help="suffix for a new align output directory")
    parser.add_argument("--axis-split", action="store_true",
                        help="align: decide symmetric and one-sided axes explicitly before comparing values")
    parser.add_argument("--axis-reference", default=".lab-output/sku-align-20261010-v1/reference/axis-correspondence-legacy.json")
    parser.add_argument("--exact-first", action="store_true",
                        help="align: map identical option strings before reading model answers")
    parser.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
    parser.add_argument("--dtype", default="float16", choices=["float32", "float16", "bfloat16"])
    parser.add_argument("--quant", default="none", choices=["none", "int8"])
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=48)
    parser.add_argument("--reverse-scope", default="axis", choices=["axis", "all"],
                        help="rerankers: reverse best match within the Rakuten axis or over all its axes")
    args = parser.parse_args()
    {"questions": questions, "ask": ask, "align": align, "score": score, "score-axes": score_axes}[args.step](args)


if __name__ == "__main__":
    main()
