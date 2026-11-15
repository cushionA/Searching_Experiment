import argparse
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from lxml import html


def navigation(element):
    for ancestor in [element, *element.iterancestors()]:
        marker = ' '.join(str(ancestor.get(key, '')) for key in ['role', 'class', 'id']).lower()
        if ancestor.tag in {'nav', 'aside', 'header', 'footer'} or re.search(r'pagination|breadcrumb|sidebar|\bnavigation\b', marker):
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    repo = Path(__file__).resolve().parents[2]
    manifest = json.loads((run / 'common-inputs/manifest.json').read_text(encoding='utf-8'))
    for case in manifest['cases']:
        seed = case['seeds'][0]
        root = html.fromstring((repo / seed['dom']).read_text(encoding='utf-8'))
        payload = json.loads((repo / seed['candidates']).read_text(encoding='utf-8'))
        eligible = []
        for key, members in payload['groups'].items():
            if len(members) < 2 or max(member['repeated_item_count'] for member in members) < 2:
                continue
            survivors = []
            for member in members:
                node = root.getroottree().xpath(member['xpath'])[0]
                url = urlsplit(urljoin(seed['url'], member['href']))
                if url.scheme in {'http', 'https'} and url.hostname in case['allowed_hosts'] and not navigation(node):
                    survivors.append(member)
            if len(survivors) >= 2:
                eligible.append({'group_key': key, 'selector': members[0]['group_selector'] + ' a[href]', 'member_count': len(survivors)})
        inference_needed = len(eligible) > 1
        if inference_needed:
            ranking = json.loads((run / 'minilm' / case['id'] / 'ranking.json').read_text(encoding='utf-8'))
            scores = {group['group_key']: group['mean_score'] for group in ranking['groups']}
            selected = max(eligible, key=lambda group: scores[group['group_key']])
        elif eligible:
            selected = eligible[0]
        else:
            selected = None
        structural = max(eligible, key=lambda group: group['member_count']) if eligible else None
        value = {'kind': 'selector', 'selector_type': 'css', 'attribute': 'href',
                 'selector': selected['selector'] if selected else None,
                 'source_urls': [seed['url']], 'representative_pages': 1, 'eligible_groups': eligible,
                 'model_needed_after_gate': inference_needed,
                 'decision_by': 'model_mean_score' if inference_needed else 'unique_structural_group' if selected else 'abstain',
                 'existing_model_scores_reused': inference_needed, 'new_model_inference_calls': 0,
                 'candidate_specific_manual_selectors': 0, 'gold_input': False,
                 'structural_only_selector': structural['selector'] if structural else None,
                 'same_as_structural_only': bool(selected and structural and selected['selector'] == structural['selector'])}
        directory = run / 'rules/MiniLMFiltered'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{case['id']}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'id': case['id'], 'eligible_groups': len(eligible), 'model_needed': inference_needed,
                          'same_as_structural_only': value['same_as_structural_only'], 'selector': value['selector']}))


if __name__ == '__main__':
    main()
