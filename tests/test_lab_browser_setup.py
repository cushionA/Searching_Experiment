import unittest

from scripts.check_playwright_browser import check_browser


class FakeLocator:
    def inner_text(self):
        return "JS ready"


class FakePage:
    def __init__(self):
        self.content = None

    def set_content(self, content):
        self.content = content

    def locator(self, selector):
        if selector != "#result":
            raise AssertionError(selector)
        return FakeLocator()


class FakeContext:
    def __init__(self):
        self.page = FakePage()

    def new_page(self):
        return self.page


class FakeBrowser:
    version = "test-browser"

    def __init__(self):
        self.context = FakeContext()
        self.closed = False

    def new_context(self, **options):
        if options != {"offline": True, "service_workers": "block"}:
            raise AssertionError(options)
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    def launch(self, **options):
        if options != {"headless": True, "chromium_sandbox": False}:
            raise AssertionError(options)
        return self.browser


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)


class FakeManager:
    def __init__(self, browser):
        self.driver = FakePlaywright(browser)

    def __enter__(self):
        return self.driver

    def __exit__(self, *_args):
        return False


class BrowserSetupTests(unittest.TestCase):
    def test_offline_browser_launch_check(self):
        browser = FakeBrowser()
        result = check_browser(lambda: FakeManager(browser))
        self.assertEqual(result["browser_launch"], "passed")
        self.assertEqual(result["javascript"], "passed")
        self.assertIn("playwright fixture", browser.context.page.content)
        self.assertTrue(browser.closed)


if __name__ == "__main__":
    unittest.main()
