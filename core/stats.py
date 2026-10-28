"""Statistics are computed from cumulative counters, never invented history."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from .wg import APIError, MODES, normalize_region, positive_id


MODE_LABELS = {
    "pvp": "随机战（含组队）", "pvp_solo": "随机战单野",
    "pvp_div2": "随机战双人组队", "pvp_div3": "随机战三人组队",
    "rank_solo": "排位战", "clan": "公会战",
}
COUNTERS = (
    "battles", "wins", "losses", "draws", "damage_dealt", "frags", "xp",
    "survived_battles", "damage_scouting", "art_agro", "torpedo_agro",
    "ships_spotted", "planes_killed", "control_captured_points",
    "control_dropped_points", "main_battery.hits", "main_battery.shots",
    "torpedoes.hits", "torpedoes.shots",
)


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def totals_from(raw: dict) -> dict:
    result = {}
    for key in COUNTERS:
        value: Any = raw
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        value = _number(value)
        if value is not None:
            result[key] = value
    if result.get("battles", 0) < 0:
        raise APIError("FORMAT", "API 返回负场次，不能计算战绩。")
    return result


def metrics_from(totals: dict) -> dict:
    battles = int(totals.get("battles", 0))

    def per(key, scale=1):
        value = totals.get(key)
        return value / battles * scale if battles > 0 and _number(value) is not None else None

    shots = totals.get("main_battery.shots", 0)
    hits = totals.get("main_battery.hits")
    potential = None
    if "art_agro" in totals and "torpedo_agro" in totals and battles > 0:
        potential = (totals["art_agro"] + totals["torpedo_agro"]) / battles
    return {
        "battles": battles,
        "wins": totals.get("wins"),
        "win_rate": per("wins", 100),
        "avg_damage": per("damage_dealt"),
        "avg_frags": per("frags"),
        "avg_xp": per("xp"),
        "survival_rate": per("survived_battles", 100),
        "main_battery_hit_rate": hits / shots * 100 if shots > 0 and _number(hits) is not None else None,
        "avg_spotting": per("damage_scouting"),
        "avg_potential": potential,
        "avg_ships_spotted": per("ships_spotted"),
        "pr": None,
        "pr_note": "未接入带版本与来源的单船期待值数据，故不计算 PR。",
        "xp_note": "API XP 包含高级账号加成，不等同于基础经验。",
    }


def delta_totals(current: dict, baseline: dict) -> dict:
    result = {}
    for key in COUNTERS:
        if key in current and key in baseline:
            difference = current[key] - baseline[key]
            if difference < 0:
                raise APIError("COUNTER_RESET", "累计战绩计数回退，旧快照不能用于近期计算。请从回退后的快照重新积累。")
            result[key] = difference
    if "battles" not in result:
        raise APIError("NO_BATTLES", "快照缺少场次计数，无法计算近期战绩。")
    return result


class StatsService:
    def __init__(self, client, storage):
        self.client = client
        self.storage = storage

    @staticmethod
    def _mode(mode: str) -> str:
        if mode not in MODES:
            raise APIError("MODE", "模式须为 pvp / pvp_solo / pvp_div2 / pvp_div3 / rank_solo / clan。")
        return mode

    def _result(self, region, account_id, account, raw, mode, ship_id=0):
        if not isinstance(raw, dict) or "battles" not in raw:
            raise APIError("NO_STATS", f"没有可读取的{MODE_LABELS[mode]}战绩；可能尚未游玩该模式或战绩已隐藏。")
        totals = totals_from(raw)
        sampled_at = time.time()
        self.storage.save_snapshot(region, account_id, mode, totals, at=sampled_at, ship_id=ship_id)
        return {
            "status": "ok", "region": region, "account_id": account_id,
            "nickname": account.get("nickname", str(account_id)),
            "mode": mode, "mode_label": MODE_LABELS[mode],
            "ship_id": ship_id, "metrics": metrics_from(totals), "raw": raw,
            "totals": totals, "updated_at": account.get("stats_updated_at") or account.get("updated_at"),
            "sampled_at": sampled_at,
            "source": "Wargaming Public API",
        }

    async def account_overview(self, region: str, account_id: int, mode: str = "pvp") -> dict:
        region = normalize_region(region)
        account_id = positive_id(account_id, "玩家 ID")
        mode = self._mode(mode)
        account = await self.client.account(region, account_id)
        statistics = account.get("statistics")
        raw = statistics.get(mode) if isinstance(statistics, dict) else None
        return self._result(region, account_id, account, raw, mode)

    async def ship_overview(self, region: str, account_id: int, ship_id: int, mode: str = "pvp") -> dict:
        region = normalize_region(region)
        account_id = positive_id(account_id, "玩家 ID")
        ship_id = positive_id(ship_id, "舰船 ID")
        mode = self._mode(mode)
        account, ships = await asyncio.gather(
            self.client.account(region, account_id), self.client.player_ships(region, account_id, ship_id)
        )
        ship = next((item for item in ships if item.get("ship_id") == ship_id), None)
        if ship is None:
            raise APIError("NO_SHIP_STATS", "此玩家没有这艘舰船的公开战绩。")
        return self._result(region, account_id, account, ship.get(mode), mode, ship_id)

    async def recent(self, region: str, account_id: int, days: int, mode: str = "pvp", ship_id: int = 0) -> dict:
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise APIError("DAYS", "近期天数须为 1–365 的整数。") from None
        if not 1 <= days <= 365:
            raise APIError("DAYS", "近期天数须为 1–365 的整数。")
        current = await (self.ship_overview(region, account_id, ship_id, mode) if ship_id else self.account_overview(region, account_id, mode))
        now = current["sampled_at"]
        cutoff = now - days * 86400
        baseline = self.storage.baseline(current["region"], current["account_id"], mode, cutoff, ship_id)
        if baseline is None:
            baseline = self.storage.earliest_snapshot(current["region"], current["account_id"], mode, cutoff, now - 1, ship_id)
        if baseline is None:
            return {
                **current, "status": "collecting", "requested_days": days,
                "message": "已保存首份快照。WG 公共接口不提供任意历史区间；今后有第二份快照后才能计算真实近期差值。",
                "metrics": None,
            }
        delta = delta_totals(current["totals"], baseline["totals"])
        actual_days = (now - baseline["at"]) / 86400
        return {
            **current, "status": "ok", "requested_days": days,
            "period_start": baseline["at"], "period_end": now,
            "actual_days": actual_days, "metrics": metrics_from(delta), "totals": delta,
            "baseline": baseline, "current": current,
            "period_note": f"按本地实际采样区间计算，覆盖 {actual_days:.2f} 天；并非精确逐局日志。",
        }

    async def fleet_overview(self, region: str, account_id: int, mode: str = "pvp") -> dict:
        """Public historical ship records, not an authenticated inventory."""
        region = normalize_region(region)
        account_id = positive_id(account_id, "玩家 ID")
        self._mode(mode)
        account, records, encyclopedia = await asyncio.gather(
            self.client.account(region, account_id), self.client.player_ships(region, account_id), self.client.ships(region)
        )
        metadata = {item["ship_id"]: item for item in encyclopedia if "ship_id" in item}
        rows, unclassified = [], 0
        sampled_at = time.time()
        for record in records:
            raw = record.get(mode)
            if not isinstance(raw, dict) or "battles" not in raw:
                continue
            ship_id = record.get("ship_id")
            if not isinstance(ship_id, int):
                continue
            totals = totals_from(raw)
            if totals.get("battles", 0) == 0:
                continue
            ship = metadata.get(ship_id, {})
            if not ship:
                unclassified += 1
            rows.append({
                "ship_id": ship_id, "ship_name": ship.get("name", str(ship_id)),
                "tier": ship.get("tier"), "type": ship.get("type"), "nation": ship.get("nation"),
                "metrics": metrics_from(totals), "totals": totals,
            })
            self.storage.save_snapshot(region, account_id, mode, totals, at=sampled_at, ship_id=ship_id)
        rows.sort(key=lambda row: (-row["metrics"]["battles"], row["ship_id"]))
        return {
            "status": "ok", "region": region, "account_id": account_id,
            "nickname": account.get("nickname", str(account_id)), "mode": mode,
            "mode_label": MODE_LABELS[mode], "rows": rows, "unclassified_ships": unclassified,
            "source": "Wargaming Public API", "sampled_at": sampled_at,
            "scope": "有公开历史战绩的舰船；不代表当前港口拥有的舰船",
        }

    async def class_overview(self, region: str, account_id: int, ship_class: str, mode: str = "pvp") -> dict:
        classes = {"cv": "AirCarrier", "bb": "Battleship", "ca": "Cruiser", "dd": "Destroyer", "ss": "Submarine"}
        ship_class = str(ship_class).lower()
        if ship_class not in {*classes, "surface"}:
            raise APIError("CLASS", "舰种须为 cv / bb / ca / dd / ss / surface。")
        fleet = await self.fleet_overview(region, account_id, mode)
        rows = [row for row in fleet["rows"] if row["type"] in ({"Battleship", "Cruiser", "Destroyer"} if ship_class == "surface" else {classes[ship_class]})]
        if not rows:
            raise APIError("NO_CLASS_STATS", "没有此舰种在该模式下的公开战绩。")
        # Missing fields stay missing rather than treating unavailable data as zero.
        common = set.intersection(*(set(row["totals"]) for row in rows))
        totals = {key: sum(row["totals"][key] for row in rows) for key in common}
        return {
            **fleet, "rows": rows, "ship_class": ship_class, "totals": totals,
            "metrics": metrics_from(totals), "ship_count": len(rows),
            "scope": "按已识别舰种的公开单船累计计数加权聚合；与全账号统计口径可能有差异",
        }

    async def leaderboard(self, region: str, ship_id: int, candidates, min_battles: int = 20, limit: int = 10, mode: str = "pvp", sort_by: str = "avg_damage") -> dict:
        region = normalize_region(region)
        ship_id = positive_id(ship_id, "舰船 ID")
        self._mode(mode)
        if sort_by not in {"avg_damage", "win_rate", "battles", "avg_frags", "avg_xp", "survival_rate"}:
            raise APIError("SORT", "排行榜排序字段不受支持。")
        min_battles, limit = int(min_battles), int(limit)
        if not 1 <= min_battles <= 100000 or not 1 <= limit <= 100:
            raise APIError("INPUT", "最低场次须为 1–100000，返回人数须为 1–100。")
        ids = list(dict.fromkeys(positive_id(item.get("account_id") if isinstance(item, dict) else item, "候选玩家 ID") for item in candidates))
        if len(ids) > 100:
            raise APIError("INPUT", "每次最多比较 100 名候选玩家。")
        rows, exclusions = [], []
        # Bounded batches also bound tasks if a group has many bindings.
        for index in range(0, len(ids), 4):
            batch = ids[index:index + 4]
            results = await asyncio.gather(*(self.ship_overview(region, item, ship_id, mode) for item in batch), return_exceptions=True)
            for account_id, result in zip(batch, results):
                if isinstance(result, APIError):
                    exclusions.append({"account_id": account_id, "reason": result.message})
                elif isinstance(result, BaseException):
                    raise result
                elif result["metrics"]["battles"] < min_battles:
                    exclusions.append({"account_id": account_id, "reason": f"单船该模式少于 {min_battles} 场"})
                elif result["metrics"].get(sort_by) is None:
                    exclusions.append({"account_id": account_id, "reason": "API 缺少排序所需数据"})
                else:
                    rows.append({"account_id": account_id, "nickname": result["nickname"], "metrics": result["metrics"]})
        rows.sort(key=lambda row: (-row["metrics"][sort_by], -row["metrics"]["battles"], row["account_id"]))
        return {
            "scope": "候选玩家池（非全服榜）", "region": region, "ship_id": ship_id,
            "mode": mode, "mode_label": MODE_LABELS[mode], "sort_by": sort_by,
            "min_battles": min_battles, "candidate_count": len(ids), "qualified_count": len(rows),
            "rows": [{"rank": index + 1, **row} for index, row in enumerate(rows[:limit])],
            "exclusions": exclusions, "source": "Wargaming Public API",
        }
