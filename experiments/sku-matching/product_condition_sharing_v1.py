"""Pure, explicitly authorized AU-product condition grouping and fan-out.

Only caller-listed forward requests may share a product-global condition.
This module never infers that an axis is asymmetric or product-global. It
reads no labels, runs no model, and leaves every other request untouched.
Raw alternatives and original SKU/row bindings survive classification.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from urllib.parse import urlsplit

VERSION = "product-condition-sharing-v1"
UNKNOWN = "記載から特定できない（診断用）"
QUESTION = (
    "現在のAU商品について、「{axis_name}」に該当する選択肢はどれですか。"
    "記載から特定できない場合は、その選択肢を選んでください。"
)
DESCRIPTION = "この商品の「{axis_name}」は「{value}」である。"
UNKNOWN_DESCRIPTION = (
    "この商品の「{axis_name}」に該当する値は、現在の商品名と商品説明から特定できない。"
)
SEMANTIC_FIELDS = (
    "direction", "axis_name", "selected_value", "option_values",
    "target_document_id", "target_document_sha256", "target_selected_axes",
    "target_selected_axes_text",
)
OLD_EXACT_FIELDS = (
    "direction", "axis_name", "option_values", "target_document_id",
    "target_document_sha256", "target_selected_axes_text",
)


def digest(value):
    """Hash complete, ordered JSON semantics without locale normalization."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _nonempty_text(value, field):
    if not isinstance(value, str) or not value:
        raise ValueError("nonempty string required: " + field)
    return value


def _indexed(values, field):
    if isinstance(values, dict):
        result = dict(values)
        if any(key != value.get(field) for key, value in result.items()):
            raise ValueError("mapping identity differs: " + field)
    else:
        result = {}
        for value in values:
            identity = _nonempty_text(value.get(field), field)
            if identity in result:
                raise ValueError("duplicate " + field)
            result[identity] = value
    return result


def canonical_au_url(document):
    """Bind an AU page to its numeric product ID, including URL-null prep."""
    if document.get("side") != "au" or document.get("fixed_page") is not True:
        raise ValueError("eligible forward target must be a fixed AU document")
    product_id = _nonempty_text(document.get("product_id"), "AU product_id")
    if not product_id.isascii() or not product_id.isdecimal():
        raise ValueError("AU product_id must be ASCII digits")
    canonical = "https://wowma.jp/item/" + product_id
    original = document.get("url")
    if original is not None:
        _nonempty_text(original, "AU URL")
        parsed = urlsplit(original)
        if (parsed.scheme not in {"http", "https"} or parsed.netloc != "wowma.jp" or
                parsed.path.rstrip("/") != "/item/" + product_id):
            raise ValueError("fixed AU URL and product_id differ")
    return canonical


def _validate_document(document):
    _nonempty_text(document.get("document_id"), "document_id")
    identity = digest({k: v for k, v in document.items() if k != "document_sha256"})
    if document.get("document_sha256") != identity:
        raise ValueError("complete document semantic SHA differs")
    for field in ("title", "description"):
        if not isinstance(document.get(field), dict) or not isinstance(document[field].get("text"), str):
            raise ValueError("complete literal document text required: " + field)


def _validate_request(request, documents):
    semantic = {field: request[field] for field in SEMANTIC_FIELDS}
    identity = digest(semantic)
    if (request.get("request_sha256") != identity or
            request.get("request_id") != "generic-description:" + identity):
        raise ValueError("original request identity differs")
    if request["direction"] not in {"rakuten_to_au", "au_to_rakuten"}:
        raise ValueError("unsupported request direction")
    _nonempty_text(request["axis_name"], "raw axis_name")
    options = request["option_values"]
    if (not isinstance(options, list) or not options or
            any(not isinstance(v, str) or not v for v in options) or
            len(set(options)) != len(options) or request["selected_value"] not in options or
            UNKNOWN in options):
        raise ValueError("unique whole alternatives must contain wanted value without unknown collision")
    axes = request["target_selected_axes"]
    if (not isinstance(axes, list) or any(not isinstance(a, dict) or
            not isinstance(a.get("axis_name"), str) or not isinstance(a.get("value"), str) for a in axes)):
        raise ValueError("original selected AU/SKU axes invalid")
    rendered = "\n".join(a["axis_name"] + "：" + a["value"] for a in axes)
    if rendered != request["target_selected_axes_text"]:
        raise ValueError("original selected axes rendering differs")
    doc = documents[request["target_document_id"]]
    if request["target_document_sha256"] != doc["document_sha256"]:
        raise ValueError("original request document SHA differs")


