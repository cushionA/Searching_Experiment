"""Repeat the two-case functionality smoke with Python networking blocked.

Prepare model snapshots and restore the public images before invoking this file.
This is a connectivity/functionality check, not an accuracy benchmark.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--clip-threshold", type=float, default=0.01)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    experiment = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(experiment))
    import evaluate_local_visual

    attempts = []

    def blocked(*_args, **_kwargs):
        attempts.append("Python network operation")
        raise RuntimeError("Networking disabled for offline smoke verification")

    original = (socket.socket.connect, socket.socket.connect_ex,
                socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = socket.socket.connect_ex = blocked
    socket.create_connection = socket.getaddrinfo = blocked
    try:
        status = evaluate_local_visual.main([
            "--manifest", str(args.manifest),
            "--image-root", str(args.image_root),
            "--model-root", str(args.model_root),
            "--output-dir", str(args.output_dir),
            "--clip-threshold", str(args.clip_threshold),
            "--device", "cpu",
        ])
        if attempts:
            raise RuntimeError("Inference attempted networking")
        record = {"exit_status": status, "network_attempts": len(attempts),
                  "guards": ["connect", "connect_ex", "create_connection", "getaddrinfo"],
                  "scope": "functionality smoke; not an accuracy estimate"}
        (args.output_dir / "offline-verification.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        print(json.dumps(record))
    finally:
        (socket.socket.connect, socket.socket.connect_ex,
         socket.create_connection, socket.getaddrinfo) = original


if __name__ == "__main__":
    main()
