"""Offline metadata audit; never import or execute candidate code or load weights."""
import ast
import fnmatch
import hashlib
import json
from pathlib import Path
import platform
import re
import time


def main():
    start = time.perf_counter()
    root = Path(__file__).resolve().parents[2]
    evidence = root / "benchmarks/daily-tech-20261026"
    sources = evidence / "sources"
    manifest = json.loads((evidence / "source-manifest.json").read_text())
    for item in manifest["sources"]:
        assert "error" not in item, item
        data = (sources / item["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], item["file"]

    # Parse only literals; no imports, eval, exec or candidate functions.
    constants = {}
    for node in ast.parse((sources / "models.py.txt").read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = ast.literal_eval(node.value)
    assert constants["HF_REVISION"] == "v1.1.0"
    refs = json.loads((sources / "hf-refs.json").read_text())
    tag = next(x for x in refs["tags"] if x["name"] == constants["HF_REVISION"])
    assert tag["targetCommit"] == manifest["resolved_tag_sha"]
    catalogue = (sources / "port-catalogue.ts.txt").read_text()
    assert tag["targetCommit"] in catalogue
    files = {x["path"]: x["size"] for x in json.loads(
        (sources / "hf-tag-tree.json").read_text()) if x["type"] == "file"}
    # Download policy transcribed from the pinned loader, not run dynamically.
    default = sorted(f for f in files if any(fnmatch.fnmatchcase(f, p) for p in
        ("*.onnx", "meta.json", "lm.npz", "lm_*.npz"))
        and f not in constants["MATH_FILES"])
    math = sorted(set(default) | set(constants["MATH_FILES"]))
    assert all(f in files for f in math)
    mapping_text = re.search(r"export const MODEL_FILES:.*?= \{(.*?)\n\};", catalogue, re.S)[1]
    mapping = dict(re.findall(r'(\w+): "([^"]+)"', mapping_text))
    base_text = re.search(r"export const BASE_FILES:.*?= \[(.*?)\];", catalogue, re.S)[1]
    base = [mapping[k] for k in re.findall(r'"([^"]+)"', base_text)]
    modes = {"python_default": default, "python_math": math,
             "port_base": base, "port_base_plus_cjk": base + [mapping["recCjk"]],
             "port_base_plus_cjk_and_cjk_lm": base + [mapping["recCjk"], mapping["lmCjk"]]}
    metadata = json.loads((sources / "hf-version.json").read_text())
    result = {
        "kind": "static_manifest_arithmetic_not_inference",
        "python": platform.python_version(), "source_files_verified": len(manifest["sources"]),
        "code_sha": manifest["code_sha"], "model_tag": constants["HF_REVISION"],
        "resolved_tag_sha": tag["targetCommit"], "api_revision_reported_sha": metadata["sha"],
        "api_revision_matches_refs": metadata["sha"] == tag["targetCommit"],
        "variants": {name: {"files": names, "file_count": len(names),
            "bytes": sum(files[n] for n in names)} for name, names in modes.items()},
        "safetensors_used_by_default": "model.safetensors" in default,
        "actual_candidate_trials": 0, "weight_bytes_downloaded": 0,
        "limitations": ["File sizes are remote metadata, not downloaded weights.",
            "Base/CJK sets are static lower bounds, not observed network or RSS.",
            "Math decoder files may share weights; summed bytes are not parameter counts.",
            "No speed, OCR quality, missing-page recovery or search coverage measurement."],
        "elapsed_s": round(time.perf_counter() - start, 6),
    }
    (evidence / "static-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
