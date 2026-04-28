from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.analysis import (
    fetch_weather_events_keyset,
    paginate,
    probe_wallet_trade_window,
    split_leaderboard_prefilter_candidates,
)
from polymarket_weather_tool.client import PolymarketClient
from polymarket_weather_tool.report import format_trade


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def fetch_events_keyset_page(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs.get("after_cursor") is None:
            return {
                "events": [{"id": "1"}, {"id": "2"}],
                "next_cursor": "cursor-2",
            }
        return {
            "events": [{"id": "3"}, {"id": "4"}],
            "next_cursor": "cursor-4",
        }


class FakeLeaderboardClient(PolymarketClient):
    def __init__(self) -> None:
        super().__init__({"use_cache": False})
        self.last_params: dict[str, Any] | None = None

    def _get_json(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self.last_params = params or {}
        return []


class FakeTradeProbeClient:
    def __init__(self, trades: list[dict[str, Any]]) -> None:
        self.trades = trades
        self.calls: list[dict[str, Any]] = []

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        return self.trades[offset : offset + limit]


class UpgradeBehaviorTests(unittest.TestCase):
    def test_format_trade_uses_size_price_when_usdc_size_is_missing(self) -> None:
        line = format_trade(
            {
                "side": "BUY",
                "title": "Example market",
                "outcome": "Yes",
                "size": 123.45,
                "price": 0.4,
            }
        )

        self.assertIn("49.38 USDC", line)

    def test_keyset_weather_fetch_respects_max_events_and_cursor(self) -> None:
        client = FakeClient()
        events = fetch_weather_events_keyset(
            client=client,  # type: ignore[arg-type]
            page_size=2,
            max_events=3,
            order="createdAt",
            ascending=False,
            tag_id=84,
            tag_slug="weather",
            active=None,
            closed=None,
            archived=False,
        )

        self.assertEqual([event["id"] for event in events], ["1", "2", "3"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["after_cursor"], "cursor-2")

    def test_leaderboard_time_period_alias_uses_day_for_1d(self) -> None:
        client = FakeLeaderboardClient()
        client.fetch_leaderboard_page(
            category="WEATHER",
            time_period="1D",
            order_by="PNL",
            limit=1,
            offset=0,
        )

        self.assertIsNotNone(client.last_params)
        self.assertEqual(client.last_params["timePeriod"], "DAY")

    def test_paginate_stops_on_http_400_after_first_page(self) -> None:
        def fetch_page(limit: int, offset: int) -> list[dict[str, Any]]:
            if offset == 0:
                return [{"id": "1"}, {"id": "2"}]
            http_error = HTTPError(
                url=f"https://data-api.polymarket.com/activity?limit={limit}&offset={offset}",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=None,
            )
            raise RuntimeError("Request failed") from http_error

        records = paginate(
            page_size=2,
            max_offset=10,
            fetch_page=fetch_page,
        )

        self.assertEqual(records, [{"id": "1"}, {"id": "2"}])

    def test_leaderboard_prefilter_skips_rows_failing_pnl_or_volume(self) -> None:
        config = {
            "wallet_filter": {
                "min_pnl": 0.01,
                "max_pnl": 200,
                "min_volume": 0,
                "max_volume": 40000,
                "include_wallets": [],
                "exclude_wallets": [],
            }
        }
        candidates, screening_records = split_leaderboard_prefilter_candidates(
            [
                {"proxyWallet": "0x1", "pnl": "10", "vol": "1000"},
                {"proxyWallet": "0x2", "pnl": "500", "vol": "1000"},
                {"proxyWallet": "0x3", "pnl": "10", "vol": "50000"},
            ],
            config,
        )

        self.assertEqual([entry["proxyWallet"] for entry in candidates], ["0x1"])
        self.assertEqual(
            [record["reasons"] for record in screening_records],
            [["failed:pnl<=200"], ["failed:volume<=40000"]],
        )

    def test_trade_probe_rejects_wallets_over_max_trade_count_without_full_snapshot(self) -> None:
        config = {
            "pagination": {"page_size": 500},
            "wallet_filter": {
                "min_traded_count": 11,
                "max_traded_count": 99,
                "include_wallets": [],
            },
        }
        trades = [{"id": str(index)} for index in range(100)]
        client = FakeTradeProbeClient(trades)

        result = probe_wallet_trade_window(
            client,  # type: ignore[arg-type]
            "0xabc",
            {"proxyWallet": "0xabc", "pnl": "20", "vol": "1000"},
            config,
        )

        self.assertIn("screening", result)
        self.assertEqual(result["screening"]["reasons"], ["failed:trade_count<=99"])
        self.assertEqual(client.calls[0]["limit"], 100)


if __name__ == "__main__":
    unittest.main()
