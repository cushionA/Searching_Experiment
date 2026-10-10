#!/usr/bin/env python3
"""Recover whole unresolved conditions omitted by Claude's frozen v4axes handoff.

Uses only the pinned alignment module's pure mappings/axis_split functions.
No model inference, labels, vocabulary additions, URL routing, or row adoption.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import io
import json
from pathlib import Path
import types
import zipfile

ARCHIVE_SHA = "b1455573e0eac04844718ad827f67654e49142442274aef14b9f39aa6b968317"
ALIGNMENT_SHA = "6a2a632773a86430b726cb75e44468aec77245b4a73c018f7b5836e02fa0545a"
PR_HEAD = "983c6086a718190ede41ca14424114e1a6085f18"
RUN = ".lab-output/sku-align-20261010-v1"
ANSWERS = "answers-japanese-reranker-small-v2-cpu-reverse-all.jsonl"
SELECTION = "align-japanese-reranker-small-v2-cpu-reverse-all-v4axes/decisions.jsonl"
MAPPED = ("mapped", "exact_string")
UNRESOLVED = ("symmetric_unresolved", "one_sided", "extra_in_value")


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def jsonl(body: bytes) -> list[tuple[int, dict]]:
    return [(line, json.loads(value)) for line, value in enumerate(body.decode("utf-8").splitlines(), 1) if value.strip()]


def index(rows: list[dict], field: str) -> dict:
    result = {}
    for row in rows:
        key = row[field]
        if key in result:
            raise ValueError(f"duplicate {field}: {key}")
        result[key] = row
    return result


def derive_row(case: dict, row: dict, maps: dict, axes: dict, au_only_names: set[str]) -> dict:
    """Reproduce upstream condition states; leave unknown symmetric values unresolved."""
    row_axes = index(row["axes"], "axis_name")
    conditions = []
    for axis in case["rakuten"]["axes"]:
        key = (case["dossier_id"], axis["axis_key"], axis["value"])
        mapping, relation = maps[key], axes[key[:2]]
        condition = {"condition_id": f"claude-whole-axis:{axis['axis_index']}",
                     "raw_condition": copy.deepcopy(axis), "axis_label": axis["axis_label"],
                     "selected_value": axis["value"], "family_values": copy.deepcopy(axis["family_values"]),
                     "axis_key": axis["axis_key"], "value_span": copy.deepcopy(axis["value_span"]),
                     "axis": copy.deepcopy(relation), "mapping": copy.deepcopy(mapping)}
        if relation["kind"] == "one_sided":
            condition["status"] = "one_sided"
        else:
            au = row_axes.get(relation["au_axis"])
            option = mapping.get("option") if mapping["status"] in MAPPED + ("one_side_has_more",) else None
            if option is None or option["axis_name"] != relation["au_axis"] or au is None:
                condition["status"] = "symmetric_unresolved"
            else:
                condition.update({"au_value": au["value"], "au_value_span": copy.deepcopy(au["value_span"])})
                condition["status"] = ("contradiction" if au["value"] != option["value"] else
                                       "extra_in_value" if mapping["status"] == "one_side_has_more" else "aligned")
        conditions.append(condition)
    au_only = [{"axis_name": name, "value": row_axes[name]["value"],
                "value_span": copy.deepcopy(row_axes[name]["value_span"])}
               for name in sorted(au_only_names) if name in row_axes]
    statuses = {condition["status"] for condition in conditions}
    upstream_state = ("contradiction" if "contradiction" in statuses else
                      "symmetric_unresolved" if "symmetric_unresolved" in statuses else
                      "aligned" if statuses <= {"aligned"} and not au_only else "pending_description_check")
    return {"case_id": case["case_id"], "dossier_id": case["dossier_id"],
            "au_product_id": case["au_product_id"], "au_row_key": row["row_key"],
            "source_sku_key": case["rakuten"]["source_sku_key"],
            "upstream_row_state": upstream_state,
            "row_state": "contradiction" if "contradiction" in statuses else
                         "pending_description_check" if any(status in UNRESOLVED for status in statuses) or au_only else "aligned",
            "conditions": conditions, "aligned_conditions": [c for c in conditions if c["status"] == "aligned"],
            "au_only_varying_conditions": au_only, "selected_au_row": copy.deepcopy(row),
            "rakuten_selected": copy.deepcopy(case["rakuten"]),
            "reverse_check_pending": bool(au_only or "extra_in_value" in statuses),
            "automatic_adoption_allowed": False, "producer_reconstructed": True, "row_adopted": False}


def cards_from_row(row: dict, provenance: dict) -> list[dict]:
    """Every unresolved whole Rakuten axis survives on every noncontradictory row."""
    if row["row_state"] == "contradiction":
        return []
    cards = []
    for condition in row["conditions"]:
        if condition["status"] not in UNRESOLVED:
            continue
        axis = condition["raw_condition"]
        cards.append({"case_id": row["case_id"], "dossier_id": row["dossier_id"],
                      "au_row_key": row["au_row_key"], "au_product_id": row["au_product_id"],
                      "condition_id": condition["condition_id"], "axis_name": axis["axis_label"],
                      "selected_value": axis["value"], "option_values": copy.deepcopy(axis["family_values"]),
                      "raw_condition": copy.deepcopy(axis),
                      "source_refs": [copy.deepcopy(axis["axis_label_span"]), copy.deepcopy(axis["value_span"])],
                      "source_sku_key": row["source_sku_key"], "direction": "rakuten_to_au",
                      "upstream_condition_status": condition["status"], "upstream_mapping": copy.deepcopy(condition["mapping"]),
                      "producer": "recover-claude-unresolved-rows-v1", "producer_reconstructed": True,
                      "provenance": copy.deepcopy(provenance)})
    return cards


def reverse_conditions_from_row(row: dict, product: dict) -> list[dict]:
    """Keep both full sides of every extra pair, plus unpaired varying AU axes."""
    if row["row_state"] == "contradiction":
        return []
    selected = index(row["selected_au_row"]["axes"], "axis_name")
    requests = [(condition["axis"]["au_axis"], "extra_in_value", condition)
                for condition in row["conditions"] if condition["status"] == "extra_in_value"]
    requests += [(condition["axis_name"], "au_only_varying", None)
                 for condition in row["au_only_varying_conditions"]]
    reverse = []
    for axis_name, reason, forward in requests:
        raw_axis = selected[axis_name]
        options, seen = [], set()
        for candidate in product["au"]["rows"]:
            for axis in candidate["axes"]:
                if axis["axis_name"] == axis_name and axis["value"] not in seen:
                    options.append(copy.deepcopy(axis))
                    seen.add(axis["value"])
        reverse.append({"case_id": row["case_id"], "dossier_id": row["dossier_id"],
                        "au_row_key": row["au_row_key"], "au_product_id": row["au_product_id"],
                        "source_sku_key": row["source_sku_key"], "direction": "au_to_rakuten",
                        "condition_id": "claude-reverse:" + (forward["condition_id"] if forward else "au-only:" + axis_name),
                        "axis_name": axis_name, "selected_value": raw_axis["value"],
                        "option_values": [axis["value"] for axis in options], "option_records": options,
                        "raw_condition": copy.deepcopy(raw_axis),
                        "source_refs": [copy.deepcopy(raw_axis["axis_name_span"]), copy.deepcopy(raw_axis["value_span"])],
                        "forward_condition_id": forward["condition_id"] if forward else None,
                        "rakuten_whole_condition": copy.deepcopy(forward["raw_condition"]) if forward else None,
                        "rakuten_selected": copy.deepcopy(row["rakuten_selected"]),
                        "reason": reason, "reverse_check_pending": True, "automatic_adoption_allowed": False,
                        "producer": "recover-claude-unresolved-rows-v1", "producer_reconstructed": True,
                        "provenance": copy.deepcopy(row.get("provenance", {}))})
    return reverse


def recover_case(case: dict, product: dict, maps: dict, axes: dict) -> tuple[list[dict], int]:
    paired = {axes[(case["dossier_id"], axis["axis_key"])].get("au_axis") for axis in case["rakuten"]["axes"]} - {None}
    values = defaultdict(set)
    for row in product["au"]["rows"]:
        for axis in row["axes"]:
            values[axis["axis_name"]].add(axis["value"])
    au_only = {name for name, options in values.items() if name not in paired and len(options) > 1}
    rows = [derive_row(case, row, maps, axes, au_only) for row in product["au"]["rows"]]
    return [row for row in rows if row["row_state"] != "contradiction"], sum(row["row_state"] == "contradiction" for row in rows)


def validate_questions(questions: list[dict], answers: list[dict]) -> None:
    by_id = index(questions, "question_id")
    keys, seen = set(), set()
    for question in questions:
        key = (question["dossier_id"], question["axis_key"], question["value"])
        if key in keys:
            raise ValueError(f"duplicate mapping question: {key}")
        keys.add(key)
    for answer in answers:
        key = (answer["question_id"], answer["order"])
        if key in seen or answer["question_id"] not in by_id or answer["order"] not in ("forward", "reverse"):
            raise ValueError(f"duplicate/unknown answer: {key}")
        choice = answer["choice"]
        if isinstance(choice, int) and (isinstance(choice, bool) or not 0 <= choice < len(by_id[answer["question_id"]]["au_options"])):
            raise ValueError(f"answer option out of bounds: {key}")
        if not isinstance(choice, int) and choice not in ("none", "invalid", "not_mutual"):
            raise ValueError(f"unknown answer choice: {choice}")
        seen.add(key)
    if seen != {(question_id, order) for question_id in by_id for order in ("forward", "reverse")}:
        raise ValueError("missing forward/reverse answers")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def recover(archive: Path, alignment_code: Path, input_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    archive_body, code = archive.read_bytes(), alignment_code.read_bytes()
    if sha(archive_body) != ARCHIVE_SHA or sha(code) != ALIGNMENT_SHA:
        raise ValueError("archive/alignment code does not match the frozen PR 26 pins")
    paths = {name: input_dir / name for name in ("cases.jsonl", "products.jsonl")}
    bodies = {name: path.read_bytes() for name, path in paths.items()}
    rows_and_lines = {name: jsonl(body) for name, body in bodies.items()}
    cases = index([row for _, row in rows_and_lines["cases.jsonl"]], "case_id")
    products = index([row for _, row in rows_and_lines["products.jsonl"]], "dossier_id")
    case_lines = {row["case_id"]: line for line, row in rows_and_lines["cases.jsonl"]}
    product_lines = {row["dossier_id"]: line for line, row in rows_and_lines["products.jsonl"]}
    for case in cases.values():
        product = products[case["dossier_id"]]
        if str(case["au_product_id"]) != str(product["au"]["product_id"]):
            raise ValueError(f"fixed AU product mismatch: {case['case_id']}")
        index(case["rakuten"]["axes"], "axis_index")
        index(case["rakuten"]["axes"], "axis_key")
    for product in products.values():
        index(product["au"]["rows"], "row_key")
        for row in product["au"]["rows"]:
            index(row["axes"], "axis_name")
    module = types.ModuleType("frozen_claude_alignment")
    module.__file__ = str(alignment_code.resolve())
    exec(compile(code, module.__file__, "exec"), module.__dict__)
    members, handoffs, cards, reverse, skipped, selected_cases = {}, [], [], [], 0, Counter()
    checked_states, upstream_states = Counter(), Counter()
    with zipfile.ZipFile(io.BytesIO(archive_body)) as source:
        if len(set(source.namelist())) != len(source.namelist()):
            raise ValueError("duplicate archive members")
        for cohort in ("legacy", "family"):
            prefix = f"{RUN}/{cohort}/"
            names = [prefix + name for name in ("questions.jsonl", ANSWERS, SELECTION)]
            for name in names:
                members[name] = source.read(name)
            q_lines, a_lines, d_lines = [jsonl(members[name]) for name in names]
            questions, answers, decisions = [[row for _, row in records] for records in (q_lines, a_lines, d_lines)]
            validate_questions(questions, answers)
            index(decisions, "case_id")
            q_by_key = {(q["dossier_id"], q["axis_key"], q["value"]): (line, q) for line, q in q_lines}
            a_by_q = defaultdict(list)
            for line, answer in a_lines:
                a_by_q[answer["question_id"]].append(line)
            maps = module.mappings(questions, answers, exact_first=True, axis_checks=False)
            axes = module.axis_split(maps)
            for decision_line, decision in d_lines:
                case = cases[decision["case_id"]]
                if case["dossier_id"] != decision["dossier_id"] or case["cohort"] != cohort:
                    raise ValueError(f"case/cohort identity mismatch: {case['case_id']}")
                if decision["reason"] != "symmetric_condition_unresolved":
                    continue
                product = products[case["dossier_id"]]
                # Exact identity/options checks make input drift fail before reconstructing a row.
                expected_options = []
                seen_options = set()
                for au_row in product["au"]["rows"]:
                    for axis in au_row["axes"]:
                        key = (axis["axis_name"], axis["value"])
                        if key not in seen_options:
                            expected_options.append({"axis_name": key[0], "value": key[1]})
                            seen_options.add(key)
                references = {}
                for axis in case["rakuten"]["axes"]:
                    key = (case["dossier_id"], axis["axis_key"], axis["value"])
                    line, question = q_by_key[key]
                    if question["axis_label"] != axis["axis_label"] or question["family_values"] != axis["family_values"] or question["au_options"] != expected_options:
                        raise ValueError(f"question/input axis or option mismatch: {key}")
                    references[f"claude-whole-axis:{axis['axis_index']}"] = {
                        "question_member": names[0], "question_member_sha256": sha(members[names[0]]),
                        "question_line": line, "question_id": question["question_id"],
                        "answers_member": names[1], "answers_member_sha256": sha(members[names[1]]),
                        "answer_lines": a_by_q[question["question_id"]]}
                recovered, omitted = recover_case(case, product, maps, axes)
                states = Counter(row["upstream_row_state"] for row in recovered)
                if omitted:
                    states["contradiction"] = omitted
                if dict(states) != decision["row_states"]:
                    raise ValueError(f"reconstructed/upstream row states differ: {case['case_id']}: {dict(states)} != {decision['row_states']}")
                checked_states.update(states)
                upstream_states.update(decision["row_states"])
                skipped += omitted
                selected_cases[cohort] += 1
                for row in recovered:
                    row["provenance"] = {"archive_sha256": ARCHIVE_SHA, "alignment_code_sha256": ALIGNMENT_SHA,
                                         "pr_head": PR_HEAD, "decision_member": names[2], "decision_line": decision_line,
                                         "decision_member_sha256": sha(members[names[2]]),
                                         "upstream_reason": decision["reason"], "condition_mapping_refs": references,
                                         "case_input_ref": {"path": str(paths["cases.jsonl"].resolve()), "sha256": sha(bodies["cases.jsonl"]), "line": case_lines[case["case_id"]]},
                                         "product_input_ref": {"path": str(paths["products.jsonl"].resolve()), "sha256": sha(bodies["products.jsonl"]), "line": product_lines[case["dossier_id"]]}}
                    handoffs.append(row)
                    cards.extend(cards_from_row(row, row["provenance"]))
                    reverse.extend(reverse_conditions_from_row(row, product))
    if any(path.read_bytes() != bodies[name] for name, path in paths.items()) or alignment_code.read_bytes() != code or archive.read_bytes() != archive_body:
        raise RuntimeError("source changed during reconstruction")
    output_dir.mkdir(parents=True, exist_ok=False)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    (code_dir / "align_sku_conditions.py").write_bytes(code)
    (code_dir / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    for name, body in members.items():
        dest = output_dir / "upstream" / name.removeprefix(RUN + "/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
    handoff_path = output_dir / "handoff-rows.jsonl"
    write_jsonl(handoff_path, handoffs)
    handoff_sha = sha(handoff_path.read_bytes())
    row_lines = {(row["case_id"], row["au_row_key"]): line for line, row in enumerate(handoffs, 1)}
    for card in cards + reverse:
        card["handoff_row_ref"] = {"path": str(handoff_path.resolve()), "sha256": handoff_sha,
                                   "line": row_lines[(card["case_id"], card["au_row_key"])]}
    write_jsonl(output_dir / "cards.jsonl", cards)
    write_jsonl(output_dir / "reverse-conditions.jsonl", reverse)
    manifest = {"schema_version": "claude-unresolved-row-recovery-v1", "pr_head": PR_HEAD,
                "archive": {"path": str(archive.resolve()), "sha256": ARCHIVE_SHA},
                "alignment_code": {"path": str(alignment_code.resolve()), "sha256": ALIGNMENT_SHA},
                "input_sha256": {name: sha(body) for name, body in bodies.items()},
                "archive_members_sha256": {name: sha(body) for name, body in members.items()},
                "code_sha256": sha(Path(__file__).read_bytes()), "selected_cases_by_cohort": dict(selected_cases),
                "case_count": sum(selected_cases.values()), "handoff_case_count": len({row['case_id'] for row in handoffs}),
                "row_state_case_checks": sum(selected_cases.values()), "upstream_row_states_reproduced": True,
                "reconstructed_upstream_row_states": dict(checked_states), "selected_decision_row_states": dict(upstream_states),
                "handoff_row_count": len(handoffs), "card_count": len(cards), "skipped_contradiction_rows": skipped,
                "upstream_condition_statuses": dict(Counter(card["upstream_condition_status"] for card in cards)),
                "au_only_varying_condition_count": sum(len(row["au_only_varying_conditions"]) for row in handoffs),
                "reverse_condition_count": len(reverse), "reverse_check_pending_rows": sum(row["reverse_check_pending"] for row in handoffs),
                "reverse_check_implemented": False, "automatic_adoption_allowed": False,
                "all_noncontradictory_candidates_retained": True, "whole_axis_values_preserved": True,
                "producer_reconstructed": True, "labels_read": False, "inference_run": False, "rows_adopted": False,
                "output_sha256": {name: sha((output_dir / name).read_bytes()) for name in ("handoff-rows.jsonl", "cards.jsonl", "reverse-conditions.jsonl")}}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "alignment-code", "input-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(recover(args.archive, args.alignment_code, args.input_dir, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
