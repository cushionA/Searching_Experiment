"""Binary, source-backed fixed-product SKU gate.

Language models may propose spans in a separate experiment. This reference
gate uses literal typed evidence, retains every AU row, and emits accept/drop.
Unknown is an internal relation, never a human-review workflow.
"""
from __future__ import annotations

from collections import Counter
import importlib
import os
from pathlib import Path
import re
import sys

import sku_nonllm_atoms_v1 as extension

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GATE_CODE = Path(os.environ.get("CLAUDE_CODE", "/workspace/Searching_Experiment-claude-share-20261010/experiments/sku-matching"))


def load_gate(code_path=DEFAULT_GATE_CODE, *, extensions=True):
    """Load the frozen baseline and install in-process extensions once."""
    sys.path.insert(0, str(code_path))
    gates = importlib.import_module("sku_gates")
    if not extensions:
        if getattr(gates, "_nonllm_extension_installed", False):
            raise RuntimeError("Baseline must run before installing extensions")
        return gates
    if getattr(gates, "_nonllm_extension_installed", False):
        return gates
    atom_mod = gates.atoms_mod
    base_atomize = atom_mod.atomize
    base_title = atom_mod.title_facts
    base_family = atom_mod.fact_family
    base_describe = atom_mod.describe_lines
    base_compare = gates.compare_atoms
    base_comparable = gates.comparable
    base_attributes = gates.attribute_facts
    base_body_dimensions = gates.Evaluator._body_dims
    base_derived_conflicts = gates.Evaluator.derived_conflicts
    # A bar is a concrete included hanger component, distinct from the use-case
    # word パンツ. Keep the ordinary component-presence parser and exact offsets.
    atom_mod.COMPONENTS.append(("バー", "hanger_bar"))
    atom_mod.COMPONENT_WORDS["バー"] = "hanger_bar"
    atom_mod._COMPONENT_RE = "|".join(re.escape(w) for w, n in atom_mod.COMPONENTS if n != "phrase")

    def atomize(*args, **kwargs):
        return extension.atomize(*args, **kwargs, base_atomize=base_atomize)

    def family(atom):
        return extension.fact_family(atom, base_fact_family=base_family)

    def title_facts(title, color_vocab, variant_tokens):
        result = extension.title_facts(title, color_vocab, variant_tokens,
                                       base_title_facts=base_title, base_atomize=base_atomize)
        # Bare opaque tokens in a title may be intended uses (ハンガー パンツ)
        # rather than declarations of a selected variant type.
        variants = result.get("variant")
        if variants:
            product_words = {word for word, component in atom_mod.COMPONENTS
                             if component in ("blanket", "electric_blanket", "duvet_cover", "mattress", "pad")}
            kept = []
            for atom in variants["atoms"]:
                value = atom["value"]
                start, end = atom["offset"]
                bounded_code = bool(re.fullmatch(r"(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]+", value)) and not (
                    re.search(r"[A-Za-z0-9]$", title[:start]) or re.match(r"[A-Za-z0-9]", title[end:]))
                named_product = any(value.endswith(word) and len(value) > len(word) for word in product_words)
                explicit_type = title[end:].startswith("タイプ")
                if bounded_code or named_product or explicit_type:
                    kept.append(atom)
            if kept:
                values = {}
                for atom in kept:
                    values.setdefault(atom["value"], []).append(atom)
                variants.update(atoms=kept, values=values, single_valued=len(values) == 1)
            else:
                result.pop("variant", None)
        # Correct bare component matches inside explicit negative phrases.
        # Conflicting explicit phrases stay multi-valued rather than overriding.
        for word, component in atom_mod.COMPONENTS:
            if component == "phrase":
                continue
            text = atom_mod.Text(title)
            matches = list(re.finditer(re.escape(word) + r"\s*(なし|無し|ありません|付属しません|付き|付|あり|有り)", text.norm))
            if not matches:
                continue
            key = "component_presence:" + component
            data = result.setdefault(key, {"atoms": [], "values": {}, "single_valued": True})
            new_atoms = [text.atom(m.start(), m.end(), type="component_presence", component=component,
                                   value=m.group(1) not in ("なし", "無し", "ありません", "付属しません")) for m in matches]
            data["atoms"] = [a for a in data["atoms"] if not any(
                n["offset"][0] <= a.get("offset", [-1, -1])[0] and a.get("offset", [-1, -1])[1] <= n["offset"][1]
                for n in new_atoms)] + new_atoms
            values = {}
            for atom in data["atoms"]:
                values.setdefault(atom["value"], []).append(atom)
            data.update(values=values, single_valued=len(values) == 1)
        return result

    def describe(lines, vocab, tokens):
        facts = base_describe(lines, vocab, tokens)
        for fact in facts:
            atom = fact.get("atom", {})
            if atom.get("type") != "dimension" or atom.get("role") != "labeled" or not atom.get("labels"):
                continue
            # Adjustable height 10/15cm is two allowed settings. The baseline
            # stopped before '/15cm' and invented a fixed height of 10cm.
            raw = fact.get("line", "")
            start, end = atom.get("offset", (0, 0))
            tail = re.match(r"((?:\s*[/／]\s*\d+(?:\.\d+)?)+)\s*(mm|cm|m)(?![A-Za-z])", raw[end:])
            if not tail:
                continue
            scale = {"mm": 0.1, "cm": 1., "m": 100.}[tail.group(2)]
            before = raw[start:end]
            if not re.search(r"(?:mm|cm|m)(?![A-Za-z])", before):
                atom["value"] = [v * scale for v in atom["value"]]
            alternatives = [atom["value"][-1]] + [float(v) * scale for v in re.findall(r"\d+(?:\.\d+)?", tail.group(1))]
            atom["alternatives"] = {atom["labels"][-1]: sorted(set(alternatives))}
            atom["offset"] = [start, end + tail.end()]
            atom["quote"] = raw[start:end + tail.end()]
        # Explicit current-page declarations were incorrectly excluded as links
        # in earlier inputs. Only this narrow declaration supplies extra facts.
        for index, line in enumerate(lines):
            if line.get("scope_tag") in ("search_keywords", "series_or_sibling_context", "detail_comment"):
                continue
            raw = line["text"]
            # Explicit, included hanger-bar prose is a feature declaration.
            # The bare word パンツ remains a use case and supplies no type fact.
            for atom in atomize(raw, color_vocab=vocab)["atoms"]:
                if atom.get("type") != "component_presence" or atom.get("component") != "hanger_bar":
                    continue
                facts.append({"line_index": index, "line": raw, "span": line.get("span"),
                              "scope_tag": line.get("scope_tag"), "kind": "page_declaration",
                              "scope": "product_page", "atom": atom, "section": None, "condition": None})
            if not re.fullmatch(r"こちらのページは.+です[。]?", atom_mod.compact(raw)):
                continue
            parsed = atomize(raw, color_vocab=vocab)
            for atom in parsed["atoms"]:
                if atom["type"] not in ("quantity", "tier_count", "piece_total"):
                    continue
                if any(f.get("line_index") == index and f.get("atom", {}).get("type") == atom["type"]
                       and f.get("atom", {}).get("value") == atom["value"] for f in facts):
                    continue
                facts.append({"line_index": index, "line": raw, "span": line.get("span"),
                              "scope_tag": line.get("scope_tag"), "kind": "page_declaration",
                              "scope": "product_page", "atom": atom, "section": None, "condition": None})
        return facts

    def comparable(req, cand):
        if req["type"] == cand["type"] == "quantity":
            return family(req) == family(cand)
        return base_comparable(req, cand)

    def compare(req, cand, contrast):
        if req["type"] == cand["type"] == "quantity":
            if not comparable(req, cand):
                return "unknown"
            return "support" if req["value"] == cand["value"] else "conflict"
        if req["type"] == cand["type"] == "dimension" and req.get("role") == cand.get("role") == "labeled":
            needed = {x: v for x, v in zip(req.get("labels", []), req["value"]) if x != "unlabeled"}
            actual = {x: v for x, v in zip(cand.get("labels", []), cand["value"]) if x != "unlabeled"}
            common = set(needed) & set(actual)
            choices = cand.get("alternatives", {})
            if any(needed[x] not in choices[x] if x in choices else needed[x] != actual[x] for x in common):
                return "conflict"
            # A partial dimension match does not prove the complete requirement.
            return "support" if needed and set(needed) <= set(actual) else "unknown"
        return base_compare(req, cand, contrast)

    def body_dimensions(facts):
        result = base_body_dimensions(facts)
        for fact in facts:
            for label in fact.get("atom", {}).get("alternatives", {}):
                if label in result:
                    result[label] = (None, fact)
        return result

    def derived_conflicts(self, au_facts, rak_facts):
        result = base_derived_conflicts(self, au_facts, rak_facts)
        for left, right in ((au_facts, rak_facts), (rak_facts, au_facts)):
            actual = body_dimensions(right)
            for fact in left:
                if not fact.get("single_valued") or not self.usable(fact):
                    continue
                for label, values in fact.get("atom", {}).get("alternatives", {}).items():
                    value, other = actual.get(label, (None, None))
                    if value is not None and value not in values:
                        a, r = (fact, other) if left is au_facts else (other, fact)
                        result.append(self._conflict("body_dimension:" + label, a, r))
        return result

    def attributes(attrs):
        result = base_attributes(attrs)
        # These values belong to the selected variant, not the page's series.
        for fact in result:
            fact["derived"] = False
        for attr in attrs or []:
            if attr.get("title") not in ("素材（生地・毛糸）", "素材(生地・毛糸)", "生地"):
                continue
            value = attr.get("value", "")
            pieces = re.split(r"[・、,/\s]+", value)
            for word, canonical in atom_mod.FABRIC.items():
                if word not in pieces:
                    continue
                start = value.find(word)
                span = gates.src.sub_span(attr["value_span"], start, len(word)) if attr.get("value_span") else None
                atom = {"type": "fabric", "value": canonical, "quote": word, "offset": [start, start + len(word)]}
                result.append({"atom": atom, "source": "rakuten_variant_attributes", "scope": "selected_sku_attributes",
                               "single_valued": True, "family": "fabric", "span": span, "derived": False})
        return result

    for module in (atom_mod, gates):
        module.atomize = atomize
        module.fact_family = family
        module.title_facts = title_facts
        module.describe_lines = describe
    gates.comparable = comparable
    gates.compare_atoms = compare
    gates.attribute_facts = attributes
    gates.Evaluator._body_dims = staticmethod(body_dimensions)
    gates.Evaluator.derived_conflicts = derived_conflicts
    gates._nonllm_extension_installed = True
    return gates


