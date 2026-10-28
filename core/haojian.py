"""Read-only adapter for Haojian's real WoWS database frontend.

The routes and inline JSON format were verified against the website on
2026-10-28. No login password is collected; an optional existing authorized
session cookie is sent only to iwarship.net, without following redirects.
Third-party ship data is fetched at runtime and is not redistributed here.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import aiohttp


class HaojianError(RuntimeError):
    """A user-readable error that never contains a session cookie."""


SECTION_NAMES = {
    "hull": "船体与隐蔽",
    "artillery": "主炮",
    "torpedoes": "鱼雷",
    "atba": "副炮",
    "airDefense": "防空",
    "aircraft": "舰载机",
    "airSupport": "空袭",
    "asw": "反潜",
    "pingerGun": "潜艇声呐",
    "ability": "消耗品",
    "penetration": "弹道与穿深",
    "specials": "特殊机制",
}


def _inline_json(html: str, name: str) -> dict[str, Any]:
    """Decode the frontend's JSON literal without evaluating JavaScript."""
    if name == "ship":
        match = re.search(
            r'\bvar\s+ship\s*=\s*JSON\.parse\(\s*("(?:[^"\\]|\\.)*")\s*\)',
            html, re.DOTALL,
        )
        if not match:
            raise HaojianError("浩舰详情页格式发生变化，未找到舰船 JSON；请打开原网页核对。")
        try:
            value = json.loads(json.loads(match.group(1)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise HaojianError("浩舰详情页的舰船 JSON 无法解析。") from exc
    else:
        match = re.search(r'\b(?:const|var|let)\s+' + re.escape(name) + r'\s*=\s*', html)
        if not match:
            raise HaojianError("浩舰页面格式发生变化，未找到数据版本信息。")
        try:
            value, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
        except json.JSONDecodeError as exc:
            raise HaojianError("浩舰数据版本信息不是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise HaojianError("浩舰数据格式异常：预期为 JSON 对象。")
    return value


def _decode_rows(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise HaojianError("浩舰比较表格式异常。")
    groups: dict[str, list[dict[str, Any]]] = {}
    try:
        for group in SECTION_NAMES:
            rows = payload.get(group, [])
            if isinstance(rows, str):
                rows = json.loads(rows)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise TypeError(group)
            groups[group] = rows
    except (TypeError, json.JSONDecodeError) as exc:
        raise HaojianError("浩舰比较表字段格式发生变化。") from exc
    if not groups.get("hull"):
        raise HaojianError("浩舰没有返回船体数据，可能需要重新登录或站点正在维护。")
    return groups


def _is_logged_in(html: str) -> bool | None:
    """The site explicitly exposes collection access for its authenticated user."""
    match = re.search(r'\bvar\s+openCollection\s*=\s*(true|false)\b', html)
    return None if not match else match.group(1) == "true"


class HaojianClient:
    """Discover versions, search ships and retrieve complete ship parameters.

    ``region`` is the website's data version selector: ASIA (WG live), PT
    (WG test), RU (Lesta live), RUPT (Lesta test). ASIA is the WG game-data
    branch shared by the supported international regions, not player stats.
    """

    def __init__(
        self,
        base_url: str = "https://iwarship.net",
        cookie_env: str = "WOWS_HAOJIAN_COOKIE",
        session: aiohttp.ClientSession | None = None,
        region: str = "ASIA",
        cache_seconds: int = 3600,
        cookie_file: str | Path | None = None,
    ):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or parsed.hostname not in {"iwarship.net", "www.iwarship.net"}
                or parsed.username or parsed.password or parsed.port not in {None, 443}
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("浩舰地址必须是 https://iwarship.net，避免将登录会话发送到其他网站。")
        region = region.upper()
        if region not in {"ASIA", "PT", "RU", "RUPT"}:
            raise ValueError("浩舰版本应为 ASIA、PT、RU 或 RUPT。")
        self.base_url = base_url.rstrip("/")
        self.cookie_env = cookie_env
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.region = region
        self.cache_seconds = max(60, int(cache_seconds))
        self._session = session
        self._owns_session = session is None
        self._lock = asyncio.Lock()
        self._catalog_at = 0.0
        self._metadata: dict[str, Any] = {}
        self._names: dict[str, Any] = {}
        self._groups: dict[str, list[dict[str, Any]]] = {}
        self._ships: dict[str, dict[str, Any]] = {}
        self._details: dict[str, tuple[float, dict[str, Any]]] = {}
        self._authenticated: bool | None = None
        self._cookie_used: str | None = None

    def _cookie(self) -> str:
        cookie = os.environ.get(self.cookie_env, "").strip() if self.cookie_env else ""
        if not cookie and self.cookie_file:
            try:
                cookie = self.cookie_file.read_text(encoding="utf-8-sig").strip()
            except OSError as exc:
                raise HaojianError("无法读取浩舰会话文件，请检查服务端文件路径和权限。") from exc
        if "\r" in cookie or "\n" in cookie:
            raise HaojianError("浩舰会话应是单行 Cookie 请求头内容，不能含换行。")
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        return cookie

    async def _get(self, path: str) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        headers = {
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.5",
            "User-Agent": "AstrBot-WoWS-Captain/1.0 (read-only ship database)",
        }
        cookie = self._cookie()
        if cookie:
            headers["Cookie"] = cookie
        try:
            async with self._session.get(
                self.base_url + path, headers=headers, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as response:
                if response.status in {401, 403}:
                    raise HaojianError("浩舰拒绝访问，请在官网检查登录状态或当前账号的数据权限。")
                if 300 <= response.status < 400:
                    raise HaojianError("浩舰要求跳转，请在官网重新登录；插件不会跟随跳转发送 Cookie。")
                if response.status == 429:
                    raise HaojianError("浩舰请求过于频繁，请稍后再试。")
                if response.status != 200:
                    raise HaojianError(f"浩舰暂时无法访问（HTTP {response.status}），请稍后再试。")
                # The verified live comparison response is about 7.6 MB. Allow
                # reasonable growth while bounding maintenance/error responses.
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 30 * 1024 * 1024:
                        raise HaojianError("浩舰响应超过 30 MB，请打开网页确认数据格式是否变更。")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8-sig")
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError) as exc:
            raise HaojianError("连接浩舰失败，请检查 AstrBot 所在设备的网络和证书。") from exc

    async def _index(self) -> str:
        html = await self._get("/wowsdb/index")
        self._metadata = _inline_json(html, "serverVersion")
        self._authenticated = _is_logged_in(html)
        return html

    async def status(self) -> dict[str, Any]:
        # Status must not replace the version attached to an already cached
        # catalog; its comparison data and detail page must share one version.
        html = await self._get("/wowsdb/index")
        metadata = _inline_json(html, "serverVersion")
        meta = metadata.get(self.region)
        return {
            "source": "浩舰", "url": self.base_url + "/wowsdb/index",
            "server": self.region,
            "version": meta.get("gameVersion") if isinstance(meta, dict) else None,
            "cookie_configured": bool(self._cookie()),
            "authenticated": _is_logged_in(html),
            "authentication_evidence": "站点 openCollection 标记；不以 Cookie 存在判断登录成功",
            "detail_enabled": bool(re.search(r'\bvar\s+openDetail\s*=\s*true\b', html)),
        }

    async def _ensure_catalog(self) -> None:
        async with self._lock:
            cookie = self._cookie()
            fresh = self._ships and time.monotonic() - self._catalog_at < self.cache_seconds
            if fresh and cookie == self._cookie_used:
                return
            await self._index()
            meta = self._metadata.get(self.region)
            if not isinstance(meta, dict) or not str(meta.get("versionId", "")).isdigit():
                raise HaojianError("浩舰没有提供所选服务器的数据版本。")
            version_id = str(meta["versionId"])
            branch = "lesta" if self.region in {"RU", "RUPT"} else "wg"
            try:
                names = json.loads(await self._get(f"/wowsdb/data/ship-name/{branch}"))
                groups = _decode_rows(json.loads(await self._get(
                    f"/wowsdb/data/get-ship-detail/{version_id}"
                )))
            except json.JSONDecodeError as exc:
                raise HaojianError("浩舰返回了网页或无效 JSON，请检查站点登录状态和接口变化。") from exc
            if not isinstance(names, dict):
                raise HaojianError("浩舰舰名索引格式异常。")
            ships = {}
            for row in groups["hull"]:
                index = str(row.get("index", ""))
                if not re.fullmatch(r"[A-Za-z0-9_]+", index):
                    continue
                name = names.get(index, {})
                if not isinstance(name, dict):
                    name = {}
                ships[index] = {
                    "ship_id": row.get("id"), "index": index,
                    "name": name.get("zh") or row.get("name") or index,
                    "name_en": name.get("en") or "",
                    "tier": row.get("level"), "type": row.get("species"),
                    "nation": row.get("nation"), "version": meta.get("gameVersion"),
                    "url": f"{self.base_url}/wowsdb/detail/{version_id}-{index}",
                }
            if not ships:
                raise HaojianError("浩舰没有返回可用的舰船目录。")
            self._names, self._groups, self._ships = names, groups, ships
            self._details.clear()
            self._catalog_at, self._cookie_used = time.monotonic(), cookie

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        await self._ensure_catalog()
        query = str(query).strip().casefold()
        if not query:
            raise HaojianError("请提供舰名、中英文名称、简拼、舰船 index 或 WG 舰船 ID。")
        exact, partial = [], []
        for index, record in self._ships.items():
            name = self._names.get(index, {})
            values = [index.casefold(), str(record["ship_id"]),
                      str(record["name"]).casefold(), str(record["name_en"]).casefold()]
            if isinstance(name, dict):
                values.append(str(name.get("py", "")).casefold())
            if query in values:
                exact.append(record.copy())
            elif any(query in value for value in values):
                partial.append(record.copy())
        return (exact + partial)[:max(1, min(int(limit), 100))]

    async def detail(self, ship_id: str | int) -> dict[str, Any]:
        await self._ensure_catalog()
        candidates = await self.search(str(ship_id), limit=100)
        key = str(ship_id).strip().casefold()
        exact = [r for r in candidates if key in {
            r["index"].casefold(), str(r["ship_id"]), r["name"].casefold(), r["name_en"].casefold()
        }]
        if exact:
            candidates = exact
        if not candidates:
            raise HaojianError("浩舰当前版本没有找到这艘船，请先搜索并使用舰船 index。")
        if len(candidates) > 1:
            raise HaojianError("存在同名或多个匹配舰船，请使用搜索结果中的舰船 index 精确查询。")
        summary = candidates[0]
        index = summary["index"]
        cached = self._details.get(index)
        if cached and time.monotonic() - cached[0] < self.cache_seconds:
            return cached[1]
        version_id = str(self._metadata[self.region]["versionId"])
        html = await self._get(f"/wowsdb/detail/{version_id}-{index}")
        raw = _inline_json(html, "ship")
        if str(raw.get("index")) != index:
            raise HaojianError("浩舰详情返回的舰船与请求不一致，请打开原网页核对。")
        detail = {
            "summary": summary, "source": "浩舰", "url": summary["url"],
            "version": summary["version"], "server": self.region,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "comparison": {group: [row for row in rows if row.get("index") == index]
                           for group, rows in self._groups.items()},
            "raw": raw,
        }
        self._details[index] = (time.monotonic(), detail)
        return detail

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None
        self._details.clear()
        self._cookie_used = None

