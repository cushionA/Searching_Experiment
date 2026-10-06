#!/usr/bin/env python3
"""Compare the other pinned ddddocr 1.5.6 model on the same offline image manifest."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import time
from PIL import Image
from evaluate_public_text import load_and_check_manifest, summarize, levenshtein, atomic_json, atomic_jsonl


class BetaOCR:
    def __init__(self):
        import ddddocr
        import onnxruntime as ort
        if importlib.metadata.version('ddddocr') != '1.5.6':
            raise RuntimeError('ddddocr 1.5.6 is required')
        started = time.perf_counter()
        self.engine = ddddocr.DdddOcr(show_ad=False, beta=True, use_gpu=False, ocr=True, det=False)
        graph = Path(self.engine._DdddOcr__graph_path)
        if graph.name != 'common.onnx':
            raise RuntimeError('beta=True did not select common.onnx')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.engine._DdddOcr__ort_session = ort.InferenceSession(str(graph), sess_options=options,
                                                                providers=['CPUExecutionProvider'])
        self.providers = self.engine._DdddOcr__ort_session.get_providers()
        self.load_seconds = time.perf_counter() - started
        self.metadata = {'filename': graph.name, 'sha256': hashlib.sha256(graph.read_bytes()).hexdigest(),
                         'bytes': graph.stat().st_size, 'beta': True, 'ddddocr_version': '1.5.6',
                         'onnxruntime_version': importlib.metadata.version('onnxruntime'),
                         'intra_op_num_threads': 2, 'inter_op_num_threads': 1,
                         'execution_mode': 'ORT_SEQUENTIAL', 'alphabet_restriction': None,
                         'normalization': None, 'image_conversion': 'PIL RGB encoded as PNG, same as baseline'}

    def predict(self, image: Image.Image) -> str:
        buffer = io.BytesIO()
        image.convert('RGB').save(buffer, format='PNG')
        return str(self.engine.classification(buffer.getvalue()))


def evaluate(manifest_path: Path, output: Path) -> None:
    paths = [output.with_suffix('.json'), output.with_suffix('.jsonl')]
    if output.exists() or any(p.exists() for p in paths):
        raise FileExistsError(output)
    manifest, samples = load_and_check_manifest(manifest_path.resolve())
    manifest['manifest_path'] = str(manifest_path.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    ocr = BetaOCR()
    with Image.open(samples[0]['path']) as image:
        ocr.predict(image)
    rows = []
    for i, sample in enumerate(samples,1):
        with Image.open(sample['path']) as image:
            start = time.perf_counter()
            answer = ocr.predict(image)
            latency = (time.perf_counter()-start)*1000
        label = sample['label']
        rows.append({k: sample[k] for k in ['id','path','source','sha256','label']} |
                    {'answer': answer, 'exact': answer == label,
                     'case_insensitive_exact': answer.casefold() == label.casefold(),
                     'distance': levenshtein(answer,label), 'characters':len(label),'latency_ms':latency})
        if i % 100 == 0 or i == len(samples):
            atomic_jsonl(paths[1],rows)
            result = summarize(rows,manifest,ocr.load_seconds,ocr.providers)
            result.update({'completed':i,'total_samples':len(samples), 'model':ocr.metadata,
                           'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                           'predictions_sha256':hashlib.sha256(paths[1].read_bytes()).hexdigest(),
                           'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'warmup':'first image once excluded from sample timing',
                           'status':'complete' if i == len(samples) else 'incomplete'})
            atomic_json(paths[0],result)
            print(f'checkpoint {i}/{len(samples)}',flush=True)
    print(json.dumps(result['by_source'],ensure_ascii=False))


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();evaluate(a.manifest,a.output)


if __name__ == '__main__':
    main()
