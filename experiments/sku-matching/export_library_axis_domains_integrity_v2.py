#!/usr/bin/env python3
"""Create an integrity-checked v2 export without modifying v1 artifacts."""
from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/sku-matching"
LAB = ROOT / ".lab-output/sku-library-axis-domains-20261015-v1"
AXRUN = LAB / "run-v1"
COMP = ROOT / ".lab-output/sku-library-components-20261015-v1/run-v3"
OLD = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1"
BASE = ROOT / ".lab-output/sku-classical-parser-20261015-v1"
BASELINE = OLD / "price-free/selection-v1"
RESULT = EXP / "results/20261015-sku-library-axis-domains-v2"
HANDOFF = EXP / "session-handoff-20261015-axis-domains-v2-final.txt"
INVENTORY = LAB / "EXPORT_INVENTORY_V2.json"
CLOSURE = LAB / "REPRODUCTION_INPUT_CLOSURE_V2.json"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as f:
        f.write(text)


def verify_pin(path: Path, expected: str, checked: dict[str, dict]) -> None:
    actual = sha(path)
    if actual != expected:
        raise ValueError(f"Frozen hash mismatch: {path}")
    key = str(path.relative_to(ROOT))
    record = checked.setdefault(key, {"path": key, "bytes": path.stat().st_size,
                                      "sha256": actual, "required_by": []})
    if record["sha256"] != expected:
        raise ValueError(f"Conflicting dependency pins for {path}")


def require(path: Path, expected: str, label: str, checked: dict[str, dict]) -> None:
    verify_pin(path, expected, checked)
    key = str(path.relative_to(ROOT))
    if label not in checked[key]["required_by"]:
        checked[key]["required_by"].append(label)


