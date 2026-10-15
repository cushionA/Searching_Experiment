#!/usr/bin/env python3
"""Product-local bipartite graph alignment for complete native option domains.

The adapter compares only exact option keys from a caller-selected fixed view.
It defines no SKU grammar, axis aliases, category rules, or semantic scorer.
"""
from __future__ import annotations

from typing import Any

import networkx as nx


SCHEMA = "library-axis-domain-alignment-v1"
SIDES = ("au", "rakuten")
MODES = ("raw", "spacy_norm")
POLICIES = ("mutual_unique", "forced_maximum")


def _max_matching(graph: nx.Graph, au_nodes: set[str]) -> dict[str, str]:
    if graph.number_of_edges() == 0:
        return {}
    # NetworkX iterates top_nodes; integer node IDs make that iteration
    # independent of PYTHONHASHSEED while insertion order keeps adjacency
    # traversal stable. The public graph evidence still uses native string IDs.
    ordered = sorted(graph.nodes)
    node_to_int = {node: index for index, node in enumerate(ordered)}
    stable_graph = nx.Graph()
    stable_graph.add_nodes_from(range(len(ordered)))
    stable_graph.add_edges_from(sorted((node_to_int[a], node_to_int[b])
                                       for a, b in graph.edges))
    stable_top = {node_to_int[node] for node in au_nodes}
    matching = nx.algorithms.bipartite.maximum_matching(stable_graph, top_nodes=stable_top)
    int_to_node = {number: node for node, number in node_to_int.items()}
    return {int_to_node[node]: int_to_node[other] for node, other in matching.items()}


def _cardinality(matching: dict[str, str]) -> int:
    return len(matching) // 2


