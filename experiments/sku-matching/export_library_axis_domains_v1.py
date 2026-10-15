#!/usr/bin/env python3
"""Verify and package the frozen library axis-domain experiment."""
from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/sku-matching"
LAB = ROOT / ".lab-output/sku-library-axis-domains-20261015-v1"
COMP = ROOT / ".lab-output/sku-library-components-20261015-v1/run-v3"
BASE = ROOT / ".lab-output/sku-classical-parser-20261015-v1"
OLD = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1"
RESULT = EXP / "results/20261015-sku-library-axis-domains-v1"
HANDOFF = EXP / "session-handoff-20261015-axis-domains-final.txt"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, data: str) -> None:
    with path.open("x", encoding="utf-8") as f:
        f.write(data)


def pin(path: Path, value: str, checked: dict[str, str]) -> None:
    actual = sha(path)
    if actual != value:
        raise ValueError(f"Frozen content changed: {path}")
    checked[str(path.relative_to(ROOT))] = actual


def verify(grade: dict) -> dict[str, str]:
    checked: dict[str, str] = {}
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    scope = read(LAB / "CURRENT_SCOPE_CONFIRMATION.json")
    if (branch, head) != (scope["branch"], scope["head"]):
        raise ValueError("Worktree branch/HEAD do not match recorded scope")
    if scope["PR_or_remote_ref_fetched_this_task"] or scope["other_branch_file_contents_read_this_task"]:
        raise ValueError("Scope confirmation records prohibited remote/branch access")
    run = LAB / "run-v1"
    freeze = read(run / "OUTPUT_FREEZE.json")
    for name, digest in freeze["input_pins"].items():
        pin(ROOT / name, digest, checked)
    for name, digest in freeze["output_pins"].items():
        pin(run / name, digest, checked)
    for name, digest in freeze["code_pins"].items():
        pin(EXP / name, digest, checked)
    pin(run / "OUTPUT_FREEZE.json", grade["prediction_freeze_sha256"], checked)
    pin(EXP / "grade_library_axis_domains_v1.py", grade["grader_code_sha256"], checked)
    queue = LAB / "residual-queue-v1"
    qmanifest = read(queue / "MANIFEST.json")
    for name, digest in qmanifest["input_pins"].items():
        pin(ROOT / name, digest, checked)
    pin(EXP / "build_axis_domain_residual_queue_v1.py", qmanifest["builder_code_sha256"], checked)
    for name, digest in qmanifest["output_pins"].items():
        pin(queue / name, digest, checked)
    oldfreeze = read(COMP / "OUTPUT_FREEZE.json")
    for name, digest in oldfreeze["input_pins"].items():
        pin(ROOT / name, digest, checked)
    for name, digest in oldfreeze["output_pins"].items():
        pin(COMP / name, digest, checked)
    for name, digest in oldfreeze["code_pins"].items():
        source = COMP / "executed-code-v2" / name if name == "run_library_components_v1.py" and digest != sha(EXP / name) else EXP / name
        pin(source, digest, checked)
    oldgrade = read(COMP / "grade.json")
    pin(COMP / "OUTPUT_FREEZE.json", oldgrade["prediction_freeze_sha256"], checked)
    pin(EXP / "grade_library_components_v1.py", oldgrade["grader_code_sha256"], checked)
    return checked


