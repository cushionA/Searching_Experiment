"""Local CPU state classifier benchmark. No live websites or purchase actions."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import time

LABELS = ['available', 'sold_out', 'preorder', 'unknown']
SCHEMA = {'current_product_status': {'labels': {
    'available': 'The target product is in stock and can be purchased for dispatch now.',
    'sold_out': 'The target product is unavailable and no orders are accepted now.',
    'preorder': 'Orders or reservations are accepted for a future release or future delivery.',
    'unknown': 'The current availability of the target product cannot be determined from the text.'},
    'prompt': 'What is the current sales status of the target product? Ignore old status and other products.'}}
# Fixed before inference; diagnostic only, NOT a calibrated acceptance threshold.
SCORE_THRESHOLD = 0.8
RULES = {
    'preorder': ['preorder', 'reserve', 'reservation', 'advance order', '予約', '先行注文'],
    'sold_out': ['sold out', 'out of stock', 'no units', 'zero remaining', '売り切れ', '在庫切れ', '完売', '品切れ', '残り0'],
    'available': ['in stock', 'buy now', 'available now', 'ready to ship', '在庫あり', '在庫があります', '販売中', '購入できます', '注文可能', '入荷しました'],
}


def load_fixture(path):
    data = json.loads(Path(path).read_text())
    rows = data['rows']
    if data['labels'] != LABELS or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Invalid labels or duplicate IDs')
    for r in rows:
        if r['label'] not in LABELS or r['split'] not in ('train', 'test') or r['lang'] not in ('en', 'ja'):
            raise ValueError('Invalid fixture row')
    train = {r['text'] for r in rows if r['split'] == 'train'}
    test = {r['text'] for r in rows if r['split'] == 'test'}
    if train & test:
        raise ValueError('Train/test text overlap')
    return rows


def rule_predict(text):
    text = text.lower()
    hits = [label for label, phrases in RULES.items() if any(p in text for p in phrases)]
    # Conflicting lexical cues cause a conservative unknown, not a precedence trick.
    return (hits[0] if len(hits) == 1 else 'unknown'), None


def typed_state(label, score):
    if label not in LABELS:
        raise ValueError('Unsupported state')
    if score is not None and (not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1):
        raise ValueError('Invalid score')
    return {'state': label, 'can_ship_now': {'available': True, 'sold_out': False, 'preorder': False, 'unknown': None}[label],
            'raw_model_score': score, 'score_is_calibrated': False,
            'requires_review': label == 'unknown' or score is None or score < SCORE_THRESHOLD}


def metrics(rows):
    cm = {a: {b: 0 for b in LABELS} for a in LABELS}
    for r in rows:
        cm[r['gold']][r['predicted']] += 1
    f1 = []
    for k in LABELS:
        tp = cm[k][k]
        denominator = sum(cm[k].values()) + sum(cm[a][k] for a in LABELS)
        f1.append(2 * tp / denominator if denominator else 0)
    correct = sum(r['gold'] == r['predicted'] for r in rows)
    selected = [r for r in rows if not r['typed']['requires_review']]
    latencies = sorted(t for r in rows for t in r['latency_ms'])
    return {'n': len(rows), 'correct': correct, 'accuracy': correct / len(rows), 'macro_f1': statistics.mean(f1),
            'confusion': cm, 'latency_median_ms': statistics.median(latencies),
            'latency_p95_ms': latencies[math.ceil(.95 * len(latencies)) - 1],
            'review_gate': {'threshold': SCORE_THRESHOLD, 'selected': len(selected),
                           'coverage': len(selected) / len(rows),
                           'correct': sum(r['gold'] == r['predicted'] for r in selected),
                           'accuracy': (sum(r['gold'] == r['predicted'] for r in selected) / len(selected)) if selected else None},
            'false_available': sum(r['predicted'] == 'available' and r['gold'] != 'available' for r in rows)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--engine', choices=['rules', 'tfidf', 'gliner'], required=True)
    p.add_argument('--fixture', type=Path, default=Path(__file__).with_name('fixture.json'))
    p.add_argument('--model-dir', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--repeats', type=int, default=2)
    args = p.parse_args()
    if args.threads < 1 or args.repeats < 1:
        p.error('threads and repeats must be positive')
    started = time.perf_counter()
    fixture = load_fixture(args.fixture)
    train = [r for r in fixture if r['split'] == 'train']
    test = [r for r in fixture if r['split'] == 'test']
    setup_started = time.perf_counter()
    extra = {}
    if args.engine == 'rules':
        predict = rule_predict
    elif args.engine == 'tfidf':
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from threadpoolctl import threadpool_limits
        threadpool_limits(limits=args.threads)
        classifier = make_pipeline(TfidfVectorizer(analyzer='char', ngram_range=(2, 5), sublinear_tf=True),
                                   LogisticRegression(C=1.0, max_iter=1000, random_state=42))
        classifier.fit([r['text'] for r in train], [r['label'] for r in train])
        def predict(text):
            scores = classifier.predict_proba([text])[0]
            i = int(scores.argmax())
            return str(classifier.classes_[i]), float(scores[i])
        extra = {'training_rows': len(train), 'hyperparameters': 'char 2-5 grams; sublinear_tf; C=1; max_iter=1000; random_state=42; no tuning'}
    else:
        if not args.model_dir:
            p.error('--model-dir is required for gliner')
        import torch
        from gliner2 import AutoExtractor
        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
        torch.manual_seed(42)
        model = AutoExtractor.from_pretrained(str(args.model_dir), map_location='cpu', use_flashdeberta=False)
        model.eval()
        def predict(text):
            with torch.inference_mode():
                result = model.classify_text(text, SCHEMA, include_confidence=True)
            value = result['current_product_status']
            return value['label'], float(value['confidence'])
        extra = {'parameters': sum(p.numel() for p in model.parameters()), 'schema': SCHEMA,
                 'device': str(next(model.parameters()).device), 'dtype': str(next(model.parameters()).dtype),
                 'word_splitter': 'upstream default whitespace; unchanged; Japanese sensitivity not tuned',
                 'training_rows': 0}
    setup_seconds = time.perf_counter() - setup_started
    t = time.perf_counter()
    predict('In stock. Buy now.')  # separate warm-up, never scored
    warmup_ms = (time.perf_counter() - t) * 1000
    predictions = []
    # Each example repeated consecutively; first result retained, repeat agreement checked.
    for r in test:
        times = []
        outcomes = []
        for _ in range(args.repeats):
            t = time.perf_counter()
            label, score = predict(r['text'])
            times.append((time.perf_counter() - t) * 1000)
            outcomes.append((label, score))
        label, score = outcomes[0]
        predictions.append({'id': r['id'], 'lang': r['lang'], 'kind': r['kind'], 'gold': r['label'],
                            'predicted': label, 'score': score, 'typed': typed_state(label, score),
                            'latency_ms': times, 'repeat_label_agreement': all(x[0] == label for x in outcomes)})
    summary = {g: metrics([r for r in predictions if g == 'all' or r['lang'] == g]) for g in ('all', 'en', 'ja')}
    report = {'engine': args.engine, 'fixture_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              'python': platform.python_version(), 'platform': platform.platform(), 'cpu_count': os.cpu_count(),
              'threads': args.threads, 'repeats': args.repeats, 'setup_seconds': setup_seconds,
              'warmup_ms': warmup_ms, 'total_seconds': time.perf_counter() - started,
              'peak_process_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
              'notes': 'Linux process lifetime peak RSS includes imports/setup; single-process CPU; no download time. Synthetic diagnostic only.',
              **extra, 'summary': summary, 'predictions': predictions}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'engine': args.engine, 'summary': summary, 'peak_rss_mib': report['peak_process_rss_mib']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
