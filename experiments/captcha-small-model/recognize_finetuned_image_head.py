"""Apply the exported S3 frozen-feature head to saved images, entirely offline.

The small head requires its original MobileCLIP2-S3 encoder. Encoder weights
must already be cached locally; this entry point never downloads them.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace


ENCODER = "MobileCLIP2-S3"


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file(path: Path, expected: str) -> None:
    if not isinstance(expected, str) or len(expected) != 64 or file_sha(path) != expected:
        raise ValueError(f"SHA-256 differs from exported identity: {path.name}")


def load_exported_head(bundle: Path):
    """Verify the exported metadata and strictly load only safe tensor weights."""
    import torch
    from safetensors.torch import load_file

    deployment = json.loads((bundle / "deployment.json").read_bytes())
    if deployment.get("schema_version") != 1 or deployment.get("encoder", {}).get("name") != ENCODER:
        raise ValueError("Export must declare the supported MobileCLIP2-S3 encoder")
    for name in ("recognize_finetuned_image_head.py", "benchmark_embeddings.py"):
        verify_file(Path(__file__).with_name(name), deployment["source_sha256"][name])
    verify_file(bundle / "metadata.json", deployment["metadata_sha256"])
    verify_file(bundle / "linear_head.safetensors", deployment["head_sha256"])
    metadata = json.loads((bundle / "metadata.json").read_bytes())
    architecture = metadata.get("architecture", {})
    labels = architecture.get("labels")
    if (architecture.get("type") != "linear" or architecture.get("input_dim") != 768
            or architecture.get("output_dim") != 16
            or architecture.get("output") != "independent sigmoid logits"
            or not isinstance(labels, list) or len(labels) != 16
            or any(not isinstance(label, str) or not label for label in labels)
            or len(set(labels)) != 16 or labels != deployment.get("labels")):
        raise ValueError("Exported head architecture/label order differs from the expected 768-to-16 head")
    if (metadata.get("provenance", {}).get("head_sha256") != deployment["head_sha256"]
            or metadata.get("selection", {}).get("fixed_board_threshold") != 0.5
            or not metadata.get("selection", {}).get("requested_count_not_used")):
        raise ValueError("Exported metadata does not match the head identity or fixed board rule")
    state = load_file(str(bundle / "linear_head.safetensors"), device="cpu")
    expected_shapes = {"linear.weight": (16, 768), "linear.bias": (16,)}
    if set(state) != set(expected_shapes):
        raise ValueError("Head checkpoint must contain exactly linear.weight and linear.bias")
    for key, shape in expected_shapes.items():
        if (tuple(state[key].shape) != shape or state[key].dtype != torch.float32
                or not torch.isfinite(state[key]).all().item()):
            raise ValueError(f"Head tensor has incorrect shape, dtype, or values: {key}")
    head = torch.nn.Sequential()
    head.add_module("linear", torch.nn.Linear(768, 16))
    head.load_state_dict(state, strict=True)
    return head.eval(), labels, deployment


def feature_logits(head, features):
    """Score normalized features in the same float32 CPU arithmetic as fitting."""
    import torch
    if features.ndim != 2 or features.shape[1] != 768 or features.shape[0] < 1:
        raise ValueError("Features must have nonempty shape [images, 768]")
    features = features.detach().to(device="cpu", dtype=torch.float32)
    if (not torch.isfinite(features).all().item()
            or not torch.allclose(features.norm(dim=1), torch.ones(len(features)), atol=0.005, rtol=0)):
        raise ValueError("Features must be finite and L2 normalized")
    with torch.inference_mode():
        logits = head(features)
    if not torch.isfinite(logits).all().item():
        raise ValueError("Head produced non-finite logits")
    return logits


def classify_logits(logits, labels: list[str], allowed_labels: list[str]) -> list[str]:
    if (not allowed_labels or len(set(allowed_labels)) != len(allowed_labels)
            or not set(allowed_labels).issubset(labels)):
        raise ValueError("allowed_labels must be a nonempty unique subset of the exported labels")
    active = [labels.index(label) for label in allowed_labels]
    return [allowed_labels[int(index)] for index in logits[:, active].argmax(dim=1).tolist()]


def board_selection(logits, labels: list[str], target: str) -> tuple[list[int], list[float]]:
    if target not in labels:
        raise ValueError("Board target must be one of the exported labels")
    probabilities = logits[:, labels.index(target)].sigmoid()
    selected = (probabilities >= 0.5).nonzero(as_tuple=True)[0].tolist()
    return selected, probabilities.tolist()


def image_features(paths: list[Path], model_dir: Path, deployment: dict, device: str,
                   precision: str, batch_size: int):
    import torch
    from PIL import Image
    import benchmark_embeddings as helper

    spec = deployment["encoder"]
    if any(helper.MOBILE_MODELS[ENCODER][key] != spec[key] for key in ("filename", "revision", "sha256", "bytes")):
        raise ValueError("Encoder pin differs from the exported head's original encoder")
    checkpoint = model_dir / spec["filename"]
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Offline inference needs the original encoder cached locally: {checkpoint}")
    verify_file(checkpoint, spec["sha256"])
    if checkpoint.stat().st_size != spec["bytes"]:
        raise ValueError("Encoder byte count differs from its pinned checkpoint")
    for package, expected in deployment["encoder_api_packages"].items():
        if importlib.metadata.version(package) != expected:
            raise ValueError(f"Encoder implementation requires {package}=={expected}")
    if helper.runtime_provenance(ENCODER)["model_config_sha256"] != spec["model_config_sha256"]:
        raise ValueError("Encoder architecture config differs from the exported checkpoint")
    args = SimpleNamespace(model_dir=model_dir, precision=precision)
    model, preprocess, _, provenance = helper.load_model(ENCODER, args, device)
    if provenance["file_sha256"] != spec["sha256"]:
        raise ValueError("Loaded encoder identity differs from its pinned checkpoint")
    batches = []
    with torch.inference_mode():
        for start in range(0, len(paths), batch_size):
            inputs = []
            for path in paths[start:start + batch_size]:
                with Image.open(path) as image:
                    inputs.append(preprocess(image.convert("RGB")))
            batch = torch.stack(inputs).to(device)
            with helper.precision_context(args, device):
                features = model.encode_image(batch)
                features /= features.norm(dim=-1, keepdim=True)
            batches.append(features.to(device="cpu", dtype=torch.float32))
    return torch.cat(batches), provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Export folder with deployment.json, metadata.json, and linear_head.safetensors")
    parser.add_argument("--model-dir", type=Path, required=True, help="Existing local cache containing mobileclip2_s3.pt; no downloads")
    parser.add_argument("--images", type=Path, nargs="+", required=True, help="Saved local images; board tiles are supplied in index order")
    parser.add_argument("--mode", choices=("classify", "board"), required=True)
    parser.add_argument("--allowed-labels", nargs="+", help="Classification candidates, quoted individually when labels contain spaces")
    parser.add_argument("--target", help="Board target label")
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="auto")
    parser.add_argument("--precision", choices=("fp32", "fp16", "auto"), default="auto")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--output", type=Path, help="New JSON output file; otherwise print JSON")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.output and args.output.exists():
        raise FileExistsError(args.output)
    if args.mode == "classify" and (not args.allowed_labels or args.target):
        parser.error("Classification requires --allowed-labels")
    if args.mode == "board" and (not args.target or args.allowed_labels):
        parser.error("Board selection requires --target")
    import torch
    torch.set_num_threads(2)
    device = ("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    precision = ("fp16" if device == "cuda" else "fp32") if args.precision == "auto" else args.precision
    if precision == "fp16" and device != "cuda":
        parser.error("fp16 requires CUDA")
    head, labels, deployment = load_exported_head(args.bundle)
    # Validate inference labels before paying the cost of encoder loading.
    if args.mode == "classify":
        classify_logits(torch.zeros((1, 16)), labels, args.allowed_labels)
    elif args.target not in labels:
        parser.error("--target must be one of the exported labels")
    features, provenance = image_features(args.images, args.model_dir, deployment, device, precision, args.batch_size)
    logits = feature_logits(head, features)
    result = {"mode": args.mode, "encoder": ENCODER, "encoder_sha256": provenance["file_sha256"],
              "head_sha256": deployment["head_sha256"], "device": device, "precision": precision,
              "images": [{"path": str(path), "sha256": file_sha(path)} for path in args.images]}
    if args.mode == "classify":
        result.update({"allowed_labels": args.allowed_labels,
                       "predicted_labels": classify_logits(logits, labels, args.allowed_labels)})
    else:
        selection, probabilities = board_selection(logits, labels, args.target)
        result.update({"target": args.target, "threshold": 0.5,
                       "selected_indices": selection, "target_sigmoid_scores": probabilities})
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
    else:
        print(raw, end="")


if __name__ == "__main__":
    main()
