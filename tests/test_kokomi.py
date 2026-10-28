"""Exercise our real localhost bridge with a fake original dispatcher.

These tests validate transport/identity/media boundaries, not the availability
of Kokomi's remote service or third-party accounts.
"""

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.kokomi_server import BridgeError, create_app, load_dispatcher
from core.kokomi import KokomiClient, KokomiError


TOKEN = "unit-test-token-only-at-least-24-characters"


class KokomiBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "original_engine"
        self.root.mkdir()
        self.calls = []
        self.result = {"type": "msg", "msg": "原引擎帮助内容"}

        async def fake_dispatcher(**kwargs):
            self.calls.append(kwargs)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        app = create_app(fake_dispatcher, self.root, TOKEN)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.client = KokomiClient(self.url, TOKEN, Path(self.temp.name) / "received")

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_command_and_identity_are_preserved_and_roles_are_not_trusted(self):
        result = await self.client.send('/wws asia "Name With Spaces" recent', "12345", platform_id="bot88", channel_id="group66", user_data={"is_admin": True})
        self.assertEqual(result, {"type": "msg", "msg": "原引擎帮助内容"})
        call = self.calls[0]
        self.assertEqual(call["msg"], ["wws", "asia", "Name With Spaces", "recent"])
        self.assertEqual(call["user_id"], "12345")
        self.assertEqual(call["channel_id"], "group66")
        self.assertEqual(call["platform_id"], "bot88")
        self.assertEqual(call["user_data"], {})
        self.assertEqual(call["platform_data"], {})

    async def test_health_discloses_no_unverified_upstream_success(self):
        health = await self.client.health()
        self.assertTrue(health["dispatcher_loaded"])
        self.assertFalse(health["upstream_verified"])

    async def test_invalid_auth_does_not_call_engine(self):
        client = KokomiClient(self.url, "wrong", self.root)
        try:
            with self.assertRaisesRegex(KokomiError, "密钥"):
                await client.send("wws help", "1")
        finally:
            await client.close()
        self.assertEqual(self.calls, [])

    async def test_image_transport_validates_image_and_writes_opaque_filename(self):
        image_path = self.root / "output.png"
        Image.new("RGB", (32, 24), (20, 40, 60)).save(image_path)
        self.result = {"type": "img", "img": str(image_path)}
        result = await self.client.send("wws me", "1")
        self.assertEqual(result["type"], "img")
        received = Path(result["img"])
        self.assertEqual(received.read_bytes(), image_path.read_bytes())
        self.assertTrue(received.name.startswith("kokomi_"))

    async def test_outside_file_is_not_exfiltrated(self):
        outside = Path(self.temp.name) / "secret.txt"
        outside.write_text("private", encoding="utf-8")
        self.result = {"type": "img", "img": str(outside)}
        with self.assertRaises(KokomiError):
            await self.client.send("wws me", "1")

    async def test_invalid_image_bytes_are_not_sent_as_an_image(self):
        bad = self.root / "bad.png"
        bad.write_text("this is not a png", encoding="utf-8")
        self.result = {"type": "img", "img": str(bad)}
        with self.assertRaisesRegex(KokomiError, "图片无法验证"):
            await self.client.send("wws me", "1")

    async def test_upstream_exception_text_is_not_relayed_or_retried(self):
        self.result = RuntimeError("https://secret.example/?password=DO_NOT_LEAK")
        with self.assertRaises(KokomiError) as caught:
            await self.client.send("wws bind asia Nickname", "1")
        self.assertNotIn("DO_NOT_LEAK", str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    async def test_non_wws_commands_are_rejected(self):
        with self.assertRaises(KokomiError):
            await self.client.send("rm something", "1")
        self.assertEqual(self.calls, [])

    def test_v5_directory_is_not_treated_as_v4(self):
        with self.assertRaisesRegex(BridgeError, "command_select.py"):
            load_dispatcher(self.root)

    def test_empty_or_embedded_url_password_is_rejected(self):
        with self.assertRaises(KokomiError):
            KokomiClient("https://user:password@example.test", TOKEN, self.root)


if __name__ == "__main__":
    unittest.main()