def restore_au_lines(context, store, gates):
    """Recover section headings and declarations from the captured HTML leaf.

    Only extraItemComment is used. Search-keyword itemComment, policy detailComment,
    and anchor text never become current-item specifications. Original contexts
    are not modified on disk.
    """
    from prepare_novel_real_inputs_v2 import SourceHTMLParser
    rel = context["au_product"]["raw_file"]
    store.raw(rel, context["au_product"]["sha256"])
    path = "$.itemInfo.extraItemComment"
    raw = gates.src.resolve_json_path(store.json(rel), path)
    if not isinstance(raw, str):
        raise ValueError("AU description must be a captured string leaf")
    lines = []
    for node in SourceHTMLParser(raw).nodes:
        span = gates.src.make_span(store, rel, {"kind": "json_leaf", "json_path": path},
                                   node["html_char_start"], node["html_char_end"], node["quote"])
        lines.append({"text": node["quote"], "span": span, "scope_tag": "product_page", "source_field": path})
    return lines


def verified_evidence(evidence, store, gates):
    spans = []
    for ev in evidence:
        if ev.get("span"):
            spans.append(ev["span"])
        spans.extend(ev.get("spans", []))
    return bool(spans) and all(verify_span_cached(s, store, gates) for s in spans)


def verify_span_cached(span, store, gates):
    cache = getattr(store, "_nonllm_span_cache", None)
    if cache is None:
        cache = store._nonllm_span_cache = {}
    key = gates.canonical_json(span) if hasattr(gates, "canonical_json") else repr(span)
    if key not in cache:
        cache[key] = gates.src.verify_span(store, span)
    return cache[key]


