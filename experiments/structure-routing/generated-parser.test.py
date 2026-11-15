import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('generated_parser', Path(__file__).with_name('generated-parser.py'))
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

PARSER = '''from pathlib import Path
from lxml import html as lxml_html

class WebPageParser:
    def parse(self, html_text: str) -> dict:
        tree = lxml_html.fromstring(html_text)
        product_urls = tree.xpath("//a/@href")
        return {"product_urls": [url.strip() for url in product_urls if url.strip()]}

def main():
    raise RuntimeError("CLI must not execute")

if __name__ == '__main__':
    main()
'''


class ExportedParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.code = Path(self.directory.name) / 'generated.py'

    def write(self, value):
        self.code.write_text(value, encoding='utf-8')
        return loader.load_parser(self.code)

    def test_class_is_unchanged_and_cli_is_excluded(self):
        parser, audit = self.write(PARSER)
        self.assertTrue(audit['class_ast_unchanged'])
        self.assertTrue(audit['excluded_main_guard'])
        self.assertEqual(audit['retained_imports'], ['from lxml import html as lxml_html'])
        self.assertEqual(loader.parse_html(parser, '<a href=" /p/1?q=x#part ">Item</a>'),
                         {'product_urls': ['/p/1?q=x#part']})

    def test_file_read_in_parser_is_rejected(self):
        with self.assertRaises(loader.LoaderError):
            self.write(PARSER.replace('lxml_html.fromstring(html_text)', 'Path(html_text).read_text()'))

    def test_top_level_execution_is_rejected(self):
        with self.assertRaises(loader.LoaderError):
            self.write(PARSER + '\nmain()\n')


if __name__ == '__main__':
    unittest.main()
