"""Output cardinality constraints for Luna SKU matching v4."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import trial_luna_sku_matching as core

VERSION_V4 = "selected-sku-scoped-bidirectional-v4-output-shape"
SHAPE_POLICY = "row_and_check_array_cardinality_from_case_input"

SHAPE_INSTRUCTIONS = """出力のJSON位置と商品の意味facet照合を混同しない。
rakuten_checksは楽天条件の個数だけ返す。au_checksは現在のAU行の条件数だけ返す。楽天条件の個数をau_checksへ複製してはならない。
rowsは入力AU行の順序を保つ。各checkはstatusとsource_idsのみを返す。
OUTPUT_SHAPE="""


def output_shape_request(case, contexts):
    """Return a v3 request with schema and prompt cardinalities tied to input."""
    request = copy.deepcopy(core.scoped_v3_request(case, contexts))
    schema = request["body"]["generationConfig"]["responseSchema"]["properties"]
    rows = schema["rows"]
    rows["minItems"] = len(case["au_rows"])
    rows["maxItems"] = len(case["au_rows"])

    row_schema = rows["items"]["properties"]
    # The smoke schema reuses one checks object for both sides. Split those
    # nodes before assigning different per-side cardinalities.
    row_schema["rakuten_checks"] = copy.deepcopy(row_schema["rakuten_checks"])
    row_schema["au_checks"] = copy.deepcopy(row_schema["au_checks"])
    rk_count = len(case["rakuten_conditions"])
    au_counts = [len(row["conditions"]) for row in case["au_rows"]]
    rk_checks = row_schema["rakuten_checks"]
    rk_checks["minItems"] = rk_count
    rk_checks["maxItems"] = rk_count
    if len(set(au_counts)) == 1:
        au_checks = row_schema["au_checks"]
        au_checks["minItems"] = au_counts[0]
        au_checks["maxItems"] = au_counts[0]

    shape = [{"row_index": i, "rakuten_checks": rk_count, "au_checks": count}
             for i, count in enumerate(au_counts)]
    text = request["body"]["contents"][0]["parts"][0]["text"]
    marker = "INPUT="
    pos = text.find(marker)
    if pos < 0:
        raise ValueError("v3 prompt is missing INPUT marker")
    text = text[:pos] + SHAPE_INSTRUCTIONS + json.dumps(shape, separators=(",", ":")) + "\n" + text[pos:]
    request["body"]["contents"][0]["parts"][0]["text"] = text
    return request


def prepare_shape_round(round_dir, cases, hashes, zip_path, selection=None):
    """Prepare a scoped-v3 round, then freeze v4-shaped requests before dispatch."""
    round_dir = Path(round_dir)
    manifest = core.prepare(round_dir, cases, hashes, zip_path, mode="scoped-v3", selection=selection)
    contexts = core.read(round_dir / "source-contexts.json")
    for case, entry in zip(cases, manifest["entries"], strict=True):
        path = round_dir / entry["request"]
        request = output_shape_request(case, contexts[case["case_id"]])
        core.save(path, request)
        entry["request_sha256"] = core.sha(path.read_bytes())
    manifest["task_version"] = VERSION_V4
    manifest["shape_policy"] = SHAPE_POLICY
    core.save(round_dir / "manifest.json", manifest)
    return manifest
