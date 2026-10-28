"""Authenticated adapter for an existing Kokomi V4 installation.

Run in Kokomi's own Python environment, after configuring its legitimate data
service credentials. This adapter does not grant access to that service.
Only the user's local original engine is imported; V5 mock code is rejected.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import importlib
import inspect
import logging
import os
import shlex
import sys
from pathlib import Path

from aiohttp import web


class BridgeError(RuntimeError):
    pass


def load_dispatcher(kokomi_root: Path):
    root = kokomi_root.expanduser().resolve()
    if not root.is_dir() or not (root / "command_select.py").is_file():
        raise BridgeError("指定目录内没有 command_select.py。请选择解压后的原版 Kokomi V4 包目录；V5 main 不兼容。")
    if not root.name.isidentifier():
        raise BridgeError("Kokomi 包文件夹名须为 Python 可导入名称，请将它命名为 Kokomi_Bot。")
    sys.path.insert(0, str(root.parent))
    os.chdir(root.parent)
    try:
        module = importlib.import_module(root.name + ".command_select")
        function = module.select_funtion.main
        inspect.signature(function).bind(
            msg=["wws", "help"], user_id="0", user_data={}, platform="qq_bot",
            platform_id="astrbot", channel_id="0", platform_data={},
        )
    except (ImportError, AttributeError, TypeError) as exc:
        raise BridgeError(f"无法加载 V4 dispatcher（{type(exc).__name__}）。请核对目录、原版依赖和版本，不能使用 V5 mock。") from None
    return function, root


def create_app(dispatcher, root: Path, token: str) -> web.Application:
    root = root.resolve()
    if len(token) < 24:
        raise BridgeError("桥接密钥至少 24 个字符；请在 WOWS_KOKOMI_BRIDGE_TOKEN 环境变量中设置随机密钥。")
    lock = asyncio.Lock()

    @web.middleware
    async def authenticate(request, handler):
        provided = request.headers.get("Authorization", "")
        if not hmac.compare_digest(provided.encode(), ("Bearer " + token).encode()):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=65536)

    async def health(request):
        return web.json_response({"ok": True, "protocol": 1, "engine": "Kokomi V4 dispatcher", "dispatcher_loaded": True, "upstream_verified": False})

    async def command(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise BridgeError("invalid_request")
            text = payload.get("command")
            if not isinstance(text, str) or not 1 <= len(text) <= 4000:
                raise BridgeError("invalid_command")
            words = shlex.split(text)
            if not words or words[0].lower().lstrip("/") != "wws":
                raise BridgeError("command_must_start_with_wws")
            words[0] = "wws"
            strings = {}
            for key, default in (("user_id", ""), ("platform", "qq_bot"), ("platform_id", "astrbot"), ("channel_id", "")):
                value = payload.get(key, default)
                if not isinstance(value, str) or len(value) > 256:
                    raise BridgeError("invalid_identity")
                strings[key] = value
            if not strings["user_id"]:
                raise BridgeError("missing_user_id")
            if strings["platform"] not in {"qq_bot", "discord", "qq_group", "qq_guild"}:
                raise BridgeError("unsupported_platform")
            # Do not import client-declared is_admin/role fields into Kokomi.
            # Its own configured root/admin IDs are the authority.
            user_data, platform_data = {}, {}
            # Original V4 uses shared temp/local state. Serialize its dispatcher.
            async with lock:
                result = await dispatcher(msg=words, user_data=user_data, platform_data=platform_data, **strings)
                if not isinstance(result, dict):
                    raise BridgeError("invalid_engine_response")
                if result.get("type") == "msg" and isinstance(result.get("msg"), str):
                    return web.json_response({"ok": True, "type": "msg", "msg": result["msg"][:30000]})
                if result.get("type") != "img" or not isinstance(result.get("img"), (str, Path)):
                    raise BridgeError("engine_must_return_msg_or_image_file")
                path = Path(result["img"])
                if not path.is_absolute():
                    path = Path.cwd() / path
                path = path.resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    raise BridgeError("image_must_be_inside_kokomi_directory") from None
                if not path.is_file() or path.stat().st_size > 12 * 1024 * 1024:
                    raise BridgeError("image_must_be_inside_kokomi_directory_and_under_12mb")
                image_data = path.read_bytes()
                return web.json_response({"ok": True, "type": "img", "image_base64": base64.b64encode(image_data).decode("ascii")})
        except (BridgeError, ValueError, UnicodeError):
            return web.json_response({"ok": False, "error": "Invalid command, configuration or image output. Check V4 setup."}, status=400)
        except Exception as exc:
            # Upstream exception text can contain its API password or URL.
            logging.error("Kokomi dispatcher failed: %s", type(exc).__name__)
            return web.json_response({"ok": False, "error": "Original Kokomi engine failed. Check its authorized data service."}, status=502)

    app.router.add_get("/health", health)
    app.router.add_post("/command", command)
    return app


def main():
    parser = argparse.ArgumentParser(description="Connect a configured Kokomi V4 engine to AstrBot")
    parser.add_argument("--kokomi-root", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=19090, type=int)
    args = parser.parse_args()
    try:
        token = os.environ.get("WOWS_KOKOMI_BRIDGE_TOKEN", "") or os.environ.get("KOKOMI_BRIDGE_TOKEN", "")
        if len(token) < 24:
            raise BridgeError("请先设置至少24字符的 WOWS_KOKOMI_BRIDGE_TOKEN 环境变量。")
        dispatcher, root = load_dispatcher(args.kokomi_root)
        app = create_app(dispatcher, root, token)
    except BridgeError as exc:
        parser.exit(2, str(exc) + "\n")
    # Access logs can contain commands/user metadata in other servers; keep ours off.
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