def main() -> None:
    outputs = [RESULT.with_suffix(".txt"), RESULT.with_suffix(".json"),
               RESULT.with_suffix(".zip"), RESULT.with_suffix(".zip.sha256"), HANDOFF, INVENTORY, CLOSURE]
    if any(p.exists() for p in outputs):
        raise FileExistsError("v2 export path already exists; refusing overwrite")
    scope = read(LAB / "CURRENT_SCOPE_CONFIRMATION.json")
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if (branch, head) != (scope["branch"], scope["head"]):
        raise ValueError("Worktree differs from frozen scope confirmation")
    if scope["PR_or_remote_ref_fetched_this_task"] or scope["other_branch_file_contents_read_this_task"]:
        raise ValueError("Scope confirmation does not permit remote/other-branch reads")

    axfreeze_path = AXRUN / "OUTPUT_FREEZE.json"
    cfreeze_path = COMP / "OUTPUT_FREEZE.json"
    bfreeze_path = BASELINE / "OUTPUT_FREEZE.json"
    axfreeze, cfreeze, bfreeze = read(axfreeze_path), read(cfreeze_path), read(bfreeze_path)
    agrad, cgrad = read(AXRUN / "grade.json"), read(COMP / "grade.json")
    state = read(LAB / "STATE_AND_QUEUE_INTEGRITY.json")
    cold_base = read(LAB / "COLD_REPRODUCTION_BASE_V2.json")
    queue_manifest = read(LAB / "residual-queue-v1/MANIFEST.json")
    if not state["mutual_vs_forced_predictions_equal_for_all_predeclared_pairs"]:
        raise ValueError("State projection equivalence is not frozen true")
    if (state["all_original_queue_rows"], state["all_original_queue_bindings"]) != (32325, 132136):
        raise ValueError("Queue integrity totals changed")
    if cold_base["base_head"] != head or cold_base["tracked_python_files"] != 592 or cold_base["remote_or_other_branch_access"]:
        raise ValueError("Cold reproduction base does not match this current HEAD")
    checked: dict[str, dict] = {}

    # Current axis-domain run: every frozen input, output, and exact current source pin.
    for name, digest in axfreeze["input_pins"].items():
        require(ROOT / name, digest, "axis_run.input", checked)
    for name, digest in axfreeze["output_pins"].items():
        require(AXRUN / name, digest, "axis_run.output", checked)
    for name, digest in axfreeze["code_pins"].items():
        require(EXP / name, digest, "axis_run.code", checked)
    require(axfreeze_path, agrad["prediction_freeze_sha256"], "axis_run.grade.freeze", checked)
    require(EXP / "grade_library_axis_domains_v1.py", agrad["grader_code_sha256"], "axis_run.grade.code", checked)

    # The component overlay's complete frozen dependency closure.
    for name, digest in cfreeze["input_pins"].items():
        require(ROOT / name, digest, "component_run.input", checked)
    for name, digest in cfreeze["output_pins"].items():
        require(COMP / name, digest, "component_run.output", checked)
    for name, digest in cfreeze["code_pins"].items():
        require(EXP / name, digest, "component_run.code", checked)
    require(cfreeze_path, cgrad["prediction_freeze_sha256"], "component_run.grade.freeze", checked)
    require(EXP / "grade_library_components_v1.py", cgrad["grader_code_sha256"], "component_run.grade.code", checked)

    # Price-free baseline: resolve its basename pins to recorded source/output dirs and
    # verify both code hashes. Do not fall back to an execution snapshot silently.
    bsource = Path(bfreeze["source_path"])
    if not bsource.is_absolute():
        bsource = ROOT / bsource
    for name, digest in bfreeze["input_pins"].items():
        require(bsource / name, digest, "baseline.input", checked)
    for name, digest in bfreeze["output_pins"].items():
        require(BASELINE / name, digest, "baseline.output", checked)
    for code, digest in (("run_installed_library_selection_v1.py", bfreeze["code_sha256"]),
                         ("replay_dev_nli_policies_v1.py", bfreeze["gate_code_sha256"])):
        require(EXP / code, digest, "baseline.code", checked)

    # Queue builder and the added integrity artifact are independently pinned here.
    for name, digest in queue_manifest["input_pins"].items():
        require(ROOT / name, digest, "residual_queue.input", checked)
    require(EXP / "build_axis_domain_residual_queue_v1.py", queue_manifest["builder_code_sha256"],
            "residual_queue.code", checked)
    queue_dir = LAB / "residual-queue-v1"
    for name, digest in queue_manifest["output_pins"].items():
        require(queue_dir / name, digest, "residual_queue.output", checked)

    # The report archive contains all three frozen pin closures plus source and test
    # files needed to inspect/replay them. Private reference archives are excluded.
    files = {p for p in LAB.rglob("*") if p.is_file()}
    files |= {p for p in BASELINE.iterdir() if p.is_file()}  # all direct frozen baseline views
    files |= {OLD / n for n in ("views-price-free.json", "price-exclusion-audit.json")}
    files |= {p for p in COMP.iterdir() if p.is_file()}
    files |= {ROOT / name for name in checked}  # all inputs/outputs/code from each frozen pin set
    extra_sources = (
        "library_axis_domain_alignment_v1.py", "run_library_axis_domains_v1.py", "grade_library_axis_domains_v1.py",
        "build_axis_domain_residual_queue_v1.py", "export_library_axis_domains_v1.py",
        "export_library_axis_domains_integrity_v2.py", "library_local_phrase_components_v1.py",
        "run_library_components_v1.py", "grade_library_components_v1.py", "probe_library_residual_text_v1.py",
        "run_installed_library_selection_v1.py", "replay_dev_nli_policies_v1.py", "grade_final_test_subset_v1.py",
        "grade_fresh_dev_sku_gate_v1.py", "grade_installed_libraries_v1.py", "installed_library_views_v1.py",
        "prepare_library_price_free_views_v1.py", "restore_session_artifacts_20261014.py",
        "trial_generic_model_nli_v1.py", "trial_scoped_condition_nli_choice_v1.py",
        "requirements-axis-domains-v1.txt", "requirements-classical-v1.txt")
    files |= {EXP / n for n in extra_sources}
    files |= {ROOT / "tests" / n for n in ("test_lab_sku_axis_domains.py", "test_lab_sku_library_components.py",
                                            "test_lab_sku_installed_libraries.py")}
    generated_members = {HANDOFF, RESULT.with_suffix(".json"), RESULT.with_suffix(".txt"), CLOSURE}
    files |= generated_members
    for p in files:
        if p not in generated_members and not p.is_file():
            raise FileNotFoundError(p)

    closure = {"schema": "sku-axis-domains-reproduction-input-closure-v2",
        "base_commit_required": scope["head"], "branch": branch,
        "frozen_sources": {"axis_domain": str(axfreeze_path.relative_to(ROOT)),
                           "component_overlay": str(cfreeze_path.relative_to(ROOT)),
                           "price_free_baseline": str(bfreeze_path.relative_to(ROOT)),
                           "cold_reproduction_base": ".lab-output/sku-library-axis-domains-20261015-v1/COLD_REPRODUCTION_BASE_V2.json"},
        "private_grading_reference_archives_included": False,
        "private_grading_reference_archives_required_to_regrade": True,
        "sealed_test_labels_included_or_used": False,
        "all_required_members_exist_and_hash_match": True,
        "pinned_files": checked}
    write_new(CLOSURE, json.dumps(closure, ensure_ascii=False, indent=2) + "\n")

    variants = agrad["variants"]
    primary, prior, raw, spacy = (variants[k] for k in ("mutual_unique_raw_components", "previous_components",
                                                        "baseline_raw", "mutual_unique_spacy_components"))
    concentration = read(AXRUN / "GAIN_CONCENTRATION.json")
    before, raw_before, allctx = (prior["workload"]["residual_contexts"], raw["workload"]["residual_contexts"],
                                  primary["workload"]["all_contexts"])
    after = primary["workload"]["residual_contexts"]
    rate = lambda a, b: 100 * (a - b) / a
    total_accepts, total_rejects = primary["counts"]["cases"]["accept"], primary["counts"]["cases"]["reject"]
    accept_unknown, reject_unknown = (len(primary["accept_reference_unknown_case_ids"]),
                                      len(primary["reject_reference_unknown_case_ids"]))
    summary = {"schema": "sku-library-axis-domains-result-v2", "date": "2026-10-15",
        "branch": branch, "base_commit": head, "primary_variant": "mutual_unique_raw_components",
        "reference_kind": agrad["reference_kind"], "independent_test": False,
        "production_adoption_allowed": False, "actual_ai_calls_tokens_cost_measured": False,
        "comparison": {"previous_component_known_correct_accepts": prior["correct_accept_known"],
            "previous_component_known_correct_rejects": prior["correct_explicit_reject_known"],
            "baseline_known_correct_accepts": raw["correct_accept_known"], "baseline_known_correct_rejects": raw["correct_explicit_reject_known"],
            "main_known_correct_accepts": primary["correct_accept_known"], "main_known_correct_rejects": primary["correct_explicit_reject_known"],
            "additional_known_correct_accepts": primary["correct_accept_known"] - prior["correct_accept_known"],
            "additional_known_correct_rejects": primary["correct_explicit_reject_known"] - prior["correct_explicit_reject_known"],
            "known_wrong_accepts": len(primary["wrong_accept_known_case_ids"]),
            "known_wrong_rejects": len(primary["wrong_explicit_reject_known_case_ids"]),
            "known_positive_rows": primary["pruning"]["known_matching_row_count"],
            "known_positive_rows_false_pruned": primary["pruning"]["matching_rows_marked_conflict"],
            "all_accepts": total_accepts, "reference_unknown_accepts": accept_unknown,
            "all_rejects": total_rejects, "reference_unknown_rejects": reject_unknown,
            "review": primary["counts"]["cases"]["review"],
            "new_reference_unknown_accepts_vs_previous": accept_unknown - len(prior["accept_reference_unknown_case_ids"])},
        "eligible_context_estimates_only": {"previous_components": before, "pre_component_raw": raw_before,
            "all_contexts": allctx, "main_residual": after,
            "reduction_vs_previous_components_percent": rate(before, after),
            "reduction_vs_pre_component_raw_percent": rate(raw_before, after),
            "reduction_vs_all_contexts_percent": rate(allctx, after),
            "actual_ai_calls_tokens_cost_measured": False},
        "concentration": {"products_with_known_accept_gain": concentration["gain_product_count"],
            "gain_cases": concentration["gain_from_previous_correct_accepts"],
            "largest_product_gain": concentration["largest_product_gain"],
            "largest_product_share_percent": concentration["largest_product_share"] * 100,
            "top_two_product_share_percent": concentration["two_largest_product_share"] * 100,
            "independent_product_generalization_proved": False},
        "spacy_overlay_diagnostic": {"known_correct_accepts": spacy["correct_accept_known"],
            "gain_products": spacy["gain_product_count"], "residual_contexts": spacy["workload"]["residual_contexts"]},
        "method": {"graph_role": "exact shared option values derive candidate axis correspondences",
            "compound_attribute_overlay_role": "separate local phrase component auxiliary evidence",
            "mapping_assumption": "reciprocal-degree-one mutual-unique on exact overlap; forced-maximum comparison is diagnostic",
            "mapping_is_semantic_identity_proof": False, "hand_alias_dictionary": False, "human_axis_registration": False,
            "networkx_version": axfreeze["networkx_version"], "models_loaded": [], "model_calls": 0,
            "elapsed_cpu_seconds": axfreeze["elapsed_seconds"], "input_read_time_separate": True,
            "all_original_cases": queue_manifest["case_count"], "all_original_rows": state["all_original_queue_rows"],
            "all_directional_condition_bindings": state["all_original_queue_bindings"],
            "mutual_unique_vs_forced_final_states_equal_all_four_pairs": state["mutual_vs_forced_predictions_equal_for_all_predeclared_pairs"],
            "unmatched_unknown_conditions_dropped": False},
        "tests": {"focused_passed": 67, "focused_failed": 0, "required_suite_total": 1554,
            "required_suite_failures": 4, "required_suite_errors": 66, "required_suite_skips": 60,
            "required_suite_passed": False},
        "dependency_closure_file": str(CLOSURE.relative_to(ROOT)),
        "cold_reproduction_base": cold_base,
        "private_reference_archives_in_zip": False}
    write_new(RESULT.with_suffix(".json"), json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    text = f"""商品選択肢から属性軸の対応候補を作る実験（2026-10-15）

対象は既探索DEVの1,370ケース・107商品。商品内の全選択肢から有限値索引を自動生成し、NetworkX 3.5で選択肢に共有される完全一致値を使って軸の対応候補を作る。主方式はraw文字列で、最低2つの共有値と相互に一意な対応がある場合だけ候補にした。人手でSKU別のalias辞書や軸登録はしていない。

このグラフの役割は商品側の軸の対応候補を得ること。前段の複合属性補助は、1つの選択値に含まれる複数の局所成分を扱う別の仕組みであり、グラフの軸対応や「1対1 matching」と同じ操作ではない。どちらも文字列構造の仮説で、軸の意味的同一性の証明ではない。

Claude Haiku 5.5の機械DEV注釈との照合（人手正解でも独立TESTでもない）：

方式                               既知正解accept  既知正解reject  誤accept/reject  既知正例の誤prune
従来の完全一致ガード                      {raw['correct_accept_known']}              {raw['correct_explicit_reject_known']}             0 / 0              0 / 904
前回の複合属性補助                         {prior['correct_accept_known']}              {prior['correct_explicit_reject_known']}             0 / 0              0 / 904
今回の軸対応候補＋複合属性補助（主方式）   {primary['correct_accept_known']}              {primary['correct_explicit_reject_known']}             0 / 0              0 / 904

主方式は合計{total_accepts}件をacceptし、うち{accept_unknown}件は参照不明。rejectは{total_rejects}件で、うち{reject_unknown}件が参照不明。{primary['counts']['cases']['review']}件はreviewに残した。前回方式から既知正解acceptは{primary['correct_accept_known']-prior['correct_accept_known']}件、既知正解rejectは{primary['correct_explicit_reject_known']-prior['correct_explicit_reject_known']}件増えた。追加acceptのうち参照不明も{accept_unknown-len(prior['accept_reference_unknown_case_ids'])}件増えている。

既知正解acceptの増加分108件は11商品に集中した。最多の1商品が56件（51.85%）、上位2商品で72.22%を占めるため、商品間の独立した汎化を示す結果ではない。spaCy正規化viewの診断overlayでは既知正解acceptが{spacy['correct_accept_known']}件、14商品、残余文脈1,312件だった。4つのraw/spaCy mutual-unique対forced-maximum比較で最終state投影は一致した。強い1対1対応仮定を緩めても、この評価では差がなかった。

残余AI候補文脈の推定値は前回方式1,926から今回1,320へ31.46%減。軸対応なしの前回raw方式2,088を基準にすると36.78%減、全4,778文脈を分母にすれば72.37%減。ただし実際のAI呼出数、token、料金は測定していない。全1,370件、32,325元行、132,136方向付き条件を保持し、未対応・unknown条件は除外していない。本番gateも変更していない。

CPU実行は約{axfreeze['elapsed_seconds']:.2f}秒（入力読込は別）。SKUモデル推論、新規Kaggle jobはなし。関連focused test 67件は成功。必須suiteは計1,554件中4 failures、66 errors、60 skipsで全体成功ではない。

出力は凍結済みDEV予測・gradeを編集せずに集計した。archiveには現在のrun、前回コンポーネントrun、price-free baselineの全frozen pin closure、3方式の関連ソース・テストを収録し、{CLOSURE.relative_to(ROOT)}に依存ファイル別hashを記録した。全direct baseline view variant（raw / width_jaconv / sudachi_norm / spacy_norm）を含む。現在HEADのPython 592ファイルのみを使ったcold reproduction baseの記録も同梱した。私有の元DEV参照archiveは再採点用のため含まず、封印TESTのラベルは使っていない。
"""
    write_new(RESULT.with_suffix(".txt"), text)
    handoff = f"""branch {branch} / HEAD {head}
v2 final export: experiments/sku-matching/results/20261015-sku-library-axis-domains-v2.zip
主方式は「選択肢からの軸対応候補＋複合属性補助」。既知correct accept/reject 458/59。前回350/58、raw baseline296/58。
Machine DEV annotation only, not human gold/independent TEST. Wrong known decisions 0; false pruning on known positive rows 0/904. Accept478 (20 reference unknown), reject83 (24 unknown), review809.
Gain108/11 products, largest56 (51.85%), top2 72.22%; independent product generalization not proved.
AI-eligible context estimates 1,926->1,320 (-31.46%); no actual calls/tokens/cost measured. Production gate unchanged.
All 1,370 cases/32,325 rows/132,136 directional bindings kept. Four mutual-unique/forced-maximum state projections match.
Dependency closure: .lab-output/sku-library-axis-domains-20261015-v1/REPRODUCTION_INPUT_CLOSURE_V2.json; inventory EXPORT_INVENTORY_V2.json.
Full direct price-free baseline variants and all baseline/current/component pins are included and hash-checked; private regrade archives excluded.
Focused 67 passed; required suite 1,554: 4 failures, 66 errors, 60 skips. No model inference, new Kaggle, production change.
"""
    write_new(HANDOFF, handoff)

    # Add deliverables to the package; do not include the archive or checksum recursively.
    files |= {HANDOFF, RESULT.with_suffix(".json"), RESULT.with_suffix(".txt"), CLOSURE}
    inventory_data = {"schema": "sku-axis-domains-export-inventory-v2",
        "base_commit_required": head, "private_grading_reference_archives_included": False,
        "sealed_test_labels_included": False,
        "files": {str(p.relative_to(ROOT)): {"bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(files)}}
    write_new(INVENTORY, json.dumps(inventory_data, ensure_ascii=False, indent=2) + "\n")
    files.add(INVENTORY)
    archive = RESULT.with_suffix(".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for p in sorted(files):
            zf.write(p, p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as zf:
        for name, record in inventory_data["files"].items():
            h, size = hashlib.sha256(), 0
            with zf.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block); size += len(block)
            if (h.hexdigest(), size) != (record["sha256"], record["bytes"]):
                raise ValueError(f"ZIP member mismatch: {name}")
    digest = sha(archive)
    write_new(archive.with_suffix(".zip.sha256"), f"{digest}  {archive.name}\n")
    print(json.dumps({"archive": str(archive.relative_to(ROOT)), "bytes": archive.stat().st_size,
        "sha256": digest, "inventory_members": len(inventory_data["files"]), "zip_members": len(files),
        "dependency_closure_files": len(checked), "closure_hashes_valid": True,
        "known_correct_accepts": primary["correct_accept_known"], "known_correct_rejects": primary["correct_explicit_reject_known"],
        "residual_contexts": after, "zip_hashes_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
