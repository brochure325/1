"""Offline tests: fake service responses do not claim live API integration."""

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.stats import StatsService, delta_totals, metrics_from, totals_from
from core.storage import Storage
from core.wg import APIError, WargamingClient, normalize_region


SAMPLE = {
    "battles": 10, "wins": 6, "losses": 4, "damage_dealt": 420000,
    "frags": 12, "xp": 15000, "survived_battles": 4,
    "damage_scouting": 180000, "art_agro": 7000000, "torpedo_agro": 500000,
    "main_battery": {"hits": 250, "shots": 1000},
}


class FakeClient:
    def __init__(self):
        self.pvp = dict(SAMPLE)
        self.rank = {**SAMPLE, "battles": 2, "wins": 1}

    async def account(self, region, account_id):
        if account_id == 999:
            raise APIError("HIDDEN", "战绩已隐藏")
        return {"nickname": f"Player{account_id}", "statistics": {"pvp": dict(self.pvp), "rank_solo": dict(self.rank)}, "stats_updated_at": 100}

    async def player_ships(self, region, account_id, ship_id=None):
        if account_id == 999:
            raise APIError("HIDDEN", "战绩已隐藏")
        return [{"ship_id": ship_id or 123, "pvp": dict(self.pvp), "rank_solo": dict(self.rank)}]


class StatsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.client = FakeClient()
        self.stats = StatsService(self.client, self.storage)

    def tearDown(self):
        self.storage.close()

    def test_weighted_metrics_and_zero_battles(self):
        metrics = metrics_from(totals_from(SAMPLE))
        self.assertEqual(metrics["win_rate"], 60)
        self.assertEqual(metrics["avg_damage"], 42000)
        self.assertEqual(metrics["avg_frags"], 1.2)
        self.assertEqual(metrics["main_battery_hit_rate"], 25)
        self.assertEqual(metrics["survival_rate"], 40)
        self.assertEqual(metrics["avg_potential"], 750000)
        self.assertIsNone(metrics["pr"])
        empty = metrics_from({"battles": 0, "wins": 0})
        self.assertIsNone(empty["win_rate"])
        self.assertIsNone(empty["avg_damage"])

    async def test_first_recent_creates_sample_but_does_not_invent_history(self):
        result = await self.stats.recent("asia", 1, 7)
        self.assertEqual(result["status"], "collecting")
        self.assertIsNone(result["metrics"])
        self.assertEqual(len(self.storage.history("asia", 1)), 1)

    async def test_recent_uses_counter_differences_not_average_differences(self):
        now = 2000000000
        old = {**totals_from(SAMPLE), "battles": 8, "wins": 4, "damage_dealt": 300000,
               "frags": 8, "xp": 12000, "survived_battles": 3,
               "main_battery.hits": 160, "main_battery.shots": 800}
        self.storage.save_snapshot("asia", 1, "pvp", old, at=now - 86400)
        with patch("core.stats.time.time", return_value=now):
            result = await self.stats.recent("asia", 1, 1)
        self.assertEqual(result["metrics"]["battles"], 2)
        self.assertEqual(result["metrics"]["win_rate"], 100)
        self.assertEqual(result["metrics"]["avg_damage"], 60000)
        self.assertEqual(result["metrics"]["main_battery_hit_rate"], 45)
        self.assertEqual(result["metrics"]["survival_rate"], 50)
        self.assertEqual(result["actual_days"], 1)

    async def test_partial_window_reports_actual_period(self):
        now = 2000000000
        self.storage.save_snapshot("asia", 1, "pvp", totals_from(SAMPLE), at=now - 3600)
        with patch("core.stats.time.time", return_value=now):
            result = await self.stats.recent("asia", 1, 30)
        self.assertEqual(result["requested_days"], 30)
        self.assertAlmostEqual(result["actual_days"], 1 / 24)
        self.assertEqual(result["metrics"]["battles"], 0)
        self.assertIsNone(result["metrics"]["win_rate"])

    async def test_modes_and_ship_snapshots_are_separate(self):
        now = 2000000000
        self.storage.save_snapshot("asia", 1, "pvp", totals_from(SAMPLE), at=now - 86400)
        with patch("core.stats.time.time", return_value=now):
            ranked = await self.stats.recent("asia", 1, 1, mode="rank_solo")
            ship = await self.stats.recent("asia", 1, 1, ship_id=123)
        self.assertEqual(ranked["status"], "collecting")
        self.assertEqual(ship["status"], "collecting")
        self.assertEqual(len(self.storage.history("asia", 1, "rank_solo")), 1)
        self.assertEqual(len(self.storage.history("asia", 1, "pvp", ship_id=123)), 1)

    def test_counter_reset_rejected(self):
        with self.assertRaisesRegex(APIError, "计数回退"):
            delta_totals({"battles": 2, "wins": 1}, {"battles": 3, "wins": 2})

    async def test_candidate_leaderboard_labels_scope_and_filters_hidden_or_low_samples(self):
        result = await self.stats.leaderboard("asia", 123, [1, 2, 999, 1], min_battles=5)
        self.assertIn("非全服榜", result["scope"])
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["rank"], 1)
        self.assertEqual(result["exclusions"][0]["account_id"], 999)
        result = await self.stats.leaderboard("asia", 123, [1], min_battles=20)
        self.assertEqual(result["rows"], [])
        self.assertEqual(len(result["exclusions"]), 1)

    async def test_class_metrics_are_weighted_and_do_not_overwrite_account_snapshots(self):
        class FleetClient(FakeClient):
            async def ships(self, region, search=""):
                return [{"ship_id": 11, "name": "巡洋A", "type": "Cruiser", "tier": 10},
                        {"ship_id": 12, "name": "巡洋B", "type": "Cruiser", "tier": 9},
                        {"ship_id": 13, "name": "航母", "type": "AirCarrier", "tier": 10}]

            async def player_ships(self, region, account_id, ship_id=None):
                return [{"ship_id": 11, "pvp": {"battles": 10, "wins": 5, "damage_dealt": 100000}},
                        {"ship_id": 12, "pvp": {"battles": 30, "wins": 21, "damage_dealt": 1500000}},
                        {"ship_id": 13, "pvp": {"battles": 60, "wins": 50, "damage_dealt": 6000000}}]

        service = StatsService(FleetClient(), self.storage)
        with patch("core.stats.time.time",return_value=1000):
            result = await service.class_overview("asia", 1, "ca")
        self.assertEqual(result["metrics"]["battles"], 40)
        self.assertEqual(result["metrics"]["avg_damage"], 40000)
        self.assertEqual(result["metrics"]["win_rate"], 65)
        self.assertEqual(len(result["rows"]), 2)
        with patch("core.stats.time.time",return_value=1001):
            surface = await service.class_overview("asia", 1, "surface")
        self.assertEqual(surface["metrics"]["battles"], 40)
        # Fleet/class totals have a different scope and must not corrupt account history.
        self.assertEqual(self.storage.history("asia", 1), [])
        self.assertEqual(len(self.storage.history("asia", 1, ship_id=11)), 2)

    def test_missing_metrics_are_not_zero(self):
        metrics = metrics_from({"battles": 5, "wins": 3})
        self.assertIsNone(metrics["avg_damage"])
        self.assertIsNone(metrics["main_battery_hit_rate"])

    def test_binding_and_samples_survive_reopen_and_are_removable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db.sqlite"
            store = Storage(path)
            store.bind("qq:g1:u1", "asia", 1, "玩家一")
            store.save_snapshot("asia", 1, "pvp", {"battles": 1}, at=100)
            store.close()
            store = Storage(path)
            self.assertEqual(store.get_binding("qq:g1:u1")["nickname"], "玩家一")
            self.assertEqual(store.baseline("asia", 1, "pvp", 101)["totals"], {"battles": 1})
            store.delete_player_data("asia", 1)
            self.assertIsNone(store.get_binding("qq:g1:u1"))
            self.assertEqual(store.history("asia", 1), [])
            store.close()


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_player_search_and_injected_session_ownership(self):
        session = FakeSession([FakeResponse({"status": "ok", "data": [{"account_id": 1, "nickname": "Captain"}]})])
        client = WargamingClient("test-id", session)
        result = await client.search_players("亚服", "Captain")
        self.assertEqual(result[0]["account_id"], 1)
        self.assertEqual(session.calls[0][1]["params"]["type"], "exact")
        self.assertTrue(session.calls[0][0].startswith("https://api.worldofwarships.asia/"))
        await client.close()
        self.assertFalse(session.closed)

    async def test_ship_pagination_cache_and_name_matching(self):
        session = FakeSession([
            FakeResponse({"status": "ok", "meta": {"page_total": 2}, "data": {"1": {"ship_id": 1, "name": "大和"}}}),
            FakeResponse({"status": "ok", "meta": {"page_total": 2}, "data": {"2": {"ship_id": 2, "name": "得梅因"}}}),
        ])
        client = WargamingClient("test-id", session)
        self.assertEqual(len(await client.ships("asia")), 2)
        self.assertEqual((await client.ships("asia", "大和"))[0]["ship_id"], 1)
        self.assertEqual((await client.ships("asia", "2"))[0]["name"], "得梅因")
        self.assertEqual(len(session.calls), 2)

    async def test_api_error_never_discloses_application_id(self):
        session = FakeSession([FakeResponse({"status": "error", "error": {"code": 407, "message": "secret-id appeared in a URL"}})])
        client = WargamingClient("secret-id", session)
        with self.assertRaises(APIError) as error:
            await client.account("eu", 1)
        self.assertNotIn("secret-id", str(error.exception))
        self.assertEqual(len(session.calls), 1)

    async def test_transient_errors_retry_reads_but_4xx_do_not(self):
        session = FakeSession([
            FakeResponse({}, 503),
            FakeResponse({"status": "ok", "data": []}),
        ])
        client = WargamingClient("test-id", session)
        with patch("core.wg.asyncio.sleep", return_value=None):
            self.assertEqual(await client.search_players("eu", "Name"), [])
        self.assertEqual(len(session.calls), 2)
        session = FakeSession([FakeResponse({}, 403)])
        client = WargamingClient("test-id", session)
        with self.assertRaises(APIError):
            await client.search_players("eu", "Name")
        self.assertEqual(len(session.calls), 1)

    def test_chinese_realm_not_mapped_to_asia(self):
        with self.assertRaises(APIError):
            normalize_region("cn")


if __name__ == "__main__":
    unittest.main()
