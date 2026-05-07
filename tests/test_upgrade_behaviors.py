from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.analysis import (
    WeatherIndex,
    analyze_leaderboard_entry,
    build_screening_record,
    compute_metrics,
    fetch_graph_activity_stream,
    fetch_wallet_snapshot,
    fetch_weather_events,
    fetch_weather_events_keyset,
    history_provider_fetch_plan,
    paginate,
    probe_wallet_trade_window,
    project_trades_page_from_activity,
    split_leaderboard_prefilter_candidates,
)
from polymarket_weather_tool.client import PolymarketClient
from polymarket_weather_tool.config import (
    SMART_WALLET_LIBRARY_REFRESH_MODE,
    apply_analysis_mode,
)
from polymarket_weather_tool.cloudflare_backend import (
    CloudflareD1Config,
    CloudflareD1RequestError,
    _cloudflare_headers,
    cloudflare_d1_delete_rows,
)
from polymarket_weather_tool.history_ledger import history_ledger_table_path
from polymarket_weather_tool.history_registry import create_history_registry
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


class KeysetFallbackClient:
    def __init__(self) -> None:
        self.keyset_calls: list[dict[str, Any]] = []
        self.offset_calls: list[dict[str, Any]] = []
        self.events = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

    def fetch_events_keyset_page(self, **kwargs: Any) -> dict[str, Any]:
        self.keyset_calls.append(kwargs)
        raise HTTPError(
            url="https://gamma-api.polymarket.com/events/keyset?limit=2",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

    def fetch_events_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.offset_calls.append(kwargs)
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        return self.events[offset : offset + limit]


class FakeTradeProbeClient:
    def __init__(self, trades: list[dict[str, Any]]) -> None:
        self.trades = trades
        self.calls: list[dict[str, Any]] = []

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        return self.trades[offset : offset + limit]


class FailingTradeProbeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        raise HTTPError(
            url="https://data-api.polymarket.com/trades?limit=100&offset=0",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )


class WindowActivityClient:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        activity_type = str(kwargs.get("activity_type") or "").upper()
        start = kwargs.get("start")
        end = kwargs.get("end")
        records = [
            dict(record)
            for record in self.records
            if not activity_type or str(record.get("type") or "").upper() == activity_type
        ]
        if start is not None:
            records = [record for record in records if int(record["timestamp"]) >= int(start)]
        if end is not None:
            records = [record for record in records if int(record["timestamp"]) <= int(end)]
        records.sort(key=lambda record: int(record["timestamp"]), reverse=True)
        return records[offset : offset + limit]

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        raise AssertionError("windowed modes should probe activity instead of /trades")

    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        return []

    def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("closed_positions", kwargs))
        return []


class AccountingPositionsFallbackClient(WindowActivityClient):
    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        error = HTTPError(
            url="https://data-api.polymarket.com/positions?limit=10&offset=0",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )
        raise RuntimeError("Request failed") from error

    def fetch_accounting_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("accounting_snapshot", kwargs))
        return {
            "positions": [
                {
                    "conditionId": "cond-1",
                    "title": "Accounting position",
                    "currentValue": "12.5",
                }
            ],
            "equity": [{"timestamp": "1777000000", "equity": "101.5"}],
            "record_counts": {"positions": 1, "equity": 1},
        }


class IncompletePositionsFallbackClient(AccountingPositionsFallbackClient):
    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {
                "conditionId": "paged-cond-1",
                "title": "Paged position 1",
                "currentValue": "5.0",
            },
            {
                "conditionId": "paged-cond-2",
                "title": "Paged position 2",
                "currentValue": "6.0",
            },
        ]
        return records[offset : offset + limit]


class ScreeningHydrationClient(WindowActivityClient):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__(records)

    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        return [
            {
                "conditionId": "cond-open",
                "title": "Hydrated position",
                "currentValue": "15.0",
            }
        ]

    def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("closed_positions", kwargs))
        return [
            {
                "conditionId": "cond-closed",
                "title": "Hydrated close",
                "realizedPnl": "5.0",
                "totalBought": "10.0",
                "endDate": "2026-04-30T00:00:00Z",
            }
        ]


