"""Verify the committed handoff using only its bundled files.

This checks evidence hashes and fixture excerpts, not matching accuracy.
Run from any working directory: python3 -B path/to/verify_handoff.py
"""
import hashlib
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(value, path):
    for key, index in re.findall(r"([^\.\[\]]+)|\[(\d+)\]", path):
        value = value[key] if key else value[int(index)]
    return value


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    contract = read_json(BASE / "rule-contract.json")
    fixtures = read_json(BASE / "fixtures/fixtures.json")
    manifest = read_json(BASE / "evidence/manifest.json")
    records = manifest["records"]
    assert len(records) == manifest["document_count"] == 58
    assert len({r["url"] for r in records}) == len(records)
    by_name = {r["product_file"]: r for r in records}
    assert len(by_name) == len(records)
    for record in records:
        file = BASE / record["export_file"]
        assert sha256(file) == record["export_sha256"], file.name
        read_json(file)

    assert len(fixtures["fixtures"]) == fixtures["fixture_count"] == 13
    allowed_routes = set(contract["rule_order"]) | {"AI"}
    actual = synthetic = checks = 0
    for fixture in fixtures["fixtures"]:
        assert fixture["expected_route"] in allowed_routes, fixture["id"]
        if fixture["case"] == "synthetic":
            synthetic += 1
            continue
        assert fixture["case"] == "actual"
        actual += 1
        assert set(fixture["comparison_pair"].values()) <= set(fixture["evidence"])
        for evidence in fixture["evidence"].values():
            ref = evidence["source"]
            record = by_name[ref["product_file"]]
            assert ref["product_sha256"] == record["original_product_sha256"]
            assert ref["appdata_sha256"] == record["original_appdata_sha256"]
            assert ref["export_file"] == record["export_file"]
            assert ref["export_sha256"] == record["export_sha256"]
            product = read_json(BASE / ref["export_file"])
            for field in evidence["fields"]:
                value = resolve(product, field["path"])
                if field["comparison_mode"] == "equals":
                    assert value == field["value"], (fixture["id"], field["path"])
                else:
                    assert field["comparison_mode"] == "contains"
                    assert field["value"] in value, (fixture["id"], field["path"])
                checks += 1

    assert actual == 9 and synthetic == 4 and checks == 59
    print(json.dumps({
        "result": "PASS", "product_documents": len(records),
        "fixtures": actual + synthetic, "actual_excerpt_checks": checks,
        "scope": "portable evidence hashes and fixture excerpts; not matching accuracy",
    }, ensure_ascii=False))


if __name__ == "__main__":
    verify()
