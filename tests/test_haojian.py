"""Synthetic response fixtures exercise the actual website's observed format."""
import json
import os
import unittest
from unittest.mock import patch

from core.haojian import HaojianClient, HaojianError, _inline_json


BASE = "https://iwarship.net"
INDEX = '''<html><script>
var openCollection = false;
var openDetail = true;
const serverVersion = {"ASIA":{"versionId":42,"gameVersion":"test.1"}};
</script></html>'''
NAMES = {"PJSB999": {"zh": "测试舰", "en": "Fixture", "py": "csj"}}
DATA = {
    "hull": json.dumps([{"id": 123, "index": "PJSB999", "level": 10,
                         "species": "Battleship", "nation": "Japan", "health": 50000}]),
    "artillery": json.dumps([{"index": "PJSB999", "shotDelay": 30,
                              "upgrade": {"art": "stock"}},
                             {"index": "PJSB999", "shotDelay": 28,
                              "upgrade": {"art": "upgraded"}}]),
}
RAW = {"index": "PJSB999", "name": "测试舰", "unit": {"_Hull": [{"name": "A"}]},
       "modernization": [[{"name": "测试升级品"}]], "component": {"A_Hull": {"hp": 50000}},
       "ability": [[{"title": "维修小组"}]]}
DETAIL = "<script>var ship = JSON.parse(" + json.dumps(json.dumps(RAW)) + ");</script>"


class Content:
    def __init__(self, text):
        self.body = text.encode("utf-8")

    async def iter_chunked(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class Response:
    def __init__(self, text, status=200):
        self.content = Content(text)
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class Session:
    def __init__(self):
        self.requests = []
        self.routes = {
            "/wowsdb/index": (INDEX, 200),
            "/wowsdb/data/ship-name/wg": (json.dumps(NAMES), 200),
            "/wowsdb/data/get-ship-detail/42": (json.dumps(DATA), 200),
            "/wowsdb/detail/42-PJSB999": (DETAIL, 200),
        }
        self.closed = False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        text, status = self.routes[url.removeprefix(BASE)]
        return Response(text, status)

    async def close(self):
        self.closed = True


class HaojianTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = Session()
        self.client = HaojianClient(session=self.session, cookie_env="TEST_HAOJIAN_COOKIE")

    async def test_search_chinese_english_initials_and_numeric_id(self):
        for query in ["测试舰", "Fixture", "csj", "PJSB999", "123"]:
            found = await self.client.search(query)
            self.assertEqual(found[0]["ship_id"], 123)
        self.assertEqual(len(self.session.requests), 3, "the 7.6 MB catalog must be cached")

    async def test_detail_keeps_all_module_variants_and_raw_parameters(self):
        result = await self.client.detail(123)
        self.assertEqual(result["raw"], RAW)
        self.assertEqual(len(result["comparison"]["artillery"]), 2)
        self.assertEqual(result["version"], "test.1")
        self.assertEqual(result["url"], BASE + "/wowsdb/detail/42-PJSB999")
        await self.client.detail("PJSB999")
        self.assertEqual(len(self.session.requests), 4)

    async def test_unknown_ship_is_reported_without_requesting_arbitrary_path(self):
        with self.assertRaises(HaojianError):
            await self.client.detail("../../login")
        self.assertEqual(len(self.session.requests), 3)

    async def test_cookie_is_not_treated_as_successful_login(self):
        with patch.dict(os.environ, {"TEST_HAOJIAN_COOKIE": "session=private"}):
            status = await self.client.status()
        self.assertTrue(status["cookie_configured"])
        self.assertFalse(status["authenticated"])
        self.assertNotIn("private", json.dumps(status))

    async def test_authenticated_status_requires_site_signal(self):
        self.session.routes["/wowsdb/index"] = (INDEX.replace("openCollection = false", "openCollection = true"), 200)
        status = await self.client.status()
        self.assertTrue(status["authenticated"])

    async def test_redirect_is_not_followed_with_cookie(self):
        self.session.routes["/wowsdb/index"] = ("", 302)
        with patch.dict(os.environ, {"TEST_HAOJIAN_COOKIE": "session=private"}):
            with self.assertRaises(HaojianError) as caught:
                await self.client.status()
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(self.session.requests[0][1]["allow_redirects"])

    async def test_cookie_changes_invalidate_authenticated_catalog_cache(self):
        await self.client.search("123")
        with patch.dict(os.environ, {"TEST_HAOJIAN_COOKIE": "session=private"}):
            await self.client.search("123")
        self.assertEqual(len(self.session.requests), 6)

    async def test_status_does_not_mix_new_version_with_cached_comparison(self):
        await self.client.search("123")
        changed = INDEX.replace('"versionId":42', '"versionId":43').replace('"test.1"', '"test.2"')
        self.session.routes["/wowsdb/index"] = (changed, 200)
        status = await self.client.status()
        self.assertEqual(status["version"], "test.2")
        detail = await self.client.detail(123)
        self.assertEqual(detail["version"], "test.1")
        self.assertIn("42-PJSB999", detail["url"])

    async def test_embedded_ship_content_is_json_never_executed(self):
        text = '<script>var ship=JSON.parse("__import__(\\\"os\\\").system(\\\"bad\\\")")</script>'
        with self.assertRaises(HaojianError):
            _inline_json(text, "ship")

    async def test_login_html_returned_instead_of_json_has_clear_error(self):
        self.session.routes["/wowsdb/data/get-ship-detail/42"] = ("<html>login</html>", 200)
        with self.assertRaisesRegex(HaojianError, "JSON"):
            await self.client.search("123")

    async def test_caller_session_is_not_closed(self):
        await self.client.close()
        self.assertFalse(self.session.closed)

    async def test_credentials_cannot_be_sent_to_other_hosts(self):
        for url in ["http://iwarship.net", "https://evil.example", "https://iwarship.net.evil.example",
                    "https://user:pass@iwarship.net", "https://iwarship.net:8443"]:
            with self.assertRaises(ValueError):
                HaojianClient(base_url=url, session=self.session)


if __name__ == "__main__":
    unittest.main()