def attach_arithmetic_proof(result, facts):
    """Expose the existing curtain-total sum's literal premises to the host."""
    for evidence in result.get("evidence", []):
        if evidence.get("span") or evidence.get("spans"):
            continue
        matches = [f for f in facts if f.get("scope") == evidence.get("scope")
                   and f.get("source") == evidence.get("source")
                   and f.get("atom", {}).get("value") == evidence.get("value")
                   and f.get("atom", {}).get("derivation") == "sum_of_curtain_panels_in_closed_contents"]
        for fact in matches:
            evidence.update(spans=fact.get("line_spans", []), derivation="sum_of_curtain_panels_in_closed_contents")
    return result


def attach_selector_family_proof(result, atom, row, case, facts, rak_facts, evaluator, store, gates):
    """Cite a different complete option on the selected selector's own axis.

    The frozen evaluator already recognizes this contrast, but omitted its
    source span. Only literal selected/sibling values that share the same color
    and explicitly distinguish this qualifier can supply the missing proof.
    A mention elsewhere on the product page is not a selector option.
    """
    if result.get("note") != "rakuten_sibling_value_carries_qualifier" or result.get("status") != "conflict":
        return result
    if not atom.get("span") or not verify_span_cached(atom["span"], store, gates):
        return result
    colors = {a["value"] for a in row["atoms"] if a["type"] == "color"}
    if len(colors) != 1:
        return result
    selected = case["rakuten_selected"]
    for axis in selected["axes"]:
        selected_span = axis.get("value_span")
        if (not selected_span or selected_span.get("quote") != axis["value"]
                or not verify_span_cached(selected_span, store, gates)):
            continue
        parsed = gates.atomize(axis["value"], axis["axis_label"], facts.vocab, tuple(axis["family_values"]))
        selected_colors = {a["value"] for a in parsed["atoms"] if a["type"] == "color"}
        if selected_colors != colors or any(
                a["type"] == atom["type"] and a["value"] == atom["value"] for a in parsed["atoms"]):
            continue
        for ev in result.get("evidence", []):
            sibling = ev.get("quote")
            if ev.get("source") != "rakuten_selector_family" or sibling == axis["value"] or sibling not in axis["family_values"]:
                continue
            other = gates.atomize(sibling, axis["axis_label"], facts.vocab, tuple(axis["family_values"]))
            if not ({a["value"] for a in other["atoms"] if a["type"] == "color"} == selected_colors
                    and any(a["type"] == atom["type"] and a["value"] == atom["value"] for a in other["atoms"])):
                continue
            span = gates.src.rakuten_selector_value_span(
                store, selected["raw_file"], selected_span["locator"].get("encoding", "utf-8"),
                axis["axis_index"], axis["axis_key"], sibling)
            if not span or not verify_span_cached(span, store, gates):
                continue
            ev.update(span=span, spans=[selected_span, atom["span"]],
                      derivation="distinct_complete_options_on_selected_axis",
                      scope="selected_selector_family", selected_value=axis["value"], axis_key=axis["axis_key"])
            # Direct selected-variant evidence that contradicts the option
            # distinction remains a disagreement, rather than being discarded.
            supports = [f for f in rak_facts if f.get("source") == "rakuten_variant_attributes"
                        and f.get("single_valued") and f.get("span")
                        and gates.comparable(atom, f["atom"])
                        and gates.compare_atoms(atom, f["atom"], evaluator.product_contrast) == "support"
                        and verify_span_cached(f["span"], store, gates)]
            if supports:
                return {"status": "ambiguous", "note": "selected_attribute_disagrees_with_option_contrast",
                        "evidence": [ev] + [{"source": f["source"], "relation": "support", "span": f["span"],
                                              "quote": f["atom"].get("quote"), "scope": f["scope"]} for f in supports]}
            return result
    return result


