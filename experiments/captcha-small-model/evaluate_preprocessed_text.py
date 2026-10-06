#!/usr/bin/env python3
"""Fixed, label-independent preprocessing ablations for the pinned common_old model."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

from PIL import Image, ImageOps
from evaluate_public_text import load_and_check_manifest, summarize, levenshtein, atomic_json, atomic_jsonl
from text_ocr import TextOCR

OLD_SHA256 = 'b8f2ad9cbc1f2e3922a6cb9459e30824e7e2467f3fb4fd61420640e34ea0bf68'


def preprocess(image: Image.Image, variant: str) -> Image.Image:
    gray = image.convert('RGB').convert('L')
    if variant == 'autocontrast':
        return ImageOps.autocontrast(gray, cutoff=1)
    if variant == 'otsu':
        import cv2
        import numpy as np
        cv2.setNumThreads(1)
        _, result = cv2.threshold(np.asarray(gray), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        return Image.fromarray(result)
    raise ValueError(f'Unknown preprocessing variant: {variant}')


def evaluate(manifest_path: Path, output: Path, variant: str) -> None:
    paths = [output.with_suffix('.json'), output.with_suffix('.jsonl')]
    if output.exists() or any(path.exists() for path in paths):
        raise FileExistsError(output)
    manifest, samples = load_and_check_manifest(manifest_path.resolve())
    manifest['manifest_path'] = str(manifest_path.resolve())
    if importlib.metadata.version('ddddocr') != '1.5.6':
        raise RuntimeError('ddddocr 1.5.6 is required')
    ocr = TextOCR()
    graph = Path(ocr.engine._DdddOcr__graph_path)
    digest = hashlib.sha256(graph.read_bytes()).hexdigest()
    if digest != OLD_SHA256:
        raise ValueError('common_old model SHA-256 mismatch')
    model = {'filename': graph.name, 'sha256': digest, 'bytes': graph.stat().st_size,
             'variant': variant, 'preprocessing': {'autocontrast': 'RGB then L; PIL autocontrast cutoff=1 percent at each tail',
                                                 'otsu': 'RGB then L; OpenCV global Otsu THRESH_BINARY, no morphology'}[variant],
             'variant_selection': 'Fixed before evaluation; no per-image label or correctness selection',
             'internal_preprocessing': 'ddddocr standard grayscale, height64 LANCZOS resize, tensor normalization',
             'normalization': None, 'alphabet_restriction': None,
             'intra_op_num_threads': 2, 'inter_op_num_threads': 1, 'execution_mode': 'ORT_SEQUENTIAL',
             'runtime_versions': {name: importlib.metadata.version(name) for name in
                                  ['ddddocr', 'onnxruntime', 'Pillow', 'numpy', 'opencv-python']}}
    with Image.open(samples[0]['path']) as image:
        ocr.predict(preprocess(image, variant))
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, sample in enumerate(samples, 1):
        answer, error = '', None
        started = time.perf_counter()
        try:
            with Image.open(sample['path']) as image:
                started = time.perf_counter()
                image.load()
                answer = ocr.predict(preprocess(image, variant))
        except Exception as exc:
            error = {'type': type(exc).__name__, 'message': str(exc)}
        latency = (time.perf_counter() - started) * 1000
        label = sample['label']
        rows.append({key: sample[key] for key in ['id', 'path', 'source', 'sha256', 'label']} |
                    {'answer': answer, 'exact': answer == label,
                     'case_insensitive_exact': answer.casefold() == label.casefold(),
                     'distance': levenshtein(answer, label), 'characters': len(label),
                     'latency_ms': latency, 'error': error})
        if index % 100 == 0 or index == len(samples):
            atomic_jsonl(paths[1], rows)
            result = summarize(rows, manifest, ocr.load_seconds, ocr.providers)
            result.update({'completed': index, 'total_samples': len(samples), 'model': model,
                           'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                           'predictions_sha256': hashlib.sha256(paths[1].read_bytes()).hexdigest(),
                           'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'adapter_sha256': hashlib.sha256(Path(__file__).with_name('text_ocr.py').read_bytes()).hexdigest(),
                           'warmup': 'first image once excluded; preprocessing included in sample timing',
                           'failed_samples': sum(row['error'] is not None for row in rows),
                           'status': 'complete' if index == len(samples) else 'incomplete'})
            atomic_json(paths[0], result)
            print(f'checkpoint {index}/{len(samples)} errors={result["failed_samples"]}', flush=True)
    print(json.dumps(result['by_source'], ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--variant', choices=['autocontrast', 'otsu'], required=True)
    args = parser.parse_args()
    evaluate(args.manifest, args.output, args.variant)


if __name__ == '__main__':
    main()
