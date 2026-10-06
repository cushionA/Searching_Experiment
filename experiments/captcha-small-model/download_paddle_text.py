#!/usr/bin/env python3
"""Download the pinned RapidOCR 3.9.2 recognition models for offline evaluation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import urllib.request
from evaluate_paddle_text import MODELS, sha256_file


def download(model_name: str, model_dir: Path) -> dict:
    model = MODELS[model_name]
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / model['filename']
    url = f"https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/{model['version']}/rec/{model['filename']}"
    if not target.exists():
        temporary = target.with_name(target.name + '.download')
        if temporary.exists():
            raise FileExistsError(temporary)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'offline-ocr-evaluation/1.0'})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('xb') as stream:
                while block := response.read(1024 * 1024):
                    stream.write(block)
            actual = sha256_file(temporary)
            if actual != model['sha256']:
                raise ValueError(f"model SHA-256 mismatch for {model_name}: {actual}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    actual = sha256_file(target)
    if actual != model['sha256']:
        raise ValueError(f"cached model SHA-256 mismatch for {model_name}: {actual}")
    return {'model': model_name, 'url': url, 'filename': target.name,
            'sha256': actual, 'bytes': target.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=sorted(MODELS), action='append',
                        help='repeat to select models; default is all five')
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    for model in args.model or list(MODELS):
        print(json.dumps(download(model, args.model_dir), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
