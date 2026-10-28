"""Read-only Wargaming Public API client.

Reference: https://developers.wargaming.net/reference/all/wows/
The three realms have separate accounts. The Chinese realm is not a WG realm.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import aiohttp


class APIError(RuntimeError):
    """A useful public error which never contains a request URL or credential."""

    def __init__(self, code: str | int, message: str):
        self.code = str(code)
        self.message = message
        super().__init__(message)


REALMS = {
    "asia": "https://api.worldofwarships.asia/wows/",
    "eu": "https://api.worldofwarships.eu/wows/",
    "na": "https://api.worldofwarships.com/wows/",
}
ALIASES = {
    "as": "asia", "亚洲": "asia", "亚服": "asia", "asia": "asia",
    "欧洲": "eu", "欧服": "eu", "eu": "eu",
    "美服": "na", "美洲": "na", "na": "na", "us": "na",
}
MODES = ("pvp", "pvp_solo", "pvp_div2", "pvp_div3", "rank_solo", "clan")


def normalize_region(region: str) -> str:
    result = ALIASES.get(str(region).strip().lower())
    if result is None:
        raise APIError("REGION", "服务器须为 asia / eu / na；国服不能直接使用 WG 国际服 API。")
    return result


def positive_id(value: Any, name: str = "ID") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise APIError("INPUT", f"{name} 必须是正整数。") from None
    if result <= 0 or str(value).strip() != str(result):
        raise APIError("INPUT", f"{name} 必须是正整数。")
    return result


class WargamingClient:
    def __init__(self, application_id: str, session=None, language: str = "zh-cn"):
        self.application_id = str(application_id).strip()
        self.language = language
        self._session = session
        self._own_session = session is None
        self._limit = asyncio.Semaphore(4)
        self._ships_cache: dict[str, tuple[float, list[dict]]] = {}
        self._info_cache: dict[str, tuple[float, dict]] = {}

    async def _request(self, region: str, method: str, **params) -> dict | list:
        region = normalize_region(region)
        if not self.application_id:
            raise APIError("NO_APPLICATION_ID", "尚未填写 WG application_id，请按安装指南申请并在插件配置中填写。")
        if self._session is None:
            self._session = aiohttp.ClientSession()
        params = {"application_id": self.application_id, "language": self.language, **params}
        async with self._limit:
            for attempt in range(3):
                retry = False
                try:
                    async with self._session.get(
                        REALMS[region] + method.strip("/") + "/",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=20),
                        allow_redirects=False,
                    ) as response:
                        if response.status == 429 or 500 <= response.status <= 599:
                            retry = True
                        elif response.status != 200:
                            raise APIError("HTTP", f"WG 数据服务返回 HTTP {response.status}，请稍后重试。")
                        else:
                            try:
                                payload = await response.json(content_type=None)
                            except (ValueError, json.JSONDecodeError):
                                raise APIError("FORMAT", "WG 数据服务返回了无效的 JSON。") from None
                            if not isinstance(payload, dict):
                                raise APIError("FORMAT", "WG 数据服务返回格式异常。")
                            if payload.get("status") == "error":
                                error = payload.get("error") or {}
                                token = str(error.get("message", "API_ERROR"))
                                token = token if re.fullmatch(r"[A-Z0-9_]{1,80}", token) else "API_ERROR"
                                if token in ("REQUEST_LIMIT_EXCEEDED", "SOURCE_REQUEST_LIMIT_EXCEEDED"):
                                    retry = True
                                else:
                                    raise APIError(error.get("code", "WG"), f"WG 接口拒绝请求：{token}。请核对配置、玩家 ID 和隐私设置。")
                            elif payload.get("status") != "ok" or "data" not in payload:
                                raise APIError("FORMAT", "WG 数据服务缺少成功状态或 data 字段。")
                            else:
                                return payload
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    retry = True
                if retry and attempt < 2:
                    await asyncio.sleep(0.5 * 2 ** attempt)
            raise APIError("UNAVAILABLE", "暂时无法连接 WG 数据服务，或请求达到限额；请稍后再试。") from None

    async def search_players(self, region: str, query: str) -> list[dict]:
        query = str(query).strip()
        if not 1 <= len(query) <= 24:
            raise APIError("INPUT", "请输入 1–24 个字符的完整游戏昵称。")
        payload = await self._request(region, "account/list", search=query, type="exact", limit=20)
        data = payload["data"]
        if not isinstance(data, list):
            raise APIError("FORMAT", "玩家搜索结果格式异常。")
        return data

    async def account(self, region: str, account_id: int) -> dict:
        account_id = positive_id(account_id, "玩家 ID")
        payload = await self._request(
            region, "account/info", account_id=account_id,
            extra="statistics.pvp_solo,statistics.pvp_div2,statistics.pvp_div3,statistics.rank_solo,statistics.clan",
        )
        data = payload["data"].get(str(account_id)) if isinstance(payload["data"], dict) else None
        if not isinstance(data, dict):
            raise APIError("PLAYER_NOT_FOUND", "未找到此玩家；请核对服务器和玩家 ID，或确认战绩是否隐藏。")
        return data

    async def player_ships(self, region: str, account_id: int, ship_id: int | None = None) -> list[dict]:
        account_id = positive_id(account_id, "玩家 ID")
        params = {"account_id": account_id, "extra": "pvp_solo,pvp_div2,pvp_div3,rank_solo,clan"}
        if ship_id is not None:
            params["ship_id"] = positive_id(ship_id, "舰船 ID")
        payload = await self._request(region, "ships/stats", **params)
        data = payload["data"].get(str(account_id)) if isinstance(payload["data"], dict) else None
        if data is None:
            raise APIError("SHIP_STATS_HIDDEN", "没有可读取的舰船战绩；玩家可能隐藏了战绩，或服务器/ID 不正确。")
        if not isinstance(data, list):
            raise APIError("FORMAT", "单船战绩返回格式异常。")
        return data

    async def ships(self, region: str, search: str = "") -> list[dict]:
        """Return every API-listed ship, with exact/fuzzy local name filtering.

        Profiles are API default/module configurations, not captain-built values.
        The public API may lag a game update; completeness refers to this API.
        """
        region = normalize_region(region)
        cached = self._ships_cache.get(region)
        if cached is None or time.monotonic() - cached[0] > 21600:
            result: list[dict] = []
            page = 1
            while True:
                payload = await self._request(region, "encyclopedia/ships", page_no=page, limit=100)
                data = payload["data"]
                if not isinstance(data, dict):
                    raise APIError("FORMAT", "舰船百科返回格式异常。")
                result.extend(item for item in data.values() if isinstance(item, dict))
                meta = payload.get("meta") or {}
                pages = meta.get("page_total")
                if (pages is not None and page >= int(pages)) or not data or (pages is None and len(data) < 100):
                    break
                page += 1
                if page > 100:
                    raise APIError("FORMAT", "舰船百科分页超过预期，未将不完整数据当作完整列表。")
            self._ships_cache[region] = (time.monotonic(), result)
        else:
            result = cached[1]
        query = str(search).strip().casefold()
        if not query:
            return list(result)
        exact = [x for x in result if query in (str(x.get("ship_id", "")), str(x.get("name", "")).casefold(), str(x.get("name_short", "")).casefold())]
        if exact:
            return exact
        return [x for x in result if query in str(x.get("name", "")).casefold() or query in str(x.get("name_short", "")).casefold()]

    async def ship_profile(self, region: str, ship_id: int) -> dict:
        ship_id = positive_id(ship_id, "舰船 ID")
        payload = await self._request(region, "encyclopedia/shipprofile", ship_id=ship_id)
        data = payload["data"].get(str(ship_id)) if isinstance(payload["data"], dict) else None
        if not isinstance(data, dict):
            raise APIError("SHIP_NOT_FOUND", "WG API 未提供此舰船的详细配置，可能是新舰/测试舰或 ID 不正确。")
        return data

    async def info(self, region: str) -> dict:
        """API's current encyclopedia version/languages, cached for one hour."""
        region = normalize_region(region)
        cached = self._info_cache.get(region)
        if cached is not None and time.monotonic() - cached[0] < 3600:
            return dict(cached[1])
        payload = await self._request(region, "encyclopedia/info")
        if not isinstance(payload["data"], dict):
            raise APIError("FORMAT", "舰船百科版本信息格式异常。")
        self._info_cache[region] = (time.monotonic(), payload["data"])
        return dict(payload["data"])

    async def clan(self, region: str, account_id: int) -> dict | None:
        account_id = positive_id(account_id, "玩家 ID")
        payload = await self._request(region, "clans/accountinfo", account_id=account_id, extra="clan")
        data = payload["data"].get(str(account_id)) if isinstance(payload["data"], dict) else None
        return data if isinstance(data, dict) else None

    async def clan_info(self, region: str, clan_id: int) -> dict | None:
        clan_id = positive_id(clan_id, "公会 ID")
        payload = await self._request(region, "clans/info", clan_id=clan_id, extra="members")
        data = payload["data"].get(str(clan_id)) if isinstance(payload["data"], dict) else None
        return data if isinstance(data, dict) else None

    async def close(self):
        if self._session is not None and self._own_session:
            await self._session.close()
            self._session = None
