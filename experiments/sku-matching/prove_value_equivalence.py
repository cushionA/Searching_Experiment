"""Prove, from axis names and option values alone, that a model candidate is the same value written another way.

Every model candidate of an alignment handoff gets one sidecar record. A proof needs every check of one type; otherwise the
pair stays a model candidate and the failed checks are its reasons. Only the cores (align v7: bracketed and '/' parts set
aside) are compared; residual parts are copied unchanged. Nothing is stripped or case-folded unconditionally, and numbers
are never compared on their own.

ascii_case_variant   the cores differ only in the case of ASCII letters; on each side's option list case separates no two
                     options and each changed letter is written in one case only; each core has exactly one counterpart;
                     no changed letter touches a digit, because a letter next to a number can be a unit or a prefix whose
                     case matters (5g/5G, 1GB/1Gb, 10mW/10MW).
own_axis_name_affix  both sides name the axis the same; one core is the other plus that axis name as a prefix or suffix;
                     every option on that side carries it in that place and no option on the other side does; removing it
                     keeps that side's options distinct and non-empty and gives each core exactly one counterpart.

A case variant that fails only because a changed letter touches a digit also records where each side's own title,
description or purchase options write the other side's spelling. That is evidence for the description check, not a proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align_sku_conditions as al  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ASCII_LETTER = re.compile(r"[A-Za-z]")
DIGIT = re.compile(r"[0-9]")


def core_of(value: str) -> str:
    return "/".join(al.parts(value)[0])


def ascii_lower(text: str) -> str:
    return "".join(c.lower() if ASCII_LETTER.fullmatch(c) else c for c in text)


def check(name, passed, **evidence):
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def letter_run(text: str, i: int) -> tuple[int, int]:
    start, end = i, i + 1
    while start > 0 and ASCII_LETTER.fullmatch(text[start - 1]):
        start -= 1
    while end < len(text) and ASCII_LETTER.fullmatch(text[end]):
        end += 1
    return start, end


def number_token(text: str, start: int, end: int) -> str:
    while start > 0 and DIGIT.fullmatch(text[start - 1]):
        start -= 1
    while end < len(text) and DIGIT.fullmatch(text[end]):
        end += 1
    return text[start:end]


def ascii_case_variant(r: str, a: str, r_options: list, a_options: list) -> dict:
    changed = [i for i, (x, y) in enumerate(zip(r, a)) if x != y] if len(r) == len(a) else None
    only_case = (changed is not None and bool(changed)
                 and all(ASCII_LETTER.fullmatch(r[i]) and r[i].lower() == a[i].lower() for i in changed))
    checks = [check("differs_only_in_ascii_letter_case", only_case, rakuten_core=r, au_core=a,
                    changed=[{"index": i, "rakuten": r[i], "au": a[i]} for i in changed or []])]
    if not only_case:
        return {"type": "ascii_case_variant", "proven": False, "checks": checks}
    letters = sorted({r[i].lower() for i in changed})
    for side, options in (("rakuten", r_options), ("au", a_options)):
        folded = Counter(ascii_lower(o) for o in options)
        merged = sorted(o for o in options if folded[ascii_lower(o)] > 1)
        cases = {l: sorted({c for o in options for c in o if c.lower() == l}) for l in letters}
        checks.append(check(f"case_separates_nothing_on_{side}_options", not merged and all(len(c) <= 1 for c in cases.values()),
                            options=options, merged_by_case=merged, cases_used=cases))
    r_hits = [o for o in a_options if ascii_lower(o) == ascii_lower(r)]
    a_hits = [o for o in r_options if ascii_lower(o) == ascii_lower(a)]
    checks.append(check("unique_counterpart", r_hits == [a] and a_hits == [r], au_options_matching_rakuten=r_hits,
                        rakuten_options_matching_au=a_hits))
    tokens = []
    for i in changed:
        start, end = letter_run(r, i)
        touches = bool((start > 0 and DIGIT.fullmatch(r[start - 1])) or (end < len(r) and DIGIT.fullmatch(r[end])))
        tokens.append({"index": i, "letter_run": r[start:end], "touches_digit": touches,
                       "rakuten_token": number_token(r, start, end), "au_token": number_token(a, start, end)})
    checks.append(check("no_changed_letter_touches_a_digit", not any(t["touches_digit"] for t in tokens), tokens=tokens))
    return {"type": "ascii_case_variant", "proven": all(c["passed"] for c in checks), "checks": checks,
            "normalization": [{"step": "ascii_letter_case_fold", "applies_to": "ASCII letters only, both sides",
                               "rakuten": ascii_lower(r), "au": ascii_lower(a)}],
            "changed_characters": [{"index": i, "rakuten": r[i], "au": a[i]} for i in changed], "tokens": tokens}


def own_axis_name_affix(r: str, a: str, r_axis: str, a_axis: str, r_options: list, a_options: list) -> dict:
    name = al.norm(a_axis)
    checks = [check("same_axis_name", name and al.norm(r_axis) == name, rakuten_axis=r_axis, au_axis=a_axis)]
    found = None
    for side, longer, shorter in (("au", a, r), ("rakuten", r, a)):
        if shorter and name:
            if longer == shorter + name:
                found = (side, "suffix")
            elif longer == name + shorter:
                found = (side, "prefix")
    checks.append(check("one_core_is_the_other_plus_the_axis_name", found is not None, rakuten_core=r, au_core=a,
                        axis_name=name, side=found and found[0], position=found and found[1]))
    if not all(c["passed"] for c in checks):
        return {"type": "own_axis_name_affix", "proven": False, "checks": checks}
    side, position = found
    own, other = (a_options, r_options) if side == "au" else (r_options, a_options)
    has = (lambda o: o.endswith(name)) if position == "suffix" else (lambda o: o.startswith(name))
    strip = (lambda o: o[:-len(name)]) if position == "suffix" else (lambda o: o[len(name):])
    checks.append(check(f"axis_name_on_every_{side}_option", all(has(o) for o in own), options=own,
                        without=[o for o in own if not has(o)]))
    checks.append(check("axis_name_on_no_option_of_the_other_side", not any(has(o) for o in other), options=other,
                        with_it=[o for o in other if has(o)]))
    stripped = [strip(o) for o in own if has(o)]
    checks.append(check("removal_keeps_options_distinct_and_non_empty",
                        len(set(stripped)) == len(stripped) and all(stripped), stripped=stripped))
    target = strip(a if side == "au" else r)
    keep = r if side == "au" else a
    own_hits = [o for o in own if has(o) and strip(o) == keep]
    other_hits = [o for o in other if o == target]
    checks.append(check("unique_counterpart", len(own_hits) == 1 and other_hits == [keep],
                        own_options_matching=own_hits, other_options_matching=other_hits))
    return {"type": "own_axis_name_affix", "proven": all(c["passed"] for c in checks), "checks": checks,
            "normalization": [{"step": "remove_axis_name_affix", "side": side, "position": position, "removed_text": name,
                               "result": target}],
            "removed_text": name, "full_option_list_bijection": sorted(stripped) == sorted(other)}


def texts_of(product: dict, side: str) -> list:
    if side == "au":
        out = [{"field": "au_product.title", "text": product["au_product"]["title"], "span": product["au_product"].get("title_span")}]
        keys = ("au_description_lines", "au_purchase_option_lines")
    else:
        rp = product["rakuten_product"]
        out = [{"field": "rakuten_product.title", "text": rp.get("title") or "", "span": rp.get("title_span")}]
        keys = ("rakuten_description_lines",)
    for k in keys:
        out += [{"field": k, "text": x.get("text", ""), "span": x.get("span")} for x in product[k] if isinstance(x, dict)]
    return out


def corroboration(attempt: dict, product: dict) -> list:
    found = []
    for t in attempt["tokens"]:
        for side, spelling in (("au", t["rakuten_token"]), ("rakuten", t["au_token"])):
            for x in texts_of(product, side):
                if spelling in al.norm(x["text"]):
                    found.append({"text_side": side, "writes_other_side_spelling": spelling, "field": x["field"],
                                  "quote": x["text"], "span": x["span"]})
    return found


def spans(product: dict, case: dict) -> tuple[dict, dict, dict]:
    au_axis, au_value = {}, {}
    for row in product["au_rows"]:
        for a in row["axes"]:
            au_axis.setdefault(a["axis_name"], a.get("axis_name_span"))
            au_value.setdefault((a["axis_name"], a["value"]), a.get("value_span"))
    rakuten = {a["axis_key"]: (i, a) for i, a in enumerate(case["rakuten_selected"]["axes"])}
    return au_axis, au_value, rakuten


def prove(cond: dict, row: dict, case: dict, product: dict) -> dict:
    au_axis_spans, au_value_spans, rakuten = spans(product, case)
    index, rax = rakuten[cond["axis_key"]]
    options = row["au_axis_options"][cond["au_axis"]]
    r, a = core_of(cond["value"]), core_of(cond["au_value"])
    r_options = [core_of(v) for v in rax["family_values"]]
    a_options = [core_of(v) for v in options]
    attempts = [ascii_case_variant(r, a, r_options, a_options),
                own_axis_name_affix(r, a, rax["axis_label"], cond["au_axis"], r_options, a_options)]
    proven = [x for x in attempts if x["proven"]]
    record = {
        "case_id": row["case_id"], "dossier_id": row["dossier_id"], "au_row_key": row["au_row_key"], "axis_key": cond["axis_key"],
        "rakuten": {"axis_label": rax["axis_label"], "axis_label_span": rax.get("axis_label_span"), "value": cond["value"],
                    "value_span": rax.get("value_span"), "family_values": rax["family_values"],
                    "family_values_source": f"inputs/cases.jsonl $.rakuten_selected.axes[{index}].family_values"},
        "au": {"axis_name": cond["au_axis"], "axis_name_span": au_axis_spans.get(cond["au_axis"]), "value": cond["au_value"],
               "value_span": au_value_spans.get((cond["au_axis"], cond["au_value"])),
               "options": [{"value": v, "value_span": au_value_spans.get((cond["au_axis"], v))} for v in options]},
        "align_v7": {"status": cond["status"], "core": cond.get("core"), "residual": cond.get("residual")},
        "normalization_before_types": [
            {"step": "nfkc_remove_whitespace", "rakuten": al.norm(cond["value"]), "au": al.norm(cond["au_value"])},
            {"step": "set_aside_bracketed_and_slash_parts", "rakuten_core": r, "au_core": a,
             "residual_kept": cond.get("residual")}],
        "attempts": attempts,
        "verdict": "proven_equivalent" if proven else "model_candidate",
        "proof_type": proven[0]["type"] if proven else None,
        "proof_scope": "cores only; the residual parts below stay unconfirmed and keep their sides",
        "residual_unconfirmed": bool(cond.get("residual")),
        "reasons": [] if proven else [f"{x['type']}: {c['name']}" for x in attempts for c in x["checks"] if not c["passed"]][:4],
        "preserved": {"residual": cond.get("residual"), "only_change": (proven[0].get("normalization") if proven else None)},
    }
    case_try = attempts[0]
    if not case_try["proven"] and [c["name"] for c in case_try["checks"] if not c["passed"]] == ["no_changed_letter_touches_a_digit"]:
        record["corroboration"] = corroboration(case_try, product)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True, help="frozen run with inputs/cases.jsonl and inputs/products.jsonl")
    p.add_argument("--align", required=True, help="align v7 output dir holding align/handoff.jsonl")
    p.add_argument("--out", required=True)
    p.add_argument("--groups", help="groups.jsonl; with --split, only those dossiers are read")
    p.add_argument("--split", action="append")
    args = p.parse_args()
    run, align_dir, out = ROOT / args.run, ROOT / args.align, ROOT / args.out
    allowed = None
    if args.groups:
        allowed = {g["dossier_id"] for g in al.read_jsonl(ROOT / args.groups) if g["split"] in set(args.split or [])}
    keep = (lambda d: True) if allowed is None else (lambda d: d in allowed)
    cases = {c["case_id"]: c for c in al.read_jsonl(run / "inputs/cases.jsonl") if keep(c["dossier_id"])}
    products = {x["dossier_id"]: x for x in al.read_jsonl(run / "inputs/products.jsonl") if keep(x["dossier_id"])}
    records = []
    for row in al.read_jsonl(align_dir / "align/handoff.jsonl"):
        if row["case_id"] not in cases:
            continue
        for cond in row["conditions"]:
            if cond["kind"] == "rakuten" and cond["status"] == "model_candidate":
                records.append(prove(cond, row, cases[row["case_id"]], products[row["dossier_id"]]))
    al.write_new(out / "sidecars.jsonl", records)
    inputs = {"cases.jsonl": run / "inputs/cases.jsonl", "products.jsonl": run / "inputs/products.jsonl",
              "handoff.jsonl": align_dir / "align/handoff.jsonl", "align_summary.json": align_dir / "align/summary.json"}
    if args.groups:
        inputs["groups.jsonl"] = ROOT / args.groups
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
        "run": args.run, "align": args.align, "groups": args.groups, "splits": args.split,
        "inputs_sha256": {k: al.sha256(v) for k, v in inputs.items()},
        "align_code_sha256": json.loads((align_dir / "align/summary.json").read_text(encoding="utf-8"))["code_sha256"],
        "code_sha256": al.sha256(Path(__file__)), "align_module_sha256": al.sha256(Path(al.__file__)),
        "cases_read": len(cases), "model_candidates": len(records),
        "verdicts": dict(Counter(x["verdict"] for x in records)),
        "proof_types": dict(Counter(x["proof_type"] for x in records if x["proof_type"])),
        "proven_with_residual_unconfirmed": sum(1 for x in records if x["proof_type"] and x["residual_unconfirmed"]),
        "distinct_value_pairs_proven": dict(Counter(x["proof_type"] for x in
                                                    {(x["rakuten"]["value"], x["au"]["value"], x["proof_type"]): x
                                                     for x in records if x["proof_type"]}.values())),
        "first_failed_check": dict(Counter(x["reasons"][0] if x["reasons"] else "proven" for x in records)),
        "with_corroboration": sum(1 for x in records if x.get("corroboration")),
        "sidecars_sha256": al.sha256(out / "sidecars.jsonl"),
    }
    al.write_new(out / "summary.json", text=json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("cases_read", "model_candidates", "verdicts", "proof_types",
                                              "distinct_value_pairs_proven", "with_corroboration")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