def _validate_bindings(requests, bindings, documents):
    if set(bindings) != set(requests):
        raise ValueError("complete original bindings must cover exactly every request")
    seen = set()
    for identity, request in requests.items():
        values = bindings[identity]
        if not isinstance(values, list) or not values:
            raise ValueError("original request needs all source task bindings")
        for task in values:
            task_id = _nonempty_text(task.get("task_id"), "task_id")
            if task_id in seen:
                raise ValueError("duplicate original task binding")
            seen.add(task_id)
            raw = task["raw_condition"]
            raw_axis = raw.get("axis_label", raw.get("axis_name"))
            if (task.get("request_id") != identity or task.get("direction") != request["direction"] or
                    raw.get("value") != request["selected_value"] or raw_axis != request["axis_name"] or
                    task.get("option_values") != request["option_values"]):
                raise ValueError("complete original raw condition binding differs")
            if request["direction"] == "rakuten_to_au":
                product_id = documents[request["target_document_id"]].get("product_id")
                if task.get("au_product_id") is not None and task["au_product_id"] != product_id:
                    raise ValueError("source task fixed AU product differs")


def _validate_contract(contract, requests):
    if (not isinstance(contract, dict) or contract.get("basis") != "user_asserted" or
            contract.get("sold_out_rows_excluded_upstream") is not True):
        raise ValueError("explicit user-asserted product-global invariance contract required")
    _nonempty_text(contract.get("statement"), "user invariance statement")
    eligible = contract.get("eligible_forward_request_ids")
    if (not isinstance(eligible, list) or any(not isinstance(v, str) for v in eligible) or
            len(set(eligible)) != len(eligible) or not set(eligible) <= set(requests)):
        raise ValueError("eligible IDs must be unique existing original request IDs")
    if any(requests[v]["direction"] != "rakuten_to_au" for v in eligible):
        raise ValueError("reverse requests cannot be product-global forward groups")
    return set(eligible)


