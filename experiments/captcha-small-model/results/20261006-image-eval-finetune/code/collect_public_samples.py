#!/usr/bin/env python3
"""Collect pinned public CAPTCHA image datasets into an offline evaluation run.

Downloads source archives without extracting arbitrary paths. It intentionally
does not perform model inference or modify labels.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import random
import re
import shutil
import tarfile
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = False

BASE = Path(__file__).resolve().parents[2]
RUN_ID = "public-images-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
RECAP_TREE = Path("/tmp/reCAPTCHA-study-tree.json")
HCAP_TREE = Path("/tmp/hcaptcha-dataset-tree.json")
MAX_ARCHIVE = 1_000_000_000
TIME_LIMIT = 180
SEED = 20261005
USER_AGENT = "offline-captcha-dataset-research/1.0"
HCAP_CATEGORIES = ["airplane", "bicycle", "boat", "motorbus", "motorcycle", "seaplane", "train", "truck"]


def normalize_label(value: str) -> str:
    word = re.sub(r"\s+", " ", value.strip().casefold())
    word = re.sub(r"^(?:a|an|the)\s+", "", word)
    aliases = {"airplane": "plane", "airplanes": "plane", "bicycles": "bicycle", "motorcycles": "motorcycle", "buses": "bus", "bus": "bus", "motorbus": "bus", "motorbuses": "bus", "trains": "train", "boats": "boat", "trucks": "truck", "seaplanes": "seaplane", "fire hydrants": "fire hydrant", "palm trees": "palm tree", "traffic lights": "traffic light", "chimneys": "chimney", "bridges": "bridge", "crosswalks": "crosswalk", "mountains": "mountain", "tractors": "tractor", "taxis": "taxi", "stairs": "stairs"}
    if word in aliases:
        return aliases[word]
    if word.endswith("s") and word[:-1] in {"bicycle", "motorcycle", "airplane", "train", "boat", "truck", "bus", "car", "bridge", "crosswalk", "hydrant", "chimney", "mountain", "tractor", "traffic light", "palm tree"}:
        return word[:-1]
    return word


def fixed_prompt_variants(label: str) -> list[str]:
    singular = normalize_label(label)
    if singular == "stairs":
        return ["a photograph containing stairs", "a photograph of a staircase"]
    plural = {"bus": "buses", "person": "people", "traffic light": "traffic lights", "palm tree": "palm trees", "fire hydrant": "fire hydrants"}.get(singular, singular + "s")
    return [f"a photograph containing a {singular}", f"a photograph containing {plural}"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_tree(path: Path) -> dict:
    d = json.loads(path.read_text())
    if not isinstance(d.get("sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", d["sha"]):
        raise ValueError(f"Invalid pinned commit SHA in {path}")
    return d


def github_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def same_tree_entries(left: list[dict], right: list[dict]) -> bool:
    def index(items: list[dict]) -> dict[str, tuple]:
        return {x["path"]: (x.get("type"), x.get("mode"), x.get("sha"), x.get("size")) for x in items}
    return index(left) == index(right)


def resolve_snapshot(repo: str, snapshot: dict, original_path: Path, meta_dir: Path) -> tuple[dict, dict]:
    """Resolve the snapshot to a real commit and Git tree, validating every path/blob."""
    original_pin = snapshot.get("commit_sha") or snapshot.get("url", "").rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[0-9a-f]{40}", original_pin or ""):
        raise ValueError(f"Snapshot lacks a pinned commit URL/SHA: {repo}")
    expected_entries = snapshot.get("tree")
    if not isinstance(expected_entries, list):
        raise ValueError(f"Snapshot has no tree entries: {repo}")
    head = github_json(f"https://api.github.com/repos/{repo}/commits/HEAD")
    head_commit = head["sha"]
    head_tree_sha = head["commit"]["tree"]["sha"]
    head_tree = github_json(f"https://api.github.com/repos/{repo}/git/trees/{head_tree_sha}?recursive=1")
    matched_head = not head_tree.get("truncated", False) and same_tree_entries(expected_entries, head_tree.get("tree", []))
    if matched_head:
        commit_sha, tree_sha, resolved_tree = head_commit, head_tree_sha, head_tree
        resolved_from = "commits/HEAD; exact tree entries match pinned input"
    else:
        pinned_commit = github_json(f"https://api.github.com/repos/{repo}/commits/{original_pin}")
        pinned_tree_sha = pinned_commit["commit"]["tree"]["sha"]
        history = github_json(f"https://api.github.com/repos/{repo}/commits?per_page=20")
        top20 = [x["sha"] for x in history if x.get("commit", {}).get("tree", {}).get("sha") == pinned_tree_sha]
        if top20:
            commit_sha = top20[0]
            pinned_commit = github_json(f"https://api.github.com/repos/{repo}/commits/{commit_sha}")
            resolved_from = "matching Git tree in bounded 20-commit history"
        else:
            commit_sha = pinned_commit["sha"]
            resolved_from = "original immutable commit SHA; HEAD/tree changed and no matching tree in top 20"
        tree_sha = pinned_commit["commit"]["tree"]["sha"]
        resolved_tree = github_json(f"https://api.github.com/repos/{repo}/git/trees/{tree_sha}?recursive=1")
        if resolved_tree.get("truncated", False) or not same_tree_entries(expected_entries, resolved_tree.get("tree", [])):
            raise ValueError(f"Resolved Git tree contents do not match the supplied snapshot: {repo}")
    if resolved_tree.get("truncated", False) or not same_tree_entries(expected_entries, resolved_tree.get("tree", [])):
        raise ValueError(f"Git tree verification failed: {repo}")
    normalized = {
        "sha": tree_sha,
        "url": f"https://api.github.com/repos/{repo}/git/trees/{tree_sha}",
        "commit_sha": commit_sha,
        "git_tree_sha": tree_sha,
        "tree": resolved_tree["tree"],
        "truncated": False,
        "snapshot_input": str(original_path),
    }
    snapshot_name = "reCAPTCHA-study-tree.json" if "reCAPTCHA" in repo else "hcaptcha-dataset-tree.json"
    snapshot_path = meta_dir / snapshot_name
    encoded = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    if snapshot_path.exists():
        existing = json.loads(snapshot_path.read_text())
        if existing.get("commit_sha") != commit_sha or existing.get("git_tree_sha") != tree_sha:
            raise ValueError(f"Refusing to replace a different source snapshot: {snapshot_path}")
    else:
        snapshot_path.write_text(encoded)
    revision = {
        "repo": repo, "commit_sha": commit_sha, "git_tree_sha": tree_sha,
        "head_at_resolution": head_commit, "head_tree_sha_at_resolution": head_tree_sha,
        "matched_head": matched_head, "resolved_from": resolved_from,
        "verified_tree_entry_count": len(expected_entries),
        "input_snapshot_sha256": sha256_file(original_path),
        "saved_snapshot_path": snapshot_path.name,
        "saved_snapshot_sha256": sha256_file(snapshot_path),
    }
    return normalized, revision


def download_archive(repo: str, commit: str, dest: Path) -> dict:
    url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    size = 0
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=25) as resp, dest.open("wb") as out:
            while True:
                if time.monotonic() - started > TIME_LIMIT:
                    raise TimeoutError(f"Archive exceeded {TIME_LIMIT}s")
                block = resp.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_ARCHIVE:
                    raise ValueError(f"Archive exceeded {MAX_ARCHIVE} bytes")
                h.update(block)
                out.write(block)
        return {"url": url, "bytes": size, "sha256": h.hexdigest(), "seconds": round(time.monotonic() - started, 3), "complete": True}
    except Exception as e:
        # Keep a partial archive and its digest for transparent accounting.
        return {"url": url, "bytes": size, "sha256": h.hexdigest(), "seconds": round(time.monotonic() - started, 3), "complete": False, "error": f"{type(e).__name__}: {e}"}


def safe_tar_extract(archive: Path, dest: Path, source: str, commit: str, tree: dict) -> dict:
    """Extract an explicit allowlist of ordinary files with traversal checks."""
    extracted = []
    rejected = 0
    repo_name = source.split("/")[-1]
    expected_root = None
    expected_blobs = {x["path"]: x for x in tree["tree"] if x.get("type") == "blob"}
    with tarfile.open(archive, mode="r:gz") as tf:
        for member in tf:
            if not member.isfile() or member.issym() or member.islnk():
                rejected += 1
                continue
            p = PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                rejected += 1
                continue
            if expected_root is None:
                prefix = repo_name + "-"
                if not p.parts[0].startswith(prefix):
                    raise ValueError(f"Unexpected archive root: {p.parts[0]}")
                root_suffix = p.parts[0][len(prefix):]
                if not re.fullmatch(r"[0-9a-f]{7,40}", root_suffix) or not (commit.startswith(root_suffix) or root_suffix.startswith(commit)):
                    raise ValueError(f"Archive root does not match pinned commit: {p.parts[0]}")
                expected_root = p.parts[0]
            if p.parts[0] != expected_root:
                raise ValueError(f"Mixed archive roots: {p.parts[0]}")
            rel = PurePosixPath(*p.parts[1:])
            rels = rel.as_posix()
            allow = False
            if source == "ssivakorn/reCAPTCHA-study":
                allow = rels in ("README.md", "LICENSE") or (
                    rels.startswith("dataset/") and
                    (rels.endswith("/info.json") or rels.endswith("/full_images.png") or re.search(r"/tile_\d+\.png$", rels))
                )
            elif source == "orlov-ai/hcaptcha-dataset":
                allow = rels in ("README.md", "LICENSE") or (
                    len(rel.parts) == 2 and rel.parts[0] in {
                        "airplane", "bicycle", "boat", "motorbus", "motorcycle", "seaplane", "train", "truck"
                    } and rel.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                )
            if not allow:
                continue
            if member.size > 20_000_000:
                rejected += 1
                continue
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                rejected += 1
                continue
            with src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            data = target.read_bytes()
            blob_sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            expected = expected_blobs.get(rels)
            if expected is None or blob_sha != expected["sha"]:
                target.unlink(missing_ok=True)
                raise ValueError(f"Extracted content does not match pinned tree: {rels}")
            extracted.append({"path": rels, "bytes": target.stat().st_size, "sha256": sha256_file(target), "git_blob_sha1": blob_sha})
    return {"files_extracted": len(extracted), "bytes_extracted": sum(x["bytes"] for x in extracted), "rejected_members": rejected, "files": extracted}


def image_record(path: Path, pixel_seen: dict[str, dict], conflict_rows: list, label: str, source_id: str, occurrences: Counter, conflict_key: str | None = None) -> dict:
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        rgb = im.convert("RGB")
        decoded = rgb.tobytes()
        pixel_hash = hashlib.sha256(str(rgb.size).encode() + b"\0" + decoded).hexdigest()
        occurrences[pixel_hash] += 1
        key = conflict_key or label
        seen_key = pixel_hash + "\0" + key
        prior = pixel_seen.get(seen_key)
        if prior and prior["label"] != label:
            conflict_rows.append({"pixel_sha256": pixel_hash, "annotation_key": key, "first": prior, "conflict": {"label": label, "source_id": source_id}})
        else:
            pixel_seen.setdefault(seen_key, {"label": label, "source_id": source_id})
        return {"path": str(path), "sha256": sha256_bytes(raw), "pixel_sha256": pixel_hash, "width": rgb.width, "height": rgb.height, "label": label}


def rel_run(path: Path, run: Path) -> str:
    return path.relative_to(run).as_posix()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=BASE / ".lab-output" / RUN_ID)
    ap.add_argument("--recaptcha-tree", type=Path, default=RECAP_TREE, help="pinned GitHub recursive tree JSON")
    ap.add_argument("--hcaptcha-tree", type=Path, default=HCAP_TREE, help="pinned GitHub recursive tree JSON")
    ap.add_argument("--manifests-only", action="store_true", help="rebuild manifests from already extracted run data")
    ap.add_argument("--provenance-only", action="store_true", help="save and verify source tree/commit metadata without touching manifests")
    args = ap.parse_args()
    run = args.run.resolve()
    if run.exists() and any(run.iterdir()) and not (args.manifests_only or args.provenance_only):
        raise SystemExit(f"Refusing non-empty run directory: {run}")
    run.mkdir(parents=True, exist_ok=True)
    data_dir = run / "data"
    meta_dir = run / "source_metadata"
    data_dir.mkdir(exist_ok=True)
    meta_dir.mkdir(exist_ok=True)
    work = Path("/tmp") / f"captcha-public-collect-{os.getpid()}"
    recap_input = read_tree(args.recaptcha_tree)
    hcap_input = read_tree(args.hcaptcha_tree)
    recap, recap_revision = resolve_snapshot("ssivakorn/reCAPTCHA-study", recap_input, args.recaptcha_tree, meta_dir)
    hcap, hcap_revision = resolve_snapshot("orlov-ai/hcaptcha-dataset", hcap_input, args.hcaptcha_tree, meta_dir)
    revisions = {"sources": [recap_revision, hcap_revision]}
    revision_path = meta_dir / "source-snapshot-revisions.json"
    if revision_path.exists():
        existing_revisions = json.loads(revision_path.read_text())
        old_by_repo = {x["repo"]: x for x in existing_revisions.get("sources", [])}
        for item in revisions["sources"]:
            old = old_by_repo.get(item["repo"])
            if old and (old.get("commit_sha") != item["commit_sha"] or old.get("git_tree_sha") != item["git_tree_sha"]):
                raise ValueError(f"Refusing to change pinned source revision for {item['repo']}")
    else:
        revision_path.write_text(json.dumps(revisions, indent=2, ensure_ascii=False) + "\n")
    if args.provenance_only:
        print(json.dumps(revisions, indent=2))
        print(f"run={run}")
        return
    sources = [
        ("orlov-ai/hcaptcha-dataset", hcap, data_dir / "hcaptcha"),
        ("ssivakorn/reCAPTCHA-study", recap, data_dir / "recaptcha"),
    ]
    if args.manifests_only:
        metadata = json.loads((meta_dir / "source_metadata.json").read_text())
    else:
        work.mkdir()
        metadata = {"run_id": run.name, "created_utc": "2026-10-05", "download_policy": {"max_archive_bytes": MAX_ARCHIVE, "max_seconds_per_archive": TIME_LIMIT, "fallback_raw_requests": "not attempted; requires new approval after archive failure"}, "sources": []}
        for repo, tree, dest in sources:
            repo_slug = repo.replace("/", "_")
            archive = work / f"{repo_slug}.tar.gz"
            commit_sha = tree["commit_sha"]
            download = download_archive(repo, commit_sha, archive)
            source_meta = {"repo": repo, "commit": commit_sha, "git_tree_sha": tree["git_tree_sha"], "tree_url": tree.get("url"), "archive": download, "declared_repository_size_bytes": 796435583 if "reCAPTCHA" in repo else 14755872, "license_file_path": "LICENSE", "attribution": repo, "extract": None}
            if download["complete"]:
                ex = safe_tar_extract(archive, dest, repo, commit_sha, tree)
                source_meta["extract"] = {k: v for k, v in ex.items() if k != "files"}
                source_meta["extracted_files"] = ex["files"]
            else:
                source_meta["extract"] = {"files_extracted": 0, "bytes_extracted": 0, "rejected_members": 0}
            metadata["sources"].append(source_meta)
    # Preserve the source's human-readable license and provenance material.
    if not args.manifests_only:
        for repo, tree, dest in sources:
            for name in ("README.md", "LICENSE"):
                p = dest / name
                if p.exists():
                    shutil.copy2(p, meta_dir / f"{repo.replace('/', '_')}-{name}")
    (meta_dir / "source_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")

    # Use only complete boards whose downloaded contents validate against the pinned tree.
    pixel_seen: dict[str, dict] = {}
    conflict_rows = []
    pixel_occurrences = Counter()
    board_candidates = []
    board_rows = []
    recap_dir = data_dir / "recaptcha"
    for info_path in sorted(recap_dir.glob("dataset/*/info.json")):
        folder = info_path.parent
        try:
            info = json.loads(info_path.read_text())
            keyword = str(info["keyword"]).strip()
            instruction = str(info["instruction"])
            gold = info["correct_answers"]
            tile_paths = sorted(folder.glob("tile_*.png"), key=lambda p: int(re.search(r"tile_(\d+)", p.name).group(1)))
            tile_count = len(tile_paths)
            if tile_count not in (9, 16) or len(gold) != len(set(gold)) or any(not isinstance(i, int) or i < 0 or i >= tile_count for i in gold):
                continue
            if not (folder / "full_images.png").is_file() or [int(re.search(r"tile_(\d+)", p.name).group(1)) for p in tile_paths] != list(range(tile_count)):
                continue
            tile_records = [image_record(p, pixel_seen, conflict_rows, "positive" if n in gold else "negative", f"recaptcha:{folder.name}:{n}", pixel_occurrences, conflict_key=f"recaptcha:{normalize_label(keyword)}") for n, p in enumerate(tile_paths)]
            with Image.open(folder / "full_images.png") as im:
                im.load()
                if im.width <= 0 or im.height <= 0:
                    continue
            board_candidates.append({"id": folder.name, "folder": folder, "keyword": keyword, "instruction": instruction, "gold": gold, "tile_count": tile_count, "tile_records": tile_records})
        except Exception as e:
            continue
    # Deduplicate by ordered decoded RGB pixels and instruction, retaining first source row.
    unique_boards = []
    seen_boards = {}
    duplicate_boards = []
    for b in board_candidates:
        key = hashlib.sha256((b["instruction"] + "\0" + "\0".join(t["pixel_sha256"] for t in b["tile_records"])).encode()).hexdigest()
        if key in seen_boards:
            duplicate_boards.append({"duplicate": b["id"], "retained": seen_boards[key], "board_hash": key})
            continue
        seen_boards[key] = b["id"]
        b["board_pixel_sha256"] = key
        unique_boards.append(b)
    for b in unique_boards:
        folder = b["folder"]
        info = json.loads((folder / "info.json").read_text())
        board_rows.append({
            "id": f"recaptcha-{b['id']}", "source_id": b["id"], "source": "ssivakorn/reCAPTCHA-study", "commit": recap["commit_sha"], "git_tree_sha": recap["git_tree_sha"],
            "target": normalize_label(b["keyword"]), "original_keyword": b["keyword"], "instruction": b["instruction"],
            "prompt_variants": fixed_prompt_variants(b["keyword"]), "source_allowed_labels": [],
            "source_type": "Type A" if b["tile_count"] == 9 else "Type B",
            "tile_count": b["tile_count"], "full_images_path": rel_run(folder / "full_images.png", run),
            "tile_paths": [rel_run(Path(t["path"]), run) for t in b["tile_records"]], "tile_sha256": [t["sha256"] for t in b["tile_records"]],
            "tile_pixel_sha256": [t["pixel_sha256"] for t in b["tile_records"]], "reference_type": "public_human_annotations_not_server_acceptance",
            "selection_rule": "select_all_margin_positive", "reference_selection": sorted(b["gold"]), "correct_answers": sorted(b["gold"]), "gold_selected_indices": sorted(b["gold"]),
            "source_info_path": rel_run(folder / "info.json", run), "source_info_sha256": sha256_file(folder / "info.json"),
            "label_provenance": "public dataset correct_answers field; human-annotated per source README"
        })
    # hCaptcha per-image labels; do not claim these are board-level challenge answers.
    hcap_records = []
    hcap_dir = data_dir / "hcaptcha"
    categories = HCAP_CATEGORIES
    for category in categories:
        for p in sorted((hcap_dir / category).glob("*")):
            if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            try:
                r = image_record(p, pixel_seen, conflict_rows, category, f"hcaptcha:{category}:{p.name}", pixel_occurrences, conflict_key="hcaptcha")
                r.update({"id": f"hcaptcha-{category}-{p.stem}", "source_id": p.relative_to(hcap_dir).as_posix(), "source": "orlov-ai/hcaptcha-dataset", "commit": hcap["commit_sha"], "git_tree_sha": hcap["git_tree_sha"], "label_provenance": "source README states manually labeled"})
                r["path"] = rel_run(p, run)
                hcap_records.append(r)
            except Exception:
                continue
    # deterministic source-balanced subset, stratified by keyword and board type
    rng = random.Random(SEED)
    strata = defaultdict(list)
    for row in board_rows:
        strata[(row["target"].casefold(), row["source_type"])].append(row)
    for rows in strata.values():
        rng.shuffle(rows)
    chosen = []
    keys = sorted(strata)
    while len(chosen) < min(120, len(board_rows)):
        changed = False
        for key in keys:
            if strata[key] and len(chosen) < min(120, len(board_rows)):
                chosen.append(strata[key].pop())
                changed = True
        if not changed:
            break
    selected_board_ids = {x["id"] for x in chosen}
    hcap_unique = []
    seen_hcap = set()
    for row in hcap_records:
        if row["pixel_sha256"] not in seen_hcap:
            seen_hcap.add(row["pixel_sha256"])
            hcap_unique.append(row)
    hgroups = defaultdict(list)
    for row in hcap_unique:
        hgroups[row["label"]].append(row)
    for rows in hgroups.values():
        rng.shuffle(rows)
    h_eval = []
    for cat in categories:
        h_eval.extend(hgroups[cat][:100])
    eval_board_ids = selected_board_ids
    for row in board_rows:
        row["source_allowed_labels"] = sorted({b["target"] for b in board_rows})
    for row in hcap_records:
        row["original_category"] = row["label"]
        row["label"] = normalize_label(row["label"])
        row["prompt_variants"] = fixed_prompt_variants(row["label"])
        row["source_allowed_labels"] = sorted({normalize_label(x) for x in categories})
    classes = sorted({normalize_label(x) for x in categories} | {b["target"] for b in board_rows})
    class_rows = [{"label": label, "prompts": fixed_prompt_variants(label)} for label in classes]
    source_classes = {
        "ssivakorn/reCAPTCHA-study": sorted({b["target"] for b in board_rows}),
        "orlov-ai/hcaptcha-dataset": sorted({normalize_label(x) for x in categories}),
    }
    public_full = {
        "schema_version": 1, "classes": class_rows, "samples": hcap_records, "cases": board_rows, "source_classes": source_classes,
        "deduplication": {"recaptcha_duplicate_boards_removed": len(duplicate_boards), "decoded_rgb_duplicate_images": sum(n - 1 for n in pixel_occurrences.values() if n > 1), "label_conflict_count": len(conflict_rows)},
        "source_totals": {"recaptcha_source_boards": len(board_candidates), "recaptcha_distinct_boards": len(board_rows), "hcaptcha_source_images_decoded": len(hcap_records), "hcaptcha_unique_pixel_samples": len(hcap_unique)},
        "notes": ["Labels are preserved from public source annotations; no inference was run.", "ReCAPTCHA answers are public human annotations, not proof of server acceptance.", "Type B is a snapshot; dynamic full-session behavior is not represented.", "hCaptcha assets have repository MIT code license but README describes image use as educational/fair use; image provenance/reuse rights remain unclear."]
    }
    public_eval = {
        "schema_version": 1, "seed": SEED, "purpose": "offline evaluation manifest; inference not run",
        "classes": class_rows, "source_classes": source_classes,
        "samples": h_eval, "cases": [b for b in board_rows if b["id"] in eval_board_ids]
    }
    (run / "public_full.json").write_text(json.dumps(public_full, indent=2, ensure_ascii=False) + "\n")
    (run / "public_eval.json").write_text(json.dumps(public_eval, indent=2, ensure_ascii=False) + "\n")
    (run / "dedup_report.json").write_text(json.dumps({"duplicate_boards": duplicate_boards, "label_conflicts": conflict_rows}, indent=2, ensure_ascii=False) + "\n")
    # Update source metadata with final retrieval and validation counts.
    metadata["retrieval_results"] = {"recaptcha_boards_downloaded_and_validated": len(board_candidates), "recaptcha_distinct_boards": len(board_rows), "recaptcha_eval_boards": len(selected_board_ids), "hcaptcha_decoded_labeled_images": len(hcap_records), "hcaptcha_unique_samples": len(hcap_unique), "hcaptcha_eval_samples": len(h_eval), "decoded_rgb_label_conflicts": len(conflict_rows), "duplicate_ordered_boards": len(duplicate_boards)}
    (meta_dir / "source_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(metadata["retrieval_results"], indent=2))
    print(f"run={run}")


if __name__ == "__main__":
    main()
