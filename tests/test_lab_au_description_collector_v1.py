from pathlib import Path
import json
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = ROOT / 'experiments/sku-matching/collect_au_description_v1.cjs'


def page(info):
    data = {'props': {'pageProps': {'initialState': {'itemDetails': {
        'itemPageInfo': {'itemInfo': info}}}}}}
    serialized = json.dumps(data).replace('<', '\\u003c')
    return '<script id="__NEXT_DATA__" type="application/json">' + serialized + '</script>'


def collect(html, expected='42'):
    result = subprocess.run(['node', str(EXTRACTOR)], cwd=ROOT,
        input=json.dumps({'html': html, 'expectedProductId': expected}),
        text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class AuDescriptionCollectorTest(unittest.TestCase):
    def test_later_field_survives_deduplication_and_preserves_cells(self):
        same = '<p>first description</p>'
        info = {'lotNumber': '42', 'itemTitle': 'product', 'extraItemComment': same,
            'extraItemCommentWithVideo': same,
            'detailComment': '<table><tr><th>容量</th><td>250g×2P</td></tr></table>'}
        result = collect(page(info))
        self.assertEqual(result['status'], 'collected')
        self.assertEqual(len(result['sections']), 2)
        self.assertEqual(len(result['sections'][0]['jsonPaths']), 2)
        self.assertEqual(result['sections'][1]['text'], '容量\t250g×2P')
        self.assertEqual(result['sections'][1]['tableCount'], 1)
        self.assertTrue(result['missingJsonPaths'][0].endswith('.itemComment'))

    def test_media_does_not_become_description_text(self):
        result = collect(page({'lotNumber': '42', 'extraItemComment':
            '<script>fake specification</script><img src="a.png" alt="Lサイズ">'}))
        self.assertEqual(result['status'], 'failure')
        self.assertEqual(result['reason'], 'NO_DESCRIPTION_TEXT')
        self.assertEqual(result['sections'][0]['text'], '')
        self.assertTrue(result['sections'][0]['mediaOnly'])
        self.assertEqual(result['sections'][0]['images'], [{'src': 'a.png', 'alt': 'Lサイズ'}])

    def test_product_mismatch_and_ambiguous_script_are_failures(self):
        self.assertEqual(collect(page({'lotNumber': '43'}))['reason'], 'PRODUCT_ID_MISMATCH')
        html = page({'lotNumber': '42'})
        self.assertEqual(collect(html + html)['reason'], 'AMBIGUOUS_NEXT_DATA')

    def test_plain_text_field_is_collected_without_table(self):
        result = collect(page({'lotNumber': 42, 'itemComment':
            '<div>商品情報<br>素材&nbsp;綿</div><style>hidden</style>'}))
        self.assertEqual(result['status'], 'collected')
        self.assertEqual(result['sections'][0]['text'], '商品情報\n素材 綿')
        self.assertEqual(result['sections'][0]['tableCount'], 0)


if __name__ == '__main__':
    unittest.main()
