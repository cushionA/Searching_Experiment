#!/usr/bin/env python3
"""Pure validation of the single-NLI worker raw pairs and option states."""
from __future__ import annotations

from collections import defaultdict
import json
from typing import Any

import kaggle_v7_small_nli_single_v1 as WORKER

GPU = WORKER.GPU
NLI = WORKER.NLI
PREPARATION_REASONS = {
    "tokenization_error", "metadata_exceeds_512_tokens",
    "single_character_exceeds_512_tokens",
}
STATE_VALUES = {"support", "conflict", "unknown"}


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reasons_cover(reasons: list[dict], start: int, end: int) -> bool:
    return any(type(reason.get("source_start")) is int
               and type(reason.get("source_end")) is int
               and reason["source_start"] <= start and reason["source_end"] >= end
               for reason in reasons)


def _gaps(intervals: list[tuple[int, int]], body_length: int) -> list[tuple[int, int]]:
    if not intervals:
        return [(0, body_length)] if body_length else []
    result = []
    cursor = 0
    for start, end in sorted(intervals):
        if start > cursor:
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < body_length:
        result.append((cursor, body_length))
    return result


def _overlaps(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result = []
    furthest_end = -1
    for start, end in sorted(intervals):
        if start < furthest_end:
            result.append((start, min(end, furthest_end)))
        furthest_end = max(furthest_end, end)
    return result


def _issue_map(option_states: list[dict], contexts_by_ref: dict[int, dict]) -> dict[int, list[dict]]:
    collected: dict[int, dict[str, dict]] = defaultdict(dict)
    for state in option_states:
        ref = state["context_ref"]
        for reason in state.get("unknown_reasons", []):
            if not isinstance(reason, dict) or reason.get("reason") not in PREPARATION_REASONS:
                continue
            issue = dict(reason)
            issue.setdefault("context_ref", ref)
            if issue["context_ref"] != ref:
                raise ValueError("preparation issue context_ref differs from option state")
            view = issue.get("view", state["view"])
            if view != state["view"]:
                raise ValueError("preparation issue view differs from option state")
            issue["view"] = view
            index = issue.get("option_index")
            if index is not None:
                if type(index) is not int or not 0 <= index < len(contexts_by_ref[ref]["option_values"]):
                    raise ValueError("preparation issue option_index is invalid")
                if index != state["option_index"]:
                    raise ValueError("preparation issue option_index differs from option state")
            for field in ("source_start", "source_end"):
                if field in issue and (type(issue[field]) is not int or issue[field] < 0):
                    raise ValueError("preparation issue source span is invalid")
            if "source_start" in issue or "source_end" in issue:
                start, end = issue.get("source_start"), issue.get("source_end")
                body_len = len(contexts_by_ref[ref]["body_text"])
                if (type(start) is not int or type(end) is not int or start > end or end > body_len):
                    raise ValueError("preparation issue source span exceeds source body")
            collected[ref][_canonical(issue)] = issue
    return {ref: list(rows.values()) for ref, rows in collected.items()}


def validate_raw(contexts: list[dict], records: list[dict], option_states: list[dict],
                 completion: dict) -> dict:
    """Validate exact candidate coverage and reproduce the worker aggregation.

    This function reads no labels, weights, network state, or model runtime. A raw
    record without ``cache_key`` is accepted only for the worker's explicit
    technical fallback record, and can never produce a decisive option state.
    """
    if not isinstance(contexts, list) or not contexts:
        raise ValueError("contexts must be a nonempty list")
    contexts_by_ref: dict[int, dict] = {}
    option_count = 0
    for context in contexts:
        ref = context.get("context_ref")
        if type(ref) is not int or ref in contexts_by_ref:
            raise ValueError("context_ref must be unique integer")
        options = context.get("option_values")
        if (not isinstance(options, list) or not options
                or any(not isinstance(value, str) or not value for value in options)
                or len(set(options)) != len(options)):
            raise ValueError("each context requires nonempty unique original options")
        if not isinstance(context.get("prefix"), str) or not isinstance(context.get("body_text"), str):
            raise ValueError("context prefix/body_text must be strings")
        if not isinstance(context.get("axis_name"), str):
            raise ValueError("context axis_name must be a string")
        contexts_by_ref[ref] = context
        option_count += len(options)

    expected_states = {
        (context["context_ref"], view, index)
        for context in contexts for view in WORKER.VIEWS
        for index in range(len(context["option_values"]))
    }
    state_by_key = {}
    for state in option_states:
        if not isinstance(state, dict):
            raise ValueError("option-state records must be objects")
        ref, view = state.get("context_ref"), state.get("view")
        index = state.get("option_index")
        if type(ref) is not int or type(index) is not int or view not in WORKER.VIEWS:
            raise ValueError("option-state context/view/index identity is invalid")
        key = (ref, view, index)
        if key not in expected_states or key in state_by_key:
            raise ValueError("foreign or duplicate option-state identity")
        context = contexts_by_ref[ref]
        if state.get("option_value") != context["option_values"][index]:
            raise ValueError("option-state original option identity differs")
        if state.get("state") not in STATE_VALUES:
            raise ValueError("option-state value is invalid")
        if not isinstance(state.get("unknown_reasons"), list):
            raise ValueError("option-state unknown_reasons must be a list")
        state_by_key[key] = state
    if set(state_by_key) != expected_states:
        raise ValueError("option-state rows do not cover every original candidate/view")

    issue_map = _issue_map(option_states, contexts_by_ref)
    records_by_ref: dict[int, list[dict]] = defaultdict(list)
    record_ids = set()
    by_candidate: dict[tuple[int, str, int], list[dict]] = defaultdict(list)
    model_scored_count = fallback_count = technical_failure_count = 0
    cache_keys = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("raw score records must be objects")
        ref, view = record.get("context_ref"), record.get("view")
        option_index = record.get("option_index")
        chunk_index = record.get("chunk_index")
        if (type(ref) is not int or ref not in contexts_by_ref or view not in WORKER.VIEWS
                or type(option_index) is not int or type(chunk_index) is not int
                or option_index < 0 or chunk_index < 0):
            raise ValueError("raw record context/view/option/chunk identity is invalid")
        context = contexts_by_ref[ref]
        if option_index >= len(context["option_values"]):
            raise ValueError("raw record has foreign option_index")
        record_id = record.get("id")
        wanted_id = f"{ref}:{view}:{option_index}:{chunk_index}"
        if not isinstance(record_id, str) or record_id != wanted_id or record_id in record_ids:
            raise ValueError("raw record ID is invalid or duplicated")
        record_ids.add(record_id)
        option = context["option_values"][option_index]
        hypothesis = WORKER.HYPOTHESIS_TEMPLATE.format(axis_name=context["axis_name"], option=option)
        if record.get("hypothesis") != hypothesis:
            raise ValueError("raw record hypothesis differs from exact original option template")
        if view == "title_row":
            if record.get("source_start") is not None or record.get("source_end") is not None:
                raise ValueError("title_row raw record must not claim body source offsets")
            expected_premise = context["prefix"]
        elif not context["body_text"]:
            if type(record.get("source_start")) is not int or type(record.get("source_end")) is not int:
                raise ValueError("empty-body raw record offsets must be integers")
            if (record["source_start"], record["source_end"]) != (0, 0):
                raise ValueError("empty-body raw record must use the empty source span")
            expected_premise = context["prefix"]
        else:
            start, end = record.get("source_start"), record.get("source_end")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end
                    or end > len(context["body_text"])):
                raise ValueError("body raw record offsets are invalid")
            if end - start > WORKER.MAX_CHUNK_CHARS:
                raise ValueError("body raw chunk exceeds the frozen character limit")
            expected_premise = (context["prefix"] + "\n引用本文：\n"
                                + context["body_text"][start:end])
        if record.get("premise") != expected_premise:
            raise ValueError("raw record premise does not match the frozen source text")

        for name, expected in (("model_id", NLI.MODEL_ID),
                               ("model_revision", NLI.MODEL_REVISION),
                               ("precision", "fp32"), ("device", "cuda")):
            if record.get(name) != expected:
                raise ValueError("raw record model pin differs: " + name)
        pair_key = NLI.pair_key(expected_premise, hypothesis)
        cache_key = record.get("cache_key")
        technical = record.get("error_type") is not None
        technical_failure_count += int(technical)
        if cache_key is None:
            if not technical:
                raise ValueError("raw record missing exact cache key without technical fallback error")
            fallback_count += 1
        else:
            if cache_key != pair_key:
                raise ValueError("raw record exact pair cache_key differs")
            cache_keys.add(cache_key)
            model_scored_count += 1
        records_by_ref[ref].append(record)
        by_candidate[(ref, view, option_index)].append(record)

    if not isinstance(completion, dict):
        raise ValueError("completion must be an object")
    expected_view_count = len(contexts) * len(WORKER.VIEWS)
    expected_options = option_count
    expected_option_states_count = len(expected_states)
    count_checks = {
        "expected_context_count": len(contexts),
        "expected_context_view_count": expected_view_count,
        "written_context_view_count": expected_view_count,
        "options_count": expected_options,
        "option_state_count": expected_option_states_count,
        "scored_pair_count": len(records),
        "technical_failure_raw_record_count": technical_failure_count,
    }
    for field, expected in count_checks.items():
        actual = completion.get(field)
        if type(actual) is not int or actual != expected:
            raise ValueError(f"completion {field} differs from validated raw outputs")
    if completion.get("actual_loaded_models") != ["nli"]:
        raise ValueError("completion must report exactly the loaded NLI model")
    inferred_unique = completion.get("inferred_unique_pair_count")
    if type(inferred_unique) is not int or not 0 <= inferred_unique <= model_scored_count:
        raise ValueError("completion inferred_unique_pair_count is inconsistent")

    expected_pin = {
        "model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
        "file_sha256": dict(NLI.NLI_FILE_SHA256), "precision": "fp32", "device": "cuda",
    }
    if completion.get("output_model_pin") != expected_pin:
        raise ValueError("completion output model pin differs from the fixed NLI pin")

    # Validate per-option source coverage and fixed body chunk partition.
    unscored_option_views = 0
    partial_source_option_views = 0
    full_body_coverage_option_views = 0
    for context in contexts:
        ref = context["context_ref"]
        for view in WORKER.VIEWS:
            for option_index in range(len(context["option_values"])):
                key = (ref, view, option_index)
                state = state_by_key[key]
                candidate_records = by_candidate.get(key, [])
                issues = [reason for reason in state["unknown_reasons"]
                          if isinstance(reason, dict)]
                if not candidate_records:
                    if state["state"] != "unknown" or not state["unknown_reasons"]:
                        raise ValueError("a candidate with no model pair must be explicitly unknown")
                    unscored_option_views += 1
                    if view == "body" and context["body_text"]:
                        # No inference for this candidate is visible; require a full-span preparation
                        # issue or an explicit no-usable-pairs reason from the worker aggregator.
                        if not (_reasons_cover(issues, 0, len(context["body_text"]))
                                or any(r.get("reason") == "no_usable_pairs" for r in issues)):
                            raise ValueError("unscored body candidate lacks an explicit full-span reason")
                    continue

                ids_by_chunk = [r["chunk_index"] for r in candidate_records]
                if len(ids_by_chunk) != len(set(ids_by_chunk)) or sorted(ids_by_chunk) != list(range(len(ids_by_chunk))):
                    raise ValueError("candidate chunk indexes must be unique and contiguous from zero")
                if view == "title_row":
                    if len(candidate_records) != 1 or candidate_records[0]["chunk_index"] != 0:
                        raise ValueError("title_row candidate requires exactly one raw pair")
                    continue
                if not context["body_text"]:
                    if len(candidate_records) != 1 or candidate_records[0]["chunk_index"] != 0:
                        raise ValueError("empty-body candidate requires exactly one raw pair")
                    continue
                intervals = [(r["source_start"], r["source_end"]) for r in candidate_records]
                gaps = _gaps(intervals, len(context["body_text"]))
                overlaps = _overlaps(intervals)
                if gaps or overlaps:
                    if state["state"] != "unknown":
                        raise ValueError("incomplete or overlapping body sources cannot have decisive state")
                    for start, end in gaps:
                        if not _reasons_cover(issues, start, end):
                            raise ValueError("missing body source span lacks an explicit unknown issue")
                    for start, end in overlaps:
                        if not _reasons_cover(issues, start, end):
                            raise ValueError("overlapping body source span lacks an explicit unknown issue")
                    partial_source_option_views += 1
                else:
                    full_body_coverage_option_views += 1

    # For normal successful inputs every original option in a context shares
    # the same body partition. Differences are accepted only when a preparation
    # issue for the affected option explicitly covers the differing source span.
    for context in contexts:
        ref = context["context_ref"]
        if not context["body_text"]:
            continue
        layouts = []
        for option_index in range(len(context["option_values"])):
            rows = by_candidate.get((ref, "body", option_index), [])
            layouts.append(tuple(sorted((r["source_start"], r["source_end"]) for r in rows)))
        baseline = layouts[0]
        for option_index, layout in enumerate(layouts[1:], 1):
            if layout == baseline:
                continue
            affected = set(baseline).symmetric_difference(layout)
            for start, end in affected:
                candidate_state = state_by_key[(ref, "body", option_index)]
                base_state = state_by_key[(ref, "body", 0)]
                candidate_reasons = [r for r in candidate_state["unknown_reasons"] if isinstance(r, dict)]
                base_reasons = [r for r in base_state["unknown_reasons"] if isinstance(r, dict)]
                if not (_reasons_cover(candidate_reasons, start, end)
                        or _reasons_cover(base_reasons, start, end)):
                    raise ValueError("body chunk partitions differ across original options without an explicit issue")

    # Rebuild only the worker's preparation issues from output state receipts,
    # then run its pure aggregation over the validated raw score records.
    recomputed_state_count = 0
    for context in contexts:
        ref = context["context_ref"]
        prep_issues = issue_map.get(ref, [])
        rebuilt = WORKER.aggregate_option_states(context, records_by_ref.get(ref, []),
                                                 records_by_ref.get(ref, []), prep_issues)
        for expected_state in rebuilt:
            key = (ref, expected_state["view"], expected_state["option_index"])
            actual_state = state_by_key[key]
            if (actual_state["state"] != expected_state["state"]
                    or sorted(map(_canonical, actual_state["unknown_reasons"]))
                    != sorted(map(_canonical, expected_state["unknown_reasons"]))):
                raise ValueError("option state differs from worker aggregation of raw pairs")
            recomputed_state_count += 1

    return {
        "context_count": len(contexts), "option_count": option_count,
        "expected_candidate_view_count": expected_option_states_count,
        "raw_record_count": len(records), "model_scored_record_count": model_scored_count,
        "technical_fallback_record_count": fallback_count,
        "technical_failure_raw_record_count": technical_failure_count,
        "unique_exact_pair_count": len(cache_keys),
        "inferred_unique_pair_count": inferred_unique,
        "not_model_scored_candidate_view_count": unscored_option_views,
        "partial_source_coverage_candidate_view_count": partial_source_option_views,
        "full_body_source_coverage_candidate_view_count": full_body_coverage_option_views,
        "recomputed_option_state_count": recomputed_state_count,
    }