def main() -> None:
    if any(p.exists() for p in (RESULT.with_suffix(".json"), RESULT.with_suffix(".txt"),
                                RESULT.with_suffix(".zip"), RESULT.with_suffix(".zip.sha256"), HANDOFF)):
        raise FileExistsError("Refusing to overwrite an existing export")
    run = LAB / "run-v1"
    grade, freeze = read(run / "grade.json"), read(run / "OUTPUT_FREEZE.json")
    concentration = read(run / "GAIN_CONCENTRATION.json")
    scope = read(LAB / "CURRENT_SCOPE_CONFIRMATION.json")
    scope_branch = scope["branch"]
    state_integrity = read(LAB / "STATE_AND_QUEUE_INTEGRITY.json")
    if not state_integrity["mutual_vs_forced_predictions_equal_for_all_predeclared_pairs"]:
        raise ValueError("Frozen mutual-unique/forced-maximum comparison is not confirmed")
    if (state_integrity["all_original_queue_rows"], state_integrity["all_original_queue_bindings"]) != (32325, 132136):
        raise ValueError("Frozen queue integrity totals changed")
    verified = verify(grade)
    v = grade["variants"]
    primary, prior, baseline = (v[k] for k in ("mutual_unique_raw_components", "previous_components", "baseline_raw"))
    spacy = v["mutual_unique_spacy_components"]
    allctx = primary["workload"]["all_contexts"]
    rawctx = baseline["workload"]["residual_contexts"]
    priorctx = prior["workload"]["residual_contexts"]
    after = primary["workload"]["residual_contexts"]
    pct = lambda old, new: 100 * (old - new) / old
    gain = concentration["gain_from_previous_correct_accepts"]
    largest = concentration["largest_product_gain"]
    top2 = concentration["two_largest_product_share"] * 100
    primary_unknown_accept = len(primary["accept_reference_unknown_case_ids"])
    primary_unknown_reject = len(primary["reject_reference_unknown_case_ids"])
    summary = {
        "schema": "sku-library-axis-domains-result-v1", "date": "2026-10-15",
        "branch": scope_branch,
        "base_commit": "ebf04aed9b5a747f5db8961b9c96fef7be81adef",
        "primary_variant": "mutual_unique_raw_components",
        "reference_kind": grade["reference_kind"], "independent_test": False,
        "production_adoption_allowed": False, "actual_ai_tokens_cost_or_calls_measured": False,
        "prediction_counts": freeze["counts"], "primary_grade": primary,
        "comparison": {"pre_component_exact_baseline_known_correct_accepts": baseline["correct_accept_known"],
                       "previous_component_known_correct_accepts": prior["correct_accept_known"],
                       "primary_known_correct_accepts": primary["correct_accept_known"],
                       "primary_known_correct_rejects": primary["correct_explicit_reject_known"],
                       "previous_known_correct_rejects": prior["correct_explicit_reject_known"],
                       "known_wrong_accepts": len(primary["wrong_accept_known_case_ids"]),
                       "known_wrong_rejects": len(primary["wrong_explicit_reject_known_case_ids"]),
                       "known_positive_rows": primary["pruning"]["known_matching_row_count"],
                       "known_positive_rows_marked_conflict": primary["pruning"]["matching_rows_marked_conflict"],
                       "all_accepts": primary["counts"]["cases"]["accept"],
                       "accepts_reference_unknown": primary_unknown_accept,
                       "all_rejects": primary["counts"]["cases"]["reject"],
                       "rejects_reference_unknown": primary_unknown_reject,
                       "review": primary["counts"]["cases"]["review"],
                       "additional_known_correct_accepts_vs_previous": gain,
                       "additional_known_correct_rejects_vs_previous": primary["correct_explicit_reject_known"] - prior["correct_explicit_reject_known"],
                       "new_unverified_accepts_vs_previous": primary_unknown_accept - len(prior["accept_reference_unknown_case_ids"])},
        "eligible_context_estimates": {"prior_components": priorctx, "pre_component_raw": rawctx,
                                       "all_contexts": allctx, "primary_residual": after,
                                       "reduction_vs_prior_components_percent": pct(priorctx, after),
                                       "reduction_vs_pre_component_raw_percent": pct(rawctx, after),
                                       "reduction_vs_all_contexts_percent": pct(allctx, after),
                                       "actual_calls_tokens_or_cost_measured": False},
        "spacy_component_overlay": {"known_correct_accepts": spacy["correct_accept_known"],
                                    "gain_products": spacy["gain_product_count"],
                                    "residual_contexts": spacy["workload"]["residual_contexts"]},
        "gain_concentration": {"products": concentration["gain_product_count"], "largest_product_gain": largest,
                               "largest_product_share_percent": concentration["largest_product_share"] * 100,
                               "two_largest_product_share_percent": top2,
                               "independent_product_generalization_proved": False},
        "state_and_queue_integrity": state_integrity,
        "method": {"library": "NetworkX 3.5", "input_finite_option_index": True,
                   "manual_alias_dictionary": False, "human_registration": False,
                   "main_acceptance": "exact shared values; reciprocal-degree-one component mapping",
                   "one_to_one_assumption_semantic_proof": False,
                   "extra_axes_unmatched_or_unknown_conditions_dropped": False,
                   "all_original_condition_bindings": freeze["counts"]["request_observation_count"],
                   "model_calls": 0, "models_loaded": [], "cpu_seconds": freeze["elapsed_seconds"],
                   "input_load_separate_from_reported_runtime": True},
        "test_results": {"focused_tests_passed": 67, "focused_tests_failed": 0,
                         "required_suite_total": 1554, "failures": 4, "errors": 66, "skips": 60,
                         "overall_required_suite_passed": False},
        "provenance": {"scope_confirmation": scope,
                       "integrity_verified_files": verified,
                       "private_original_dev_reference_archive_included": False,
                       "sealed_test_labels_used_or_included": False},
    }
    write_new(RESULT.with_suffix(".json"), json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    report = f"""商品選択肢から属性軸を機械照合する実験（2026-10-15）

対象は既探索DEVの1,370ケース・107商品。商品内の元選択肢から有限の値索引を自動生成し、NetworkX 3.5で同じ値が共有される軸の成分を作った。主方式はraw文字列で、2つ以上の共有値と相互に一意な成分対応がある場合だけ候補を使う。手書きalias辞書、人の項目登録、SKUモデル推論はない。文字列上の対応であり、軸の意味が同一だとは証明していない。

Claude Haiku 5.5による機械注釈との照合（人手goldでも独立TESTでもない）：

方式                       既知正解accept  既知正解reject  誤accept/reject  既知正例行の誤prune
従来の完全一致ガード              {baseline['correct_accept_known']}              {baseline['correct_explicit_reject_known']}             0 / 0              0 / 904
従来コンポーネント重ね合わせ      {prior['correct_accept_known']}              {prior['correct_explicit_reject_known']}             0 / 0              0 / 904
今回 raw 軸成分（主方式）          {primary['correct_accept_known']}              {primary['correct_explicit_reject_known']}             0 / 0              0 / 904

主方式のacceptは合計478件で、うち20件は参照不明。rejectは83件で、うち24件が参照不明。809件はreviewに残した。前回コンポーネント方式から既知正解acceptが108件、既知正解rejectが1件増えた一方、新たにaccept扱いとなった参照不明が10件ある。

追加108件は11商品に集中し、最多の1商品が56件（51.85%）、上位2商品で72.22%を占める。商品をまたぐ独立した汎化の証拠ではない。spaCy正規化 viewの診断 overlayは既知正解accept 462件、14商品、残り1,312文脈だった。raw/spaCyの4つのmutual-unique対forced-maximum比較で最終state投影はすべて一致した。より広い1対1対応の仮定による追加改善は見られない。

未解決AI候補文脈の推定値は、従来コンポーネント方式1,926から1,320へ31.46%減、前コンポーネントraw方式2,088から1,320へ36.78%減。全4,778文脈を分母にすれば72.37%減。ただし、これは候補文脈数の試算であり、実際のAI呼出し・token・料金削減ではない。全1,370件のqueue case、元候補32,325行、132,136方向付き条件を保持した。未対応軸やunknownは除去していない。本番gateは変更していない。

NetworkX 3.5を使ったCPU実行は約{freeze['elapsed_seconds']:.2f}秒（入力読込は別計測）。モデル・新規Kaggle jobはなし。67件の関連focused testは成功。必須suiteは1,554件中4 failures、66 errors、60 skipsで、全体成功ではない。

現在のworktree branch {scope_branch} / HEAD ebf04ae。結果は凍結出力から集計し、gradeや予測を編集していない。zipには今回のLAB一式、必要なraw構造入力、price-free baseline、前回frozen component run、再実行用コードとテストを含む。元のDEV参照archiveは含めず、graderを再実行する場合は既存の私有参照archiveが必要。封印TESTの参照は使用・収録していない。
"""
    write_new(RESULT.with_suffix(".txt"), report)
    handoff = f"""branch codex-sku-generic-model-gates-20261010 / HEAD ebf04aed9b5a747f5db8961b9c96fef7be81adef
最新結果: experiments/sku-matching/results/20261015-sku-library-axis-domains-v1.zip
主方式 mutual_unique_raw_components: known correct accept 458, reject 59; previous component 350/58; exact baseline 296/58.
主方式 all accept 478 (20 ref unknown), reject 83 (24 ref unknown), review 809; known wrong 0; known positive rows falsely pruned 0/904.
Eligible context estimate 1,926 -> 1,320 (-31.46%); all-context denominator 4,778 -> 1,320 (-72.37%). Not measured AI calls/tokens/cost; production gate unchanged.
Gain +108 known correct accepts across 11 products; largest product 56/108=51.85%, top two 72.22%; independent product generalization unproved.
Four mutual-unique vs forced-maximum state projections match; all 1,370 queue cases, 32,325 rows, and 132,136 directional bindings retained.
NetworkX 3.5 finite option index; no hand alias dictionary/human item registration/model inference. Exact component overlap is not semantic identity.
All 132,136 condition bindings retained; unknown/residual conditions held. Forced maximum matched primary aggregates. spaCy diagnostic 462 known accepts/14 products/1,312 contexts.
67 focused tests pass; required suite 1,554 total, 4 failures/66 errors/60 skipped. Logs are in new LAB tree.
Reproduce on base ebf04ae with requirements-axis-domains-v1.txt and requirements-classical-v1.txt. Grade re-run needs private DEV reference archive not in ZIP.
ZIP excludes private original reference archives and sealed TEST labels. No production change, model call, or new Kaggle job.
"""
    write_new(HANDOFF, handoff)

    files = {p for p in LAB.rglob("*") if p.is_file()}
    files |= {BASE / "population-inputs" / n for n in
              ("case-plans.jsonl", "documents.jsonl", "manifest.json", "requests.jsonl", "row-plans.jsonl", "tasks.jsonl")}
    files |= {BASE / "source-dev/population/run/inputs" / n for n in ("cases.jsonl", "products.jsonl")}
    files |= {OLD / "price-free/selection-v1" / n for n in
              ("OUTPUT_FREEZE.json", "raw-cases.jsonl", "raw-requests.jsonl", "raw-rows.jsonl")}
    files |= {OLD / n for n in ("views-price-free.json", "price-exclusion-audit.json")}
    files |= {p for p in COMP.iterdir() if p.is_file()}
    source_names = ("library_axis_domain_alignment_v1.py", "run_library_axis_domains_v1.py",
        "grade_library_axis_domains_v1.py", "build_axis_domain_residual_queue_v1.py", "export_library_axis_domains_v1.py",
        "library_local_phrase_components_v1.py", "run_library_components_v1.py", "grade_library_components_v1.py",
        "probe_library_residual_text_v1.py", "run_installed_library_selection_v1.py", "replay_dev_nli_policies_v1.py",
        "grade_final_test_subset_v1.py", "grade_fresh_dev_sku_gate_v1.py", "grade_installed_libraries_v1.py",
        "installed_library_views_v1.py", "prepare_library_price_free_views_v1.py", "restore_session_artifacts_20261014.py",
        "trial_generic_model_nli_v1.py", "trial_scoped_condition_nli_choice_v1.py",
        "requirements-axis-domains-v1.txt", "requirements-classical-v1.txt")
    files |= {EXP / n for n in source_names}
    files |= {ROOT / "tests" / n for n in ("test_lab_sku_axis_domains.py", "test_lab_sku_library_components.py",
                                            "test_lab_sku_installed_libraries.py")}
    files |= {HANDOFF, RESULT.with_suffix(".json"), RESULT.with_suffix(".txt")}
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    inventory = {"schema": "sku-library-axis-domains-export-inventory-v1",
        "base_repository_commit_required": summary["base_commit"], "sealed_test_reference_included": False,
        "private_reference_archives_included": False,
        "files": {str(p.relative_to(ROOT)): {"bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(files)}}
    inventory_path = LAB / "EXPORT_INVENTORY.json"
    write_new(inventory_path, json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    files.add(inventory_path)
    archive = RESULT.with_suffix(".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in sorted(files):
            z.write(p, p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as z:
        for name, record in inventory["files"].items():
            h, size = hashlib.sha256(), 0
            with z.open(name) as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
                    size += len(block)
            if (h.hexdigest(), size) != (record["sha256"], record["bytes"]):
                raise ValueError(f"ZIP content verification failed: {name}")
    archive_sha = sha(archive)
    write_new(archive.with_suffix(".zip.sha256"), f"{archive_sha}  {archive.name}\n")
    print(json.dumps({"report": str(RESULT.with_suffix('.txt').relative_to(ROOT)),
        "json": str(RESULT.with_suffix('.json').relative_to(ROOT)), "archive": str(archive.relative_to(ROOT)),
        "archive_bytes": archive.stat().st_size, "archive_sha256": archive_sha,
        "inventory_file_count": len(inventory["files"]), "zip_members": len(files),
        "known_correct_accepts": primary["correct_accept_known"], "correct_rejects": primary["correct_explicit_reject_known"],
        "residual_contexts": after, "zip_hashes_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
