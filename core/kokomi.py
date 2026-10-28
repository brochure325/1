"""Client for the separately installed, user-authorized Kokomi V4 engine.

No upstream code or assets are redistributed. Original command dispatch runs
in another process so its packages/dependencies do not collide with AstrBot.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image


class KokomiError(RuntimeError):
    pass


class KokomiClient:
    def __init__(self, base_url: str, token: str, output_dir, session=None, timeout: int = 90):
        self.base_url = str(base_url).strip().rstrip("/")
        self.token = str(token).strip()
        self.output_dir = Path(output_dir)
        self._session = session
        self._own_session = session is None
        self.timeout = max(10, min(int(timeout), 300))
        if self.base_url:
            url = urlsplit(self.base_url)
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise KokomiError("Kokomi 桥接地址应是 http(s)://主机:端口，不要在地址中放账号或密钥。")

    async def _request(self, path: str, payload: dict | None = None) -> dict:
        if not self.base_url or not self.token:
            raise KokomiError("尚未配置 Kokomi 桥接地址与密钥；请按 docs/Kokomi接入.md 安装原版 V4 引擎并启动桥接服务。")
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            # Never retry command submissions: bind/update may change upstream data.
            method = self._session.get if payload is None else self._session.post
            kwargs = {"headers": {"Authorization": f"Bearer {self.token}"}, "timeout": aiohttp.ClientTimeout(total=self.timeout), "allow_redirects": False}
            if payload is not None:
                kwargs["json"] = payload
            async with method(self.base_url + path, **kwargs) as response:
                if response.status == 401:
                    raise KokomiError("Kokomi 桥接密钥不一致，请核对插件配置与服务端环境变量。")
                if response.status != 200:
                    raise KokomiError(f"Kokomi 桥接返回 HTTP {response.status}；请检查桥接窗口和原版 Kokomi 配置。")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > 18 * 1024 * 1024:
                        raise KokomiError("Kokomi 桥接返回数据过大，已停止接收。")
                import json
                try:
                    result = json.loads(body)
                except (ValueError, UnicodeError):
                    raise KokomiError("Kokomi 桥接返回的不是有效 JSON，请核对地址和端口。") from None
                if not isinstance(result, dict):
                    raise KokomiError("Kokomi 桥接返回格式不正确。")
                if result.get("ok") is not True:
                    raise KokomiError("原版 Kokomi 未能完成请求，请检查其数据接口授权和运行窗口。")
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise KokomiError("无法连接 Kokomi 桥接，或等待原版引擎超时。请确认服务已启动、地址可达。") from None

    async def health(self) -> dict:
        return await self._request("/health")

    async def send(self, command: str, user_id: str, platform: str = "qq_bot", platform_id: str = "astrbot", channel_id: str = "", user_data: dict | None = None, platform_data: dict | None = None) -> dict:
        result = await self._request("/command", {
            "command": str(command), "user_id": str(user_id), "platform": str(platform),
            "platform_id": str(platform_id), "channel_id": str(channel_id),
            "user_data": user_data or {}, "platform_data": platform_data or {},
        })
        if result.get("type") == "msg" and isinstance(result.get("msg"), str):
            return {"type": "msg", "msg": result["msg"][:30000]}
        if result.get("type") != "img" or not isinstance(result.get("image_base64"), str):
            raise KokomiError("Kokomi 桥接没有返回文字或图片。")
        try:
            blob = base64.b64decode(result["image_base64"], validate=True)
            if len(blob) > 12 * 1024 * 1024:
                raise KokomiError("原版 Kokomi 图片超过 12 MB。")
            with Image.open(io.BytesIO(blob)) as img:
                formats = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
                suffix = formats.get(img.format)
                if suffix is None or img.width * img.height > 25000000:
                    raise KokomiError("原版 Kokomi 图片格式或尺寸不受支持。")
                img.verify()
        except (ValueError, binascii.Error, OSError, Image.DecompressionBombError):
            raise KokomiError("Kokomi 返回的图片无法验证，未保存为可发送图片。") from None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / ("kokomi_" + uuid.uuid4().hex + suffix)
        path.write_bytes(blob)
        return {"type": "img", "img": str(path.resolve())}

    async def close(self):
        if self._session is not None and self._own_session:
            await self._session.close()
            self._session = None
