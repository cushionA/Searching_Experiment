"""Summarize frozen real-SKU predictions, keeping diagnostic and test scopes distinct."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from evaluate_luna_real_skus import metrics, read_jsonl

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.lab-output/sku-real-luna-model-evaluation-20261010-v1'
INPUTS = ROOT / '.lab-output/sku-real-luna-annotation-inputs-20261010-v2'
LABELS = ROOT / '.lab-output/sku-real-luna-labels-20261010-v1'
DECIDE = ROOT / '.lab-output/sku-real-luna-decide-evaluation-20261010-v1/newexclusive/summary.json'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def percentage(value):
    return '-' if value is None else f'{100 * value:.1f}%'


def main():
    cases = read_jsonl(INPUTS / 'cases.jsonl')
    labels = {r['case_id']: r for r in read_jsonl(LABELS / 'labels.jsonl')}
    cases_by_id = {
        r['case_id']: {'case_id': r['case_id'], 'split': r['split'],
                       'decision': labels[r['case_id']]['decision'],
                       'gold_row_keys': labels[r['case_id']]['matching_au_row_keys']}
        for r in cases
    }
    test_ids = [r['case_id'] for r in cases if r['split'] == 'test']
    test_cases = [cases_by_id[key] for key in test_ids]
    decide = json.loads(DECIDE.read_text())
    sample_ids = set(decide['sampling']['selected_case_ids'])
    sample_test_ids = [key for key in test_ids if key in sample_ids]
    sample_test_cases = [cases_by_id[key] for key in sample_test_ids]
    summaries = {}
    sources = {str(DECIDE.relative_to(ROOT)): sha(DECIDE)}
    table_lines = ['model\tinput\tE2E_precision\tE2E_recall\ttop1_before_threshold\tfalse_accepts_including_wrong_AU_SKU']
    for name in ('minilm', 'bekko', 'granite', 'ruri'):
        summary_path = BASE / name / 'summary.json'
        summary = json.loads(summary_path.read_text())
        sources[str(summary_path.relative_to(ROOT))] = sha(summary_path)
        summaries[name] = {}
        for mode in ('sku', 'title-sku'):
            pred_path = BASE / name / f'predictions-{mode}.json'
            predictions = json.loads(pred_path.read_text())['rows']
            pred_by_id = {r['case_id']: r for r in predictions}
            if len(pred_by_id) != len(cases) or set(pred_by_id) != set(cases_by_id):
                raise ValueError('Predictions must cover the entire frozen dataset')
            threshold = summary['modes'][mode]['frozen_test_threshold']
            def decisions(ids):
                return [{**pred_by_id[key], 'decision': 'matched' if pred_by_id[key]['score'] >= threshold else 'unmatched'} for key in ids]
            full_metrics = metrics(test_cases, decisions(test_ids))
            if full_metrics != summary['modes'][mode]['test_metrics']:
                raise ValueError('Frozen test metrics do not reproduce')
            positives = [key for key in test_ids if cases_by_id[key]['decision'] == 'matched']
            ranks = []
            for key in positives:
                gold = set(cases_by_id[key]['gold_row_keys'])
                rank = next((i for i, candidate in enumerate(pred_by_id[key]['top10'], 1) if candidate['row_key'] in gold), None)
                ranks.append(rank)
            ranking = {f'recall_at_{k}': sum(rank is not None and rank <= k for rank in ranks) / len(ranks) for k in (1, 5, 10)}
            summaries[name][mode] = {
                'test_metrics': full_metrics, 'test_ranking': ranking,
                'frozen_dev_selected_threshold': threshold,
                'dev_selection_status': summary['modes'][mode]['threshold_selection_dev_only']['selection_status'],
                'same_62_test_cases_as_decide': metrics(sample_test_cases, decisions(sample_test_ids)),
                'unique_text_encoding_seconds': summary['inference_timing'][mode]['seconds'],
                'unique_encoded_texts': summary['inference_timing'][mode]['unique_input_text_count'],
            }
            table_lines.append('\t'.join([name, mode, percentage(full_metrics['known_case_precision']),
                                          percentage(full_metrics['known_case_recall']),
                                          percentage(ranking['recall_at_1']),
                                          str(full_metrics['known_false_accept_count_including_wrong_au_row'])]))
            sources[str(pred_path.relative_to(ROOT))] = sha(pred_path)
    decide_metrics = {}
    for mode, rows in decide['case_rows'].items():
        rows_by_id = {row['case_id']: row for row in rows}
        if set(rows_by_id) != sample_ids:
            raise ValueError('Decide prediction sample IDs changed')
        preds = [{'decision': rows_by_id[key]['prediction_decision'],
                  'top_row_key': rows_by_id[key]['top_row_key']} for key in sample_test_ids]
        decide_metrics[mode] = metrics(sample_test_cases, preds)
    result = {
        'annotation_kind': 'Luna machine reference labels, human unverified',
        'case_count': 1383, 'unique_rakuten_skus': 644, 'family_groups': 12,
        'dev_cases': 625, 'test_cases': 758,
        'test_gold_counts': dict(Counter(c['decision'] for c in test_cases)),
        'synthetic_data_count': 0, 'models': summaries,
        'decide': {'sample_cases': 166, 'test_cases': len(sample_test_ids),
                   'test_gold_counts': dict(Counter(c['decision'] for c in sample_test_cases)),
                   'test_metrics': decide_metrics,
                   'all_166_diagnostic_metrics': decide['metrics'],
                   'retrieval': 'Ruri SKU-only top1, chosen by development ranking before Decide inference'},
        'source_sha256': sources,
        'conclusion': 'Embedding ranking can nominate candidates, but neither concatenated titles nor a cosine cutoff reliably establishes SKU identity. Keep fixed AU context, extract page-specific constraints, and retain review where evidence is incomplete.',
    }
    with (BASE / 'comparison.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    label_counts = json.loads((LABELS / 'manifest.json').read_text())['decision_counts']
    report = '\n'.join([
        '実SKUラベル・CPUモデル検証', '',
        'Lunaによる機械注釈1,383件: 一致470、不一致911、保留2。人手確認は未実施。',
        '29固定au商品・17楽天ページ・12関連商品群。楽天の独立な原SKU行は644件。',
        '全ケースと12,615引用を原本照合。別Lunaが56ケースを資料から再読し、29商品ペアを確認。',
        '注釈出力を一度誤削除した履歴と再生成・根拠修正の履歴はレビューと監査に記録。取得原本は保持。',
        '実データのみ。新しいSKU、価格、在庫、不一致例は合成していない。', '',
        'au価格は商品ページ単位。楽天は実SKUごとに固有価格を持つ別レコード。',
        '楽天SKU1件を、固定したau商品とその全実SKU配列に対して比較する。別URLへ振り分けない。',
        '価格・在庫・判定ラベル・注釈属性・引用はモデル入力に使わない。販売状態は同一性判定とは別。', '',
        'CPU2 threads / batch8。dev625件(8商品群)で閾値選択、test758件(4商品群)で固定評価。',
        'カーテンct0/ctkは関連群としてまとめてtest固定。関連商品や同一楽天SKUの再参照はsplitをまたがない。',
        '表のprecision/recallは正しいau SKU行まで選べたかを含む。保留の真偽は未確定で別集計。',
        *table_lines, '',
        'SKU単体の候補検索ではBekkoのtest top1が375/375、Ruriが367/375。',
        'ただしSKU単体の類似度だけでは商品ページ側のレース有無・サイズ・構成を判定しきれない。',
        '商品名をそのまま足した入力では全モデルのSKU候補選択が悪化した。',
        'MiniLMは102,140入力参照中198件が128token上限超過。他3モデルは超過なし。',
        '従って今回の悪化を切り詰めだけの原因とは断定しない。', '',
        'GLiNER2.5-multi-Decide追加診断: stable case hashで166件を選択(うちtest62件)。',
        '候補はRuri SKU-only top1を固定。SKU-only選択はdev top1 .9368、title+SKU .7579に基づく。',
        '元GLiNER summaryのmetricsは166件全体の診断。comparison.jsonにはtest62件だけも別計算。',
        *[f"Decide {mode}: test E2E P={percentage(m['known_case_precision'])}, R={percentage(m['known_case_recall'])}, known false accepts={m['known_false_accept_count_including_wrong_au_row']}" for mode, m in decide_metrics.items()],
        'Decideのこの試行は十分な同一SKU受理・不一致除外に達しなかった。', '',
        'この検証から候補検索と最終同一SKU判定を分ける。候補検索は小型CPU埋め込みで実行可能。',
        '最終判定には商品ページの固定仕様と選択SKUの属性照合、根拠不足時の保留が必要。',
        '現在のモデルの類似度だけでSKUを削除する運用には進めていない。取得原本・不一致・保留は保持。', '',
        '限界: 注釈は同一Lunaモデル系列、人手未確認。WEIMALL主体で、test758件中720件はカーテン。',
        '件数は独立商品数や市場全体の精度を示さない。閾値はこのデータのdevに基づく参考で本番保証ではない。',
        'モデル重み・venvは配布ZIP外。既存固定revision/hashとruntime記録を使って別途準備。',
        '入力・ラベル・予測・コードはそれぞれhashを固定し、過去ZIP・原本を上書きしていない。', '',
        '確認用CSV/原本ZIP: .lab-output/sku-luna-review-delivery-20261010-v1/',
    ]) + '\n'
    with (BASE / 'result-review.txt').open('x', encoding='utf-8') as stream:
        stream.write(report)
    print(json.dumps({'label_counts': label_counts, 'decide_test_metrics': decide_metrics,
                      'comparison': str(BASE / 'comparison.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
