#!/usr/bin/env python3
"""Package a frozen historical library-volume run and its reproducible inputs.

This exporter is deliberately read-only with respect to experiment inputs and
outputs. It validates the run's input/code/output pins before writing an
exclusive, versioned report bundle. Private reference archives are never
copied into the export.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ("baseline_raw", "previous_components", "mutual_unique_raw_components")
PRIVATE_NAMES = {"grade_final_test_subset_v1.py"}  # grader may access private archives
PRIVATE_PARTS = {".deps", "venv", "runtime", "models", "weights"}
OMIT_PART = "unmodified-preparer-staging"
AXIS_V2_ZIP = ROOT / "experiments/sku-matching/results/20261015-sku-library-axis-domains-v2.zip"
AXIS_V2_SHA256 = "c2797784133fd8f983420559ae980f2a1aff3ffc9fcf0220998aa9820fa4ff8b"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError as e:
        raise ValueError(f"export input outside repository root: {resolved}") from e


def safe_source(path: Path) -> bool:
    if not path.is_file() or any(part in PRIVATE_PARTS for part in path.parts):
        return False
    if OMIT_PART in path.parts:
        return False
    if path.name in PRIVATE_NAMES or "label-archive" in path.name.lower():
        return False
    if path.suffix.lower() in {".pt", ".pth", ".safetensors", ".gguf", ".onnx"}:
        return False
    return True


def collect_tree(root: Path, files: set[Path]) -> None:
    if not root.exists():
        return
    if root.is_file():
        if safe_source(root):
            files.add(root.resolve())
        return
    for path in root.rglob("*"):
        if safe_source(path):
            files.add(path.resolve())


def pin_file(path: Path, expected: str, label: str, verified: dict[str, str]) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} missing: {path}")
    actual = sha(path)
    if actual != expected:
        raise ValueError(f"{label} pin mismatch: {path}: {actual} != {expected}")
    verified[rel(path)] = actual


def verify_freeze(run: Path, plan: dict, verified: dict[str, str]) -> dict:
    freeze_path = run / "RUN_FREEZE.json"
    freeze = read_json(freeze_path)
    verified[rel(freeze_path)] = sha(freeze_path)
    if freeze.get("schema") != "sku-library-volume-output-freeze-v1":
        raise ValueError("unexpected run freeze schema")
    if freeze.get("independent_test_claim") is not False or freeze.get("labels_read") is not False:
        raise ValueError("run freeze does not preserve historical/label-blind protocol")
    if (freeze.get("model_calls") != 0 or freeze.get("models_loaded") != []
            or freeze.get("grades_read") is not False
            or freeze.get("all_native_axes_rows_conditions_retained") is not True
            or freeze.get("production_adoption_allowed") is not False):
        raise ValueError("run freeze violates the label-blind/full-coverage diagnostic protocol")
    if tuple(freeze.get("methods", VARIANTS)) != VARIANTS:
        raise ValueError("variant list differs from the predeclared three variants")
    if plan.get("primary") != VARIANTS[-1] or tuple(plan.get("variants", ())) != VARIANTS:
        raise ValueError("experiment plan variants or primary differ")
    for name, digest in freeze.get("input_pins", {}).items():
        pin_file(Path(name), digest, "input", verified)
    for name, digest in freeze.get("code_pins", {}).items():
        pin_file(Path(__file__).with_name(name), digest, "code", verified)
    for name, digest in freeze.get("output_pins", {}).items():
        pin_file(run / name, digest, "prediction output", verified)
    if freeze.get("source_path"):
        source = Path(freeze["source_path"])
        original = Path(freeze["original_path"])
        for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "row-plans.jsonl",
                     "case-plans.jsonl", "tasks.jsonl"):
            path = source / name
            if path.is_file():
                verified[rel(path)] = sha(path)
        for name in ("cases.jsonl", "products.jsonl"):
            path = original / name
            if path.is_file():
                verified[rel(path)] = sha(path)
    return freeze


def format_report(lab: Path, run: Path, plan: dict, freeze: dict, grade: dict,
                  benchmark: dict, audit: dict, dev: dict) -> tuple[dict, str]:
    variants = grade.get("variants")
    if not isinstance(variants, dict) or any(name not in variants for name in VARIANTS):
        raise ValueError("grade.json lacks all frozen variants")
    primary = variants[VARIANTS[-1]]
    counts = primary.get("counts", {})
    source_counts = grade.get("source_counts", freeze.get("counts", {}))
    references = grade.get("reference_counts", {})
    def n(value):
        return int(value) if isinstance(value, (int, float)) else 0
    def workload(v):
        w = v.get("workload_estimate", {})
        return {k: n(w.get(k)) for k in ("all_contexts", "residual_contexts", "nonconflict_rows")}
    workload_rows = []
    variant_scores = {}
    for name in VARIANTS:
        v = variants[name]
        workload_rows.append({"variant": name, **workload(v)})
        variant_scores[name] = {
            "counts": v.get("counts"),
            "correct_accept_known": v.get("correct_accept_known"),
            "correct_reject_known": v.get("correct_reject_known"),
            "wrong_accept_known_count": len(v.get("wrong_accept_known_case_ids", [])),
            "wrong_reject_known_count": len(v.get("wrong_reject_known_case_ids", [])),
            "accept_reference_unknown_count": len(v.get("accept_reference_unknown_case_ids", [])),
            "reject_reference_unknown_count": len(v.get("reject_reference_unknown_case_ids", [])),
            "products_with_known_correct_accept": v.get("products_with_known_correct_accept"),
            "pruning": v.get("pruning"),
        }
    try_runs = benchmark.get("variants", {})
    loadtest = try_runs.get("axis_compound_main:50000", {})
    native_cases = freeze.get("counts", {}).get("cases", 0)
    full = benchmark.get("full_run_generation_reference", {})
    method = "mutual_unique_raw_components"
    result = {
        "schema": "sku-library-volume-report-v1", "primary": method,
        "independent_test": False,
        "historical_data_previously_evaluated_by_other_methods": True,
        "reference_kind": grade.get("reference_kind"),
        "source_counts": source_counts,
        "reference_counts": references,
        "primary_metrics": {
            "decisions": counts,
            "correct_accept_known": primary.get("correct_accept_known"),
            "correct_reject_known": primary.get("correct_reject_known"),
            "wrong_accept_known_count": len(primary.get("wrong_accept_known_case_ids", [])),
            "wrong_reject_known_count": len(primary.get("wrong_reject_known_case_ids", [])),
            "accept_reference_unknown_count": len(primary.get("accept_reference_unknown_case_ids", [])),
            "reject_reference_unknown_count": len(primary.get("reject_reference_unknown_case_ids", [])),
            "pruning": primary.get("pruning"),
            "products_with_known_correct_accept": primary.get("products_with_known_correct_accept"),
        },
        "variant_metrics": variant_scores,
        "all_variant_workload_estimates": workload_rows,
        "benchmark": {
            "type": "duplicated throughput replay, not accuracy population",
            "accuracy_population": False,
            "replayed_cases": loadtest.get("actual_replayed_cases"),
            "replayed_rows": loadtest.get("actual_replayed_rows"),
            "replayed_condition_bindings": loadtest.get("actual_replayed_condition_bindings"),
            "same_prediction_states_as_frozen": loadtest.get("same_prediction_states_as_frozen"),
            "gate_reassembly_wall_seconds": loadtest.get("gate_reassembly_wall_seconds"),
            "peak_rss_bytes": loadtest.get("process_peak_rss_bytes"),
            "unique_population_cases": benchmark.get("population", {}).get("cases"),
            "new_historical_test_cases": native_cases,
            "full_run_generation_reference": full,
            "source_load_wall_seconds": benchmark.get("population", {}).get("source_load_wall_seconds"),
        },
        "native_run_performance": {
            "source_population_cases": native_cases,
            "phase_wall_seconds": freeze.get("phase_timings_seconds"),
            "process_peak_rss_bytes": max(freeze.get("peak_rss_bytes_by_phase", {}).values(), default=None),
            "cpu_time_measured": False,
        },
        "tests": {
            "focused_related_tests_passed": 52,
            "required_library_environment": {"tests": 1558, "failures": 4, "errors": 66, "skipped": 60,
                "log": ".lab-output/sku-library-volume-20261015-v1/required-tests-library-env-v1.log"},
            "prior_system_environment": {"tests": 1570, "failures": 4, "errors": 46, "skipped": 116,
                "log": ".lab-output/sku-library-volume-20261015-v1/required-tests-v1.log",
                "interpretation": "different system Python environment; library-dependent import errors, retained as separate evidence"},
        },
        "protocol": {
            "all_native_rows_and_condition_bindings_preserved": True,
            "price_features_masked_by_frozen_library_currency_spans": True,
            "historical_cohort_not_independent": True,
            "new_model_calls": 0, "new_rule_or_threshold_changes": False,
            "production_adoption_allowed": False,
            "test_population_note": audit.get("historical_full_test", {}).get("reason"),
        },
        "reproduction_base": {
            "repository_base_commit": "ebf04ae",
            "axis_domains_v2_archive_sha256": AXIS_V2_SHA256,
            "axis_domains_v2_archive_included": False,
            "prep_staging_snapshot_omitted": True,
            "staging_note": "The unmodified-preparer-staging snapshot duplicates the final hash-pinned prepared population inputs and is unnecessary for replay.",
        },
        "integrity": {
            "run_freeze_sha256": sha(run / "RUN_FREEZE.json"),
            "grade_sha256": sha(run / "grade.json"),
            "compatibility_all_variants_identical": dev.get("all_3_variants_all_request_row_case_states_identical"),
            "plan_sha256": sha(lab / "EXPERIMENT_PLAN.json"),
        },
    }
    lines = [
        "SKUライブラリ・ボリューム検証（凍結済み履歴データ）",
        "",
        f"主方式は{method}。既存手法で評価済みの履歴コホートを使った診断であり、独立テストではありません。",
        f"参照は{grade.get('reference_kind', 'unspecified')}。参照内訳: {json.dumps(references, ensure_ascii=False, sort_keys=True)}。Claude Haikuの機械注釈で、人手goldではありません。",
        f"追加の履歴テスト母集団: {freeze.get('counts', {}).get('products')}商品・{source_counts.get('cases')}ケース、{source_counts.get('rows')}行、{source_counts.get('bindings')}方向付き条件。元の商品行と条件を全件保持し、価格spanだけ凍結済みライブラリ検出器で評価viewから除外しました。",
        "",
        "主方式の結果:",
        f"  accept/reject/review = {counts.get('accept', 0)}/{counts.get('reject', 0)}/{counts.get('review', 0)}",
        f"  既知参照で正しいaccept/reject = {primary.get('correct_accept_known')}/{primary.get('correct_reject_known')}",
        f"  既知参照で誤ったaccept/reject = {len(primary.get('wrong_accept_known_case_ids', []))}/{len(primary.get('wrong_reject_known_case_ids', []))}",
        f"  既知正例の正解候補の誤除外 = {primary.get('pruning', {}).get('matching_rows_marked_conflict')}/{primary.get('pruning', {}).get('known_matching_row_count')}",
        f"  一致あり参照{references.get('matched')}ケースのうち、正しいSKU行を確定したのは{primary.get('correct_accept_known')}ケース。残りは保留・未確定として扱う。",
        f"  参照unknownのままaccept/reject = {len(primary.get('accept_reference_unknown_case_ids', []))}/{len(primary.get('reject_reference_unknown_case_ids', []))}",
        "全方式: " + "; ".join(f"{name}: 正しいaccept/reject {variants[name].get('correct_accept_known')}/{variants[name].get('correct_reject_known')}, 誤り {len(variants[name].get('wrong_accept_known_case_ids', []))}/{len(variants[name].get('wrong_reject_known_case_ids', []))}" for name in VARIANTS),
        f"各方式のAI対象context推定（全体→残り）: " + "; ".join(f"{r['variant']} {r['all_contexts']}→{r['residual_contexts']}" for r in workload_rows),
        "context数は推定で、実際のAI呼び出し回数・token数・費用ではありません。production gateは変更していません。",
        "",
        "負荷試験:",
        f"  重複を含むreplayは{loadtest.get('actual_replayed_cases')}ケース / {loadtest.get('actual_replayed_rows')}行 / {loadtest.get('actual_replayed_condition_bindings')}条件binding、gate再組立{loadtest.get('gate_reassembly_wall_seconds')}秒、peak RSS {loadtest.get('process_peak_rss_bytes')} bytes。",
        f"  重複なしで巡回したDEV母集団は{benchmark.get('population', {}).get('cases')}ケースです。50k目標に届くため同一母集団を再巡回しており、重複は速度測定だけで精度データではありません。新しいテストの{native_cases}ケースとは別の数です。",
        f"  新母集団のnative full-run phase別wall time: {json.dumps(freeze.get('phase_timings_seconds', {}), ensure_ascii=False, sort_keys=True)}。peak RSS {max(freeze.get('peak_rss_bytes_by_phase', {}).values(), default=0)} bytes（約2GB）。生成処理のCPU時間は未計測。既存DEVでの生成参考値: {json.dumps(full, ensure_ascii=False, sort_keys=True)}。",
        "",
        "テスト: 関連focused tests 52件pass。必須venv suiteは1558件中fail 4 / error 66 / skip 60。旧system Python実行は1570件中fail 4 / error 46 / skip 116で、networkx等の環境依存import失敗を含みます。ログは両方同梱します。",
        "",
        f"再現ベースはrepository commit ebf04ae と axis-domains v2 ZIP SHA256 {AXIS_V2_SHA256}（大きなZIP自体は重複収録しません）。凍結済みprepared inputsは収録し、同じ内容を含むunmodified preparer staging snapshotは省略します。grade再現にはbundle外のprivate reference archiveが必要です。",
        f"Source audit: {audit.get('historical_full_test', {}).get('eligibility_note', '')}",
        "原ケース行は保存済み入力から複写し、商品コンテキストは既存抽出器でraw captureから再構築した。元productsファイル全体とはSHAが一致していない。旧DEV107商品の行・軸・値・引用・原本SHAは、参照パス区切りだけを比較用に正規化すると全107商品で一致した。値や引用そのものは正規化していない。",
    ]
    return result, "\n".join(lines) + "\n"


def export(lab: Path, run_name: str, prefix: Path) -> None:
    lab = lab.resolve()
    run = (lab / run_name).resolve()
    if not lab.is_dir() or not run.is_dir():
        raise FileNotFoundError("LAB or run directory is missing")
    plan_path = lab / "EXPERIMENT_PLAN.json"
    audit_path = lab / "source-audit-final.json" if (lab / "source-audit-final.json").exists() else lab / "source-audit.json"
    dev_path, benchmark_path = lab / "DEV_COMPATIBILITY_VERIFIED.json", lab / "gate-benchmark-v1.log"
    benchmark_json = ROOT / ".lab-output/sku-library-volume-benchmark-20261015-v1/benchmark.json"
    plan, audit, dev = read_json(plan_path), read_json(audit_path), read_json(dev_path)
    freeze = read_json(run / "RUN_FREEZE.json")
    grade = read_json(run / "grade.json")
    grader_path = ROOT / "experiments/sku-matching/grade_library_volume_v1.py"
    for field, expected in (("prediction_freeze_sha256", sha(run / "RUN_FREEZE.json")),
                            ("plan_sha256", sha(plan_path)),
                            ("grader_code_sha256", sha(grader_path))):
        if grade.get(field) != expected:
            raise ValueError(f"grade provenance mismatch: {field}")
    if (grade.get("independent_test") is not False
            or grade.get("production_adoption_allowed") is not False
            or grade.get("actual_AI_calls_tokens_or_cost_measured") is not False
            or grade.get("rules_thresholds_or_dictionaries_changed_after_new_reference_read") is not False):
        raise ValueError("grade metadata violates the predeclared diagnostic protocol")
    if not benchmark_json.is_file():
        raise FileNotFoundError(f"benchmark JSON missing: {benchmark_json}")
    benchmark = read_json(benchmark_json)
    if (benchmark.get("accuracy_population") is not False
            or benchmark.get("duplicated_replays_are_not_new_examples") is not True
            or benchmark.get("labels_or_grade_read") is not False
            or benchmark.get("model_calls") != 0):
        raise ValueError("throughput benchmark metadata fails its label-blind scope guards")
    if not AXIS_V2_ZIP.is_file() or sha(AXIS_V2_ZIP) != AXIS_V2_SHA256:
        raise ValueError("frozen axis-domains v2 dependency archive missing or changed")
    if not AXIS_V2_ZIP.is_file() or sha(AXIS_V2_ZIP) != AXIS_V2_SHA256:
        raise ValueError("frozen axis-domains v2 dependency archive missing or changed")
    verified: dict[str, str] = {}
    verified[rel(plan_path)] = sha(plan_path)
    verified[rel(audit_path)] = sha(audit_path)
    verified[rel(dev_path)] = sha(dev_path)
    verified[rel(run / "grade.json")] = sha(run / "grade.json")
    verified_freeze = verify_freeze(run, plan, verified)
    if verified_freeze != freeze:
        raise AssertionError("freeze changed during export validation")
    result, report = format_report(lab, run, plan, freeze, grade, benchmark, audit, dev)

    files: set[Path] = set()
    # Frozen run outputs and all new-run preparation evidence, with explicit
    # private-file/model/venv exclusions in safe_source().
    collect_tree(run, files)
    collect_tree(lab, files)
    collect_tree(benchmark_json.parent, files)
    for path in (ROOT / "experiments/sku-matching/prepare_library_volume_v1.py",
                 ROOT / "experiments/sku-matching/run_library_volume_v1.py",
                 ROOT / "experiments/sku-matching/grade_library_volume_v1.py",
                 ROOT / "experiments/sku-matching/benchmark_library_volume_v1.py",
                 ROOT / "experiments/sku-matching/restore_volume_source_checkpoint_v1.py",
                 ROOT / "experiments/sku-matching/restore_library_volume_source_v1.py",
                 ROOT / "experiments/sku-matching/export_library_volume_v1.py",
                 ROOT / "experiments/sku-matching/installed_library_views_v1.py",
                 ROOT / "experiments/sku-matching/prepare_library_price_free_views_v1.py",
                 ROOT / "experiments/sku-matching/requirements-axis-domains-v1.txt",
                 ROOT / "experiments/sku-matching/requirements-classical-v1.txt"):
        if path.is_file(): files.add(path.resolve())
    # Include focused tests when present, without pulling unrelated test suites.
    for path in (ROOT / "tests/test_lab_sku_library_volume.py",
                 ROOT / "tests/test_lab_sku_axis_domains.py",
                 ROOT / "tests/test_lab_sku_library_components.py",
                 ROOT / "tests/test_lab_sku_installed_libraries.py"):
        if path.is_file(): files.add(path.resolve())
    # The dependencies frozen by the run include the exact policy methods.
    for name in freeze.get("code_pins", {}):
        p = ROOT / "experiments/sku-matching" / name
        if p.is_file(): files.add(p.resolve())
    # Add current run's explicit input and output paths after pin checks.
    for path_text in verified:
        p = ROOT / path_text
        if p.is_file() and safe_source(p): files.add(p.resolve())

    prefix = prefix if prefix.is_absolute() else ROOT / prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {"txt": prefix.with_suffix(".txt"), "json": prefix.with_suffix(".json"),
             "zip": prefix.with_suffix(".zip"), "sha256": prefix.with_suffix(".zip.sha256"),
             "inventory": prefix.with_suffix(".inventory.json")}
    if any(p.exists() for p in paths.values()):
        raise FileExistsError("one or more versioned export outputs already exist")
    if not AXIS_V2_ZIP.is_file() or sha(AXIS_V2_ZIP) != AXIS_V2_SHA256:
        raise ValueError("axis-domains v2 dependency archive changed before export")
    readme = prefix.with_name(prefix.name + ".README.txt")
    readme_text = (
        "再現手順\n"
        "1. Repository base commit ebf04ae を用意する。\n"
        f"2. experiments/sku-matching/results/20261015-sku-library-axis-domains-v2.zip のSHA256が {AXIS_V2_SHA256} と一致することを確認する（このbundleには重複収録しない）。\n"
        "3. ZIP内の凍結prepared inputsとsourceを使い、prepare_library_volume_v1.py / run_library_volume_v1.py に記録されたCLIで予測runを再現する。\n"
        "   既存の専用venvを使う場合（なければPython3.12以上のvenvにrequirements-axis-domains-v1.txtを導入する）:\n"
        "   .deps/sku-classical-venv/bin/python -B experiments/sku-matching/run_library_volume_v1.py --source .lab-output/sku-library-volume-20261015-v1/prepared-test-v1/population-inputs --original .lab-output/sku-library-volume-20261015-v1/source-test-v2/inputs --views .lab-output/sku-library-volume-20261015-v1/prepared-test-v1/views/views-price-free.json --output .lab-output/sku-library-volume-20261015-v1/replay-new\n"
        "   --outputは存在しない保存先にする。入力だけ確認する場合は--preflight-onlyを付ける。\n"
        "4. grade_library_volume_v1.py の再実行には別途private reference archiveが必要で、このbundleには含めない。\n"
        "5. 環境と依存versionはRUN_FREEZE.json、requirements、テストログを参照する。モデル呼び出しは不要。\n"
    )
    with readme.open("x", encoding="utf-8") as f:
        f.write(readme_text)
    files.add(readme.resolve())
    inventory = {"schema": "sku-library-volume-export-inventory-v1",
        "members": {}, "verified_frozen_files": verified,
        "excluded_private_reference_archives": True,
        "zip_excludes_itself_and_external_inventory": True,
        "omitted_duplicate_snapshots": ["unmodified-preparer-staging/**"],
        "required_external_base": {"repository_commit": "ebf04ae",
            "axis_domains_v2_archive_sha256": AXIS_V2_SHA256, "included": False}}
    # Report and JSON are staged with exclusive creation before ZIP creation;
    # on any failure, partial outputs remain for inspection and are not replaced.
    with paths["txt"].open("x", encoding="utf-8") as f: f.write(report)
    with paths["json"].open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False); f.write("\n")
    files.update({paths["txt"].resolve(), paths["json"].resolve()})
    for p in sorted(files, key=lambda q: rel(q)):
        if not safe_source(p) and p not in {paths["txt"].resolve(), paths["json"].resolve()}:
            continue
        inventory["members"][rel(p)] = {"bytes": p.stat().st_size, "sha256": sha(p)}
    inventory["members"]["EXPORT_INVENTORY.json"] = {"self_excluded": True}
    with paths["zip"].open("xb") as raw:
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for p in sorted(files, key=lambda q: rel(q)):
                if safe_source(p) or p in {paths["txt"].resolve(), paths["json"].resolve()}:
                    zf.write(p, rel(p))
            zf.writestr("EXPORT_INVENTORY.json", json.dumps(inventory, ensure_ascii=False,
                sort_keys=True, indent=2) + "\n")
    # Verify every ZIP member by streaming from the archive and comparing bytes
    # and SHA256 against the source inventory.
    with zipfile.ZipFile(paths["zip"], "r") as zf:
        names = set(zf.namelist())
        expected_names = set(inventory["members"])
        if names != expected_names:
            raise ValueError("ZIP member set differs from inventory")
        for name, meta in inventory["members"].items():
            if name == "EXPORT_INVENTORY.json":
                continue
            h, size = hashlib.sha256(), 0
            with zf.open(name) as src:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    h.update(block); size += len(block)
            if size != meta["bytes"] or h.hexdigest() != meta["sha256"]:
                raise ValueError(f"ZIP member verification failed: {name}")
    zip_sha = sha(paths["zip"])
    with paths["sha256"].open("x", encoding="ascii") as f: f.write(f"{zip_sha}  {paths['zip'].name}\n")
    with paths["inventory"].open("x", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False, sort_keys=True, indent=2); f.write("\n")
    print(json.dumps({"zip": str(paths["zip"]), "zip_sha256": zip_sha,
        "zip_bytes": paths["zip"].stat().st_size, "member_count": len(inventory["members"]),
        "primary_metrics": result["primary_metrics"]}, ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, required=True)
    parser.add_argument("--run", default="test-run-v1")
    parser.add_argument("--output", type=Path, required=True, help="output path prefix without extension")
    args = parser.parse_args()
    export(args.lab, args.run, args.output)


if __name__ == "__main__":
    main()
