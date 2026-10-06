#!/usr/bin/env python3
"""Fetch SHA-pinned public OCR weights into an explicit experiment cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request
import zipfile

from evaluate_crnn_text import FILE_SHA256, REPOSITORY, REVISION, sha256_file

PARSEQ_SHA256 = 'e7a21b543c98e67414a584c93b1dbb71c26e9463ae4aaa391d54e833660a4711'
EASYOCR_ZIP_SHA256 = '1b5eaebf1c062de6205560c97ffcfa8dc0e6f413c340e8adc5cfc57e159f61ff'
EASYOCR_SHA256 = 'e2272681d9d67a04e2dff396b6e95077bc19001f8f6d3593c307b9852e1c29e8'


def fetch(url: str, target: Path, expected: str) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_name(target.name+'.download')
        if temporary.exists():
            raise FileExistsError(temporary)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'offline-ocr-evaluation/1.0'})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('xb') as stream:
                while block := response.read(1024*1024):
                    stream.write(block)
            if sha256_file(temporary) != expected:
                raise ValueError(f'download hash mismatch: {target.name}')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256_file(target) != expected:
        raise ValueError(f'cached file hash mismatch: {target.name}')
    return {'url': url, 'file': str(target), 'sha256': expected, 'bytes': target.stat().st_size}


def download(name: str, directory: Path) -> list[dict]:
    if name == 'captcha-crnn-finetuned':
        return [fetch(f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{filename}',
                      directory/'captcha-crnn'/filename, digest) for filename, digest in FILE_SHA256.items()]
    if name == 'parseq-tiny':
        filename = 'parseq_tiny-e7a21b54.pt'
        return [fetch('https://github.com/baudm/parseq/releases/download/v1.0.0/'+filename,
                      directory/filename, PARSEQ_SHA256)]
    if name != 'easyocr-en-g2':
        raise ValueError(name)
    archive = directory/'english_g2.zip'
    receipt = fetch('https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/english_g2.zip',
                    archive, EASYOCR_ZIP_SHA256)
    target = directory/'easyocr'/'english_g2.pth'
    if not target.exists():
        with zipfile.ZipFile(archive) as bundle:
            content = bundle.read('english_g2.pth')
        import hashlib
        if hashlib.sha256(content).hexdigest() != EASYOCR_SHA256:
            raise ValueError('EasyOCR weight hash mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    if sha256_file(target) != EASYOCR_SHA256:
        raise ValueError('cached EasyOCR weight hash mismatch')
    return [receipt, {'file': str(target), 'sha256': EASYOCR_SHA256, 'bytes': target.stat().st_size}]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=['captcha-crnn-finetuned', 'parseq-tiny', 'easyocr-en-g2'], action='append')
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    for name in args.model or ['captcha-crnn-finetuned', 'parseq-tiny', 'easyocr-en-g2']:
        print(json.dumps({'model': name, 'files': download(name, args.model_dir)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
