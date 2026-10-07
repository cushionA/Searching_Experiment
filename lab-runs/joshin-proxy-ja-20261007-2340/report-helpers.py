import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DESTINATION = Path('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs') / ROOT.name
MODES = ['fixture-camoufox-ja', 'fixture-fourplay-ja', 'fixture-ja',
         'live-camoufox-ja', 'live-fourplay-ja', 'live-dom-ja']


def read(file):
    return json.loads(file.read_text(encoding='utf-8-sig'))


def write(file, value):
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(directory):
    chunks = read(directory / 'fingerprint.json')
    return json.loads(''.join(chunks[key] for key in sorted(chunks, key=lambda key: int(key.removeprefix('CAMOU_CONFIG_')))))


class ProductPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = 0
        self.fields = {}

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == 'div' and 'search_container' in values.get('class', '').split():
            self.cards += 1
        if tag == 'input' and values.get('data-field') in {'HIT_COUNT', 'MAX_PAGE'}:
            number = values.get('data-value', '')
            if number.isdecimal():
                self.fields[values['data-field']] = int(number)


def product_observation(measured):
    search = measured.get('search') or {}
    if search.get('http_status') != 200 or not measured.get('result_dom_obtained'):
        return None
    parser = ProductPage()
    parser.feed(search.get('dom', ''))
    displayed = re.search(r'([\d,]+)件中\s*(\d+)～(\d+)件', search.get('body_text', ''))
    observed = {'card_count': parser.cards, 'displayed_total': parser.fields.get('HIT_COUNT'),
                'maximum_page_metadata': parser.fields.get('MAX_PAGE'),
                'displayed_range': [int(displayed[2]), int(displayed[3])] if displayed else None,
                'pagination_traversed': False, 'whole_catalog_retrieved': False}
    if not parser.cards or not displayed or int(displayed[1].replace(',', '')) != observed['displayed_total']:
        raise RuntimeError('Successful search lacks corroborating product DOM: ' + measured['arm'])
    if parser.cards != int(displayed[3]) - int(displayed[2]) + 1:
        raise RuntimeError('Product card count disagrees with display range: ' + measured['arm'])
    return observed


def summarize(directory):
    results = read(directory / 'results.json')
    if len(results) != 1:
        raise RuntimeError('Expected exactly one measurement in ' + directory.name)
    result = []
    for measured in results:
        arm = directory / f"{measured['attempt']:02d}-{measured['arm']}"
        records = read(arm / 'ledger.json')['records']
        search = next((record for record in records if record['kind'] == 'document' and record['stage'] == 'search'), None)
        trace = read(arm / 'trace.json')
        events = {}
        for event in trace:
            count = events.setdefault(event['type'], {'count': 0, 'trusted': 0})
            count['count'] += 1
            count['trusted'] += event['isTrusted'] is True
        result.append({
            'run': directory.name,
            'arm': measured['arm'],
            'homepage_status': (measured.get('homepage') or {}).get('http_status'),
            'search_status': (measured.get('search') or {}).get('http_status'),
            'search_success': measured.get('search_success'),
            'result_dom_obtained': measured.get('result_dom_obtained'),
            'product_dom_observation': None if directory.name.startswith('fixture-') else product_observation(measured),
            'stop_reason': measured.get('stop_reason'),
            'verification': measured.get('verification'),
            'fixture_verification': measured.get('fixture_verification'),
            'events': events,
            'search_cookie_names': search.get('cookie_names') if search else None,
            'homepage_cookie_names': (measured.get('homepage') or {}).get('environment', {}).get('cookie_names'),
            'environment': (measured.get('homepage') or {}).get('environment'),
            'measurement': str(arm.relative_to(ROOT) / 'measurement.json'),
        })
    return result
