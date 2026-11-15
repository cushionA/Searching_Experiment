import argparse
import json
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def flatten(evaluation, source):
    rows = []
    for row in evaluation.get("rows", []):
        rows.append({**row, "source": source})
    return rows


def jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=Path(__file__).parent)
    parser.add_argument("--repair", type=Path)
    args = parser.parse_args()

    evaluations = [(args.run / "evaluation.json", "original")]
    if args.repair:
        repair_eval = args.repair / "evaluation.json"
        if repair_eval.exists():
            evaluations.append((repair_eval, "repair"))

    all_rows = []
    raw_rows = []
    for path, source in evaluations:
        if path.exists():
            all_rows.extend(flatten(read_json(path), source))
        raw_path = path.parent / "results.jsonl"
        raw_rows.extend({**row, "source": source} for row in jsonl(raw_path))

    warm_skip = {}
    original_dispatch = args.run / "warm-dispatch.log"
    if original_dispatch.exists():
        for outcome in read_json(original_dispatch).get("outcomes", []):
            warm_skip[outcome["id"]] = outcome
    if args.repair:
        eligibility_path = args.repair / "warm-eligibility.json"
        if eligibility_path.exists():
            for outcome in read_json(eligibility_path):
                if not outcome.get("eligible"):
                    warm_skip[outcome["id"]] = outcome
        repair_dispatch = args.repair / "warm-dispatch.log"
        if repair_dispatch.exists():
            for outcome in read_json(repair_dispatch).get("outcomes", []):
                warm_skip[outcome["id"]] = outcome

    selected = {}
    for row in all_rows:
        key = (row["id"], row["phase"])
        current = selected.get(key)
        if current is None or row["source"] == "repair":
            selected[key] = row

    target_rows = jsonl(args.run / "targets.jsonl")
    cases = []
    for target in target_rows:
        case_id = target["id"]
        inventory_path = args.run / "sites" / case_id / "inventory.json"
        if not inventory_path.exists():
            inventory_path = args.run / "repair-run" / "sites" / case_id / "inventory.json"
        inventory = read_json(inventory_path) if inventory_path.exists() else {}
        sitemap_count = len(inventory.get("urls", []))
        for phase in ("cold", "warm"):
            row = selected.get((case_id, phase))
            raw = next((r for r in raw_rows if r["id"] == case_id and r["phase"] == phase and (row is None or r["source"] == row["source"])), None)
            if row:
                phase_status = raw.get("status", "evaluated") if raw else "evaluated"
                outcome = {
                    "source": row["source"],
                    "phase_status": phase_status,
                    "attempted_items": row.get("attempted_items", 0),
                    "saved_html_valid_count": row.get("saved_html_valid_count", 0),
                    "strict_success": row.get("strict_success", 0),
                    "unverified": row.get("unverified", 0),
                    "candidate_saved_without_strict_evidence": row.get("candidate_saved_without_strict_evidence", 0),
                    "scroll_growth_successes": row.get("scroll_growth_successes", 0),
                    "evaluation_items": row.get("items", []),
                }
                if raw:
                    outcome["raw_result"] = {k: raw.get(k) for k in ("status", "stop_reason", "task_success", "scroll_growth_success", "seed_url", "selected_candidate_id", "selected_candidate_source", "fresh_links_after_scroll", "fresh_item_urls_saved_after_scroll", "selected_items", "listing_candidates_visited", "cost", "budget")}
                    outcome["result_ref"] = f"{'repair-run/' if raw['source'] == 'repair' else ''}results.jsonl#{case_id}:{phase}"
                outcome["evaluation_ref"] = f"{'repair-run/' if row['source'] == 'repair' else ''}evaluation.json#{case_id}:{phase}"
            elif sitemap_count == 0:
                outcome = {"source": None, "phase_status": "not_run_no_sitemap_input", "attempted_items": 0, "saved_html_valid_count": 0, "strict_success": 0, "unverified": 0}
            else:
                skip = warm_skip.get(case_id, {}) if phase == "warm" else {}
                reason = skip.get("skip_reason") or skip.get("reason") or skip.get("status")
                status = {
                    "cold_access_or_error": "not_run_cold_access_or_error",
                    "cumulative_document_or_operation_budget": "not_run_cumulative_budget",
                    "no_agent_answer": "not_run_cold_access_or_error",
                    "skipped": "not_run_cold_access_or_error",
                }.get(reason, "not_run_unknown_reason")
                outcome = {"source": None, "phase_status": status, "skip_reason": reason, "attempted_items": 0, "saved_html_valid_count": 0, "strict_success": 0, "unverified": 0}
            cases.append({"id": case_id, "domain": target.get("domain"), "sitemap_url_count": sitemap_count, "phase": phase, **outcome})

    grouped = {}
    for row in cases:
        grouped.setdefault(row["id"], {})[row["phase"]] = row
    warm_url_metrics = {"warm_item_attempts": 0, "new_url_attempts": 0, "same_url_replays": 0, "new_url_strict_successes": 0, "new_url_attempt_sites": 0, "new_seed_or_candidate_choices": 0}
    site_metrics = {"sites_with_at_least_one_strict_success": 0, "sites_with_three_strict_successes": 0}
    all_cold_raw = [row for row in raw_rows if row["phase"] == "cold"]
    cold_observed_urls = set()
    cold_seed_urls = set()
    for raw in all_cold_raw:
        cold_seed_urls.update([raw["seed_url"]] if raw.get("seed_url") else [])
        cold_observed_urls.update(x.get("url") for x in raw.get("selected_items", []) if x.get("url"))
        cold_observed_urls.update(x.get("url") for x in raw.get("listing_candidates_visited", []) if x.get("url"))
    strict_items = {(row["id"], row["phase"], item.get("url")) for row in selected.values() for item in row.get("items", []) if item.get("classification") == "strict_success"}
    warm_new_sites = set()
    warm_seed_choices = set()
    warm_new_by_site = {}
    for raw in [row for row in raw_rows if row["phase"] == "warm" and selected.get((row["id"], "warm"), {}).get("source") == row["source"]]:
        items = [item for item in raw.get("selected_items", []) if item.get("url")]
        warm_url_metrics["warm_item_attempts"] += len(items)
        for item in items:
            url = item["url"]
            if url in cold_observed_urls:
                warm_url_metrics["same_url_replays"] += 1
            else:
                warm_url_metrics["new_url_attempts"] += 1
                warm_new_sites.add(raw["id"])
                site_counts = warm_new_by_site.setdefault(raw["id"], {"new_target_attempts": 0, "strict_successes": 0})
                site_counts["new_target_attempts"] += 1
                if (raw["id"], "warm", url) in strict_items:
                    warm_url_metrics["new_url_strict_successes"] += 1
                    site_counts["strict_successes"] += 1
        if raw.get("seed_url") and raw["seed_url"] not in cold_seed_urls:
            warm_seed_choices.add((raw["id"], raw["seed_url"], raw.get("selected_candidate_source")))
    warm_url_metrics["new_url_attempt_sites"] = len(warm_new_sites)
    warm_url_metrics["new_seed_or_candidate_choices"] = len(warm_seed_choices)
    warm_url_metrics["seed_choices"] = [{"site": site, "seed_url": url, "candidate_source": source} for site, url, source in sorted(warm_seed_choices)]
    warm_url_metrics["new_targets_by_site"] = warm_new_by_site
    reused_targets = {site: counts for site, counts in warm_new_by_site.items() if site in {"02", "10"}}
    extension_targets = {site: counts for site, counts in warm_new_by_site.items() if site not in {"02", "10"}}
    warm_url_metrics["new_extension_excluding_reused_02_10"] = {
        "sites": len(extension_targets),
        "new_target_attempts": sum(value["new_target_attempts"] for value in extension_targets.values()),
        "strict_successes": sum(value["strict_successes"] for value in extension_targets.values()),
        "by_site": extension_targets,
    }
    warm_url_metrics["reused_02_10"] = {
        "new_target_attempts": sum(value["new_target_attempts"] for value in reused_targets.values()),
        "strict_successes": sum(value["strict_successes"] for value in reused_targets.values()),
    }
    for phases_for_site in grouped.values():
        strict_count = phases_for_site["cold"].get("strict_success", 0) + phases_for_site["warm"].get("strict_success", 0)
        site_metrics["sites_with_at_least_one_strict_success"] += strict_count >= 1
        site_metrics["sites_with_three_strict_successes"] += strict_count >= 3
    site_metrics["strict_success_site_ids"] = [case_id for case_id, phases in sorted(grouped.items()) if phases["cold"].get("strict_success", 0) + phases["warm"].get("strict_success", 0) >= 1]
    site_metrics["strict_success_denominators"] = {"all_registered": len(target_rows), "URL_input_available": sum(bool(grouped[target["id"]]["cold"]["sitemap_url_count"]) for target in target_rows)}
    listing_proxy_ids = sorted({raw["id"] for raw in raw_rows if raw.get("listing_found") and (raw.get("snapshots") or {}).get("before_source_sha256")})
    phase_status_counts = {phase: {} for phase in ("cold", "warm")}
    for case in cases:
        counts = phase_status_counts[case["phase"]]
        counts[case["phase_status"]] = counts.get(case["phase_status"], 0) + 1
    audit_path = args.run / "metrics-audit.json"
    cost_path = args.run / "cost-audit.json"

    output = {
        "coverage_denominator": 80,
        "evaluation_rows": len(selected),
        "operational_rows": len(cases),
        "site_outcomes": site_metrics,
        "candidate_listing_and_source_proxy_site_ids": listing_proxy_ids,
        "phase_status_counts": phase_status_counts,
        "warm_url_evidence": {**warm_url_metrics, "denominator_note": "Only selected warm item URLs count as target attempts. All cold trial item URLs and visited listing candidate URLs, including failed item attempts, are excluded. Seed/candidate transfers are counted separately."},
        "graph_search": {"status": "not_measured", "denominator": 0},
        "metrics_audit": read_json(audit_path) if audit_path.exists() else None,
        "cost_audit": read_json(cost_path) if cost_path.exists() else None,
        "case_phases": cases,
        "source_evaluations": [str(path) for path, _ in evaluations if path.exists()],
        "method_note": "Task/type-based evaluated saved detail pages; not fixed-gold URL recall.",
    }
    (args.run / "summary.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.run / "operational-results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases), encoding="utf-8"
    )
    phases = {}
    for case in cases:
        phases.setdefault(case["id"], {})[case["phase"]] = case
    phase_totals = {}
    for phase in ("cold", "warm"):
        entries = [case for case in cases if case["phase"] == phase]
        phase_totals[phase] = {key: sum(case.get(key, 0) for case in entries) for key in ("attempted_items", "saved_html_valid_count", "strict_success", "unverified", "candidate_saved_without_strict_evidence", "scroll_growth_successes")}
    strict_ids = site_metrics["strict_success_site_ids"]
    listing_count = len(listing_proxy_ids)
    input_sites = site_metrics["strict_success_denominators"]["URL_input_available"]
    cost_audit = output["cost_audit"] or {}
    metrics_audit = output["metrics_audit"] or {}
    source_cost = metrics_audit.get("source_browser_cost_including_imported_02_10", {})
    model_usage = read_json(args.run / "model-usage.json") if (args.run / "model-usage.json").exists() else {}
    metadata_cost = cost_audit.get("metadata", {})
    source_reservations = metrics_audit.get("all_source_browser_reservations", 0)
    prior_reservations = metrics_audit.get("prior_global_browser_upper_bound", 0)
    original_new_reservations = cost_audit.get("browser_document_reservations", {}).get("current_run_reservations_from_ledgers", 0)
    imported_site_reservations = 28
    repair_reservations = source_reservations - original_new_reservations
    eval_summary = {key: sum(v.get(key, 0) for v in phase_totals.values()) for key in ("attempted_items", "saved_html_valid_count", "strict_success", "unverified", "candidate_saved_without_strict_evidence", "scroll_growth_successes")}
    status_table = phase_status_counts
    lines = [
        "# サイトマップURL選択・全80サイト評価",
        "",
        "## 結果",
        "",
        f"**判定:** URL-only sitemap経路は型検査を通ったページを一部の対象で取得できたが、採用済みstrict successは{len(strict_ids)}/{len(target_rows)}サイト（入力のあった{input_sites}サイト中{len(strict_ids)}）に限られる。共通ルートの実験部品としては有望だが、全サイトで使える標準経路と結論づけるには根拠が足りない。固定URL recallと比較ベースラインは未測定。",
        "",
        f"全80サイトを分母に維持。URL inventoryあり{input_sites}、URLなし{len(target_rows)-input_sites}。original/repair rawには80 phase rows（50+30、うち18は同じsite/phaseのrepair）あり、採用したunique phase rowsは{len(selected)}、未実行phaseは{160-len(selected)}（URLなし{2*(len(target_rows)-input_sites)}、warm skip{160-len(selected)-2*(len(target_rows)-input_sites)}）。cold/warm status内訳: `{json.dumps(status_table, ensure_ascii=False)}`。URLなしはサイトがブロックした意味ではない。",
        "",
        f"frozen typed evaluatorの採用集計: {eval_summary['attempted_items']} item attempts、{eval_summary['saved_html_valid_count']} saved HTML valid、{eval_summary['strict_success']} strict successes、{eval_summary['unverified']} unverified、{eval_summary['candidate_saved_without_strict_evidence']} saved candidates without strict evidence。unverifiedをfalseとは断定しない。site strict IDs: {', '.join(strict_ids)}。strict success件数はraw HTML独立判定なので、candidate-side success flagがfalseでもstrictとなる例がsite 52に1件ある。自動評価がknown wrongと判定した候補はなし。別個の採用済み02/10 post-hoc検証ではMagicの誤ったindex候補1件を記録。",
        "",
        f"candidate-reported listing-found + before-source SHAの観測proxyは{listing_count}サイト（{len(strict_ids)} strict sites / {listing_count} proxy sites）だが、独立したlisting goldではない。scroll-growth strict successは{eval_summary['scroll_growth_successes']}件で、全て採用済み02 coldの既存観測。all80延長のrepairでは0件。",
        "",
        f"Warmのitem target attemptsは{warm_url_metrics['warm_item_attempts']}。cold全試行のselected item URLとvisited listing candidatesを（失敗attemptも含めて）除外集合とし、新規target {warm_url_metrics['new_url_attempts']}、same-URL replay {warm_url_metrics['same_url_replays']}、新規target strict success {warm_url_metrics['new_url_strict_successes']}。このうち02/10再利用分は{warm_url_metrics['reused_02_10']['new_target_attempts']} attempts / {warm_url_metrics['reused_02_10']['strict_successes']} strict、all80延長の新規target分は{warm_url_metrics['new_extension_excluding_reused_02_10']['new_target_attempts']} attempts / {warm_url_metrics['new_extension_excluding_reused_02_10']['strict_successes']} strict ({json.dumps(warm_url_metrics['new_extension_excluding_reused_02_10']['by_site'], ensure_ascii=False)})。list seed/candidate choiceは{warm_url_metrics['new_seed_or_candidate_choices']}件を別集計し、target denominatorには含めない。これはrouteのwarm replayであり、graph search Gは未実装・未測定（分母0）。",
        "",
        "実行経路: sitemap URL group → listing → scroll → observed item → raw HTML + rendered DOM保存。HTML本文はURL choice modelへ渡さず、pageごとの追加LLMは0。09はg1を選んだがdetail URL groupではなく、listing discovery後のrepeated-anchor fallbackも行われず失敗。これは共通ルートにfallback改善余地があることを示し、サイトの不可能性を示すものではない。",
        "HTTP拒否と局所scope停止を区別する。repair scope/login guard: 23 idp.nature.com authorize/login、30 zenodo.org/login unsafe_route、37 NOAA related host scope外、26 Frontiers Partnerships related host scope外（停止前にHTML 1件保存）、52 main-content block。repair対象から外したsite 46はcold main URLが実際にHTTP 403。残るaccess停止理由は各phase evidence参照に記録。",
        "",
        "## 既存観測と費用",
        "",
        "02/10の同一条件結果は明示採用し、過去route evidenceとして明示した。新しいall80 URL choicesとは区別。参照: [adopted-prior-evidence-ref.json](adopted-prior-evidence-ref.json)、[adopted-prior-route-verification.json](adopted-prior-route-verification.json)、元資料の `lab-runs/structure-routing-sitemap-agent-robots-discovery-only-20261115/route-verification.json`。post-hoc確認はMagicの期待商品5件と誤ったindex1件で、自動frozen scoreには混ぜない。",
        "",
        f"browser events (source totals incl. imported 02/10): HTTP {source_cost.get('http_requests',0)} requests / {source_cost.get('http_responses',0)} responses, XHR {source_cost.get('xhr_fetch',0)}, measured response body {source_cost.get('response_bytes',0):,} bytes, UI {source_cost.get('ui_operations',0)}, browser time {source_cost.get('browser_ms',0)/1000:.1f}s. Metadata this run: {metadata_cost.get('new_requests_this_run',0)} requests / {metadata_cost.get('new_responses_this_run',0)} responses / {metadata_cost.get('new_response_bytes',0):,} response bytes. Document reservations: new parent {original_new_reservations-imported_site_reservations} + imported 02/10 {imported_site_reservations} + repair {repair_reservations} + prior global {prior_reservations} = {source_reservations+prior_reservations}. Imported phase reservations 28 are part of parent 185; the earlier per-site prior 11 is part of global prior 46. No double count. Reservations are ceilings, not successful document loads. Full breakdown: [cost-audit.json](cost-audit.json).",
        "",
        f"新規URL choiceは{model_usage.get('URL_choice',{}).get('new_native_jobs',0)} jobs / {model_usage.get('URL_choice',{}).get('new_site_choices',0)} sites、入力{model_usage.get('URL_choice',{}).get('input_characters',0):,}文字、HTML {model_usage.get('URL_choice',{}).get('HTML_bytes',0)} bytes。native token/invocation実数とmodel identityは未検証・不明（0ではない）。route executionの外部/ローカル推論は0。engineering/report usageも不明。B1/C1再実行なし、比較優位性は主張しない。",
        "",
        "## 全80サイト phase表",
        "",
        "`strict/saved` はstrict success数 / saved HTML valid数。候補選択や観測段階の失敗を隠さない完全なphase別status・証拠参照は [operational-results.jsonl](operational-results.jsonl) を参照。",
        "",
        "| ID | domain | sitemap URLs | cold | warm |",
        "|---:|---|---:|---|---|",
    ]
    for target in target_rows:
        phases_for_site = phases[target["id"]]
        cells = []
        for phase in ("cold", "warm"):
            entry = phases_for_site[phase]
            if entry.get("source"):
                cells.append(f"{entry['phase_status']} ({entry['strict_success']}/{entry['saved_html_valid_count']})")
            else:
                cells.append(entry["phase_status"])
        lines.append(f"| {target['id']} | {target.get('domain','')} | {phases_for_site['cold']['sitemap_url_count']} | {cells[0]} | {cells[1]} |")
    lines.extend([
        "",
        "domain列は登録時の原文を保持し、正規化labelと混同しない。",
        "",
        "## 失敗・修正と採否",
        "",
        "汎用の標準経路としての採用は見送る。最小構成の採用候補は、サイトマップ取得・URL群の保存/分類・URLだけによる入口候補選別・観測したリンクからのHTML保存。既存Skillの代表経路生成に渡す前段として扱い、生成グラフだけでライブ構造や反復処理まで発見できるとは仮定しない。",
        "",
        "追加実装が必要なのは、URL群が得られない場合の入口からの限定探索、一覧/詳細groupを取り違えた場合の棄却と反復リンクfallback、観測根拠に基づく関連ホストの範囲設定、保存HTMLの型検査、クリック型ページ送り/Load More等の実行部。今回のscroll操作はこれら全操作の代替ではない。目的URLが既知ならB0として直接取得し、この方式の到達成功へ混ぜない。",
        "",
        "61楽天・62au PAYマーケットは今回の取得上限/ホスト条件でサイトマップURLを得られず、この方式による一覧→商品HTML取得は未実施。商品取得不能やサイトマップ不存在を証明した結果ではない。固定商品ID・正解経路・専用セレクタは投入していない。",
        "",
        "共通実装の修正は3系統: XML解析時の反復ハッシュ処理をSAXの単一走査へ変更、保存済みmetadataレスポンス再利用時の消費済みbodyを修正、子frame拒否による主ページ全体の誤停止を修正。途中結果・修正前試行・費用は保持した。サイト専用セレクタ/巡回コードの手作業補正は0。評価用のページ型条件は事前固定し、専用経路とは別管理。",
        "",
        "HTTP拒否・login・許可ホスト外停止と、取得後のgroup不一致・探索不足・型未確認を区別した。目的型一致は固定したHTML型判定器による結果で、全件を人が正誤確認した値ではない。誤成功の完全な件数は不明。既知の誤選択は採用済みMagicのindex1件。要求item URLの同一phase内重複は全試行で0、最終URLの重複は修正前1/修正後3（失敗redirectを含む）。warm再訪24件とは別の値。",
        "",
        "過去のinventory/URL選択を再利用したためfull coldの総費用は未計測。02/10の再利用phase28文書は親run185文書に含み、個別site prior11文書は既存global46文書に含む。モデル非使用は取得実行部分に限り、URL選別や開発・報告のnative token量は不明。保存規則内の追加LLM呼出しは0だが、未対応ページ型への修復費用は未測定。今回は10/30/100の連続取得を追加実行しておらず、その増分コストは推定しない。",
        "",
        "## 再現・継続",
        "",
        "構造ルーティング実装は `experiments/structure-routing/sitemap-discovery.mjs`、`sitemap-agent-routing.mjs`、`sitemap-agent-browser.mjs`。記録済みrunの再集計は `python -B lab-runs/structure-routing-sitemap-agent-all80-20261115/summarize.py --repair lab-runs/structure-routing-sitemap-agent-all80-20261115/repair-run`。HTML本体を読まず、評価JSONとphase JSONLのみを使用する。",
        "",
        "検証結果・依存lock・ frozen evaluation policy・package/checkpoint情報は [HANDOFF.md](HANDOFF.md) を参照。",
        "",
    ])
    (args.run / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