def add_declared_lace_absence(row, facts, gates):
    """Derive lace=0 from an explicit total and drape count, never word absence.

    This uses the curtain-specific piece_total definition already present in
    the source task: total=drape+lace. Both quantities must be literal, scoped,
    single-valued declarations for this row. Missing/contradictory counts do
    not yield an absence fact.
    """
    totals = [a for a in row["atoms"] if a["type"] == "piece_total" and a.get("span")]
    drapes = [f for f in facts if f.get("single_valued") and f.get("span")
              and f.get("atom", {}).get("type") == "component_count"
              and f["atom"].get("component") == "drape"]
    known_lace = [f for f in facts if f.get("atom", {}).get("component") == "lace"]
    if len({a["value"] for a in totals}) != 1 or len({f["atom"]["value"] for f in drapes}) != 1 or known_lace:
        return facts
    if not totals or not drapes or totals[0]["value"] != drapes[0]["atom"]["value"]:
        return facts
    total, drape = totals[0], drapes[0]
    atom = {"type": "component_presence", "component": "lace", "value": False,
            "quote": total["quote"] + " / " + drape["atom"]["quote"],
            "derivation": "declared_curtain_total_minus_drape_equals_zero"}
    return facts + [{"atom": atom, "source": "au_declared_package", "scope": "declared_package_arithmetic",
                     "single_valued": True, "family": "component_presence:lace", "span": None,
                     "line_spans": [total["span"], drape["span"]], "derived": False}]


