"""Combine three prompt variants as one group-level vote each."""
from __future__ import annotations

from copy import deepcopy
import json

import product_condition_sharing_v1 as SHARING


COMPONENT_COUNT = 3


def _quote_key(ref):
    return json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def combine(group, component_outcomes):
    """Return a two-of-three consensus over exact original option strings.

    Each component contributes at most one group-level vote. Block-level
    records are retained for audit and never counted as additional votes.
    Callers are responsible for validating source bindings and quote refs.
    """
    options = group.get("option_values")
    if (not isinstance(options, list) or not options or
            any(not isinstance(value, str) or not value for value in options) or
            len(options) != len(set(options))):
        raise ValueError("ordered original option values required")
    if SHARING.UNKNOWN in options:
        raise ValueError("original options collide with reserved unknown label")
    if not isinstance(component_outcomes, list) or len(component_outcomes) != COMPONENT_COUNT:
        raise ValueError("exactly three component outcomes required")

    names = []
    for outcome in component_outcomes:
        if not isinstance(outcome, dict):
            raise ValueError("component outcome must be an object")
        name = outcome.get("component_name")
        if not isinstance(name, str) or not name:
            raise ValueError("nonempty component_name required")
        names.append(name)
    if len(set(names)) != COMPONENT_COUNT:
        raise ValueError("component names must be distinct")

    vote_counts = {value: 0 for value in options}
    votes = []
    for outcome in component_outcomes:
        name = outcome["component_name"]
        status = outcome.get("status")
        winner = outcome.get("winning_value")
        if winner is not None and winner != SHARING.UNKNOWN and winner not in options:
            raise ValueError("component winner is not an original raw option")
        vote = winner if (status == "ok" and winner in options
                          and outcome.get("tie") is not True
                          and not outcome.get("error_type")) else None
        if vote is not None:
            vote_counts[vote] += 1
        votes.append({
            "component_name": name,
            "status": status,
            "winning_value": winner,
            "vote": vote,
            "block_winners": deepcopy(outcome.get("block_winners", {})),
            "block_votes": deepcopy(outcome.get("block_votes", {})),
            "supporting_block_ids": deepcopy(outcome.get("supporting_block_ids", [])),
        })

    winning_value = next((value for value, count in vote_counts.items() if count >= 2), None)
    supporting = [outcome for outcome, audit in zip(component_outcomes, votes, strict=True)
                  if winning_value is not None and audit["vote"] == winning_value]
    supporting_block_ids = []
    seen_blocks = set()
    quote_refs = []
    seen_quotes = set()
    for outcome in supporting:
        for block_id in outcome.get("supporting_block_ids", []):
            if block_id not in seen_blocks:
                supporting_block_ids.append(block_id)
                seen_blocks.add(block_id)
        for ref in outcome.get("quote_refs", []):
            key = _quote_key(ref)
            if key not in seen_quotes:
                quote_refs.append(deepcopy(ref))
                seen_quotes.add(key)

    return {
        "group_id": group.get("group_id"),
        "group_sha256": group.get("group_sha256"),
        "status": "ok" if winning_value is not None else "unknown",
        "winning_value": winning_value,
        "vote_counts": vote_counts,
        "component_names": names,
        "component_outcomes": votes,
        "supporting_block_ids": supporting_block_ids,
        "quote_refs": quote_refs,
        "scope_status": "unproved",
        "production_adoption_allowed": False,
    }