def build_groups(requests, documents, bindings, *, model_identity, content_version, invariance_contract):
    """Build product-only classification inputs and immutable original mappings.

    ``invariance_contract`` must contain ``basis='user_asserted'``, a literal
    nonempty ``statement``, ``sold_out_rows_excluded_upstream=True``, and an
    explicit list ``eligible_forward_request_ids``. Eligibility is never
    guessed from axis names, categories, or task origin. All unlisted requests
    are caller-baseline passthrough, including every reverse request.

    ``model_identity`` records a pinned ``model_id`` and ``revision``; ensemble
    component pins, code hashes and precision may be included and are hashed.
    ``content_version`` pins caller rendering/evidence policy. Callers may use
    group.model_request question/choices with their own scoped block renderer;
    that renderer and ensemble policy must be pinned in content_version or
    model_identity and must exclude wanted values and selected-row axes.
    """
    _nonempty_text(content_version, "content_version")
    if not isinstance(model_identity, dict):
        raise ValueError("pinned model identity must be an object")
    _nonempty_text(model_identity.get("model_id"), "model_id")
    _nonempty_text(model_identity.get("revision"), "model revision")
    request_map = _indexed(requests, "request_id")
    document_map = _indexed(documents, "document_id")
    for document in document_map.values():
        _validate_document(document)
    for request in request_map.values():
        _validate_request(request, document_map)
    _validate_bindings(request_map, bindings, document_map)
    eligible = _validate_contract(invariance_contract, request_map)
    groups, request_to_group, old_inputs = {}, {}, {}
    url_titles = {}
    for identity, request in request_map.items():
        if identity not in eligible:
            continue
        document = document_map[request["target_document_id"]]
        url = canonical_au_url(document)
        title, description = document["title"]["text"], document["description"]["text"]
        if not title:
            raise ValueError("product-global context requires its original AU title")
        previous_title = url_titles.setdefault(url, title)
        if previous_title != title:
            raise ValueError("same fixed AU URL has conflicting titles in this preparation")
        semantic = {
            "contract_version": VERSION, "content_version": content_version,
            "model_identity": copy.deepcopy(model_identity), "canonical_au_url": url,
            "target_document_id": document["document_id"],
            "target_document_sha256": document["document_sha256"],
            "axis_name": request["axis_name"], "option_values": copy.deepcopy(request["option_values"]),
        }
        group_sha = digest(semantic)
        group_id = "product-condition:" + group_sha
        if group_id not in groups:
            model_request = {
                "id": group_id, "question": QUESTION.format(axis_name=request["axis_name"]),
                "state": "現在のAU商品URL：" + url + "\n現在の商品名：" + title +
                         "\nこの商品ページの説明全文：\n" + description,
                "choices": [{"label": value, "description": DESCRIPTION.format(
                    axis_name=request["axis_name"], value=value)} for value in request["option_values"]] +
                    [{"label": UNKNOWN, "description": UNKNOWN_DESCRIPTION.format(axis_name=request["axis_name"])}],
            }
            groups[group_id] = {
                **semantic, "group_id": group_id, "group_sha256": group_sha,
                "model_cache_key": digest({"identity": semantic, "model_input": model_request}),
                "title": title, "description": description, "request_ids": [],
                "model_request": model_request,
            }
        groups[group_id]["request_ids"].append(identity)
        request_to_group[identity] = group_id
        old_key = digest({field: request[field] for field in OLD_EXACT_FIELDS})
        old_inputs.setdefault(old_key, len(request["option_values"]) + 1)
    group_values = list(groups.values())
    original_pairs = sum(len(request_map[v]["option_values"]) + 1 for v in eligible)
    old_pairs = sum(old_inputs.values())
    shared_pairs = sum(len(group["option_values"]) + 1 for group in group_values)
    plan = {
        "schema_version": VERSION, "labels_read": False, "labels_in_model_inputs": False,
        "production_adoption_allowed": False, "scope_applicability_proved": False,
        "product_global_invariance_basis": "user_asserted",
        "invariance_contract": copy.deepcopy(invariance_contract),
        "model_identity": copy.deepcopy(model_identity), "content_version": content_version,
        "groups": group_values, "model_requests": [copy.deepcopy(g["model_request"]) for g in group_values],
        "request_to_group": request_to_group,
        "passthrough_request_ids": [v for v in request_map if v not in eligible],
        "original_requests": copy.deepcopy(request_map), "bindings": copy.deepcopy(bindings),
        "documents": copy.deepcopy(document_map),
        "savings": {
            "total_original_request_count": len(request_map), "eligible_original_request_count": len(eligible),
            "passthrough_request_count": len(request_map) - len(eligible),
            "eligible_old_exact_input_count": len(old_inputs), "shared_group_count": len(groups),
            "old_exact_dedup_saved_request_count": len(eligible) - len(old_inputs),
            "additional_saved_model_input_count": len(old_inputs) - len(groups),
            "eligible_original_candidate_pair_count": original_pairs,
            "eligible_old_exact_candidate_pair_count": old_pairs,
            "shared_candidate_pair_count": shared_pairs,
            "additional_saved_candidate_pair_count": old_pairs - shared_pairs,
        },
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def _literal_product_refs(refs, group, documents):
    if not isinstance(refs, list):
        raise ValueError("group quote_refs must be a list")
    document = documents[group["target_document_id"]]
    for quote in refs:
        if not isinstance(quote, dict) or quote.get("document_id") != document["document_id"]:
            raise ValueError("group quote binds another document")
        field = quote.get("field")
        if field not in {"title", "description"}:
            raise ValueError("product-global quote must be title/description, never selected AU row")
        if quote.get("document_sha256", document["document_sha256"]) != document["document_sha256"]:
            raise ValueError("group quote document version differs")
        text = document[field]["text"]
        start, end = quote.get("start"), quote.get("end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or
                not isinstance(quote.get("quote"), str) or quote["quote"] != text[start:end]):
            raise ValueError("group quote is not a literal product document span")
    return copy.deepcopy(refs)


def _group_outcome(group, result, documents):
    if not isinstance(result, dict) or result.get("group_sha256") != group["group_sha256"]:
        raise ValueError("group decision version/hash binding differs")
    status = result.get("status")
    if status not in {"ok", "unknown", "error"}:
        raise ValueError("group decision status must be ok/unknown/error")
    refs = _literal_product_refs(result.get("quote_refs", []), group, documents)
    winner = result.get("winning_value")
    options = group["option_values"]
    if winner is not None and winner not in options and winner != UNKNOWN:
        raise ValueError("group winner is not a whole original alternative")
    reason = result.get("reason")
    tie = result.get("tie", False)
    if type(tie) is not bool:
        raise ValueError("group tie must be boolean")
    scores = result.get("scores")
    if scores is not None:
        if (not isinstance(scores, dict) or set(scores) != set(options) | {UNKNOWN} or
                any(type(value) not in {int, float} or not math.isfinite(value) for value in scores.values())):
            raise ValueError("complete finite group scores required, including unknown control")
        maximum = max(scores.values())
        leaders = [value for value in options + [UNKNOWN] if scores[value] == maximum]
        score_winner = leaders[0] if len(leaders) == 1 else None
        if winner is not None and winner != score_winner:
            raise ValueError("provided winner differs from fixed score argmax")
        winner, tie = score_winner, len(leaders) != 1
    if status != "ok":
        return None, [], reason or "group_" + status, tie
    if tie:
        return None, [], reason or "group_score_tie", True
    if winner in {None, UNKNOWN}:
        return None, [], reason or "group_unknown_control", False
    if not refs:
        return None, [], reason or "group_missing_product_quote", False
    return winner, refs, reason or "product_global_option_selected", False


def fan_out(plan, group_results, baseline_predictions):
    """Return every original request in order; all noneligible records unchanged.

    ``group_results[group_id]`` binds ``group_sha256`` and ``status``; it may
    provide an original-option ``winning_value`` or complete finite ``scores``
    for all options plus UNKNOWN. Scores use one unique argmax, no threshold.
    ``quote_refs`` must be literal title/description references. An alternative
    winner produces unknown/drop rather than inferred contradiction. Explicit
    absence is reusable when the winning original option itself means absence;
    this helper never interprets the text or invents negative evidence.
    """
    if (plan.get("schema_version") != VERSION or
            plan.get("plan_sha256") != digest({k: v for k, v in plan.items() if k != "plan_sha256"})):
        raise ValueError("immutable sharing plan was modified")
    groups = {g["group_id"]: g for g in plan["groups"]}
    if set(group_results) != set(groups):
        raise ValueError("group results must cover exactly every prepared group")
    baseline = _indexed(baseline_predictions, "request_id")
    if not set(plan["passthrough_request_ids"]) <= set(baseline) or not set(baseline) <= set(plan["original_requests"]):
        raise ValueError("baseline must cover every passthrough original request without unknown IDs")
    outcomes = {identity: _group_outcome(group, group_results[identity], plan["documents"])
                for identity, group in groups.items()}
    predictions = []
    for identity, request in plan["original_requests"].items():
        group_id = plan["request_to_group"].get(identity)
        if group_id is None:
            original = baseline[identity]
            if (original.get("request_sha256") != request["request_sha256"] or
                    original.get("selected_value") != request["selected_value"] or
                    original.get("source_request") != request or
                    original.get("source_task_bindings") != plan["bindings"][identity]):
                raise ValueError("unchanged passthrough original request/bindings differ")
            predictions.append(copy.deepcopy(original))
            continue
        winner, refs, reason, tie = outcomes[group_id]
        support = winner == request["selected_value"]
        predictions.append({
            "request_id": identity, "request_sha256": request["request_sha256"],
            "selected_value": request["selected_value"], "direction": request["direction"],
            "source_request": copy.deepcopy(request), "source_task_bindings": copy.deepcopy(plan["bindings"][identity]),
            "state": "support" if support else "unknown", "winning_value": winner,
            "diagnostic_winning_value": group_results[group_id].get("winning_value", winner),
            "quote_refs": copy.deepcopy(refs) if support else [],
            "reason": reason if winner is None or support else "product_global_alternative_value_selected",
            "group_id": group_id, "group_sha256": groups[group_id]["group_sha256"],
            "group_model_cache_key": groups[group_id]["model_cache_key"],
            "shared_group_decision": copy.deepcopy(group_results[group_id]),
            "product_global_invariance_basis": "user_asserted", "scope_status": "unproved",
            "production_adoption_allowed": False, "tie": tie, "variant": VERSION,
        })
    return predictions