def build_product_graph(dossier_id: str, families_by_side: dict[str, dict[str, dict[str, Any]]],
                        raw_view: dict[str, str], norm_view: dict[str, str], mode: str) -> dict[str, Any]:
    """Build one dossier/view graph and compute the two predeclared edge policies.

    `families_by_side` must contain every observed family, including invalid
    families. Each family entry carries `valid`, `raw_values`, `request_ids`,
    `invalid_reason`, and all observed `vectors` for provenance.
    """
    if mode not in MODES:
        raise ValueError(f"unsupported alignment view: {mode!r}")
    if set(families_by_side) != set(SIDES):
        raise ValueError("both AU and Rakuten family maps are required")
    view = raw_view if mode == "raw" else norm_view
    nodes: list[dict[str, Any]] = []
    axis_domains: dict[str, dict[str, dict[str, Any]]] = {side: {} for side in SIDES}
    hold_reasons: list[str] = []
    for side in SIDES:
        for axis_name, family in sorted(families_by_side[side].items(), key=lambda item: repr(item[0])):
            raw_values = family.get("raw_values", [])
            values = raw_values if isinstance(raw_values, list) else []
            raw_values_valid = all(isinstance(value, str) for value in values)
            view_values = ([view.get(value) for value in values] if raw_values_valid else
                           [None for _ in values])
            reason = family.get("invalid_reason")
            if not family.get("valid", False):
                reason = reason or "native_family_invalid"
            elif not isinstance(axis_name, str) or not axis_name:
                reason = "empty_or_nonstring_axis_identifier"
            elif not isinstance(raw_values, list) or not raw_values_valid:
                reason = "nonstring_or_invalid_native_option_vector"
            elif not values or any(not isinstance(value, str) or not value for value in values):
                reason = "empty_or_nonstring_native_option"
            elif len(values) != len(set(values)):
                reason = "duplicate_raw_native_option"
            elif any(value not in view for value in values):
                reason = "missing_view_for_native_option"
            elif any(not isinstance(value, str) or not value for value in view_values):
                reason = "empty_or_nonstring_view_key"
            elif len(view_values) != len(set(view_values)):
                reason = "within_axis_view_collision"
            node_id = f"{side.upper()}::{axis_name!r}"
            node = {"node_id": node_id, "side": side, "axis_name": axis_name,
                    "raw_values": values, "view_values": view_values,
                    "request_ids": family.get("request_ids", []),
                    "source_vectors": family.get("vectors", []),
                    "valid": reason is None, "invalid_reason": reason}
            nodes.append(node)
            axis_domains[side][axis_name] = node
            if reason:
                hold_reasons.append(f"{side}:{axis_name}:{reason}")

    au_nodes = {node["node_id"] for node in nodes if node["side"] == "au"}
    rk_nodes = {node["node_id"] for node in nodes if node["side"] == "rakuten"}
    graph = nx.Graph()
    for node in sorted(nodes, key=lambda item: item["node_id"]):
        graph.add_node(node["node_id"], **node)
    edges: list[dict[str, Any]] = []
    # An incomplete/inconsistent family invalidates the entire product graph
    # for this view. Keep every axis node but withhold all candidate edges.
    if not hold_reasons and au_nodes and rk_nodes:
        for au_axis, au_node in sorted(axis_domains["au"].items()):
            au_map = dict(zip(au_node["view_values"], au_node["raw_values"]))
            for rk_axis, rk_node in sorted(axis_domains["rakuten"].items()):
                rk_map = dict(zip(rk_node["view_values"], rk_node["raw_values"]))
                shared = sorted(set(au_map).intersection(rk_map))
                if len(shared) < 2:
                    continue
                record = {"source_node": au_node["node_id"], "target_node": rk_node["node_id"],
                    "source_axis_name": au_axis, "target_axis_name": rk_axis,
                    "shared_keys": shared,
                    "shared_pairs": [{"key": key, "au_raw_value": au_map[key],
                                      "rakuten_raw_value": rk_map[key]} for key in shared]}
                edges.append(record)
                graph.add_edge(au_node["node_id"], rk_node["node_id"], evidence=record)
    if not au_nodes or not rk_nodes:
        hold_reasons.append("missing_side_axis_catalog")

    matching = _max_matching(graph, au_nodes)
    maximum_pairs = sorted((node_id, other) for node_id, other in matching.items()
                           if node_id in au_nodes)
    maximum_size = _cardinality(matching)
    mutual = []
    for au_node, rk_node in sorted(graph.edges()):
        if au_node not in au_nodes:
            au_node, rk_node = rk_node, au_node
        if graph.degree(au_node) == 1 and graph.degree(rk_node) == 1:
            mutual.append({"source_axis_name": graph.nodes[au_node]["axis_name"],
                           "target_axis_name": graph.nodes[rk_node]["axis_name"],
                           "source_node": au_node, "target_node": rk_node})
    forced = []
    if not hold_reasons:
        for au_node, rk_node in maximum_pairs:
            graph.remove_edge(au_node, rk_node)
            reduced_size = _cardinality(_max_matching(graph, au_nodes))
            graph.add_edge(au_node, rk_node, evidence=next(
                edge for edge in edges if edge["source_node"] == au_node and edge["target_node"] == rk_node))
            if reduced_size < maximum_size:
                forced.append({"source_axis_name": graph.nodes[au_node]["axis_name"],
                               "target_axis_name": graph.nodes[rk_node]["axis_name"],
                               "source_node": au_node, "target_node": rk_node,
                               "maximum_size_without_edge": reduced_size})
    status = "ready" if not hold_reasons else "held"
    return {"schema": SCHEMA, "graph_id": f"{dossier_id}:{mode}",
            "dossier_id": dossier_id, "view": mode, "status": status,
            "hold_reasons": sorted(set(hold_reasons)), "nodes": nodes,
            "edges": edges, "maximum_cardinality": maximum_size,
            "maximum_matching_pairs": maximum_pairs,
            "mutual_unique_edges": mutual, "forced_maximum_edges": forced,
            "axis_correspondence_is_semantic_proof": False,
            "one_to_one_axis_mapping_assumption": True,
            "production_adoption_allowed": False}


def apply_policy_edges(graph_record: dict[str, Any], policy: str) -> dict[str, str]:
    """Return raw axis-name pairs under a fixed structural edge policy."""
    if policy not in POLICIES:
        raise ValueError(f"unknown axis mapping policy: {policy!r}")
    if graph_record.get("status") != "ready":
        return {}
    entries = graph_record["mutual_unique_edges" if policy == "mutual_unique" else "forced_maximum_edges"]
    mapping: dict[str, str] = {}
    targets: set[str] = set()
    for edge in entries:
        source, target = edge["source_axis_name"], edge["target_axis_name"]
        if source in mapping or target in targets:
            raise ValueError("policy edge set is not one-to-one")
        mapping[source] = target
        targets.add(target)
    return mapping


__all__ = ["SCHEMA", "SIDES", "MODES", "POLICIES", "build_product_graph", "apply_policy_edges"]
