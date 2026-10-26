"""Prepare immutable, safetensors-only Hugging Face snapshots for offline inference."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

# Both CLIP snapshots expose Transformers-compatible CLIPModel configs and safetensors.
PROFILES = {
    "small": {
        "clip": ("laion/CLIP-ViT-B-32-laion2B-s34B-b79K", "clip"),
        "owl": ("google/owlv2-base-patch16-ensemble", "owlv2"),
    },
    "large": {
        "clip": ("laion/CLIP-ViT-H-14-laion2B-s32B-b79K", "clip"),
        "owl": ("google/owlv2-base-patch16-ensemble", "owlv2"),
    },
}
REVISIONS = {
    "laion/CLIP-ViT-B-32-laion2B-s34B-b79K": "1a25a446712ba5ee05982a381eed697ef9b435cf",
    "laion/CLIP-ViT-H-14-laion2B-s32B-b79K": "1c2b8495b28150b8a4922ee1c8edee224c284c0c",
    "google/owlv2-base-patch16-ensemble": "cfd3195ba4ea9592eec887ded089f4c08eff231d",
}
ALLOW = ["config.json", "model.safetensors", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "vocab.json", "merges.txt", "vocab.txt", "*.model"]

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="small")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clip-revision", help="Optional immutable HF commit override; default is pinned")
    parser.add_argument("--owl-revision", help="Optional immutable HF commit override; default is pinned")
    args = parser.parse_args()
    for revision in (args.clip_revision, args.owl_revision):
        if revision is not None and (len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision)):
            parser.error("Revision overrides must be 40-character lowercase commit SHAs")
    if args.output.exists():
        raise FileExistsError(f"Refusing to write into existing output path: {args.output}")
    from huggingface_hub import model_info, snapshot_download
    args.output.mkdir(parents=True, exist_ok=False)
    models = {}
    try:
        for name, (repo_id, expected_type) in PROFILES[args.profile].items():
            requested_revision = getattr(args, f"{name}_revision") or REVISIONS[repo_id]
            revision = model_info(repo_id, revision=requested_revision).sha
            if revision != requested_revision:
                raise RuntimeError(f"HF API did not return an immutable commit for {repo_id}")
            local_dir = args.output / name
            snapshot_download(repo_id, revision=revision, local_dir=local_dir, allow_patterns=ALLOW)
            files = []
            for path in sorted(local_dir.rglob("*")):
                if not path.is_file() or ".cache" in path.relative_to(local_dir).parts:
                    continue
                files.append({"path": path.relative_to(local_dir).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size})
            if not any(item["path"].endswith(".safetensors") for item in files):
                raise RuntimeError(f"{repo_id}@{revision} has no safetensors weights")
            config_path = local_dir / "config.json"
            if not config_path.is_file() or json.loads(config_path.read_text()).get("model_type") != expected_type:
                raise RuntimeError(f"{repo_id}@{revision} has an incompatible Transformers model config")
            models[name] = {"repo_id": repo_id, "revision": revision, "model_type": expected_type, "directory": name, "files": files}
        manifest = {"format": "local-visual-models-v1", "profile": args.profile, "implementation_note": "Independent implementation; exact paper checkpoints unidentified.", "models": models}
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(args.output / "manifest.json")
    except Exception:
        # Preserve all downloaded/cache files for inspection; no cleanup or deletion.
        raise

if __name__ == "__main__": main()