def strict_case(case, facts, gates, store):
    """One selected Rakuten SKU -> accept one fixed AU row or drop the record."""
    evaluator = gates.make_evaluator("B", facts, "full_no_closed_list")
    if getattr(gates, "_nonllm_extension_installed", False):
        original_usable = evaluator.usable
        evaluator.usable = lambda f: (bool(f.get("line_spans")) and all(f["line_spans"])) if (
            f.get("atom", {}).get("derivation") == "declared_curtain_total_minus_drape_equals_zero") else original_usable(f)
    requirements = gates.selected_atoms(case, facts)
    rak_facts = evaluator.rak_facts_for(requirements, case["rakuten_selected"].get("variant_attributes"))
    valid_requirements = bool(requirements) and all(r.get("span") and verify_span_cached(r["span"], store, gates)
                                                   for r in requirements)
    partial = any(r.get("decomposition") != "complete" for r in requirements)
    rows = []
    for row in facts.rows:
        au_facts = evaluator.au_facts_for(row)
        if getattr(gates, "_nonllm_extension_installed", False):
            au_facts = add_declared_lace_absence(row, au_facts, gates)
        results = []
        for req in requirements:
            # No cross-SKU requirement cache: numeric units and selection scope
            # are part of identity, including conditions on the same AU row.
            result = evaluator.evaluate(req, row, au_facts)
            result = attach_arithmetic_proof(result, au_facts)
            for ev in result.get("evidence", []):
                if ev.get("source") == "au_declared_package":
                    proof = [f for f in au_facts if f.get("source") == "au_declared_package"]
                    if proof:
                        ev.update(spans=proof[0]["line_spans"], derivation="declared_curtain_total_minus_drape_equals_zero")
            if result["status"] in ("support", "conflict") and not verified_evidence(result["evidence"], store, gates):
                result = {"status": "unknown", "evidence": result["evidence"], "note": "unverified_evidence"}
            results.append({"requirement_id": req["requirement_id"], "condition": req, **result})
        reverse = []
        for atom in row["atoms"]:
            # A forward partial comparison does not waive the reverse condition.
            if any(gates.comparable(req, atom) and gates.compare_atoms(atom, req, evaluator.product_contrast) == "support"
                   for req in requirements):
                continue
            result = evaluator.evaluate_au_only(atom, requirements, rak_facts, row)
            result = attach_arithmetic_proof(result, rak_facts)
            if getattr(gates, "_nonllm_extension_installed", False):
                result = attach_selector_family_proof(result, atom, row, case, facts, rak_facts, evaluator, store, gates)
            if result["status"] in ("support", "conflict") and not verified_evidence(result["evidence"], store, gates):
                result = {"status": "unknown", "evidence": result["evidence"], "note": "unverified_reverse_evidence"}
            reverse.append({"condition": atom, **result})
        derived = evaluator.derived_conflicts(au_facts, rak_facts)
        explicit_conflict = any(r["status"] == "conflict" for r in results + reverse)
        supported = (valid_requirements and not partial and bool(results)
                     and all(r["status"] == "support" for r in results + reverse)
                     and not derived)
        status = "conflict" if explicit_conflict else "supported" if supported else "unproven"
        # One contradiction suffices to exclude a full-pool row. Retain its
        # verified proof without duplicating every requirement source per row.
        if status == "conflict":
            results = [r for r in results if r["status"] == "conflict"][:1]
        for result in results:
            result["condition"] = {k: v for k, v in result["condition"].items()
                                   if k in ("type", "component", "value", "quote", "unit", "quantity_role", "labels", "role")}
        rows.append({"row_key": row["row_key"], "status": status, "conditions": results,
                     "au_only_conditions": reverse, "source_disagreements": derived})
    supported = [r["row_key"] for r in rows if r["status"] == "supported"]
    excluded = sum(r["status"] == "conflict" for r in rows)
    accept = len(supported) == 1 and excluded == len(rows) - 1
    reason = ("unique_fully_supported_row" if accept else
              "no_au_rows" if not rows else
              "selected_requirement_unverified" if not valid_requirements else
              "every_row_has_explicit_conflict" if excluded == len(rows) else
              "multiple_supported_rows" if len(supported) > 1 else
              "remaining_condition_not_proven")
    return {"schema_version": "sku-nonllm-binary-v1", "case_id": case["case_id"], "dossier_id": case["dossier_id"],
            "decision": "accept" if accept else "drop", "au_row_key": supported[0] if accept else None,
            "reason": reason, "row_status_counts": dict(Counter(r["status"] for r in rows)),
            "au_product_id": case["au_product_id"], "rakuten_variant_id": case["rakuten_selected"]["variant_id"],
            "rakuten_source_sku_key": case["rakuten_selected"]["source_sku_key"],
            "full_au_row_keys": [r["row_key"] for r in rows], "requirements": requirements, "rows": rows}
