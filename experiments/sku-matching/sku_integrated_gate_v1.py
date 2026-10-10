"""Source-only adapter for the vendored Claude v9 fixed-pair gate."""
from __future__ import annotations

from functools import lru_cache
import importlib
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def load_gate():
    """Load the vendor through the namespace shared with the input builder."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    return importlib.import_module("claude_v9_gate.sku_gates")


def predict_case(case, facts, *, evaluator=None, store=None):
    """Run structural A and return binary accept/drop for a fixed pair.

    A accepts only when exactly one AU row is fully supported and every other
    row has an explicit conflict. Bounded evidence completions remain a later
    model layer; the complete raw A output is retained for that layer.
    """
    gates = load_gate()
    if store is not None:
        _bind_selector_option_spans(case, facts, store, gates.src)
    result = gates.run_method("A", case, facts, "full_spec_notice", evaluator)
    rows = result.get("rows", [])
    row_map = {r.get("row_key"): r for r in rows}
    selected = result.get("top_row_key")
    accepted = (result.get("decision") == "matched" and selected is not None
                and row_map.get(selected, {}).get("status") == "full"
                and all(r.get("status") == "conflict" for r in rows if r.get("row_key") != selected))
    proof_failure = None
    if accepted and store is not None:
        sources = getattr(gates, "src", None)
        chosen = row_map[selected]
        accepted_evidence = (sources is not None and _verified_acceptance(
            chosen, result.get("requirements", []), store, sources))
        competing_evidence = (sources is not None and _verified_competing_rows(
            rows, selected, store, sources))
        accepted = accepted_evidence and competing_evidence
        if not competing_evidence:
            proof_failure = "unquoted_competing_evidence"
        elif not accepted_evidence:
            proof_failure = "unquoted_or_unverified_evidence"
    decision = "accept" if accepted else "drop"
    reason = (proof_failure if proof_failure else
              "unquoted_or_unverified_evidence" if result.get("decision") == "matched" and not accepted and store is not None
              else result.get("reason") or ("one_full_row_and_all_other_rows_conflict" if accepted
                                            else "candidate_pool_not_fully_proven"))
    return {"decision": decision, "au_row_key": selected if accepted else None,
            "reason": reason, "requirements": result.get("requirements", []),
            "rows": rows, "notices": result.get("binary", {}).get("notices", []),
            "case_id": result.get("case_id"), "dossier_id": result.get("dossier_id"),
            "au_product_id": result.get("au_product_id"),
            "row_status_counts": result.get("row_status_counts", {}),
            "gate_decision": result.get("decision"),
            "binary": {"decision": "matched" if accepted else "unmatched",
                       "top_row_key": selected if accepted else None,
                       "reason_codes": [reason]},
            "gate_results": {"A": result},
            "excluded_non_identity_fields": list(gates.NON_IDENTITY_FIELDS),
            "store_supplied": store is not None}


def _verified_acceptance(row, requirements, store, sources):
    """Require every adopted proof to resolve to an intact source span."""
    def span_ok(span, quote=None):
        if not isinstance(span, dict) or not sources.verify_span(store, span):
            return False
        return quote is None or span.get("quote") == quote

    if not requirements or any(not span_ok(req.get("span"), req.get("quote")) for req in requirements):
        return False
    for result in row.get("atom_results", []):
        if result.get("status") != "support":
            continue
        evidence = result.get("evidence", [])
        if not evidence or any(not _evidence_ok(item, span_ok) for item in evidence):
            return False
    for result in row.get("au_only_atoms", []):
        if result.get("status") != "support":
            continue
        evidence = result.get("evidence", [])
        if not evidence or any(not _evidence_ok(item, span_ok) for item in evidence):
            return False
    return True


def _evidence_ok(evidence, span_ok):
    spans = evidence.get("spans")
    if spans:
        return all(span_ok(span) for span in spans)
    return span_ok(evidence.get("span"), evidence.get("quote"))


def _bind_selector_option_spans(case, facts, store, sources):
    """Attach literal spans for selector-list sibling values used as conflicts."""
    selected = case.get("rakuten_selected", {})
    raw_file = selected.get("raw_file")
    digest = selected.get("sha256")
    axes = selected.get("axes", [])
    if not raw_file or not digest or not axes:
        return
    try:
        store.raw(raw_file, digest)
    except (OSError, ValueError, AttributeError):
        # Missing or invalid raw input simply leaves sibling contradictions
        # without admissible source proof; the strict row guard then drops.
        return
    locator = (axes[0].get("value_span") or {}).get("locator", {})
    encoding = locator.get("encoding", "utf-8")
    for axis in axes:
        family = next((f for f in facts.rak_families if f.get("key") == axis.get("axis_key")), None)
        if family is None:
            continue
        spans = []
        for value in family["values"]:
            try:
                spans.append(sources.rakuten_selector_value_span(
                    store, raw_file, encoding, axis["axis_index"], axis["axis_key"], value))
            except (OSError, ValueError, AttributeError, KeyError):
                spans.append(None)
        family["value_spans"] = spans


def _verified_competing_rows(rows, selected, store, sources):
    """Every excluded row needs a verified, explicit contradiction source."""
    def verified_conflict(evidence):
        return any(item.get("relation") == "conflict" and _evidence_ok(item, span_ok)
                   for item in evidence or [])

    def span_ok(span, quote=None):
        if not isinstance(span, dict) or not sources.verify_span(store, span):
            return False
        return quote is None or span.get("quote") == quote

    for row in rows:
        if row.get("row_key") == selected:
            continue
        explicit = False
        for item in row.get("atom_results", []):
            status = item.get("status")
            if status == "conflict" and verified_conflict(item.get("evidence")):
                explicit = True
                break
            if status == "ambiguous" and verified_conflict(item.get("evidence")):
                explicit = True
                break
        if not explicit:
            explicit = any(item.get("status") == "conflict"
                           and verified_conflict(item.get("evidence"))
                           for item in row.get("au_only_atoms", []))
        if not explicit:
            return False
    return True