class GraphHistoryProviderClient(WindowActivityClient):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__(records)
        self.graphql_calls: list[dict[str, Any]] = []

    def fetch_graphql(
        self,
        *,
        endpoint_url: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.graphql_calls.append(
            {
                "endpoint_url": endpoint_url,
                "query": query,
                "variables": dict(variables or {}),
            }
        )
        if "WalletOrderFills" in query:
            return {
                "data": {
                    "maker": [
                        {
                            "id": "graphql-fill-1",
                            "transactionHash": "0xfill1",
                            "timestamp": "1777000000",
                            "maker": "0xabc",
                            "taker": "0xdef",
                            "makerAssetId": "0",
                            "takerAssetId": "1001",
                            "makerAmountFilled": "420000",
                            "takerAmountFilled": "1000000",
                            "fee": "3000",
                        }
                    ],
                    "taker": [],
                }
            }
        if "TokenIdConditions" in query:
            return {
                "data": {
                    "tokenIdConditions": [
                        {
                            "id": "1001",
                            "complement": False,
                            "outcomeIndex": "0",
                            "condition": {"id": "cond-graphql"},
                        }
                    ]
                }
            }
        if "WalletSplits" in query:
            return {
                "data": {
                    "splits": [
                        {
                            "id": "split-1",
                            "timestamp": "1777000001",
                            "stakeholder": "0xabc",
                            "amount": "1000000",
                            "condition": {"id": "cond-graphql"},
                        }
                    ]
                }
            }
        if "WalletMerges" in query:
            return {"data": {"merges": []}}
        if "WalletRedemptions" in query:
            return {"data": {"redemptions": []}}
        if "WalletNegRiskConversions" in query:
            return {"data": {"negRiskConversions": []}}
        raise AssertionError(f"unexpected graphql query: {query}")


class IncompleteGraphHistoryProviderClient(GraphHistoryProviderClient):
    def __init__(self) -> None:
        super().__init__([])
        self.rest_records = [
            {
                "type": "TRADE",
                "id": "rest-trade",
                "timestamp": 1_776_999_900,
                "side": "BUY",
                "asset": "999",
                "size": 1.0,
                "usdcSize": 0.2,
                "price": 0.2,
            }
        ]

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        if kwargs.get("start") is not None or kwargs.get("end") is not None:
            return []
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        return self.rest_records[offset : offset + limit]

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        return self.rest_records[offset : offset + limit]


class FailingRecentActivityProviderClient(GraphHistoryProviderClient):
    def __init__(self) -> None:
        super().__init__([])

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        raise RuntimeError("recent activity failed")


class FailingTradeProbeProviderClient(GraphHistoryProviderClient):
    def __init__(self) -> None:
        super().__init__([])

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        raise HTTPError(
            url="https://data-api.polymarket.com/trades?limit=100&offset=0",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )


class FailingScreeningWindowProviderClient(GraphHistoryProviderClient):
    def __init__(self) -> None:
        super().__init__([])

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        raise RuntimeError("screening window activity failed")


class FailingTradeProbeWindowProviderClient(GraphHistoryProviderClient):
    def __init__(self) -> None:
        super().__init__([])

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        raise RuntimeError("trade probe window failed")


class FailingTradeProbeAllSourcesClient(FailingTradeProbeProviderClient):
    def fetch_graphql(
        self,
        *,
        endpoint_url: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.graphql_calls.append(
            {
                "endpoint_url": endpoint_url,
                "query": query,
                "variables": dict(variables or {}),
            }
        )
        raise RuntimeError("history provider probe failed")


class FailingTradeProbeWindowAllSourcesClient(FailingTradeProbeWindowProviderClient):
    def fetch_graphql(
        self,
        *,
        endpoint_url: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.graphql_calls.append(
            {
                "endpoint_url": endpoint_url,
                "query": query,
                "variables": dict(variables or {}),
            }
        )
        raise RuntimeError("history provider window probe failed")


class FailingScreeningWindowAllSourcesClient(FailingScreeningWindowProviderClient):
    def fetch_graphql(
        self,
        *,
        endpoint_url: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.graphql_calls.append(
            {
                "endpoint_url": endpoint_url,
                "query": query,
                "variables": dict(variables or {}),
            }
        )
        raise RuntimeError("history provider screening failed")


class UpgradeBehaviorTests(unittest.TestCase):
    def read_history_ledger_rows(self, artifacts_root: Path, kind: str) -> list[dict[str, Any]]:
        path = history_ledger_table_path(artifacts_root, kind)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

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

    def test_fetch_weather_events_falls_back_to_offset_pagination_when_keyset_fails(self) -> None:
        client = KeysetFallbackClient()
        config = {
            "pagination": {"page_size": 2, "max_offset": 10},
            "weather": {
                "tag_id": 84,
                "tag_slug": "weather",
                "use_keyset": True,
                "order": "createdAt",
                "ascending": False,
                "max_events": 3,
                "active_only": False,
                "closed_only": False,
                "include_archived": False,
                "page_size": 2,
            },
        }

        events = fetch_weather_events(client, config)  # type: ignore[arg-type]

        self.assertEqual([event["id"] for event in events], ["1", "2", "3"])
        self.assertEqual(len(client.keyset_calls), 1)
        self.assertEqual(
            [call["offset"] for call in client.offset_calls],
            [0, 2],
        )

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

    def test_leaderboard_prefilter_dedupes_wallets_across_pages(self) -> None:
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
        seen_wallets: set[str] = set()

        first_candidates, first_screening = split_leaderboard_prefilter_candidates(
            [{"proxyWallet": "0x1", "pnl": "10", "vol": "1000"}],
            config,
            seen_wallets=seen_wallets,
        )
        second_candidates, second_screening = split_leaderboard_prefilter_candidates(
            [{"proxyWallet": "0x1", "pnl": "10", "vol": "1000"}],
            config,
            seen_wallets=seen_wallets,
        )

        self.assertEqual([entry["proxyWallet"] for entry in first_candidates], ["0x1"])
        self.assertEqual(first_screening, [])
        self.assertEqual(second_candidates, [])
        self.assertEqual(second_screening[0]["reasons"], ["duplicate wallet in leaderboard"])

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

    def test_trade_probe_falls_back_to_full_snapshot_when_probe_request_fails(self) -> None:
        config = {
            "history_provider": {
                "enabled": False,
            },
            "pagination": {"page_size": 500},
            "wallet_filter": {
                "min_traded_count": 11,
                "max_traded_count": 99,
                "include_wallets": [],
            },
        }
        client = FailingTradeProbeClient()

        result = probe_wallet_trade_window(
            client,  # type: ignore[arg-type]
            "0xabc",
            {"proxyWallet": "0xabc", "pnl": "20", "vol": "1000"},
            config,
        )

        self.assertEqual(result, {"prefetched_trades": None, "trade_probe_fetched": False})
        self.assertEqual(client.calls[0]["limit"], 100)

    def test_trade_probe_can_fallback_to_history_provider_for_full_history(self) -> None:
        config = {
            "history_provider": {
                "enabled": True,
                "trade_probe_fallback_enabled": True,
                "page_size": 100,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            },
            "pagination": {"page_size": 500},
            "wallet_filter": {
                "min_traded_count": 1,
                "max_traded_count": 99,
                "include_wallets": [],
            },
        }
        client = FailingTradeProbeProviderClient()

        result = probe_wallet_trade_window(
            client,  # type: ignore[arg-type]
            "0xabc",
            {"proxyWallet": "0xabc", "pnl": "20", "vol": "1000"},
            config,
        )

        self.assertTrue(result["trade_probe_fetched"])
        self.assertEqual(len(result["prefetched_trades"]), 1)
        self.assertEqual(result["prefetched_trades"][0]["conditionId"], "cond-graphql")
        self.assertEqual(client.calls[0][0], "trades")
        self.assertTrue(any("WalletOrderFills" in call["query"] for call in client.graphql_calls))

    def test_trade_probe_can_fallback_to_history_ledger_when_live_and_provider_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            current_ts = 1_777_000_000
            seed_client = WindowActivityClient(
                [
                    {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 120},
                ]
            )
            config = {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                },
                "history_ledger": {"enabled": True, "backend": "local"},
                "history_provider": {
                    "enabled": True,
                    "screening_fallback_enabled": True,
                    "trade_probe_fallback_enabled": True,
                    "page_size": 50,
                    "max_pages_per_stream": 1,
                    "token_lookup_chunk_size": 10,
                },
                "pagination": {"page_size": 50, "max_offset": 100},
                "runtime": {
                    "artifacts_root": str(artifacts_root),
                    "run_id": "trade-probe-ledger-seed",
                },
                "wallet_filter": {
                    "min_traded_count": 1,
                    "max_traded_count": 99,
                    "include_wallets": [],
                    "exclude_wallets": [],
                },
                "leaderboard": {"time_period": "DAY"},
                "chain_validation": {"enabled": False},
            }
            fetch_wallet_snapshot(
                seed_client,  # type: ignore[arg-type]
                "0xabc",
                config,
                snapshot_scope="screening",
            )

            failing_client = FailingTradeProbeWindowAllSourcesClient()
            result = probe_wallet_trade_window(
                failing_client,  # type: ignore[arg-type]
                "0xabc",
                {"proxyWallet": "0xabc", "pnl": "20", "vol": "1000"},
                config,
            )

        self.assertTrue(result["trade_probe_fetched"])
        self.assertEqual(len(result["prefetched_trades"]), 1)
        self.assertTrue(result["prefetched_trades"][0]["_history_ledger"])

    def test_project_trades_page_from_complete_activity_even_without_partition_recovery(self) -> None:
        projected = project_trades_page_from_activity(
            {
                "complete": True,
                "collection_mode": "aggregate",
                "records": [
                    {"type": "REWARD", "id": "reward"},
                    {"type": "TRADE", "id": "trade-1"},
                    {"type": "TRADE", "id": "trade-2"},
                ],
                "stop_reason": "last_page_partial",
            }
        )

        self.assertIsNotNone(projected)
        assert projected is not None
        self.assertEqual([record["id"] for record in projected["records"]], ["trade-1", "trade-2"])
        self.assertEqual(projected["collection_mode"], "activity_projection")
        self.assertEqual(projected["projection_source_mode"], "aggregate")

    def test_day_trade_probe_uses_windowed_activity_instead_of_lifetime_trades(self) -> None:
        current_ts = 1_777_000_000
        records = [
            {"type": "TRADE", "id": f"recent-{index}", "timestamp": current_ts - 60 * index}
            for index in range(6)
        ]
        records.append({"type": "TRADE", "id": "old", "timestamp": current_ts - 3 * 24 * 3600})
        client = WindowActivityClient(records)
        config = {
            "analysis": {"current_datetime": "2026-04-25T00:00:00+00:00"},
            "leaderboard": {"time_period": "DAY"},
            "pagination": {"page_size": 500},
            "wallet_filter": {
                "min_traded_count": 5,
                "max_traded_count": 10,
                "include_wallets": [],
            },
        }

        result = probe_wallet_trade_window(
            client,  # type: ignore[arg-type]
            "0xabc",
            {"proxyWallet": "0xabc", "pnl": "20", "vol": "1000"},
            config,
        )

        self.assertTrue(result["trade_probe_fetched"])
        self.assertEqual([record["id"] for record in result["prefetched_trades"]], [f"recent-{index}" for index in range(6)])
        activity_calls = [kwargs for name, kwargs in client.calls if name == "activity"]
        self.assertEqual(len(activity_calls), 1)
        self.assertEqual(activity_calls[0]["activity_type"], "TRADE")
        self.assertIn("start", activity_calls[0])
        self.assertIn("end", activity_calls[0])
        self.assertFalse(any(name == "trades" for name, _kwargs in client.calls))

    def test_day_trade_probe_can_fallback_to_history_provider_when_window_probe_fails(self) -> None:
        config = {
            "analysis": {"current_datetime": "2026-04-25T00:00:00+00:00"},
            "history_provider": {
                "enabled": True,
                "trade_probe_fallback_enabled": True,
                "page_size": 100,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            },
            "leaderboard": {"time_period": "DAY"},
            "pagination": {"page_size": 500},
            "wallet_filter": {
                "min_traded_count": 1,
                "max_traded_count": 10,
                "include_wallets": [],
            },
        }
        client = FailingTradeProbeWindowProviderClient()

        result = probe_wallet_trade_window(
            client,  # type: ignore[arg-type]
            "0xabc",
            {"proxyWallet": "0xabc", "pnl": "20", "vol": "1000"},
            config,
        )

        self.assertTrue(result["trade_probe_fetched"])
        self.assertEqual(len(result["prefetched_trades"]), 1)
        window_query = next(call for call in client.graphql_calls if "WalletOrderFillsWindow" in call["query"])
        self.assertIn("start", window_query["variables"])
        self.assertIn("end", window_query["variables"])
        self.assertFalse(any(name == "trades" for name, _kwargs in client.calls))

    def test_fetch_wallet_snapshot_uses_screening_window_for_weekly_mode(self) -> None:
        current_ts = 1_777_000_000
        old_ts = current_ts - 20 * 24 * 3600
        records = [
            {"type": "REWARD", "id": "recent-reward", "timestamp": current_ts - 60},
            {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 120},
            {"type": "TRADE", "id": "old-trade", "timestamp": old_ts},
        ]
        client = WindowActivityClient(records)
        config = {
            "analysis": {
                "current_datetime": "2026-04-25T00:00:00+00:00",
                "position_size_threshold": 0.1,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "WEEK"},
            "pagination": {"page_size": 10, "max_offset": 100},
            "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
        }

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
            snapshot_scope="screening",
        )

        self.assertEqual([record["id"] for record in snapshot["activity"]], ["recent-reward", "recent-trade"])
        self.assertEqual([record["id"] for record in snapshot["trades"]], ["recent-trade"])
        self.assertEqual(snapshot["collection_status"]["activity"]["history_scope"], "screening_window")
        self.assertEqual(snapshot["collection_status"]["trades"]["history_scope"], "screening_window")
        self.assertEqual(snapshot["collection_status"]["positions"]["collection_mode"], "deferred")
        self.assertEqual(snapshot["collection_status"]["closed_positions"]["collection_mode"], "deferred")
        activity_calls = [kwargs for name, kwargs in client.calls if name == "activity"]
        self.assertTrue(all("start" in kwargs and "end" in kwargs for kwargs in activity_calls))
        self.assertFalse(any(name == "trades" for name, _kwargs in client.calls))
        self.assertFalse(any(name == "positions" for name, _kwargs in client.calls))
        self.assertFalse(any(name == "closed_positions" for name, _kwargs in client.calls))

        metrics = compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "120", "vol": "2000"},
            weather_index=WeatherIndex(set(), set(), set(), set(), {}),
            config=config,
        )
        self.assertFalse(metrics["snapshot_complete"])
        self.assertTrue(metrics["screening_evidence_complete"])
        self.assertEqual(metrics["history_scope"], "screening_window")

    def test_fetch_wallet_snapshot_persists_screening_history_ledger_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            current_ts = 1_777_000_000
            old_ts = current_ts - 20 * 24 * 3600
            records = [
                {"type": "REWARD", "id": "recent-reward", "timestamp": current_ts - 60},
                {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 120},
                {"type": "TRADE", "id": "old-trade", "timestamp": old_ts},
            ]
            client = WindowActivityClient(records)
            config = {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                },
                "chain_validation": {"enabled": False},
                "history_ledger": {"enabled": True, "backend": "local"},
                "leaderboard": {"time_period": "WEEK"},
                "pagination": {"page_size": 10, "max_offset": 100},
                "runtime": {
                    "artifacts_root": str(artifacts_root),
                    "run_id": "screening-ledger-run",
                },
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            }

            snapshot = fetch_wallet_snapshot(
                client,  # type: ignore[arg-type]
                "0xabc",
                config,
                snapshot_scope="screening",
            )

            self.assertEqual(snapshot["history_ledger"]["status"], "persisted")
            trade_rows = self.read_history_ledger_rows(artifacts_root, "trades")
            operation_rows = self.read_history_ledger_rows(artifacts_root, "operations")
            gap_rows = self.read_history_ledger_rows(artifacts_root, "gaps")
            self.assertEqual(len(trade_rows), 1)
            self.assertEqual(trade_rows[0]["snapshot_scope"], "screening")
            self.assertEqual(trade_rows[0]["history_scope"], "screening_window")
            self.assertEqual(operation_rows, [])
            self.assertTrue(
                any(
                    row["section_name"] == "positions"
                    and row["snapshot_scope"] == "screening"
                    and not row["complete"]
                    for row in gap_rows
                )
            )
            self.assertTrue(
                any(
                    row["section_name"] == "trades"
                    and row["history_scope"] == "screening_window"
                    for row in gap_rows
                )
            )

    def test_fetch_wallet_snapshot_replicates_history_ledger_rows_to_cloudflare_when_backend_is_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            current_ts = 1_777_000_000
            client = WindowActivityClient(
                [
                    {"type": "REWARD", "id": "recent-reward", "timestamp": current_ts - 60},
                    {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 120},
                ]
            )
            config = {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                },
                "chain_validation": {"enabled": False},
                "history_ledger": {
                    "enabled": True,
                    "backend": "local",
                    "cloudflare_account_id": "account-id",
                    "cloudflare_d1_database_id": "database-id",
                    "cloudflare_api_token": "api-token",
                    "replicate_to_cloudflare": True,
                },
                "leaderboard": {"time_period": "WEEK"},
                "pagination": {"page_size": 10, "max_offset": 100},
                "runtime": {
                    "artifacts_root": str(artifacts_root),
                    "run_id": "screening-ledger-cloudflare-replica",
                },
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            }

            with patch(
                "polymarket_weather_tool.history_ledger.cloudflare_d1_upsert_rows",
                side_effect=lambda _config, _table, *, rows, on_conflict: [dict(row) for row in rows],
            ) as upsert_mock:
                snapshot = fetch_wallet_snapshot(
                    client,  # type: ignore[arg-type]
                    "0xabc",
                    config,
                    snapshot_scope="screening",
                )

        self.assertEqual(snapshot["history_ledger"]["status"], "persisted")
        self.assertEqual(snapshot["history_ledger"]["backend"], "local")
        self.assertEqual(snapshot["history_ledger"]["replica_backend"], "cloudflare")
        self.assertEqual(snapshot["history_ledger"]["replica_status"], "persisted")
        self.assertTrue(
            any(call.args[1] == "wallet_trade_ledger" for call in upsert_mock.call_args_list)
        )
        self.assertTrue(
            any(call.args[1] == "wallet_history_gaps" for call in upsert_mock.call_args_list)
        )

    def test_smart_wallet_refresh_keeps_lifetime_activity_by_default(self) -> None:
        current_ts = 1_777_000_000
        records = [
            {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 60},
            {"type": "TRADE", "id": "old-trade", "timestamp": current_ts - 20 * 24 * 3600},
        ]
        client = WindowActivityClient(records)
        config = apply_analysis_mode(
            {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                },
                "chain_validation": {"enabled": False},
                "leaderboard": {"time_period": "DAY"},
                "pagination": {"page_size": 10, "max_offset": 100},
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            },
            SMART_WALLET_LIBRARY_REFRESH_MODE,
        )

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
        )

        self.assertEqual([record["id"] for record in snapshot["activity"]], ["recent-trade", "old-trade"])
        activity_calls = [kwargs for name, kwargs in client.calls if name == "activity"]
        self.assertTrue(any("start" not in kwargs and "end" not in kwargs for kwargs in activity_calls))

    def test_smart_wallet_refresh_uses_recent_activity_screening_snapshot(self) -> None:
        current_ts = 1_777_000_000
        records = [
            {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 60},
            {"type": "REWARD", "id": "recent-reward", "timestamp": current_ts - 30},
            {"type": "TRADE", "id": "old-trade", "timestamp": current_ts - 20 * 24 * 3600},
        ]
        client = ScreeningHydrationClient(records)
        config = apply_analysis_mode(
            {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                    "top_trades_in_report": 3,
                    "top_positions_in_report": 3,
                    "top_closed_positions_in_report": 3,
                },
                "chain_validation": {"enabled": False},
                "leaderboard": {"time_period": "DAY"},
                "pagination": {"page_size": 10, "max_offset": 100},
                "wallet_filter": {
                    "include_wallets": [],
                    "exclude_wallets": [],
                    "activity_filter_mode": "normal_active",
                },
            },
            SMART_WALLET_LIBRARY_REFRESH_MODE,
        )

        result = analyze_leaderboard_entry(
            client=client,  # type: ignore[arg-type]
            leaderboard_entry={
                "proxyWallet": "0xabc",
                "pnl": "20",
                "vol": "1000",
                "rank": 1,
                "userName": "smart-refresh",
            },
            weather_index=WeatherIndex(set(), set(), set(), set(), {}),
            config=config,
        )

        wallet_result = result["wallet_result"]
        self.assertTrue(wallet_result["screening"]["selected"])
        self.assertEqual(
            wallet_result["deep_hydration"]["reason"],
            "full_hydration_not_required",
        )
        self.assertEqual(
            wallet_result["metrics"]["history_scope"],
            "recent_activity",
        )
        self.assertTrue(wallet_result["metrics"]["screening_evidence_complete"])
        self.assertFalse(wallet_result["metrics"]["snapshot_complete"])
        self.assertFalse(any(name == "positions" for name, _kwargs in client.calls))
        self.assertFalse(any(name == "closed_positions" for name, _kwargs in client.calls))

    def test_smart_wallet_refresh_can_fallback_to_history_provider_when_recent_activity_fails(self) -> None:
        client = FailingRecentActivityProviderClient()
        config = apply_analysis_mode(
            {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                },
                "history_provider": {
                    "enabled": True,
                    "screening_fallback_enabled": True,
                    "page_size": 50,
                    "max_pages_per_stream": 1,
                    "token_lookup_chunk_size": 10,
                },
                "chain_validation": {"enabled": False},
                "leaderboard": {"time_period": "DAY"},
                "pagination": {"page_size": 10, "max_offset": 100},
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            },
            SMART_WALLET_LIBRARY_REFRESH_MODE,
        )

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
            snapshot_scope="screening",
        )
        metrics = compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "20", "vol": "1000"},
            weather_index=WeatherIndex(set(), set(), {"cond-graphql"}, set(), {}),
            config=config,
        )

        self.assertEqual(snapshot["activity"], [])
        self.assertTrue(
            any(record.get("_source") == "history_provider" for record in snapshot["trades"])
        )
        self.assertEqual(snapshot["collection_status"]["trades"]["collection_mode"], "history_provider")
        self.assertEqual(snapshot["collection_status"]["trades"]["history_scope"], "recent_activity")
        self.assertTrue(metrics["screening_evidence_complete"])
        self.assertEqual(metrics["history_scope"], "recent_activity")

    def test_fetch_wallet_snapshot_falls_back_to_accounting_snapshot_for_positions(self) -> None:
        client = AccountingPositionsFallbackClient([])
        config = {
            "analysis": {
                "position_size_threshold": 0.1,
                "accounting_snapshot_fallback": True,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "ALL"},
            "pagination": {"page_size": 10, "max_offset": 100},
            "wallet_filter": {
                "include_wallets": [],
                "exclude_wallets": [],
                "min_traded_count": 1,
            },
        }

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
        )

        self.assertEqual(snapshot["positions"][0]["_source"], "accounting_snapshot")
        self.assertEqual(snapshot["equity"][0]["equity"], "101.5")
        self.assertEqual(
            snapshot["collection_status"]["positions"]["collection_mode"],
            "accounting_snapshot",
        )
        self.assertTrue(any(name == "accounting_snapshot" for name, _kwargs in client.calls))

    def test_fetch_wallet_snapshot_uses_accounting_snapshot_when_positions_pagination_is_incomplete(self) -> None:
        client = IncompletePositionsFallbackClient([])
        config = {
            "analysis": {
                "position_size_threshold": 0.1,
                "accounting_snapshot_fallback": True,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "ALL"},
            "pagination": {"page_size": 1, "max_offset": 0},
            "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
        }

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
        )

        self.assertEqual(snapshot["positions"][0]["title"], "Accounting position")
        self.assertEqual(
            snapshot["collection_status"]["positions"]["fallback_from"],
            "max_offset_reached",
        )
        self.assertEqual(
            snapshot["collection_status"]["positions"]["collection_mode"],
            "accounting_snapshot",
        )

    def test_fetch_wallet_snapshot_merges_graphql_history_provider_trades_into_full_snapshot(self) -> None:
        client = GraphHistoryProviderClient(
            [
                {
                    "type": "TRADE",
                    "id": "rest-trade",
                    "timestamp": 1_776_999_900,
                    "side": "BUY",
                    "asset": "999",
                    "size": 1.0,
                    "usdcSize": 0.2,
                    "price": 0.2,
                }
            ]
        )
        config = {
            "analysis": {
                "position_size_threshold": 0.1,
            },
            "history_provider": {
                "enabled": True,
                "always_for_full_snapshot": True,
                "page_size": 50,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "ALL"},
            "pagination": {"page_size": 50, "max_offset": 100},
            "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
        }

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
        )

        self.assertEqual(len(client.graphql_calls), 6)
        self.assertTrue(
            any(
                record.get("conditionId") == "cond-graphql"
                and record.get("_source") == "history_provider"
                for record in snapshot["trades"]
            )
        )
        self.assertEqual(
            snapshot["collection_status"]["history_provider"]["trades_complete"],
            True,
        )
        self.assertEqual(
            snapshot["collection_status"]["trades"]["collection_mode"],
            "history_provider_merge",
        )
        self.assertEqual(snapshot["collection_status"]["trades"]["history_scope"], "full_history")

    def test_history_provider_fetch_plan_only_requests_missing_sections(self) -> None:
        config = {
            "history_provider": {
                "enabled": True,
                "always_for_full_snapshot": False,
                "fetch_when_trades_incomplete": True,
                "fetch_when_activity_incomplete": True,
            }
        }

        plan = history_provider_fetch_plan(
            config=config,
            snapshot_scope="full",
            trades_page={"complete": True},
            activity_page={"complete": False},
        )
        self.assertEqual(
            plan,
            {
                "enabled": True,
                "need_trade_history": False,
                "need_operations": True,
            },
        )

        all_complete_plan = history_provider_fetch_plan(
            config=config,
            snapshot_scope="full",
            trades_page={"complete": True},
            activity_page={"complete": True},
        )
        self.assertEqual(
            all_complete_plan,
            {
                "enabled": False,
                "need_trade_history": False,
                "need_operations": False,
            },
        )

    def test_fetch_wallet_snapshot_marks_full_snapshot_complete_when_provider_restores_history(self) -> None:
        client = IncompleteGraphHistoryProviderClient()
        config = {
            "analysis": {
                "position_size_threshold": 0.1,
            },
            "history_provider": {
                "enabled": True,
                "page_size": 50,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "ALL"},
            "pagination": {"page_size": 1, "max_offset": 0},
            "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
        }

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
        )
        metrics = compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "10", "vol": "100"},
            weather_index=WeatherIndex(set(), set(), {"cond-graphql"}, set(), {}),
            config=config,
        )

        self.assertTrue(snapshot["operation_audit"]["complete"])
        self.assertTrue(metrics["snapshot_complete"])
        self.assertTrue(snapshot["collection_status"]["history_provider"]["trades_complete"])
        self.assertTrue(snapshot["collection_status"]["history_provider"]["operations_complete"])
        self.assertEqual(snapshot["operation_audit"]["operations"]["split"]["count"], 1)

    def test_fetch_wallet_snapshot_persists_full_history_operations_into_history_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            client = IncompleteGraphHistoryProviderClient()
            config = {
                "analysis": {
                    "position_size_threshold": 0.1,
                },
                "history_ledger": {"enabled": True, "backend": "local"},
                "history_provider": {
                    "enabled": True,
                    "page_size": 50,
                    "max_pages_per_stream": 1,
                    "token_lookup_chunk_size": 10,
                },
                "chain_validation": {"enabled": False},
                "leaderboard": {"time_period": "ALL"},
                "pagination": {"page_size": 1, "max_offset": 0},
                "runtime": {
                    "artifacts_root": str(artifacts_root),
                    "run_id": "full-ledger-run",
                },
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            }

            snapshot = fetch_wallet_snapshot(
                client,  # type: ignore[arg-type]
                "0xabc",
                config,
            )

            self.assertEqual(snapshot["history_ledger"]["status"], "persisted")
            self.assertEqual(snapshot["operation_audit"]["operations"]["split"]["count"], 1)
            trade_rows = self.read_history_ledger_rows(artifacts_root, "trades")
            operation_rows = self.read_history_ledger_rows(artifacts_root, "operations")
            gap_rows = self.read_history_ledger_rows(artifacts_root, "gaps")
            self.assertGreaterEqual(len(trade_rows), 2)
            self.assertTrue(any(row["operation_type"] == "split" for row in operation_rows))
            self.assertTrue(
                any(
                    row["section_name"] == "history_provider"
                    and row["snapshot_scope"] == "full"
                    and row["complete"]
                    for row in gap_rows
                )
            )

    def test_graph_activity_stream_recovers_with_time_partitions(self) -> None:
        class PartitionedGraphClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def fetch_graphql(
                self,
                *,
                endpoint_url: str,
                query: str,
                variables: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                del endpoint_url, query
                call = dict(variables or {})
                self.calls.append(call)
                if len(self.calls) == 1:
                    records = [
                        {"id": "split-a", "timestamp": "100", "amount": "1000000"},
                        {"id": "split-b", "timestamp": "90", "amount": "1000000"},
                    ]
                elif int(call.get("skip", 0) or 0) == 0:
                    records = [
                        {
                            "id": f"split-partition-{len(self.calls)}",
                            "timestamp": str(100 - len(self.calls)),
                            "amount": "1000000",
                        }
                    ]
                else:
                    records = []
                return {"data": {"splits": records}}

        client = PartitionedGraphClient()

        page = fetch_graph_activity_stream(
            client=client,  # type: ignore[arg-type]
            wallet="0xabc",
            endpoint_url="https://example.test/graphql",
            query="query WalletSplits($wallet: String!, $first: Int!, $skip: Int!, $start: BigInt!, $end: BigInt!) { splits { id timestamp amount } }",
            root_field="splits",
            page_size=2,
            max_pages=1,
        )

        self.assertTrue(page["complete"])
        self.assertEqual(page["stop_reason"], "partitioned_complete")
        self.assertGreaterEqual(int(page.get("partition_count", 0)), 2)
        self.assertTrue(all("start" in call and "end" in call for call in client.calls))

    def test_fetch_wallet_snapshot_can_reuse_history_ledger_operations_when_provider_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            operation_path = history_ledger_table_path(artifacts_root, "operations")
            gap_path = history_ledger_table_path(artifacts_root, "gaps")
            operation_path.parent.mkdir(parents=True, exist_ok=True)
            operation_path.write_text(
                json.dumps(
                    [
                        {
                            "record_key": "seed-operation-1",
                            "wallet_address": "0xabc",
                            "run_id": "seed-run",
                            "snapshot_scope": "full",
                            "history_scope": "full_history",
                            "operation_type": "split",
                            "event_timestamp": 1_777_000_001,
                            "payload": {
                                "operation": "split",
                                "timestamp": 1_777_000_001,
                                "transaction_hash": "0xledger-split",
                                "title": "ledger split",
                                "market": "cond-ledger",
                                "notional": 1.0,
                                "source": "history_provider.activity",
                            },
                            "updated_at": "2026-05-06T00:00:00+00:00",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            gap_path.write_text(
                json.dumps(
                    [
                        {
                            "gap_key": "seed-provider-gap",
                            "wallet_address": "0xabc",
                            "run_id": "seed-run",
                            "snapshot_scope": "full",
                            "section_name": "history_provider",
                            "history_scope": "full_history",
                            "collection_mode": "graphql_history_provider",
                            "stop_reason": "trade_history_incomplete_but_operations_complete",
                            "complete": False,
                            "range_start": None,
                            "range_end": None,
                            "payload": {
                                "trades_complete": False,
                                "operations_complete": True,
                            },
                            "updated_at": "2026-05-06T00:00:00+00:00",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = IncompleteGraphHistoryProviderClient()
            config = {
                "analysis": {
                    "position_size_threshold": 0.1,
                },
                "history_ledger": {"enabled": True, "backend": "local"},
                "history_provider": {
                    "enabled": False,
                },
                "chain_validation": {"enabled": False},
                "leaderboard": {"time_period": "ALL"},
                "pagination": {"page_size": 1, "max_offset": 0},
                "runtime": {
                    "artifacts_root": str(artifacts_root),
                    "run_id": "reuse-operation-ledger-run",
                },
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            }

            snapshot = fetch_wallet_snapshot(
                client,  # type: ignore[arg-type]
                "0xabc",
                config,
            )

            self.assertEqual(
                snapshot["collection_status"]["history_ledger_operations"][
                    "operations_complete"
                ],
                True,
            )
            self.assertEqual(snapshot["operation_audit"]["operations"]["split"]["count"], 1)
            self.assertTrue(
                any(
                    record.get("source") == "history_ledger.operation"
                    for record in snapshot["operation_audit"]["records"]
                )
            )

    def test_weekly_screening_window_can_fallback_to_history_provider_when_window_requests_fail(self) -> None:
        client = FailingScreeningWindowProviderClient()
        config = {
            "analysis": {
                "current_datetime": "2026-04-25T00:00:00+00:00",
                "position_size_threshold": 0.1,
            },
            "history_provider": {
                "enabled": True,
                "screening_fallback_enabled": True,
                "page_size": 50,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "WEEK"},
            "pagination": {"page_size": 10, "max_offset": 100},
            "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
        }

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            "0xabc",
            config,
            snapshot_scope="screening",
        )
        metrics = compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "20", "vol": "1000"},
            weather_index=WeatherIndex(set(), set(), {"cond-graphql"}, set(), {}),
            config=config,
        )

        self.assertEqual(snapshot["activity"], [])
        self.assertTrue(
            any(record.get("_source") == "history_provider" for record in snapshot["trades"])
        )
        self.assertEqual(snapshot["collection_status"]["trades"]["history_scope"], "screening_window")
        self.assertTrue(metrics["screening_evidence_complete"])
        self.assertEqual(metrics["history_scope"], "screening_window")

    def test_weekly_screening_window_can_fallback_to_history_ledger_when_live_and_provider_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            current_ts = 1_777_000_000
            seed_client = WindowActivityClient(
                [
                    {"type": "REWARD", "id": "recent-reward", "timestamp": current_ts - 60},
                    {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 120},
                ]
            )
            config = {
                "analysis": {
                    "current_datetime": "2026-04-25T00:00:00+00:00",
                    "position_size_threshold": 0.1,
                },
                "history_ledger": {"enabled": True, "backend": "local"},
                "history_provider": {
                    "enabled": True,
                    "screening_fallback_enabled": True,
                    "page_size": 50,
                    "max_pages_per_stream": 1,
                    "token_lookup_chunk_size": 10,
                },
                "chain_validation": {"enabled": False},
                "leaderboard": {"time_period": "WEEK"},
                "pagination": {"page_size": 10, "max_offset": 100},
                "runtime": {
                    "artifacts_root": str(artifacts_root),
                    "run_id": "screening-ledger-seed",
                },
                "wallet_filter": {"include_wallets": [], "exclude_wallets": []},
            }
            fetch_wallet_snapshot(
                seed_client,  # type: ignore[arg-type]
                "0xabc",
                config,
                snapshot_scope="screening",
            )

            failing_client = FailingScreeningWindowAllSourcesClient()
            snapshot = fetch_wallet_snapshot(
                failing_client,  # type: ignore[arg-type]
                "0xabc",
                config,
                snapshot_scope="screening",
            )
            metrics = compute_metrics(
                snapshot=snapshot,
                leaderboard_entry={"pnl": "20", "vol": "1000"},
                weather_index=WeatherIndex(set(), set(), set(), set(), {}),
                config=config,
            )

        self.assertEqual(snapshot["collection_status"]["trades"]["collection_mode"], "history_ledger")
        self.assertTrue(snapshot["trades"][0]["_history_ledger"])
        self.assertTrue(snapshot["collection_status"]["history_ledger"]["trades_complete"])
        self.assertTrue(metrics["screening_evidence_complete"])
        self.assertEqual(metrics["history_scope"], "screening_window")

    def test_trade_probe_can_read_history_ledger_from_cloudflare_when_backend_is_local(self) -> None:
        client = FailingTradeProbeAllSourcesClient()
        config = {
            "analysis": {"position_size_threshold": 0.1},
            "history_ledger": {
                "enabled": True,
                "backend": "local",
                "cloudflare_account_id": "account-id",
                "cloudflare_d1_database_id": "database-id",
                "cloudflare_api_token": "api-token",
                "read_cloud_fallback_enabled": True,
            },
            "history_provider": {
                "enabled": True,
                "trade_probe_fallback_enabled": True,
                "page_size": 50,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "ALL"},
            "pagination": {"page_size": 10, "max_offset": 100},
            "wallet_filter": {
                "include_wallets": [],
                "exclude_wallets": [],
                "min_traded_count": 1,
            },
        }

        def fake_cloudflare_select(
            _config: Any,
            table: str,
            *,
            columns: list[str],
            filters: dict[str, str] | None = None,
            order: str | None = None,
            limit: int | None = None,
            offset: int | None = None,
        ) -> list[dict[str, Any]]:
            if table == "wallet_history_gaps":
                return [
                    {
                        "gap_key": "gap-1",
                        "wallet_address": "0xabc",
                        "run_id": "seed-run",
                        "snapshot_scope": "full",
                        "section_name": "trades",
                        "history_scope": "full_history",
                        "collection_mode": "history_ledger",
                        "stop_reason": "history_ledger_trade_history_complete",
                        "complete": True,
                        "range_start": None,
                        "range_end": None,
                        "payload": {},
                        "updated_at": "2026-05-06T00:00:00+00:00",
                    }
                ]
            if table == "wallet_trade_ledger":
                return [
                    {
                        "record_key": "trade-1",
                        "wallet_address": "0xabc",
                        "run_id": "seed-run",
                        "snapshot_scope": "full",
                        "history_scope": "full_history",
                        "event_timestamp": 1_777_000_000,
                        "payload": {
                            "id": "cloudflare-trade-1",
                            "timestamp": 1_777_000_000,
                            "side": "BUY",
                            "asset": "1001",
                            "size": 3,
                            "usdcSize": 1.5,
                        },
                        "updated_at": "2026-05-06T00:00:00+00:00",
                    }
                ]
            return []

        with patch(
            "polymarket_weather_tool.history_ledger.cloudflare_d1_select_rows",
            side_effect=fake_cloudflare_select,
        ) as select_mock:
            result = probe_wallet_trade_window(
                client,  # type: ignore[arg-type]
                "0xabc",
                {"rank": 1, "userName": "cloud-fallback"},
                config,
            )

        self.assertTrue(result["trade_probe_fetched"])
        self.assertEqual(result["prefetched_trades"][0]["id"], "cloudflare-trade-1")
        self.assertTrue(result["prefetched_trades"][0]["_history_ledger"])
        self.assertTrue(any(call.args[1] == "wallet_history_gaps" for call in select_mock.call_args_list))
        self.assertTrue(any(call.args[1] == "wallet_trade_ledger" for call in select_mock.call_args_list))

    def test_analyze_leaderboard_entry_hydrates_selected_wallet_after_screening_snapshot(self) -> None:
        current_ts = 1_777_000_000
        records = [
            {"type": "TRADE", "id": "recent-trade", "timestamp": current_ts - 60},
            {"type": "REWARD", "id": "recent-reward", "timestamp": current_ts - 30},
        ]
        client = ScreeningHydrationClient(records)
        config = {
            "analysis": {
                "current_datetime": "2026-04-25T00:00:00+00:00",
                "position_size_threshold": 0.1,
                "screening_snapshot_enabled": True,
                "hydrate_selected_wallet_full_history": True,
                "top_trades_in_report": 3,
                "top_positions_in_report": 3,
                "top_closed_positions_in_report": 3,
            },
            "chain_validation": {"enabled": False},
            "leaderboard": {"time_period": "DAY"},
            "pagination": {"page_size": 10, "max_offset": 100},
            "wallet_filter": {
                "min_pnl": 0,
                "min_volume": 0,
                "min_traded_count": 1,
                "min_weather_trade_ratio": 0,
                "include_wallets": [],
                "exclude_wallets": [],
            },
        }

        result = analyze_leaderboard_entry(
            client=client,  # type: ignore[arg-type]
            leaderboard_entry={
                "proxyWallet": "0xabc",
                "pnl": "20",
                "vol": "1000",
                "rank": 1,
                "userName": "hydrate-me",
            },
            weather_index=WeatherIndex(set(), set(), set(), set(), {}),
            config=config,
        )

        wallet_result = result["wallet_result"]
        self.assertEqual(wallet_result["deep_hydration"]["status"], "completed")
        self.assertEqual(wallet_result["metrics"]["snapshot_collection_status"]["positions"]["record_count"], 1)
        self.assertEqual(wallet_result["metrics"]["snapshot_collection_status"]["closed_positions"]["record_count"], 1)
        self.assertTrue(any(name == "positions" for name, _kwargs in client.calls))
        self.assertTrue(any(name == "closed_positions" for name, _kwargs in client.calls))

    def test_screening_accepts_partial_snapshot_when_window_evidence_is_complete(self) -> None:
        config = {
            "runtime": {},
            "wallet_filter": {
                "min_pnl": 10,
                "min_volume": 100,
                "min_traded_count": 2,
                "min_weather_trade_ratio": 0.5,
                "include_wallets": [],
                "exclude_wallets": [],
            },
        }
        metrics = {
            "leaderboard_pnl": 120.0,
            "leaderboard_volume": 2000.0,
            "trade_count": 2,
            "screening_trade_count": 2,
            "weather_trade_count": 2,
            "screening_weather_trade_count": 2,
            "weather_trade_ratio": 1.0,
            "screening_weather_trade_ratio": 1.0,
            "weather_notional_ratio": 1.0,
            "screening_weather_notional_ratio": 1.0,
            "snapshot_complete": False,
            "screening_evidence_complete": True,
        }

        screening = build_screening_record(
            "0xabc",
            {"rank": 1, "userName": "partial-window"},
            metrics,
            config,
        )

        self.assertTrue(screening["selected"])
        self.assertEqual(
            screening["reasons"],
            ["partial_snapshot:screening_window_complete", "passed all numeric filters"],
        )

    def test_history_registry_cloudflare_read_fallback_returns_false_on_d1_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_root = Path(temp_dir)
            registry = create_history_registry(
                artifacts_root,
                {
                    "history_registry": {
                        "enabled": True,
                        "backend": "local",
                        "cloudflare_account_id": "account-id",
                        "cloudflare_d1_database_id": "database-id",
                        "cloudflare_api_token": "api-token",
                        "read_cloud_fallback_enabled": True,
                    }
                },
            )

            with patch(
                "polymarket_weather_tool.history_registry.cloudflare_d1_select_rows",
                side_effect=CloudflareD1RequestError("boom"),
            ):
                self.assertFalse(registry.contains("0xabc"))

    def test_cloudflare_delete_rows_requires_filters(self) -> None:
        config = CloudflareD1Config(
            account_id="account-id",
            database_id="database-id",
            api_token="api-token",
        )

        with self.assertRaisesRegex(CloudflareD1RequestError, "Refusing to delete rows without filters"):
            cloudflare_d1_delete_rows(config, "wallet_registry", filters={})

    def test_cloudflare_d1_config_supports_global_api_key_auth(self) -> None:
        config = CloudflareD1Config.from_settings(
            {
                "cloudflare_account_id": "account-id",
                "cloudflare_d1_database_id": "database-id",
                "cloudflare_email": "user@example.com",
                "cloudflare_global_api_key": "global-key",
            }
        )

        headers = _cloudflare_headers(config)

        self.assertTrue(config.enabled)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["X-Auth-Email"], "user@example.com")
        self.assertEqual(headers["X-Auth-Key"], "global-key")


if __name__ == "__main__":
    unittest.main()
