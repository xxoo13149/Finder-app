from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool import analysis
from polymarket_weather_tool.config import WEEKLY_HIGH_PROFIT_MODE, apply_analysis_mode


UTC = timezone.utc
BASE_TS = 1_700_000_000
BASE_DT = datetime.fromtimestamp(BASE_TS, tz=UTC)
WALLET = "0xabc1230000000000000000000000000000000000"


class FakePolymarketClient:
    instances: list["FakePolymarketClient"] = []

    def __init__(self, api_config: dict[str, Any]) -> None:
        self.api_config = api_config
        self.calls: list[tuple[str, dict[str, Any]]] = []
        type(self).instances.append(self)

    def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("leaderboard", kwargs))
        return [
            {
                "rank": 1,
                "proxyWallet": WALLET,
                "userName": "smoke-weather",
                "xUsername": "smoke_weather",
                "pnl": "250.50",
                "vol": "2400",
            }
        ]

    def fetch_events_keyset_page(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("events_keyset", kwargs))
        return {
            "events": [
                {
                    "id": "weather-event-1",
                    "slug": "rain-in-nyc",
                    "series": [{"title": "NYC Daily Weather"}],
                    "tags": [{"label": "Weather"}, {"label": "NYC"}],
                    "markets": [
                        {
                            "conditionId": "cond-weather-yes",
                            "slug": "rain-in-nyc-yes",
                        }
                    ],
                }
            ],
            "next_cursor": None,
        }

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        return [{"type": "REWARD", "usdcSize": "12.34"}]

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        return [
            {
                "asset": "rain-yes",
                "side": "BUY",
                "title": "NYC rain",
                "outcome": "Yes",
                "eventId": "weather-event-1",
                "eventSlug": "rain-in-nyc",
                "conditionId": "cond-weather-yes",
                "slug": "rain-in-nyc-yes",
                "timestamp": BASE_TS,
                "size": "100",
                "price": "0.40",
                "usdcSize": "40",
            },
            {
                "asset": "rain-yes",
                "side": "SELL",
                "title": "NYC rain",
                "outcome": "Yes",
                "eventId": "weather-event-1",
                "eventSlug": "rain-in-nyc",
                "conditionId": "cond-weather-yes",
                "slug": "rain-in-nyc-yes",
                "timestamp": BASE_TS + 48 * 3600,
                "size": "50",
                "price": "0.70",
                "usdcSize": "35",
            },
            {
                "asset": "snow-no",
                "side": "BUY",
                "title": "Boston snow",
                "outcome": "No",
                "eventId": "other-event",
                "eventSlug": "snow-in-boston",
                "conditionId": "cond-other",
                "slug": "snow-in-boston-no",
                "timestamp": BASE_TS + 72 * 3600,
                "size": "100",
                "price": "1.00",
                "usdcSize": "100",
            },
            {
                "asset": "rain-yes-late",
                "side": "BUY",
                "title": "NYC rain",
                "outcome": "Yes",
                "eventSlug": "rain-in-nyc",
                "conditionId": "cond-weather-yes",
                "timestamp": BASE_TS + 96 * 3600,
                "size": "50",
                "price": "0.50",
                "usdcSize": "25",
            },
        ]

    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {
                "title": "Long dated rain basket",
                "outcome": "Yes",
                "conditionId": "cond-weather-yes",
                "slug": "rain-in-nyc-yes",
                "eventSlug": "rain-in-nyc",
                "currentValue": "88",
                "cashPnl": "18",
                "endDate": "2030-01-01T00:00:00Z",
            }
        ]
        return records[offset : offset + limit]

    def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("closed_positions", kwargs))
        end_date = (BASE_DT + timedelta(days=10)).isoformat()
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {"title": "A", "conditionId": "cond-weather-yes", "realizedPnl": "20", "totalBought": "50", "endDate": end_date},
            {"title": "B", "conditionId": "cond-other", "realizedPnl": "-5", "totalBought": "20", "endDate": end_date},
            {"title": "C", "conditionId": "cond-third", "realizedPnl": "1", "totalBought": "10", "endDate": end_date},
        ]
        return records[offset : offset + limit]


def raise_terminal_http_400(section: str, *, limit: int, offset: int) -> None:
    error = HTTPError(
        url=f"https://data-api.polymarket.com/{section}?limit={limit}&offset={offset}",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=None,
    )
    raise RuntimeError("Request failed") from error


def raise_terminal_transport_error(section: str, *, limit: int, offset: int) -> None:
    error = URLError(f"{section} pagination transport failed at offset={offset}, limit={limit}")
    raise RuntimeError("Request failed") from error


class PartitionRecoveringPolymarketClient(FakePolymarketClient):
    def __init__(self, api_config: dict[str, Any]) -> None:
        super().__init__(api_config)
        self.trade_records = [
            {
                "asset": "rain-yes",
                "side": "BUY",
                "title": "NYC rain",
                "outcome": "Yes",
                "eventId": "weather-event-1",
                "eventSlug": "rain-in-nyc",
                "conditionId": "cond-weather-yes",
                "slug": "rain-in-nyc-yes",
                "timestamp": BASE_TS,
                "size": "100",
                "price": "0.40",
                "usdcSize": "40",
                "transactionHash": "0xtx-1",
            },
            {
                "asset": "rain-yes",
                "side": "SELL",
                "title": "NYC rain",
                "outcome": "Yes",
                "eventId": "weather-event-1",
                "eventSlug": "rain-in-nyc",
                "conditionId": "cond-weather-yes",
                "slug": "rain-in-nyc-yes",
                "timestamp": BASE_TS + 48 * 3600,
                "size": "50",
                "price": "0.70",
                "usdcSize": "35",
                "transactionHash": "0xtx-2",
            },
            {
                "asset": "snow-no",
                "side": "BUY",
                "title": "Boston snow",
                "outcome": "No",
                "eventId": "other-event",
                "eventSlug": "snow-in-boston",
                "conditionId": "cond-other",
                "slug": "snow-in-boston-no",
                "timestamp": BASE_TS + 72 * 3600,
                "size": "100",
                "price": "1.00",
                "usdcSize": "100",
                "transactionHash": "0xtx-3",
            },
            {
                "asset": "rain-yes-late",
                "side": "BUY",
                "title": "NYC rain",
                "outcome": "Yes",
                "eventSlug": "rain-in-nyc",
                "conditionId": "cond-weather-yes",
                "timestamp": BASE_TS + 96 * 3600,
                "size": "50",
                "price": "0.50",
                "usdcSize": "25",
                "transactionHash": "0xtx-4",
            },
        ]
        self.activity_records = [
            {"type": "REWARD", "usdcSize": "12.34", "timestamp": BASE_TS + 110 * 3600, "transactionHash": "0xreward"},
            *[
                {
                    **record,
                    "type": "TRADE",
                }
                for record in self.trade_records
            ],
        ]

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        activity_type = str(kwargs.get("activity_type") or "").upper()
        start = kwargs.get("start")
        end = kwargs.get("end")
        records = [
            dict(record)
            for record in self.activity_records
            if not activity_type or str(record.get("type") or "").upper() == activity_type
        ]
        if start is None and end is None:
            if offset == 0:
                records.sort(key=lambda record: int(record["timestamp"]), reverse=True)
                return records[:1]
            raise_terminal_http_400("activity", limit=limit, offset=offset)
        filtered = [
            record
            for record in records
            if int(record["timestamp"]) >= int(start) and int(record["timestamp"]) <= int(end)
        ]
        filtered.sort(key=lambda record: int(record["timestamp"]), reverse=True)
        return filtered[offset : offset + limit]

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        if offset == 0:
            records = sorted(
                (dict(record) for record in self.trade_records),
                key=lambda record: int(record["timestamp"]),
                reverse=True,
            )
            return records[:1]
        raise_terminal_http_400("trades", limit=limit, offset=offset)


class GraphQLHistoryPipelineClient(FakePolymarketClient):
    def __init__(self, api_config: dict[str, Any]) -> None:
        super().__init__(api_config)
        self.graphql_calls: list[dict[str, Any]] = []

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        return [
            {
                "type": "REWARD",
                "usdcSize": "12.34",
                "timestamp": BASE_TS + 110 * 3600,
                "transactionHash": "0xreward",
            }
        ]

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        raise AssertionError("provider smoke should not rely on REST trades")

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
                            "transactionHash": "0xfill-1",
                            "timestamp": str(BASE_TS),
                            "maker": WALLET,
                            "taker": "0x0000000000000000000000000000000000000001",
                            "makerAssetId": "0",
                            "takerAssetId": "1001",
                            "makerAmountFilled": "40000000",
                            "takerAmountFilled": "100000000",
                            "fee": "10000",
                        },
                        {
                            "id": "graphql-fill-2",
                            "transactionHash": "0xfill-2",
                            "timestamp": str(BASE_TS + 48 * 3600),
                            "maker": WALLET,
                            "taker": "0x0000000000000000000000000000000000000002",
                            "makerAssetId": "1001",
                            "takerAssetId": "0",
                            "makerAmountFilled": "50000000",
                            "takerAmountFilled": "35000000",
                            "fee": "10000",
                        },
                    ],
                    "taker": [
                        {
                            "id": "graphql-fill-3",
                            "transactionHash": "0xfill-3",
                            "timestamp": str(BASE_TS + 72 * 3600),
                            "maker": "0x0000000000000000000000000000000000000003",
                            "taker": WALLET,
                            "makerAssetId": "0",
                            "takerAssetId": "1001",
                            "makerAmountFilled": "25000000",
                            "takerAmountFilled": "50000000",
                            "fee": "5000",
                        },
                    ],
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
                            "condition": {"id": "cond-weather-yes"},
                        }
                    ]
                }
            }
        if "WalletSplits" in query:
            return {"data": {"splits": []}}
        if "WalletMerges" in query:
            return {"data": {"merges": []}}
        if "WalletRedemptions" in query:
            return {"data": {"redemptions": []}}
        if "WalletNegRiskConversions" in query:
            return {"data": {"negRiskConversions": []}}
        raise AssertionError(f"unexpected graphql query: {query}")


class TransportRecoveringPolymarketClient(PartitionRecoveringPolymarketClient):
    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        activity_type = str(kwargs.get("activity_type") or "").upper()
        start = kwargs.get("start")
        end = kwargs.get("end")
        records = [
            dict(record)
            for record in self.activity_records
            if not activity_type or str(record.get("type") or "").upper() == activity_type
        ]
        if start is None and end is None:
            if offset == 0:
                records.sort(key=lambda record: int(record["timestamp"]), reverse=True)
                return records[:1]
            raise_terminal_transport_error("activity", limit=limit, offset=offset)
        filtered = [
            record
            for record in records
            if int(record["timestamp"]) >= int(start) and int(record["timestamp"]) <= int(end)
        ]
        filtered.sort(key=lambda record: int(record["timestamp"]), reverse=True)
        return filtered[offset : offset + limit]

    def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("trades", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        if offset == 0:
            records = sorted(
                (dict(record) for record in self.trade_records),
                key=lambda record: int(record["timestamp"]),
                reverse=True,
            )
            return records[:1]
        raise_terminal_transport_error("trades", limit=limit, offset=offset)


class InitialTransportRecoveringPolymarketClient(PartitionRecoveringPolymarketClient):
    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        activity_type = str(kwargs.get("activity_type") or "").upper()
        start = kwargs.get("start")
        end = kwargs.get("end")
        records = [
            dict(record)
            for record in self.activity_records
            if not activity_type or str(record.get("type") or "").upper() == activity_type
        ]
        if start is None and end is None:
            raise_terminal_transport_error("activity", limit=limit, offset=offset)
        filtered = [
            record
            for record in records
            if int(record["timestamp"]) >= int(start) and int(record["timestamp"]) <= int(end)
        ]
        filtered.sort(key=lambda record: int(record["timestamp"]), reverse=True)
        return filtered[offset : offset + limit]


def small_config(cache_dir: Path) -> dict[str, Any]:
    return {
        "api": {
            "timeout_seconds": 1,
            "retry_count": 0,
            "retry_backoff_seconds": 0,
            "request_delay_seconds": 0,
            "user_agent": "pipeline-smoke-test",
            "use_cache": False,
            "cache_dir": str(cache_dir),
            "cache_ttl_seconds": 0,
        },
        "leaderboard": {
            "category": "WEATHER",
            "time_period": "ALL",
            "order_by": "PNL",
            "fetch_limit": 1,
            "page_size": 1,
        },
        "wallet_filter": {
            "target_count": 1,
            "min_pnl": 100,
            "min_volume": 1000,
            "min_traded_count": 3,
            "min_weather_trade_ratio": 0.5,
            "include_wallets": [],
            "exclude_wallets": [],
        },
        "pagination": {"page_size": 10, "max_offset": 0},
        "weather": {
            "tag_id": 84,
            "tag_slug": "weather",
            "use_keyset": True,
            "order": "createdAt",
            "ascending": False,
            "max_events": 1,
            "active_only": False,
            "closed_only": False,
            "include_archived": False,
            "page_size": 10,
        },
        "analysis": {
            "concurrent_wallets": 1,
            "long_dated_threshold_days": 90,
            "position_size_threshold": 0.1,
            "top_positions_in_report": 3,
            "top_trades_in_report": 3,
            "top_closed_positions_in_report": 3,
        },
        "labels": [
            {
                "key": "weather_specialist",
                "display_name": "Weather specialist",
                "any": [
                    {"field": "weather_notional_ratio", "op": ">=", "value": 0.5},
                    {"field": "weather_trade_ratio", "op": ">=", "value": 0.75},
                ],
            },
            {
                "key": "high_win_rate",
                "display_name": "High win rate",
                "all": [
                    {"field": "closed_position_count", "op": ">=", "value": 3},
                    {"field": "closed_position_sample_win_rate", "op": ">=", "value": 0.6},
                ],
            },
        ],
    }


def fallback_analysis_summary(**kwargs: Any) -> dict[str, Any]:
    wallet_results = kwargs["wallet_results"]
    notionals = [
        result["metrics"]["median_trade_notional"]
        for result in wallet_results
    ]
    return {
        "leaderboard_rows": len(kwargs["leaderboard"]),
        "weather_events": len(kwargs["weather_events"]),
        "selected_wallets": len(wallet_results),
        "wallets_screened": len(kwargs["screening_records"]),
        "errors": len(kwargs["errors"]),
        "median_trade_notional_values": notionals,
    }


def history_record_path(root: Path, wallet: str) -> Path:
    normalized_wallet = analysis.normalize_address(wallet)
    return root / analysis.HISTORY_REGISTRY_DIRNAME / f"{normalized_wallet}.json"


def disable_light_first_gate(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("analysis", {})["full_history_core_gate_enabled"] = False
    return config


def seed_history_record(
    root: Path,
    wallet: str,
    *,
    user_name: str = "seeded-user",
    x_username: str = "seeded_user",
    first_seen_at: str = "2026-04-01T00:00:00+00:00",
    last_seen_at: str = "2026-04-02T00:00:00+00:00",
    run_count: int = 1,
    last_run_id: str = "prior-run",
    last_status: str = "selected",
) -> None:
    path = history_record_path(root, wallet)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "wallet_address": analysis.normalize_address(wallet),
                "user_name": user_name,
                "x_username": x_username,
                "first_seen_at": first_seen_at,
                "last_seen_at": last_seen_at,
                "run_count": run_count,
                "last_run_id": last_run_id,
                "last_status": last_status,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class PipelineSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakePolymarketClient.instances.clear()

    def test_build_screening_record_enforces_max_numeric_filters(self) -> None:
        config = small_config(Path("cache"))
        config["wallet_filter"].update(
            {
                "min_pnl": 10,
                "max_pnl": 200,
                "min_volume": 100,
                "max_volume": 400,
                "min_traded_count": 2,
                "max_traded_count": 4,
            }
        )
        metrics = {
            "leaderboard_pnl": 250.0,
            "leaderboard_volume": 500.0,
            "trade_count": 5,
            "weather_trade_count": 3,
            "weather_trade_ratio": 0.6,
            "weather_notional_ratio": 0.7,
        }

        screening = analysis.build_screening_record(
            WALLET,
            {"rank": 1, "userName": "screening-max", "xUsername": "screening_max"},
            metrics,
            config,
        )

        self.assertFalse(screening["selected"])
        self.assertEqual(
            screening["reasons"],
            [
                "failed:pnl<=200",
                "failed:volume<=400",
                "failed:trade_count<=4",
            ],
        )

    def test_build_screening_record_enforces_weather_trade_ratio_filter(self) -> None:
        config = small_config(Path("cache"))
        metrics = {
            "leaderboard_pnl": 120.0,
            "leaderboard_volume": 2000.0,
            "trade_count": 12,
            "weather_trade_count": 4,
            "weather_trade_ratio": 4 / 12,
            "weather_notional_ratio": 0.7,
        }

        screening = analysis.build_screening_record(
            WALLET,
            {"rank": 1, "userName": "screening-weather", "xUsername": "screening_weather"},
            metrics,
            config,
        )

        self.assertFalse(screening["selected"])
        self.assertIn("failed:weather_trade_ratio>=0.5", screening["reasons"])

    def test_build_screening_record_prefers_period_scoped_trade_metrics(self) -> None:
        config = small_config(Path("cache"))
        config["wallet_filter"].update(
            {
                "min_traded_count": 2,
                "max_traded_count": 10,
                "min_weather_trade_ratio": 0.5,
            }
        )
        metrics = {
            "leaderboard_pnl": 120.0,
            "leaderboard_volume": 2000.0,
            "trade_count": 300,
            "screening_trade_count": 5,
            "weather_trade_count": 12,
            "screening_weather_trade_count": 3,
            "weather_trade_ratio": 0.04,
            "screening_weather_trade_ratio": 0.6,
            "weather_notional_ratio": 0.1,
            "screening_weather_notional_ratio": 0.8,
        }

        screening = analysis.build_screening_record(
            WALLET,
            {"rank": 1, "userName": "screening-period", "xUsername": "screening_period"},
            metrics,
            config,
        )

        self.assertTrue(screening["selected"])
        self.assertEqual(screening["trade_count"], 5)
        self.assertEqual(screening["weather_trade_count"], 3)
        self.assertAlmostEqual(screening["weather_trade_ratio"], 0.6)
        self.assertAlmostEqual(screening["weather_notional_ratio"], 0.8)

    def test_build_screening_record_weekly_mode_accepts_weather_notional_ratio_even_when_trade_ratio_is_low(self) -> None:
        standard_config = small_config(Path("cache"))
        weekly_config = apply_analysis_mode(small_config(Path("cache")), WEEKLY_HIGH_PROFIT_MODE)
        metrics = {
            "leaderboard_pnl": 120.0,
            "leaderboard_volume": 2000.0,
            "trade_count": 12,
            "weather_trade_count": 2,
            "weather_trade_ratio": 2 / 12,
            "weather_notional_ratio": 0.5,
        }
        leaderboard_entry = {
            "rank": 1,
            "userName": "screening-weekly",
            "xUsername": "screening_weekly",
        }

        standard_screening = analysis.build_screening_record(
            WALLET,
            leaderboard_entry,
            metrics,
            standard_config,
        )
        weekly_screening = analysis.build_screening_record(
            WALLET,
            leaderboard_entry,
            metrics,
            weekly_config,
        )

        self.assertFalse(standard_screening["selected"])
        self.assertIn("failed:weather_trade_ratio>=0.5", standard_screening["reasons"])
        self.assertTrue(weekly_screening["selected"])
        self.assertEqual(weekly_screening["reasons"], ["passed all numeric filters"])

    def test_build_screening_record_rejects_incomplete_snapshot_by_default(self) -> None:
        config = small_config(Path("cache"))
        metrics = {
            "leaderboard_pnl": 120.0,
            "leaderboard_volume": 2000.0,
            "trade_count": 12,
            "weather_trade_count": 8,
            "weather_trade_ratio": 8 / 12,
            "weather_notional_ratio": 0.7,
            "snapshot_complete": False,
        }

        screening = analysis.build_screening_record(
            WALLET,
            {"rank": 1, "userName": "screening-incomplete", "xUsername": "screening_incomplete"},
            metrics,
            config,
        )

        self.assertFalse(screening["selected"])
        self.assertIn("failed:snapshot_complete", screening["reasons"])

    def test_weather_index_maps_market_dates_for_high_temperature_records(self) -> None:
        weather_index = analysis.build_weather_index(
            [
                {
                    "id": "event-high-temp",
                    "slug": "highest-temperature-in-nyc-on-april-25-2026",
                    "series": [{"title": "NYC Daily Weather"}],
                    "endDate": "2026-04-25T00:00:00Z",
                    "markets": [
                        {
                            "id": "market-high-temp",
                            "conditionId": "cond-high-temp",
                            "slug": "highest-temperature-in-nyc-on-april-25-2026",
                        }
                    ],
                }
            ]
        )
        enriched = analysis.enrich_trades_with_regions(
            [
                {
                    "conditionId": "cond-high-temp",
                    "slug": "highest-temperature-in-nyc-on-april-25-2026",
                }
            ],
            weather_index=weather_index,
            region_fields=("_region", "region"),
        )

        self.assertEqual(enriched[0]["_region"], "NYC")
        self.assertEqual(enriched[0]["_market_date"], "2026-04-25")

    def test_weather_record_uses_record_level_weather_evidence_when_index_misses(self) -> None:
        empty_index = analysis.WeatherIndex(set(), set(), set(), set(), {})

        self.assertTrue(
            analysis.is_weather_record(
                {
                    "title": "Highest temperature in Austin on May 8?",
                    "slug": "highest-temperature-in-austin-on-may-8",
                    "tags": [{"slug": "weather"}],
                },
                empty_index,
            )
        )
        self.assertTrue(
            analysis.is_weather_record(
                {"eventSlug": "daily-temperature-in-chicago", "conditionId": "missing-cond"},
                empty_index,
            )
        )
        self.assertFalse(
            analysis.is_weather_record(
                {"title": "Bitcoin range by Friday", "tags": [{"slug": "recurring"}]},
                empty_index,
            )
        )

    def test_compute_metrics_counts_record_level_weather_evidence_without_index_match(self) -> None:
        empty_index = analysis.WeatherIndex(set(), set(), set(), set(), {})
        snapshot = {
            "wallet": WALLET,
            "activity": [],
            "trades": [
                {
                    "title": "Daily temperature in Austin on May 8?",
                    "slug": "daily-temperature-in-austin-on-may-8",
                    "tags": [{"slug": "weather"}],
                    "timestamp": BASE_TS,
                    "side": "BUY",
                    "size": "100",
                    "price": "0.50",
                    "usdcSize": "50",
                },
                {
                    "title": "Bitcoin range by Friday",
                    "slug": "bitcoin-range-by-friday",
                    "timestamp": BASE_TS + 3600,
                    "side": "BUY",
                    "size": "100",
                    "price": "0.50",
                    "usdcSize": "50",
                },
            ],
            "rewards": [],
            "positions": [],
            "closed_positions": [],
        }

        metrics = analysis.compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "100", "vol": "100"},
            weather_index=empty_index,
            config=small_config(Path("cache")),
        )

        self.assertEqual(metrics["weather_trade_count"], 1)
        self.assertAlmostEqual(metrics["weather_trade_ratio"], 0.5)
        self.assertAlmostEqual(metrics["weather_notional_ratio"], 0.5)

    def test_compute_metrics_covers_weather_ratio_win_rate_cost_and_frequency(self) -> None:
        client = FakePolymarketClient({"use_cache": False})
        events = client.fetch_events_keyset_page(limit=1)["events"]
        weather_index = analysis.build_weather_index(events)
        snapshot = {
            "wallet": WALLET,
            "activity": client.fetch_activity_page(user=WALLET, limit=10, offset=0),
            "trades": client.fetch_trades_page(user=WALLET, limit=10, offset=0),
            "rewards": [{"type": "REWARD", "usdcSize": "12.34"}],
            "positions": client.fetch_positions_page(user=WALLET, limit=10, offset=0),
            "closed_positions": client.fetch_closed_positions_page(user=WALLET, limit=10, offset=0),
        }

        metrics = analysis.compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "250.50", "vol": "2400"},
            weather_index=weather_index,
            config=small_config(Path("cache")),
        )

        self.assertEqual(metrics["trade_count"], 4)
        self.assertEqual(metrics["weather_trade_count"], 3)
        self.assertAlmostEqual(metrics["weather_notional_ratio"], 0.5)
        self.assertEqual(metrics["screening_trade_count"], 4)
        self.assertEqual(metrics["screening_weather_trade_count"], 3)
        self.assertAlmostEqual(metrics["screening_weather_trade_ratio"], 0.75)
        self.assertAlmostEqual(metrics["screening_weather_notional_ratio"], 0.5)
        self.assertAlmostEqual(metrics["closed_position_win_rate"], 2 / 3)
        self.assertAlmostEqual(metrics["closed_position_sample_win_rate"], 2 / 3)
        self.assertAlmostEqual(metrics["wallet_win_rate"], 1 / 3)
        self.assertEqual(metrics["wallet_win_rate_source"], "regional_trade_day_cashflow")
        self.assertAlmostEqual(metrics["median_trade_notional"], 37.5)
        self.assertGreater(metrics["trades_per_active_day"], 0)
        self.assertAlmostEqual(metrics["reward_total_usdc"], 12.34)
        self.assertAlmostEqual(metrics["holding_duration_coverage"], 1.0)
        self.assertAlmostEqual(metrics["median_holding_hours"], 48.0)
        self.assertGreater(metrics["time_to_end_coverage"], 0)
        self.assertEqual(metrics["dominant_region"], "NYC")
        self.assertAlmostEqual(metrics["dominant_region_trade_ratio"], 1.0)
        self.assertEqual(metrics["chain_validation_status"], "disabled")
        self.assertFalse(metrics["split_player_validation_passed"])
        self.assertIn("profile", metrics)
        profile = metrics["profile"]
        self.assertEqual(
            set(profile),
            {
                "average_buy_price",
                "city_distribution",
                "top_cities",
                "buy_price_distribution",
                "closed_position_pnl",
            },
        )
        self.assertAlmostEqual(
            profile["average_buy_price"]["weighted_average_price"],
            165 / 250,
        )
        self.assertEqual(profile["city_distribution"]["unknown_city_trade_count"], 1)
        self.assertEqual(profile["closed_position_pnl"]["win_count"], 2)
        self.assertEqual(profile["closed_position_pnl"]["loss_count"], 1)
        self.assertAlmostEqual(profile["closed_position_pnl"]["total_realized_pnl"], 16.0)

    def test_compute_metrics_scopes_screening_trade_counts_to_leaderboard_period(self) -> None:
        client = FakePolymarketClient({"use_cache": False})
        events = client.fetch_events_keyset_page(limit=1)["events"]
        weather_index = analysis.build_weather_index(events)
        snapshot = {
            "wallet": WALLET,
            "activity": client.fetch_activity_page(user=WALLET, limit=10, offset=0),
            "trades": client.fetch_trades_page(user=WALLET, limit=10, offset=0),
            "rewards": [{"type": "REWARD", "usdcSize": "12.34"}],
            "positions": client.fetch_positions_page(user=WALLET, limit=10, offset=0),
            "closed_positions": client.fetch_closed_positions_page(user=WALLET, limit=10, offset=0),
        }
        config = small_config(Path("cache"))
        config["leaderboard"]["time_period"] = "DAY"
        config["analysis"]["current_datetime"] = (BASE_DT + timedelta(days=5)).isoformat()

        metrics = analysis.compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "250.50", "vol": "2400"},
            weather_index=weather_index,
            config=config,
        )

        self.assertEqual(metrics["trade_count"], 4)
        self.assertEqual(metrics["screening_trade_count"], 1)
        self.assertEqual(metrics["screening_weather_trade_count"], 1)
        self.assertAlmostEqual(metrics["screening_weather_trade_ratio"], 1.0)
        self.assertAlmostEqual(metrics["screening_weather_notional_ratio"], 1.0)

    def test_fetch_optional_chain_validation_extracts_positions_converted_logs(self) -> None:
        class FakeChainClient:
            def fetch_polygon_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
                return [
                    {
                        "address": analysis.DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
                        "topics": [
                            analysis.POSITIONS_CONVERTED_TOPIC0,
                            analysis.address_to_topic(WALLET),
                            "0x" + "ab" * 32,
                            hex(2),
                        ],
                        "data": hex(500),
                        "transactionHash": "0xconvert",
                        "blockNumber": hex(123),
                        "timeStamp": hex(BASE_TS),
                        "logIndex": hex(1),
                    }
                ]

            def fetch_polygon_transactions(self, **kwargs: Any) -> list[dict[str, Any]]:
                return [{"timeStamp": str(BASE_TS - 100), "hash": "0xfirst"}]

        result = analysis.fetch_optional_chain_validation(
            FakeChainClient(),  # type: ignore[arg-type]
            WALLET,
            {
                "chain_validation": {
                    "enabled": True,
                    "api_key": "test-key",
                }
            },
        )

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["split_evidence_count"], 1)
        self.assertEqual(result["first_transaction_hash"], "0xfirst")
        self.assertEqual(result["evidence"][0]["stakeholder"], WALLET)
        self.assertEqual(result["evidence"][0]["index_set"], 2)

    def test_fetch_optional_chain_validation_overlaps_logs_and_transactions(self) -> None:
        barrier = threading.Barrier(2)

        class ConcurrentChainClient:
            def fetch_polygon_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
                barrier.wait(timeout=1)
                return []

            def fetch_polygon_transactions(self, **kwargs: Any) -> list[dict[str, Any]]:
                barrier.wait(timeout=1)
                return [{"timeStamp": str(BASE_TS - 100), "hash": "0xfirst"}]

        result = analysis.fetch_optional_chain_validation(
            ConcurrentChainClient(),  # type: ignore[arg-type]
            WALLET,
            {
                "chain_validation": {
                    "enabled": True,
                    "api_key": "test-key",
                }
            },
        )

        self.assertEqual(result["status"], "no_split_evidence")
        self.assertEqual(result["first_transaction_hash"], "0xfirst")

    def test_split_player_requires_average_cost_and_chain_evidence(self) -> None:
        config = small_config(Path("cache"))
        config["chain_validation"] = {
            "enabled": True,
            "min_split_evidence_count": 2,
            "split_target_avg_chip_cost": 5,
            "split_avg_chip_cost_tolerance": 0.5,
        }
        snapshot = {
            "wallet": WALLET,
            "activity": [],
            "trades": [],
            "rewards": [],
            "positions": [
                {"avgPrice": "0.05", "size": "100"},
                {"avgPrice": "0.052", "size": "100"},
            ],
            "closed_positions": [],
            "chain_validation": {
                "status": "verified",
                "reason": "positions converted logs found",
                "split_evidence_count": 2,
                "evidence": [{"transaction_hash": "0x1"}, {"transaction_hash": "0x2"}],
            },
        }

        metrics = analysis.compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "0", "vol": "0"},
            weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
            config=config,
        )

        self.assertAlmostEqual(metrics["split_avg_chip_cost"], 5.1)
        self.assertTrue(metrics["split_avg_chip_cost_matched"])
        self.assertTrue(metrics["split_chain_verified"])
        self.assertTrue(metrics["split_player_validation_passed"])

        snapshot["chain_validation"] = {"status": "no_split_evidence", "split_evidence_count": 0}
        metrics_without_chain = analysis.compute_metrics(
            snapshot=snapshot,
            leaderboard_entry={"pnl": "0", "vol": "0"},
            weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
            config=config,
        )
        self.assertFalse(metrics_without_chain["split_player_validation_passed"])

    def test_saved_disabled_and_deleted_label_rules_do_not_reappear_in_analysis(self) -> None:
        config = small_config(Path("cache"))
        config["labels"] = [
            {
                "key": "weather_specialist",
                "display_name": "Weather specialist",
                "enabled": False,
                "any": [
                    {"field": "weather_notional_ratio", "op": ">=", "value": 0.5},
                    {"field": "weather_trade_ratio", "op": ">=", "value": 0.75},
                ],
            }
        ]
        weather_index = analysis.WeatherIndex(
            event_ids={"weather-event-1"},
            event_slugs={"highest-temperature-in-shanghai-on-april-13"},
            condition_ids={"cond-shanghai"},
            market_slugs={"highest-temperature-in-shanghai-on-april-13-20c"},
            regions_by_key={"cond-shanghai": "Shanghai"},
            market_dates_by_key={"cond-shanghai": "2026-04-13"},
        )
        snapshot = {
            "wallet": WALLET,
            "activity": [],
            "trades": [
                {
                    "eventId": "weather-event-1",
                    "conditionId": "cond-shanghai",
                    "slug": "highest-temperature-in-shanghai-on-april-13-20c",
                    "timestamp": BASE_TS,
                    "side": "BUY",
                    "price": "0.10",
                    "size": "100",
                    "usdcSize": "10",
                },
                {
                    "eventId": "weather-event-1",
                    "conditionId": "cond-shanghai",
                    "slug": "highest-temperature-in-shanghai-on-april-13-20c",
                    "timestamp": BASE_TS + 3600,
                    "side": "BUY",
                    "price": "0.20",
                    "size": "100",
                    "usdcSize": "20",
                },
            ],
            "rewards": [],
            "positions": [],
            "closed_positions": [],
        }

        wallet_result = analysis.analyze_wallet(
            wallet=WALLET,
            leaderboard_entry={"rank": 1, "userName": "saved-rules", "pnl": "1000", "vol": "10000"},
            snapshot=snapshot,
            weather_index=weather_index,
            config=config,
        )

        metrics = wallet_result["metrics"]
        self.assertGreaterEqual(metrics["weather_notional_ratio"], 0.5)
        self.assertGreater(metrics["low_chip_cost_trade_ratio"], 0.5)
        label_keys = [label["key"] for label in wallet_result["labels"]]
        self.assertIn("high_frequency_region", label_keys)
        self.assertIn("lottery_player", label_keys)
        self.assertTrue(all(label.get("system_core") for label in wallet_result["labels"]))
        self.assertEqual(len(wallet_result["label_evaluations"]), 6)
        self.assertNotIn(
            "weather_specialist",
            [item["key"] for item in wallet_result["label_evaluations"]],
        )
        self.assertNotIn("Weather specialist", wallet_result["selection_record"]["labels"])

    def test_run_pipeline_smoke_uses_fake_client_and_writes_analysis_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", FakePolymarketClient))
                if not hasattr(analysis, "progress"):
                    stack.enter_context(patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True))
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            self.assertEqual(result["errors"], [])
            self.assertTrue((output_dir / "leaderboard.json").exists())
            self.assertTrue((output_dir / "weather_events.json").exists())
            self.assertTrue((output_dir / "screening_records.json").exists())
            self.assertTrue((output_dir / "selected_wallets.json").exists())
            self.assertTrue((output_dir / "analysis_summary.json").exists())
            self.assertTrue((output_dir / "report.txt").exists())

            wallet_path = output_dir / "wallets" / f"{WALLET}.json"
            wallet_result = json.loads(wallet_path.read_text(encoding="utf-8"))
            metrics = wallet_result["metrics"]
            self.assertAlmostEqual(metrics["weather_notional_ratio"], 0.5)
            self.assertAlmostEqual(metrics["closed_position_win_rate"], 2 / 3)
            self.assertAlmostEqual(metrics["closed_position_sample_win_rate"], 2 / 3)
            self.assertAlmostEqual(metrics["wallet_win_rate"], 1 / 3)
            self.assertEqual(metrics["wallet_win_rate_source"], "regional_trade_day_cashflow")
            self.assertAlmostEqual(metrics["median_trade_notional"], 37.5)
            self.assertEqual(
                wallet_result["selection_record"]["labels"],
                ["高频地区：NYC", "Weather specialist", "High win rate"],
            )
            self.assertEqual(
                [item["key"] for item in wallet_result["label_evidence"]],
                ["high_frequency_region", "weather_specialist", "high_win_rate"],
            )
            self.assertTrue(wallet_result["label_evidence"][0]["matched"])
            self.assertIn("conditions", wallet_result["label_evidence"][0]["details"])
            self.assertEqual(
                [label["key"] for label in wallet_result["labels"]],
                ["high_frequency_region", "weather_specialist", "high_win_rate"],
            )
            self.assertTrue(wallet_result["labels"][0]["system_core"])
            self.assertFalse(wallet_result["labels"][1].get("system_core", False))
            self.assertFalse(wallet_result["labels"][2].get("system_core", False))
            self.assertEqual(len(wallet_result["label_evaluations"]), 6)
            label_evaluations = wallet_result["label_evaluations"]
            self.assertEqual(
                [item["key"] for item in label_evaluations],
                [
                    "high_frequency_region",
                    "high_daily_region_profit",
                    "regional_high_win_rate",
                    "lottery_player",
                    "split_player",
                    "liquidity_player",
                ],
            )
            for evaluation in label_evaluations:
                self.assertTrue(
                    {
                        "key",
                        "display_name",
                        "description",
                        "matched",
                        "reason",
                        "facts",
                        "records",
                        "details",
                    }.issubset(evaluation)
                )
                self.assertTrue(evaluation["records"])
            self.assertTrue(label_evaluations[0]["matched"])
            self.assertEqual(label_evaluations[0]["key"], "high_frequency_region")
            split_evaluation = next(
                item for item in label_evaluations if item["key"] == "split_player"
            )
            self.assertFalse(split_evaluation["matched"])
            self.assertEqual(split_evaluation["records"][0]["type"], "counterevidence")
            self.assertIn("operation_audit", wallet_result)
            self.assertIn("profit_summary", wallet_result["operation_audit"])
            self.assertAlmostEqual(
                wallet_result["operation_audit"]["profit_summary"]["trade_liquidity_profit"],
                -130.0,
            )
            self.assertAlmostEqual(
                wallet_result["operation_audit"]["profit_summary"]["final_settlement_profit"],
                16.0,
            )
            self.assertTrue(metrics["snapshot_complete"])
            self.assertNotIn("records", metrics["snapshot_collection_status"]["activity"])
            self.assertNotIn("records", metrics["snapshot_collection_status"]["trades"])
            self.assertNotIn("records", wallet_result["operation_audit"]["collection_status"]["activity"])
            self.assertIn("finder_ai", wallet_result)
            self.assertTrue(wallet_result["finder_ai"]["runId"])
            self.assertEqual(wallet_result["finder_ai"]["normalizedAddress"], WALLET)
            self.assertEqual(wallet_result["finder_ai"]["wallet"]["address"], WALLET)
            self.assertEqual(wallet_result["finder_ai"]["providerMeta"]["provider"], "deepseek")
            self.assertEqual(
                wallet_result["finder_ai"]["providerMeta"]["promptVersion"],
                "finder-weather-brief-v6",
            )
            prompt_context = wallet_result["finder_ai"]["layeredInput"]
            self.assertNotIn("closed_position_win_rate", prompt_context["L3"]["behaviorSnapshot"])
            self.assertTrue(
                str(wallet_result["finder_ai"]["providerMeta"]["inputHash"]).startswith("sha256:")
            )
            self.assertIn("layeredInput", wallet_result["finder_ai"])
            self.assertEqual(
                wallet_result["finder_ai"]["layeredInput"]["L0"]["normalizedAddress"],
                WALLET,
            )
            self.assertEqual(
                wallet_result["finder_ai"]["layeredInput"]["L2"]["primarySignals"][0]["key"],
                "high_frequency_region",
            )
            self.assertIn("briefGeneration", wallet_result["finder_ai"])
            self.assertTrue(wallet_result["finder_ai"]["briefGeneration"]["enabled"])
            self.assertEqual(
                wallet_result["finder_ai"]["briefGeneration"]["status"],
                "fallback",
            )
            self.assertEqual(
                wallet_result["finder_ai"]["briefGeneration"]["reason"],
                "local_fallback",
            )
            self.assertGreaterEqual(
                wallet_result["finder_ai"]["briefGeneration"]["gate"]["structuredEvidenceCount"],
                1,
            )
            self.assertIn("structured_materials", wallet_result)
            structured_materials = wallet_result["structured_materials"]
            self.assertEqual(structured_materials["identity"]["normalized_address"], WALLET)
            self.assertEqual(structured_materials["summary"]["main_region"], "NYC")
            self.assertTrue(structured_materials["summary"]["source_excerpt"])
            self.assertTrue(structured_materials["summary"]["latest_evidence_date"])
            self.assertEqual(
                structured_materials["signals"]["label_hits"][0]["label_key"],
                "high_frequency_region",
            )
            self.assertEqual(
                structured_materials["signals"]["primary_signals"][0]["key"],
                "high_frequency_region",
            )
            self.assertEqual(
                structured_materials["signals"]["weather_signals"]["market_scope"],
                "weather",
            )
            self.assertTrue(structured_materials["records"]["trade_samples"])
            self.assertTrue(structured_materials["records"]["trade_samples"][0]["market_title"])

            profile = wallet_result["profile"]
            self.assertEqual(profile, metrics["profile"])
            self.assertAlmostEqual(
                profile["average_buy_price"]["weighted_average_price"],
                165 / 250,
            )
            self.assertEqual(profile["city_distribution"]["unknown_city_trade_count"], 1)
            self.assertEqual(profile["top_cities"]["by_realized_pnl"][0]["city"], "NYC")
            self.assertAlmostEqual(
                profile["closed_position_pnl"]["total_realized_pnl"],
                16.0,
            )

            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )
            self.assertEqual(selected_wallets[0]["wallet"], WALLET)
            self.assertEqual(selected_wallets[0]["main_region"], "NYC")
            self.assertIn("highest_burst", selected_wallets[0])
            self.assertTrue(selected_wallets[0]["recent_evidence_date"])

            history_record = json.loads(
                history_record_path(temp_path, WALLET).read_text(encoding="utf-8")
            )
            self.assertEqual(history_record["wallet_address"], WALLET)
            self.assertEqual(history_record["user_name"], "smoke-weather")
            self.assertEqual(history_record["x_username"], "smoke_weather")
            self.assertEqual(history_record["run_count"], 1)
            self.assertEqual(history_record["last_run_id"], "out")
            self.assertEqual(history_record["last_status"], "selected")
            self.assertTrue(history_record["first_seen_at"])
            self.assertTrue(history_record["last_seen_at"])

            calls = [name for name, _kwargs in FakePolymarketClient.instances[0].calls]
            self.assertEqual(
                calls,
                [
                    "leaderboard",
                    "events_keyset",
                    "trades",
                    "activity",
                    "positions",
                    "closed_positions",
                ],
            )

    def test_run_pipeline_compacts_batch_memory_without_dropping_wallet_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["analysis"]["concurrent_wallets"] = 2
            config["analysis"]["lightweight_batch_cleanup_enabled"] = True

            cleanup_calls: list[dict[str, Any]] = []

            original_cleanup = analysis.cleanup_completed_analysis_batch

            def cleanup_spy(
                cleanup_config: dict[str, Any],
                batch_results: list[dict[str, Any]],
            ) -> dict[str, Any]:
                result = original_cleanup(cleanup_config, batch_results)
                cleanup_calls.append(result)
                return result

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", FakePolymarketClient))
                stack.enter_context(
                    patch.object(
                        analysis,
                        "cleanup_completed_analysis_batch",
                        side_effect=cleanup_spy,
                    )
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True))
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            self.assertTrue(cleanup_calls)
            self.assertEqual(cleanup_calls[-1]["status"], "completed")
            self.assertGreater(cleanup_calls[-1]["released_result_count"], 0)

            wallet_path = output_dir / "wallets" / f"{WALLET}.json"
            wallet_result = json.loads(wallet_path.read_text(encoding="utf-8"))
            self.assertEqual(wallet_result["wallet"], WALLET)
            self.assertTrue(wallet_result["labels"])
            self.assertTrue(wallet_result["label_evaluations"])
            self.assertIn("Weather specialist", wallet_result["selection_record"]["labels"])
            self.assertIn("High win rate", wallet_result["selection_record"]["labels"])
            self.assertIn("finder_ai", wallet_result)
            self.assertIn("structured_materials", wallet_result)
            self.assertTrue(wallet_result["top_trades"])

            summary = json.loads((output_dir / "analysis_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["wallets_selected"], 1)

    def test_run_pipeline_smoke_can_use_graphql_history_provider_without_rest_trades(self) -> None:
        GraphQLHistoryPipelineClient.instances.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["wallet_filter"]["min_traded_count"] = 0
            config["history_provider"] = {
                "enabled": True,
                "always_for_full_snapshot": True,
                "page_size": 50,
                "max_pages_per_stream": 1,
                "token_lookup_chunk_size": 10,
            }

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(analysis, "PolymarketClient", GraphQLHistoryPipelineClient)
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "progress",
                            lambda *_args, **_kwargs: None,
                            create=True,
                        )
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            client = GraphQLHistoryPipelineClient.instances[0]
            self.assertTrue(client.graphql_calls)
            self.assertFalse(any(name == "trades" for name, _kwargs in client.calls))

            wallet_result = json.loads(
                (output_dir / "wallets" / f"{WALLET}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(any(label.get("system_core") for label in wallet_result["labels"]))
            metrics = wallet_result["metrics"]
            self.assertTrue(metrics["snapshot_complete"])
            self.assertEqual(metrics["trade_count"], 3)
            self.assertAlmostEqual(metrics["weather_notional_ratio"], 1.0)
            self.assertEqual(
                metrics["snapshot_collection_status"]["trades"]["collection_mode"],
                "history_provider",
            )
            self.assertTrue(
                metrics["snapshot_collection_status"]["history_provider"]["trades_complete"]
            )

    def test_run_pipeline_recovers_partitioned_activity_and_keeps_finder_ai_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["pagination"] = {"page_size": 1, "max_offset": 10}

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(analysis, "PolymarketClient", PartitionRecoveringPolymarketClient)
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "current_partition_end_epoch",
                        return_value=BASE_TS + 120 * 3600,
                    )
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            wallet_result = json.loads(
                (output_dir / "wallets" / f"{WALLET}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(wallet_result["metrics"]["snapshot_complete"])
            self.assertTrue(wallet_result["operation_audit"]["complete"])
            self.assertTrue(wallet_result["finder_ai"]["briefGeneration"]["enabled"])
            self.assertTrue(wallet_result["finder_ai"]["briefGeneration"]["gate"]["auditComplete"])
            self.assertEqual(
                wallet_result["finder_ai"]["briefGeneration"]["gate"]["reason"],
                "ready_for_brief",
            )
            self.assertFalse(wallet_result["finder_ai"]["needsReview"])
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["activity"]["collection_mode"],
                "partition_recovery",
            )
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["trades"]["collection_mode"],
                "activity_projection",
            )
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["trades"]["stop_reason"],
                "projected_from_activity",
            )

    def test_run_pipeline_recovers_partitioned_activity_after_transport_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["pagination"] = {"page_size": 1, "max_offset": 10}

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(analysis, "PolymarketClient", TransportRecoveringPolymarketClient)
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "current_partition_end_epoch",
                        return_value=BASE_TS + 120 * 3600,
                    )
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            wallet_result = json.loads(
                (output_dir / "wallets" / f"{WALLET}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(wallet_result["metrics"]["snapshot_complete"])
            self.assertTrue(wallet_result["operation_audit"]["complete"])
            self.assertTrue(wallet_result["finder_ai"]["briefGeneration"]["enabled"])
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["activity"]["recovered_from"],
                "terminal_transport_error",
            )
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["trades"]["recovered_from"],
                "terminal_transport_error",
            )
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["trades"]["collection_mode"],
                "activity_projection",
            )

    def test_run_pipeline_recovers_from_initial_activity_transport_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["pagination"] = {"page_size": 1, "max_offset": 10}

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        analysis,
                        "PolymarketClient",
                        InitialTransportRecoveringPolymarketClient,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "current_partition_end_epoch",
                        return_value=BASE_TS + 120 * 3600,
                    )
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            wallet_result = json.loads(
                (output_dir / "wallets" / f"{WALLET}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(wallet_result["metrics"]["snapshot_complete"])
            self.assertTrue(
                wallet_result["metrics"]["snapshot_collection_status"]["activity"]["initial_request_failed"]
            )
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["activity"]["recovered_from"],
                "initial_transport_error",
            )
            self.assertEqual(
                wallet_result["metrics"]["snapshot_collection_status"]["trades"]["collection_mode"],
                "activity_projection",
            )

    def test_build_analysis_summary_tracks_finder_ai_run_stats(self) -> None:
        def wallet_result(
            wallet: str,
            *,
            status: str,
            generated_at: str = "",
            needs_review: bool = False,
            has_conflict: bool = False,
            eligible: bool = True,
        ) -> dict[str, Any]:
            return {
                "wallet": wallet,
                "metrics": {
                    "wallet_win_rate": 0.55,
                    "weather_notional_ratio": 0.5,
                    "closed_position_win_rate": 0.6,
                    "closed_position_sample_win_rate": 0.6,
                    "closed_profit_multiple": 1.8,
                    "trades_per_active_day": 3.5,
                    "leaderboard_pnl": 120.0,
                    "trade_count": 14,
                },
                "labels": [{"display_name": "Weather specialist"}],
                "label_evaluations": [
                    {"key": "high_frequency_region", "matched": wallet.endswith("1")}
                ],
                "leaderboard_entry": {
                    "rank": 1,
                    "userName": f"user-{wallet[-1]}",
                    "xUsername": f"x_user_{wallet[-1]}",
                },
                "selection_record": {"user_name": f"user-{wallet[-1]}"},
                "finder_ai": {
                    "needsReview": needs_review,
                    "hasConflict": has_conflict,
                    "providerMeta": {"generatedAt": generated_at},
                    "briefGeneration": {
                        "status": status,
                        "gate": {"eligible": eligible},
                    },
                },
            }

        summary = analysis.build_analysis_summary(
            leaderboard=[{"wallet": "a"}],
            weather_events=[{"id": "weather-event"}],
            screening_records=[{"wallet": "a"}, {"wallet": "b"}, {"wallet": "c"}, {"wallet": "d"}, {"wallet": "e"}],
            wallet_results=[
                wallet_result(
                    "0x0000000000000000000000000000000000000001",
                    status="generated",
                    generated_at="2026-05-05T09:00:00+00:00",
                ),
                wallet_result(
                    "0x0000000000000000000000000000000000000002",
                    status="cached",
                    generated_at="2026-05-05T10:00:00+00:00",
                    has_conflict=True,
                ),
                wallet_result(
                    "0x0000000000000000000000000000000000000003",
                    status="failed",
                    needs_review=True,
                ),
                wallet_result(
                    "0x0000000000000000000000000000000000000004",
                    status="fallback",
                ),
                wallet_result(
                    "0x0000000000000000000000000000000000000005",
                    status="insufficient",
                    eligible=False,
                ),
            ],
            errors=[],
        )

        self.assertEqual(summary["wallets_selected"], 5)
        self.assertEqual(summary["wallets_core_labeled"], 1)
        self.assertEqual(summary["finder_ai_summary"]["selected_wallets"], 5)
        self.assertEqual(summary["finder_ai_summary"]["finder_ai_present"], 5)
        self.assertEqual(summary["finder_ai_summary"]["eligible"], 4)
        self.assertEqual(summary["finder_ai_summary"]["generated"], 1)
        self.assertEqual(summary["finder_ai_summary"]["cached"], 1)
        self.assertEqual(summary["finder_ai_summary"]["fallback"], 1)
        self.assertEqual(summary["finder_ai_summary"]["failed"], 1)
        self.assertEqual(summary["finder_ai_summary"]["skipped"], 1)
        self.assertEqual(summary["finder_ai_summary"]["needs_review"], 1)
        self.assertEqual(summary["finder_ai_summary"]["has_conflict"], 1)
        self.assertEqual(
            summary["finder_ai_summary"]["latest_generated_at"],
            "2026-05-05T10:00:00+00:00",
        )

    def test_run_pipeline_generates_finder_ai_brief_for_selected_wallet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            captured: dict[str, Any] = {}

            def fake_generate(*, payload: dict[str, Any] | None, wallet_result: dict[str, Any]) -> dict[str, Any]:
                finder_ai = json.loads(json.dumps(payload or {}, ensure_ascii=False))
                captured["normalizedAddress"] = finder_ai.get("normalizedAddress")
                captured["promptVersion"] = finder_ai.get("providerMeta", {}).get("promptVersion")
                captured["cacheKey"] = finder_ai.get("briefGeneration", {}).get("cacheKey")
                captured["statusBefore"] = finder_ai.get("briefGeneration", {}).get("status")
                captured["primarySignalKey"] = (
                    finder_ai.get("layeredInput", {})
                    .get("L2", {})
                    .get("primarySignals", [{}])[0]
                    .get("key")
                )
                finder_ai["aiBriefShort"] = "测试短摘要"
                finder_ai["aiBriefNote"] = "这是一个用于测试的 AI 简报。"
                finder_ai.setdefault("providerMeta", {})["generatedAt"] = "2026-05-05T00:00:00+00:00"
                finder_ai.setdefault("briefGeneration", {})["status"] = "generated"
                finder_ai["briefGeneration"]["reason"] = "generated"
                return finder_ai

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", FakePolymarketClient))
                stack.enter_context(patch.object(analysis, "generate_finder_ai_brief", fake_generate))
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            self.assertEqual(captured["normalizedAddress"], WALLET)
            self.assertEqual(captured["promptVersion"], "finder-weather-brief-v6")
            self.assertEqual(captured["statusBefore"], "ready")
            self.assertEqual(captured["primarySignalKey"], "high_frequency_region")
            self.assertTrue(str(captured["cacheKey"]).startswith(f"{WALLET}|sha256:"))

            wallet_result = json.loads(
                (output_dir / "wallets" / f"{WALLET}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(wallet_result["finder_ai"]["aiBriefShort"], "测试短摘要")
            self.assertEqual(wallet_result["finder_ai"]["aiBriefNote"], "这是一个用于测试的 AI 简报。")
            self.assertTrue(any(label.get("system_core") for label in wallet_result["labels"]))
            self.assertEqual(
                wallet_result["finder_ai"]["providerMeta"]["generatedAt"],
                "2026-05-05T00:00:00+00:00",
            )
            self.assertEqual(wallet_result["finder_ai"]["briefGeneration"]["status"], "generated")
            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )
            self.assertEqual(selected_wallets[0]["ai_brief_short"], "测试短摘要")
            self.assertEqual(selected_wallets[0]["ai_strategy_focus"], wallet_result["finder_ai"]["strategyFocus"])
            self.assertEqual(selected_wallets[0]["ai_generation_status"], "generated")
            self.assertEqual(selected_wallets[0]["ai_generation_reason"], "generated")
            self.assertFalse(selected_wallets[0]["ai_needs_review"])
            self.assertFalse(selected_wallets[0]["ai_has_conflict"])
            self.assertEqual(
                selected_wallets[0]["ai_evidence_level"],
                wallet_result["finder_ai"]["evidenceLevel"],
            )
            analysis_summary = json.loads(
                (output_dir / "analysis_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(analysis_summary["finder_ai_summary"]["selected_wallets"], 1)
            self.assertEqual(analysis_summary["finder_ai_summary"]["generated"], 1)
            self.assertEqual(analysis_summary["finder_ai_summary"]["cached"], 0)
            self.assertEqual(analysis_summary["finder_ai_summary"]["fallback"], 0)
            self.assertEqual(analysis_summary["finder_ai_summary"]["failed"], 0)
            self.assertEqual(analysis_summary["finder_ai_summary"]["skipped"], 0)
            self.assertEqual(
                analysis_summary["finder_ai_summary"]["latest_generated_at"],
                "2026-05-05T00:00:00+00:00",
            )
            self.assertIn("falcon_display", analysis_summary)
            self.assertIn("falcon_win_rate", analysis_summary["averages"])
            self.assertIn("falcon_total_roi", analysis_summary["averages"])

    def test_run_pipeline_does_not_generate_finder_ai_for_selected_wallet_without_system_core_label(self) -> None:
        class NonCorePipelineClient(FakePolymarketClient):
            def fetch_events_keyset_page(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(("events_keyset", kwargs))
                return {"events": [], "next_cursor": None}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["wallet_filter"]["min_weather_trade_ratio"] = 0
            config["leaderboard"]["auto_extend_to_target"] = False

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", NonCorePipelineClient))
                generate_mock = stack.enter_context(
                    patch.object(
                        analysis,
                        "generate_finder_ai_brief",
                        side_effect=AssertionError("DeepSeek should not be called"),
                    )
                )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            wallet_result = json.loads(
                (output_dir / "wallets" / f"{WALLET}.json").read_text(encoding="utf-8")
            )
            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )
            analysis_summary = json.loads(
                (output_dir / "analysis_summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["selected_wallet_count"], 1)
        self.assertFalse(generate_mock.called)
        self.assertTrue(wallet_result["screening"]["selected"])
        self.assertFalse(any(label.get("system_core") for label in wallet_result["labels"]))
        self.assertNotEqual(selected_wallets[0].get("ai_generation_status"), "generated")
        self.assertEqual(analysis_summary["finder_ai_summary"]["selected_wallets"], 1)
        self.assertEqual(analysis_summary["finder_ai_summary"]["generated"], 0)
        self.assertEqual(analysis_summary["finder_ai_summary"]["skipped"], 1)

    def test_run_pipeline_only_fetches_falcon_metrics_for_selected_wallets(self) -> None:
        class MixedSelectionClient(FakePolymarketClient):
            instances: list["MixedSelectionClient"] = []

            def __init__(self, api_config: dict[str, Any]) -> None:
                super().__init__(api_config)
                type(self).instances.append(self)

            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                return [
                    {
                        "rank": 1,
                        "proxyWallet": "0x1110000000000000000000000000000000000000",
                        "userName": "selected-wallet",
                        "xUsername": "selected_wallet",
                        "pnl": "250.50",
                        "vol": "2400",
                    },
                    {
                        "rank": 2,
                        "proxyWallet": "0x2220000000000000000000000000000000000000",
                        "userName": "screened-wallet",
                        "xUsername": "screened_wallet",
                        "pnl": "230.00",
                        "vol": "2200",
                    },
                ]

            def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("trades", kwargs))
                wallet = str(kwargs.get("user") or "")
                if wallet == "0x2220000000000000000000000000000000000000":
                    return [
                        {
                            "asset": "non-weather",
                            "side": "BUY",
                            "title": "Sports market",
                            "outcome": "Yes",
                            "eventId": "sports-event",
                            "eventSlug": "sports-event",
                            "conditionId": "sports-condition",
                            "slug": "sports-market",
                            "timestamp": BASE_TS,
                            "size": "100",
                            "price": "0.40",
                            "usdcSize": "40",
                        }
                    ]
                return super().fetch_trades_page(**kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["wallet_filter"]["target_count"] = 1
            falcon_calls: list[str] = []

            def fake_falcon(wallet: str, **_kwargs: Any) -> dict[str, Any]:
                falcon_calls.append(wallet)
                return {
                    "wallet": wallet,
                    "total_pnl": 321.0,
                    "total_roi": 0.42,
                    "win_rate": 0.6,
                    "win_rate_source": "falcon_wallet_360",
                    "win_rate_window_label": "Falcon 15d",
                    "metric_source": "falcon",
                }

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", MixedSelectionClient))
                stack.enter_context(
                    patch.object(analysis, "falcon_display_metrics_for_wallet", side_effect=fake_falcon)
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            self.assertEqual(falcon_calls, ["0x1110000000000000000000000000000000000000"])
            wallet_result = json.loads(
                (output_dir / "wallets" / "0x1110000000000000000000000000000000000000.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(wallet_result["selection_record"]["display_pnl"], 321.0)
            self.assertEqual(wallet_result["metrics"]["falcon_total_pnl"], 321.0)
            self.assertIn("structured_materials", wallet_result)
            self.assertIn("finder_ai", wallet_result)

    def test_run_pipeline_keeps_selected_wallet_order_stable_when_finder_ai_finishes_out_of_order(self) -> None:
        wallet_fast = "0x1110000000000000000000000000000000000000"
        wallet_slow = "0x2220000000000000000000000000000000000000"
        releases: dict[str, threading.Event] = {
            wallet_fast: threading.Event(),
            wallet_slow: threading.Event(),
        }

        class MultiWalletClient(FakePolymarketClient):
            instances: list["MultiWalletClient"] = []

            def __init__(self, api_config: dict[str, Any]) -> None:
                super().__init__(api_config)
                type(self).instances.append(self)

            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                return [
                    {
                        "rank": 1,
                        "proxyWallet": wallet_fast,
                        "userName": "first-wallet",
                        "xUsername": "first_wallet",
                        "pnl": "250.50",
                        "vol": "2400",
                    },
                    {
                        "rank": 2,
                        "proxyWallet": wallet_slow,
                        "userName": "second-wallet",
                        "xUsername": "second_wallet",
                        "pnl": "230.00",
                        "vol": "2200",
                    },
                ]

        def fake_generate(*, payload: dict[str, Any] | None, wallet_result: dict[str, Any]) -> dict[str, Any]:
            finder_ai = json.loads(json.dumps(payload or {}, ensure_ascii=False))
            wallet = str(wallet_result.get("wallet") or "")
            if wallet == wallet_fast:
                releases[wallet_slow].wait(timeout=1)
            finder_ai["aiBriefShort"] = f"brief-{wallet[-4:]}"
            finder_ai["aiBriefNote"] = f"note-{wallet[-4:]}"
            finder_ai.setdefault("providerMeta", {})["generatedAt"] = "2026-05-05T00:00:00+00:00"
            finder_ai.setdefault("briefGeneration", {})["status"] = "generated"
            finder_ai["briefGeneration"]["reason"] = "generated"
            releases[wallet].set()
            return finder_ai

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["analysis"]["concurrent_wallets"] = 2
            config["analysis"]["finder_ai_concurrency"] = 2
            config["wallet_filter"]["target_count"] = 2

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", MultiWalletClient))
                stack.enter_context(patch.object(analysis, "generate_finder_ai_brief", fake_generate))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertEqual(
            [row["wallet"] for row in selected_wallets],
            [wallet_fast, wallet_slow],
        )

    def test_run_pipeline_flushes_selected_wallets_json_once_per_batch(self) -> None:
        wallet_fast = "0x1110000000000000000000000000000000000000"
        wallet_slow = "0x2220000000000000000000000000000000000000"
        releases: dict[str, threading.Event] = {
            wallet_fast: threading.Event(),
            wallet_slow: threading.Event(),
        }

        class MultiWalletClient(FakePolymarketClient):
            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                return [
                    {
                        "rank": 1,
                        "proxyWallet": wallet_fast,
                        "userName": "first-wallet",
                        "xUsername": "first_wallet",
                        "pnl": "250.50",
                        "vol": "2400",
                    },
                    {
                        "rank": 2,
                        "proxyWallet": wallet_slow,
                        "userName": "second-wallet",
                        "xUsername": "second_wallet",
                        "pnl": "230.00",
                        "vol": "2200",
                    },
                ]

        def fake_generate(*, payload: dict[str, Any] | None, wallet_result: dict[str, Any]) -> dict[str, Any]:
            finder_ai = json.loads(json.dumps(payload or {}, ensure_ascii=False))
            wallet = str(wallet_result.get("wallet") or "")
            if wallet == wallet_fast:
                releases[wallet_slow].wait(timeout=1)
            finder_ai["aiBriefShort"] = f"brief-{wallet[-4:]}"
            finder_ai["aiBriefNote"] = f"note-{wallet[-4:]}"
            finder_ai.setdefault("providerMeta", {})["generatedAt"] = "2026-05-05T00:00:00+00:00"
            finder_ai.setdefault("briefGeneration", {})["status"] = "generated"
            finder_ai["briefGeneration"]["reason"] = "generated"
            releases[wallet].set()
            return finder_ai

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["analysis"]["concurrent_wallets"] = 2
            config["analysis"]["finder_ai_concurrency"] = 2
            config["wallet_filter"]["target_count"] = 2

            original_write_json = analysis.write_json
            selected_wallets_write_payloads: list[list[dict[str, Any]]] = []

            def write_json_spy(path: Path, data: Any) -> None:
                if path.name == "selected_wallets.json":
                    selected_wallets_write_payloads.append(json.loads(json.dumps(data, ensure_ascii=False)))
                original_write_json(path, data)

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", MultiWalletClient))
                stack.enter_context(patch.object(analysis, "generate_finder_ai_brief", fake_generate))
                stack.enter_context(patch.object(analysis, "write_json", side_effect=write_json_spy))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertGreaterEqual(len(selected_wallets_write_payloads), 2)
        self.assertEqual(selected_wallets_write_payloads[0], [])
        self.assertFalse(
            any(len(payload) == 1 for payload in selected_wallets_write_payloads[1:]),
        )
        self.assertEqual(
            [row["wallet"] for row in selected_wallets_write_payloads[1]],
            [wallet_fast, wallet_slow],
        )
        self.assertEqual(
            [row["wallet"] for row in selected_wallets_write_payloads[-1]],
            [wallet_fast, wallet_slow],
        )

    def test_run_pipeline_processes_next_screening_batch_while_full_hydration_is_pending(self) -> None:
        wallet_first = "0x1110000000000000000000000000000000000000"
        wallet_second = "0x2220000000000000000000000000000000000000"
        second_batch_started = threading.Event()
        hydration_saw_second_batch: list[bool] = []
        analyzed_wallets: list[str] = []
        completion_wallets: list[str] = []

        class TwoWalletClient(FakePolymarketClient):
            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                return [
                    {
                        "rank": 1,
                        "proxyWallet": wallet_first,
                        "userName": "first-wallet",
                        "xUsername": "first_wallet",
                        "pnl": "250.50",
                        "vol": "2400",
                    },
                    {
                        "rank": 2,
                        "proxyWallet": wallet_second,
                        "userName": "second-wallet",
                        "xUsername": "second_wallet",
                        "pnl": "230.00",
                        "vol": "2200",
                    },
                ]

        def deferred_wallet_result(entry: dict[str, Any]) -> dict[str, Any]:
            wallet = analysis.normalize_address(entry["proxyWallet"])
            return {
                "wallet": wallet,
                "leaderboard_entry": dict(entry),
                "screening": {
                    "wallet": wallet,
                    "selected": True,
                    "trade_count": 1,
                    "weather_trade_count": 1,
                    "reasons": [],
                },
                "selection_record": {"wallet": wallet, "selected": True},
                "labels": [{"key": "high_frequency_region", "system_core": True}],
                "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
                "metrics": {"trade_count": 1},
                "deep_hydration": {
                    "status": "deferred",
                    "reason": "scheduled_after_screening_batch",
                },
            }

        def fake_iter_analyze_wallet_batch_results(**kwargs: Any) -> Iterator[tuple[int, dict[str, Any]]]:
            self.assertTrue(kwargs["defer_full_hydration"])
            entry = kwargs["leaderboard_entries"][0]
            wallet = analysis.normalize_address(entry["proxyWallet"])
            analyzed_wallets.append(wallet)
            if wallet == wallet_second:
                second_batch_started.set()
            yield 0, {
                "wallet": wallet,
                "wallet_result": deferred_wallet_result(entry),
                "snapshot": {
                    "wallet": wallet,
                    "activity": [],
                    "trades": [],
                    "rewards": [],
                    "positions": [],
                    "closed_positions": [],
                    "collection_status": {},
                    "snapshot_scope": "screening",
                },
                "leaderboard_entry": dict(entry),
            }

        def fake_complete_deferred_selected_wallet_result(**kwargs: Any) -> dict[str, Any]:
            wallet_result = json.loads(json.dumps(kwargs["wallet_result"], ensure_ascii=False))
            wallet = wallet_result["wallet"]
            completion_wallets.append(wallet)
            if wallet == wallet_first:
                hydration_saw_second_batch.append(second_batch_started.wait(timeout=1))
            wallet_result["deep_hydration"] = {"status": "completed", "snapshot_scope": "full"}
            wallet_result["finder_ai"] = {
                "aiBriefShort": f"brief-{wallet[-4:]}",
                "briefGeneration": {"status": "generated", "reason": "generated"},
            }
            wallet_result["selection_record"]["has_core_label"] = True
            wallet_result["selection_record"]["core_label_keys"] = ["high_frequency_region"]
            snapshot = dict(kwargs["snapshot"])
            snapshot["snapshot_scope"] = "full"
            return {"wallet_result": wallet_result, "snapshot": snapshot}

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = small_config(temp_path / "cache")
            config["leaderboard"]["fetch_limit"] = 2
            config["leaderboard"]["page_size"] = 2
            config["wallet_filter"]["target_count"] = 2
            config["analysis"]["concurrent_wallets"] = 1
            config["analysis"]["full_hydration_concurrency"] = 1
            config["analysis"]["defer_selected_wallet_full_hydration"] = True

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", TwoWalletClient))
                stack.enter_context(
                    patch.object(
                        analysis,
                        "iter_analyze_wallet_batch_results",
                        side_effect=fake_iter_analyze_wallet_batch_results,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "complete_deferred_selected_wallet_result",
                        side_effect=fake_complete_deferred_selected_wallet_result,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "build_analysis_summary",
                        return_value={"wallets_selected": 2},
                    )
                )
                stack.enter_context(patch.object(analysis, "build_report", return_value="report"))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertEqual(analyzed_wallets, [wallet_first, wallet_second])
        self.assertEqual(completion_wallets, [wallet_first, wallet_second])
        self.assertEqual(hydration_saw_second_batch, [True])
        self.assertEqual([row["wallet"] for row in selected_wallets], [wallet_first, wallet_second])

    def test_run_pipeline_starts_completed_batch_hydration_before_slow_peer_finishes(self) -> None:
        wallet_slow = "0x1110000000000000000000000000000000000000"
        wallet_fast = "0x2220000000000000000000000000000000000000"
        fast_hydration_started = threading.Event()
        slow_saw_fast_hydration: list[bool] = []
        analyzed_wallets: list[str] = []
        completion_wallets: list[str] = []

        class TwoWalletClient(FakePolymarketClient):
            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                return [
                    {
                        "rank": 1,
                        "proxyWallet": wallet_slow,
                        "userName": "slow-wallet",
                        "xUsername": "slow_wallet",
                        "pnl": "250.50",
                        "vol": "2400",
                    },
                    {
                        "rank": 2,
                        "proxyWallet": wallet_fast,
                        "userName": "fast-wallet",
                        "xUsername": "fast_wallet",
                        "pnl": "230.00",
                        "vol": "2200",
                    },
                ]

        def deferred_wallet_result(entry: dict[str, Any]) -> dict[str, Any]:
            wallet = analysis.normalize_address(entry["proxyWallet"])
            return {
                "wallet": wallet,
                "leaderboard_entry": dict(entry),
                "screening": {
                    "wallet": wallet,
                    "selected": True,
                    "trade_count": 1,
                    "weather_trade_count": 1,
                    "reasons": [],
                },
                "selection_record": {"wallet": wallet, "selected": True},
                "labels": [{"key": "high_frequency_region", "system_core": True}],
                "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
                "metrics": {"trade_count": 1},
                "deep_hydration": {
                    "status": "deferred",
                    "reason": "scheduled_after_screening_batch",
                },
            }

        def fake_analyze_leaderboard_entry(**kwargs: Any) -> dict[str, Any]:
            entry = kwargs["leaderboard_entry"]
            wallet = analysis.normalize_address(entry["proxyWallet"])
            analyzed_wallets.append(wallet)
            if wallet == wallet_slow:
                slow_saw_fast_hydration.append(fast_hydration_started.wait(timeout=1))
            return {
                "wallet": wallet,
                "wallet_result": deferred_wallet_result(entry),
                "snapshot": {
                    "wallet": wallet,
                    "activity": [],
                    "trades": [],
                    "rewards": [],
                    "positions": [],
                    "closed_positions": [],
                    "collection_status": {},
                    "snapshot_scope": "screening",
                },
                "leaderboard_entry": dict(entry),
            }

        def fake_complete_deferred_selected_wallet_result(**kwargs: Any) -> dict[str, Any]:
            wallet_result = json.loads(json.dumps(kwargs["wallet_result"], ensure_ascii=False))
            wallet = wallet_result["wallet"]
            completion_wallets.append(wallet)
            if wallet == wallet_fast:
                fast_hydration_started.set()
            wallet_result["deep_hydration"] = {"status": "completed", "snapshot_scope": "full"}
            wallet_result["selection_record"]["has_core_label"] = True
            wallet_result["selection_record"]["core_label_keys"] = ["high_frequency_region"]
            snapshot = dict(kwargs["snapshot"])
            snapshot["snapshot_scope"] = "full"
            return {
                "wallet_result": wallet_result,
                "snapshot": snapshot,
                "finder_ai_pending": False,
                "finalized": True,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = small_config(temp_path / "cache")
            config["leaderboard"]["fetch_limit"] = 2
            config["leaderboard"]["page_size"] = 2
            config["wallet_filter"]["target_count"] = 2
            config["analysis"]["concurrent_wallets"] = 2
            config["analysis"]["full_hydration_concurrency"] = 2
            config["analysis"]["defer_selected_wallet_full_hydration"] = True

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", TwoWalletClient))
                stack.enter_context(
                    patch.object(
                        analysis,
                        "analyze_leaderboard_entry",
                        side_effect=fake_analyze_leaderboard_entry,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "complete_deferred_selected_wallet_result",
                        side_effect=fake_complete_deferred_selected_wallet_result,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "build_analysis_summary",
                        return_value={"wallets_selected": 2},
                    )
                )
                stack.enter_context(patch.object(analysis, "build_report", return_value="report"))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertIn(wallet_fast, completion_wallets)
        self.assertEqual(slow_saw_fast_hydration, [True])
        self.assertCountEqual(analyzed_wallets, [wallet_slow, wallet_fast])
        self.assertEqual([row["wallet"] for row in selected_wallets], [wallet_slow, wallet_fast])

    def test_run_pipeline_lookahead_keeps_screening_workers_filled_without_extra_concurrency(self) -> None:
        wallets = [
            "0x1110000000000000000000000000000000000000",
            "0x2220000000000000000000000000000000000000",
            "0x3330000000000000000000000000000000000000",
            "0x4440000000000000000000000000000000000000",
        ]
        slow_started = threading.Event()
        lookahead_started = threading.Event()
        active_count = 0
        max_active_count = 0
        active_lock = threading.Lock()
        started_wallets: list[str] = []
        history_writes: list[tuple[str, str]] = []

        class LookaheadClient(FakePolymarketClient):
            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                return [
                    {
                        "rank": index + 1,
                        "proxyWallet": wallet,
                        "userName": f"wallet-{index}",
                        "xUsername": f"wallet_{index}",
                        "pnl": "250.50",
                        "vol": "2400",
                    }
                    for index, wallet in enumerate(wallets)
                ]

        def selected_result(entry: dict[str, Any], selected: bool) -> dict[str, Any]:
            wallet = analysis.normalize_address(entry["proxyWallet"])
            return {
                "wallet": wallet,
                "leaderboard_entry": dict(entry),
                "screening": {
                    "wallet": wallet,
                    "selected": selected,
                    "trade_count": 1,
                    "weather_trade_count": 1 if selected else 0,
                    "reasons": [],
                },
                "selection_record": {"wallet": wallet, "selected": selected},
                "labels": [{"key": "high_frequency_region", "system_core": True}] if selected else [],
                "label_evaluations": [{"key": "high_frequency_region", "matched": True}] if selected else [],
                "metrics": {"trade_count": 1},
                "deep_hydration": {"status": "skipped", "reason": "unit_test"},
            }

        def fake_analyze_leaderboard_entry(**kwargs: Any) -> dict[str, Any]:
            nonlocal active_count, max_active_count
            entry = kwargs["leaderboard_entry"]
            wallet = analysis.normalize_address(entry["proxyWallet"])
            with active_lock:
                active_count += 1
                max_active_count = max(max_active_count, active_count)
                started_wallets.append(wallet)
            try:
                if wallet == wallets[0]:
                    slow_started.set()
                    self.assertTrue(lookahead_started.wait(timeout=1))
                if wallet == wallets[2]:
                    lookahead_started.set()
                return {
                    "wallet": wallet,
                    "wallet_result": selected_result(entry, wallet in {wallets[0], wallets[1]}),
                    "snapshot": {
                        "wallet": wallet,
                        "activity": [],
                        "trades": [],
                        "rewards": [],
                        "positions": [],
                        "closed_positions": [],
                        "collection_status": {},
                        "snapshot_scope": "screening",
                    },
                    "leaderboard_entry": dict(entry),
                    "history_record_status": "selected_pending" if wallet in {wallets[0], wallets[1]} else "screened_out",
                }
            finally:
                with active_lock:
                    active_count -= 1

        def fake_write_wallet_history_record(**kwargs: Any) -> None:
            history_writes.append((kwargs["wallet"], kwargs["status"]))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = small_config(temp_path / "cache")
            config["leaderboard"]["fetch_limit"] = 4
            config["leaderboard"]["page_size"] = 4
            config["wallet_filter"]["target_count"] = 2
            config["analysis"]["concurrent_wallets"] = 2
            config["analysis"]["wallet_screening_lookahead_multiplier"] = 2

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", LookaheadClient))
                stack.enter_context(
                    patch.object(
                        analysis,
                        "analyze_leaderboard_entry",
                        side_effect=fake_analyze_leaderboard_entry,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "write_wallet_history_record",
                        side_effect=fake_write_wallet_history_record,
                    )
                )
                stack.enter_context(
                    patch.object(
                        analysis,
                        "build_analysis_summary",
                        return_value={"wallets_selected": 2},
                    )
                )
                stack.enter_context(patch.object(analysis, "build_report", return_value="report"))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertTrue(slow_started.is_set())
        self.assertTrue(lookahead_started.is_set())
        self.assertIn(wallets[2], started_wallets)
        self.assertLessEqual(max_active_count, 2)
        self.assertEqual([row["wallet"] for row in selected_wallets], wallets[:2])
        self.assertEqual({wallet for wallet, _status in history_writes}, set(wallets[:2]))
        self.assertEqual(
            {status for _wallet, status in history_writes},
            {"selected_pending", "selected"},
        )

    def test_deferred_completion_applies_deepseek_gate_after_hydration_relabel(self) -> None:
        wallet = "0xabc1230000000000000000000000000000000000"
        lightweight_wallet_result = {
            "wallet": wallet,
            "screening": {"wallet": wallet, "selected": True, "reasons": []},
            "selection_record": {"wallet": wallet, "selected": True},
            "labels": [{"key": "high_frequency_region", "system_core": True}],
            "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
            "metrics": {"trade_count": 1},
        }
        hydrated_wallet_result = {
            "wallet": wallet,
            "screening": {"wallet": wallet, "selected": True, "reasons": []},
            "selection_record": {"wallet": wallet, "selected": True},
            "labels": [],
            "label_evaluations": [],
            "metrics": {"trade_count": 1},
            "deep_hydration": {"status": "completed", "snapshot_scope": "full"},
        }
        snapshot = {"wallet": wallet, "snapshot_scope": "screening"}
        full_snapshot = {"wallet": wallet, "snapshot_scope": "full"}

        def identity_enrich(**kwargs: Any) -> dict[str, Any]:
            return kwargs["wallet_result"]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    analysis,
                    "hydrate_selected_wallet_result_full_history",
                    return_value=(hydrated_wallet_result, full_snapshot),
                )
            )
            stack.enter_context(patch.object(analysis, "enrich_wallet_result_artifacts", side_effect=identity_enrich))
            stack.enter_context(
                patch.object(
                    analysis,
                    "generate_finder_ai_for_wallet_result",
                    side_effect=AssertionError("DeepSeek should use post-hydration core gate"),
                )
            )
            with analysis.ThreadPoolExecutor(max_workers=1) as ai_executor:
                result = analysis.complete_deferred_selected_wallet_result(
                    client=object(),  # type: ignore[arg-type]
                    leaderboard_entry={"proxyWallet": wallet},
                    wallet_result=lightweight_wallet_result,
                    snapshot=snapshot,
                    weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
                    config=small_config(Path("cache")),
                    ai_executor=ai_executor,
                )

        final_wallet_result = result["wallet_result"]
        self.assertFalse(result["finder_ai_pending"])
        self.assertFalse(result["finalized"])
        self.assertFalse(final_wallet_result["selection_record"]["has_core_label"])
        self.assertEqual(final_wallet_result["selection_record"]["core_label_keys"], [])
        self.assertNotIn("finder_ai", final_wallet_result)

    def test_selected_wallet_prefetches_falcon_during_deferred_hydration(self) -> None:
        wallet = "0xabc1230000000000000000000000000000000000"
        lightweight_wallet_result = {
            "wallet": wallet,
            "screening": {"wallet": wallet, "selected": True, "reasons": []},
            "selection_record": {"wallet": wallet, "selected": True},
            "labels": [{"key": "high_frequency_region", "system_core": True}],
            "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
            "metrics": {"trade_count": 1},
            "deep_hydration": {
                "status": "deferred",
                "reason": "scheduled_after_screening_batch",
            },
        }
        hydrated_wallet_result = {
            "wallet": wallet,
            "screening": {"wallet": wallet, "selected": True, "reasons": []},
            "selection_record": {"wallet": wallet, "selected": True},
            "labels": [{"key": "high_frequency_region", "system_core": True}],
            "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
            "metrics": {"trade_count": 1},
            "deep_hydration": {"status": "completed", "snapshot_scope": "full"},
        }
        snapshot = {
            "wallet": wallet,
            "activity": [],
            "trades": [],
            "rewards": [],
            "positions": [],
            "closed_positions": [],
            "collection_status": {},
            "snapshot_scope": "screening",
        }
        full_snapshot = dict(snapshot)
        full_snapshot["snapshot_scope"] = "full"
        falcon_started = threading.Event()
        hydration_started = threading.Event()
        hydration_saw_falcon_started: list[bool] = []

        def fake_fetch_falcon(wallet_arg: str, _config: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(wallet_arg, wallet)
            falcon_started.set()
            hydration_started.wait(timeout=1)
            return {
                "wallet": wallet,
                "total_pnl": 321.0,
                "total_roi": 0.42,
                "win_rate": 0.6,
                "win_rate_source": "falcon_wallet_360",
                "win_rate_window_label": "Falcon 15d",
                "metric_source": "falcon",
            }

        def fake_hydrate(**_kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            hydration_started.set()
            hydration_saw_falcon_started.append(falcon_started.wait(timeout=1))
            return hydrated_wallet_result, full_snapshot

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    analysis,
                    "fetch_falcon_metrics_for_selected_wallet",
                    side_effect=fake_fetch_falcon,
                )
            )
            stack.enter_context(
                patch.object(
                    analysis,
                    "falcon_display_metrics_for_wallet",
                    side_effect=AssertionError("Falcon metrics should be reused from the prefetch future"),
                )
            )
            stack.enter_context(
                patch.object(
                    analysis,
                    "hydrate_selected_wallet_result_full_history",
                    side_effect=fake_hydrate,
                )
            )
            with analysis.ThreadPoolExecutor(max_workers=1) as falcon_executor:
                with analysis.ThreadPoolExecutor(max_workers=1) as completion_executor:
                    with analysis.ThreadPoolExecutor(max_workers=1) as ai_executor:
                        pending = analysis.start_selected_wallet_pending_result(
                            client=object(),  # type: ignore[arg-type]
                            batch_index=0,
                            batch_result={
                                "wallet": wallet,
                                "wallet_result": lightweight_wallet_result,
                                "snapshot": snapshot,
                                "leaderboard_entry": {"proxyWallet": wallet},
                            },
                            selected_completion_executor=completion_executor,
                            falcon_executor=falcon_executor,
                            ai_executor=ai_executor,
                            weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
                            config=small_config(Path("cache")),
                        )
                        self.assertIsNotNone(pending.completion_future)
                        completed = pending.completion_future.result(timeout=2)

        final_wallet_result = completed["wallet_result"]
        key_metrics = {
            str(item.get("key")): item.get("value")
            for item in final_wallet_result["finder_ai"]["keyMetrics"]
            if isinstance(item, dict)
        }
        self.assertEqual(hydration_saw_falcon_started, [True])
        self.assertEqual(final_wallet_result["selection_record"]["display_pnl"], 321.0)
        self.assertEqual(final_wallet_result["metrics"]["falcon_total_pnl"], 321.0)
        self.assertEqual(key_metrics["pnl"], 321.0)

    def test_flush_schedules_deferred_deepseek_without_blocking_hydration_result(self) -> None:
        wallet = "0xabc1230000000000000000000000000000000000"
        wallet_result = {
            "wallet": wallet,
            "screening": {"wallet": wallet, "selected": True, "reasons": []},
            "selection_record": {"wallet": wallet, "selected": True},
            "labels": [{"key": "high_frequency_region", "system_core": True}],
            "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
            "metrics": {"trade_count": 1},
        }
        snapshot = {
            "wallet": wallet,
            "activity": [],
            "trades": [],
            "rewards": [],
            "positions": [],
            "closed_positions": [],
            "collection_status": {},
            "snapshot_scope": "full",
        }
        completion_future: analysis.Future[dict[str, Any]] = analysis.Future()
        completion_future.set_result(
            {
                "wallet_result": wallet_result,
                "snapshot": snapshot,
                "finder_ai_pending": True,
                "finalized": False,
            }
        )
        ai_future: analysis.Future[dict[str, Any]] = analysis.Future()

        class FakeAiExecutor:
            submitted = False

            def submit(self, *args: Any, **kwargs: Any) -> analysis.Future[dict[str, Any]]:
                self.submitted = True
                return ai_future

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            wallets_dir = output_dir / "wallets"
            wallets_dir.mkdir()
            selected_wallets: list[dict[str, Any]] = []
            wallet_results: list[dict[str, Any]] = []
            pending = [
                analysis.PendingSelectedWalletResult(
                    sequence=0,
                    wallet=wallet,
                    wallet_result=wallet_result,
                    snapshot=snapshot,
                    completion_future=completion_future,
                )
            ]
            config = small_config(output_dir / "cache")
            config["runtime"] = {"progress_log_path": str(output_dir / "progress.log")}
            fake_executor = FakeAiExecutor()

            flushed = analysis.flush_pending_selected_wallet_results(
                pending_wallets=pending,
                selected_wallets=selected_wallets,
                wallet_results=wallet_results,
                wallets_dir=wallets_dir,
                output_dir=output_dir,
                config=config,
                weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
                target_count=1,
                ai_executor=fake_executor,  # type: ignore[arg-type]
            )

            self.assertFalse(flushed)
            self.assertTrue(fake_executor.submitted)
            self.assertEqual(len(pending), 1)
            self.assertFalse(selected_wallets)

            ai_future.set_result(
                {
                    "aiBriefShort": "brief",
                    "briefGeneration": {"status": "generated", "reason": "generated"},
                }
            )
            flushed = analysis.flush_pending_selected_wallet_results(
                pending_wallets=pending,
                selected_wallets=selected_wallets,
                wallet_results=wallet_results,
                wallets_dir=wallets_dir,
                output_dir=output_dir,
                config=config,
                weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
                target_count=1,
                ai_executor=fake_executor,  # type: ignore[arg-type]
            )

            detail = json.loads((wallets_dir / f"{wallet}.json").read_text(encoding="utf-8"))

        self.assertTrue(flushed)
        self.assertEqual(len(selected_wallets), 1)
        self.assertEqual(detail["finder_ai"]["aiBriefShort"], "brief")

    def test_waiting_flush_schedules_deepseek_for_completed_hydration_out_of_order(self) -> None:
        wallet_slow = "0x1110000000000000000000000000000000000000"
        wallet_fast = "0x2220000000000000000000000000000000000000"

        def wallet_result(wallet: str) -> dict[str, Any]:
            return {
                "wallet": wallet,
                "screening": {"wallet": wallet, "selected": True, "reasons": []},
                "selection_record": {"wallet": wallet, "selected": True},
                "labels": [{"key": "high_frequency_region", "system_core": True}],
                "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
                "metrics": {"trade_count": 1},
            }

        def snapshot(wallet: str) -> dict[str, Any]:
            return {
                "wallet": wallet,
                "activity": [],
                "trades": [],
                "rewards": [],
                "positions": [],
                "closed_positions": [],
                "collection_status": {},
                "snapshot_scope": "full",
            }

        completion_slow: analysis.Future[dict[str, Any]] = analysis.Future()
        completion_fast: analysis.Future[dict[str, Any]] = analysis.Future()
        ai_futures: dict[str, analysis.Future[dict[str, Any]]] = {}
        fast_ai_submitted = threading.Event()
        slow_ai_submitted = threading.Event()

        class FakeAiExecutor:
            def submit(self, *args: Any, **kwargs: Any) -> analysis.Future[dict[str, Any]]:
                submitted_wallet_result = args[1]
                wallet = str(submitted_wallet_result["wallet"])
                future: analysis.Future[dict[str, Any]] = analysis.Future()
                ai_futures[wallet] = future
                if wallet == wallet_fast:
                    fast_ai_submitted.set()
                if wallet == wallet_slow:
                    slow_ai_submitted.set()
                return future

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            wallets_dir = output_dir / "wallets"
            wallets_dir.mkdir()
            selected_wallets: list[dict[str, Any]] = []
            wallet_results: list[dict[str, Any]] = []
            pending = [
                analysis.PendingSelectedWalletResult(
                    sequence=0,
                    wallet=wallet_slow,
                    wallet_result=wallet_result(wallet_slow),
                    snapshot=snapshot(wallet_slow),
                    completion_future=completion_slow,
                ),
                analysis.PendingSelectedWalletResult(
                    sequence=1,
                    wallet=wallet_fast,
                    wallet_result=wallet_result(wallet_fast),
                    snapshot=snapshot(wallet_fast),
                    completion_future=completion_fast,
                ),
            ]
            config = small_config(output_dir / "cache")
            config["runtime"] = {"progress_log_path": str(output_dir / "progress.log")}
            result: dict[str, Any] = {}
            done = threading.Event()

            def run_flush() -> None:
                try:
                    result["flushed"] = analysis.flush_pending_selected_wallet_results(
                        pending_wallets=pending,
                        selected_wallets=selected_wallets,
                        wallet_results=wallet_results,
                        wallets_dir=wallets_dir,
                        output_dir=output_dir,
                        config=config,
                        weather_index=analysis.WeatherIndex(set(), set(), set(), set(), {}),
                        target_count=2,
                        ai_executor=FakeAiExecutor(),  # type: ignore[arg-type]
                        wait=True,
                    )
                except BaseException as exc:  # pragma: no cover - surfaced by assertion below
                    result["error"] = exc
                finally:
                    done.set()

            thread = threading.Thread(target=run_flush)
            thread.start()
            completion_fast.set_result(
                {
                    "wallet_result": wallet_result(wallet_fast),
                    "snapshot": snapshot(wallet_fast),
                    "finder_ai_pending": True,
                    "finalized": False,
                }
            )

            self.assertTrue(fast_ai_submitted.wait(timeout=1))
            self.assertFalse(slow_ai_submitted.is_set())
            self.assertFalse(done.is_set())

            completion_slow.set_result(
                {
                    "wallet_result": wallet_result(wallet_slow),
                    "snapshot": snapshot(wallet_slow),
                    "finder_ai_pending": True,
                    "finalized": False,
                }
            )
            self.assertTrue(slow_ai_submitted.wait(timeout=1))
            ai_futures[wallet_fast].set_result(
                {
                    "aiBriefShort": "brief-fast",
                    "briefGeneration": {"status": "generated", "reason": "generated"},
                }
            )
            ai_futures[wallet_slow].set_result(
                {
                    "aiBriefShort": "brief-slow",
                    "briefGeneration": {"status": "generated", "reason": "generated"},
                }
            )
            self.assertTrue(done.wait(timeout=1))
            thread.join(timeout=1)

        self.assertNotIn("error", result)
        self.assertTrue(result["flushed"])
        self.assertEqual([row["wallet"] for row in selected_wallets], [wallet_slow, wallet_fast])

    def test_run_pipeline_reuses_recent_activity_screening_page_for_full_hydration(self) -> None:
        class RecentActivityReuseClient(FakePolymarketClient):
            instances: list["RecentActivityReuseClient"] = []

            def __init__(self, api_config: dict[str, Any]) -> None:
                super().__init__(api_config)
                type(self).instances.append(self)

            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("activity", kwargs))
                if kwargs.get("offset") == 0 and kwargs.get("start") is None and kwargs.get("end") is None:
                    return [
                        {
                            "type": "TRADE",
                            "asset": "rain-yes",
                            "side": "BUY",
                            "title": "NYC rain",
                            "outcome": "Yes",
                            "eventId": "weather-event-1",
                            "eventSlug": "rain-in-nyc",
                            "conditionId": "cond-weather-yes",
                            "slug": "rain-in-nyc-yes",
                            "timestamp": BASE_TS,
                            "size": "100",
                            "price": "0.40",
                            "usdcSize": "40",
                            "transactionHash": "0xtx-1",
                        },
                        {
                            "type": "TRADE",
                            "asset": "rain-yes",
                            "side": "SELL",
                            "title": "NYC rain",
                            "outcome": "Yes",
                            "eventId": "weather-event-1",
                            "eventSlug": "rain-in-nyc",
                            "conditionId": "cond-weather-yes",
                            "slug": "rain-in-nyc-yes",
                            "timestamp": BASE_TS + 48 * 3600,
                            "size": "50",
                            "price": "0.70",
                            "usdcSize": "35",
                            "transactionHash": "0xtx-2",
                        },
                        {
                            "type": "TRADE",
                            "asset": "snow-no",
                            "side": "BUY",
                            "title": "Boston snow",
                            "outcome": "No",
                            "eventId": "other-event",
                            "eventSlug": "snow-in-boston",
                            "conditionId": "cond-other",
                            "slug": "snow-in-boston-no",
                            "timestamp": BASE_TS + 72 * 3600,
                            "size": "100",
                            "price": "1.00",
                            "usdcSize": "100",
                            "transactionHash": "0xtx-3",
                        },
                        {
                            "type": "TRADE",
                            "asset": "rain-yes-late",
                            "side": "BUY",
                            "title": "NYC rain",
                            "outcome": "Yes",
                            "eventSlug": "rain-in-nyc",
                            "conditionId": "cond-weather-yes",
                            "timestamp": BASE_TS + 96 * 3600,
                            "size": "50",
                            "price": "0.50",
                            "usdcSize": "25",
                            "transactionHash": "0xtx-4",
                        },
                    ]
                return super().fetch_activity_page(**kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["analysis"]["screening_window_first"] = False
            config["analysis"]["recent_activity_screening_snapshot_enabled"] = True
            config["analysis"]["hydrate_selected_wallet_full_history"] = True
            config["pagination"] = {"page_size": 10, "max_offset": 0}

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(analysis, "PolymarketClient", RecentActivityReuseClient)
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            client = RecentActivityReuseClient.instances[0]
            activity_zero_calls = [
                kwargs
                for name, kwargs in client.calls
                if name == "activity"
                and kwargs.get("offset") == 0
                and kwargs.get("start") is None
                and kwargs.get("end") is None
            ]

        self.assertEqual(result["selected_wallet_count"], 1)
        self.assertEqual(len(activity_zero_calls), 1)

    def test_analyze_leaderboard_entry_uses_full_activity_after_screening_window(self) -> None:
        class ScreeningTradesReuseClient(FakePolymarketClient):
            instances: list["ScreeningTradesReuseClient"] = []

            def __init__(self, api_config: dict[str, Any]) -> None:
                super().__init__(api_config)
                type(self).instances.append(self)
                self.screening_trade = {
                    "asset": "rain-yes",
                    "side": "BUY",
                    "title": "NYC rain",
                    "outcome": "Yes",
                    "eventId": "weather-event-1",
                    "eventSlug": "rain-in-nyc",
                    "conditionId": "cond-weather-yes",
                    "slug": "rain-in-nyc-yes",
                    "timestamp": BASE_TS,
                    "size": "100",
                    "price": "0.40",
                    "usdcSize": "40",
                    "transactionHash": "0xtx-screening",
                    "type": "TRADE",
                }
                self.full_only_trade = {
                    "asset": "rain-yes-older",
                    "side": "SELL",
                    "title": "NYC rain",
                    "outcome": "Yes",
                    "eventId": "weather-event-1",
                    "eventSlug": "rain-in-nyc",
                    "conditionId": "cond-weather-yes",
                    "slug": "rain-in-nyc-yes",
                    "timestamp": BASE_TS - 2 * 24 * 3600,
                    "size": "50",
                    "price": "0.60",
                    "usdcSize": "30",
                    "transactionHash": "0xtx-full-only",
                    "type": "TRADE",
                }

            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("activity", kwargs))
                start = kwargs.get("start")
                end = kwargs.get("end")
                limit = int(kwargs["limit"])
                offset = int(kwargs["offset"])
                if start is not None and end is not None:
                    records = [
                        record
                        for record in (self.screening_trade, self.full_only_trade)
                        if int(record["timestamp"]) >= int(start) and int(record["timestamp"]) <= int(end)
                    ]
                    records.sort(key=lambda record: int(record["timestamp"]), reverse=True)
                    return [dict(record) for record in records[offset : offset + limit]]
                if offset == 0:
                    return [{"type": "REWARD", "usdcSize": "12.34", "timestamp": BASE_TS + 3600}]
                raise_terminal_http_400("activity", limit=limit, offset=offset)

            def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("trades", kwargs))
                limit = int(kwargs["limit"])
                offset = int(kwargs["offset"])
                records = [dict(self.screening_trade), dict(self.full_only_trade)]
                return records[offset : offset + limit]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["leaderboard"]["time_period"] = "DAY"
            config["analysis"]["current_datetime"] = (BASE_DT + timedelta(hours=1)).isoformat()
            config["analysis"]["screening_window_first"] = True
            config["analysis"]["recent_activity_screening_snapshot_enabled"] = False
            config["analysis"]["hydrate_selected_wallet_full_history"] = True
            config["pagination"] = {"page_size": 1, "max_offset": 1}
            config["wallet_filter"]["min_traded_count"] = 1

            client = ScreeningTradesReuseClient(config["api"])
            weather_index = analysis.build_weather_index(
                client.fetch_events_keyset_page(limit=1)["events"]
            )
            leaderboard_entry = client.fetch_leaderboard_page(
                category="WEATHER",
                time_period="DAY",
                order_by="PNL",
                limit=1,
                offset=0,
            )[0]

            result = analysis.analyze_leaderboard_entry(
                client=client,
                leaderboard_entry=leaderboard_entry,
                weather_index=weather_index,
                config=config,
            )

            trades_calls = [kwargs for name, kwargs in client.calls if name == "trades"]
            wallet_result = result["wallet_result"]

        self.assertIn("wallet_result", result)
        self.assertEqual(wallet_result["deep_hydration"]["status"], "completed")
        self.assertEqual(trades_calls, [])
        self.assertEqual(wallet_result["raw_counts"]["trade_count"], 2)
        self.assertEqual(wallet_result["raw_counts"]["activity_count"], 3)
        self.assertEqual(wallet_result["screening"]["trade_count"], 1)
        self.assertEqual(wallet_result["metrics"]["screening_trade_count"], 1)

    def test_run_pipeline_resume_skips_completed_wallets_and_keeps_existing_details(self) -> None:
        completed_wallet = "0xaaa0000000000000000000000000000000000000"
        next_wallet = "0xbbb0000000000000000000000000000000000000"

        class ResumeImportClient(FakePolymarketClient):
            instances: list["ResumeImportClient"] = []

            def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("trades", kwargs))
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return super().fetch_trades_page(**kwargs)

            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("activity", kwargs))
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return [{"type": "REWARD", "usdcSize": "12.34"}]

            def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("positions", kwargs))
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return []

            def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("closed_positions", kwargs))
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return super().fetch_closed_positions_page(**kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            wallets_dir = output_dir / "wallets"
            wallets_dir.mkdir(parents=True)
            (wallets_dir / f"{completed_wallet}.json").write_text(
                json.dumps(
                    {
                        "wallet": completed_wallet,
                        "leaderboard_entry": {
                            "rank": 1,
                            "proxyWallet": completed_wallet,
                            "userName": "already-done",
                            "pnl": 200,
                            "vol": 2000,
                        },
                        "screening": {"wallet": completed_wallet, "selected": True},
                        "selection_record": {
                            "wallet": completed_wallet,
                            "selected": True,
                            "labels": ["Existing"],
                        },
                        "labels": [{"display_name": "Existing"}],
                        "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
                        "metrics": {
                            "leaderboard_pnl": 200,
                            "leaderboard_volume": 2000,
                            "wallet_win_rate": 0.7,
                            "weather_notional_ratio": 0.8,
                            "closed_position_win_rate": 0.7,
                            "closed_position_sample_win_rate": 0.7,
                            "closed_profit_multiple": 2.0,
                            "trades_per_active_day": 4,
                            "trade_count": 4,
                        },
                        "finder_ai": {
                            "briefGeneration": {"status": "cached"},
                            "providerMeta": {"generatedAt": "2026-05-05T00:00:00+00:00"},
                            "matched": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            import_rows_path = output_dir / "smart_wallet_import_rows.json"
            import_rows_path.write_text(
                json.dumps(
                    [
                        {
                            "wallet": {"normalizedAddress": completed_wallet},
                            "userName": "already-done",
                            "metrics": {"pnl": 200, "volume": 2000},
                        },
                        {
                            "wallet": {"normalizedAddress": next_wallet},
                            "userName": "resume-next",
                            "metrics": {"pnl": 180, "volume": 1800},
                        },
                    ]
                ),
                encoding="utf-8",
            )
            (output_dir / "weather_events.json").write_text("[]", encoding="utf-8")
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["runtime"] = {
                "resume_existing_output": True,
                "smart_wallet_library_source_path": str(import_rows_path),
                "smart_wallet_library_process_all": True,
            }
            config["wallet_filter"]["target_count"] = 2
            config["wallet_filter"]["min_pnl"] = 0
            config["wallet_filter"]["min_volume"] = 0
            config["wallet_filter"]["min_traded_count"] = 0
            config["wallet_filter"]["min_weather_trade_ratio"] = 0

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", ResumeImportClient))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )
            calls_by_wallet = [
                kwargs["user"]
                for name, kwargs in ResumeImportClient.instances[0].calls
                if name in {"trades", "activity", "positions", "closed_positions"}
            ]
            completed_wallet_detail_exists = (output_dir / "wallets" / f"{completed_wallet}.json").exists()
            next_wallet_detail_exists = (output_dir / "wallets" / f"{next_wallet}.json").exists()

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertEqual([row["wallet"] for row in selected_wallets], [completed_wallet, next_wallet])
        self.assertNotIn(completed_wallet, calls_by_wallet)
        self.assertIn(next_wallet, calls_by_wallet)
        self.assertTrue(completed_wallet_detail_exists)
        self.assertTrue(next_wallet_detail_exists)

    def test_run_pipeline_resume_uses_lightweight_index_before_final_wallet_reload(self) -> None:
        completed_wallet = "0xaaa0000000000000000000000000000000000000"
        next_wallet = "0xbbb0000000000000000000000000000000000000"

        class ResumeImportClient(FakePolymarketClient):
            def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return super().fetch_trades_page(**kwargs)

            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return [{"type": "REWARD", "usdcSize": "12.34"}]

            def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return []

            def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                if kwargs["user"] == completed_wallet:
                    raise AssertionError("completed wallet should not be fetched again")
                return super().fetch_closed_positions_page(**kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            wallets_dir = output_dir / "wallets"
            wallets_dir.mkdir(parents=True)
            completed_payload = {
                "wallet": completed_wallet,
                "leaderboard_entry": {
                    "rank": 1,
                    "proxyWallet": completed_wallet,
                    "userName": "already-done",
                    "pnl": 200,
                    "vol": 2000,
                },
                "screening": {"wallet": completed_wallet, "selected": True},
                "selection_record": {
                    "wallet": completed_wallet,
                    "selected": True,
                    "labels": ["Existing"],
                },
                "labels": [{"display_name": "Existing"}],
                "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
                "metrics": {
                    "leaderboard_pnl": 200,
                    "leaderboard_volume": 2000,
                    "wallet_win_rate": 0.7,
                    "weather_notional_ratio": 0.8,
                    "closed_position_win_rate": 0.7,
                    "closed_position_sample_win_rate": 0.7,
                    "closed_profit_multiple": 2.0,
                    "trades_per_active_day": 4,
                    "trade_count": 4,
                },
                "finder_ai": {
                    "briefGeneration": {"status": "cached"},
                    "providerMeta": {"generatedAt": "2026-05-05T00:00:00+00:00"},
                    "matched": True,
                },
            }
            completed_wallet_path = wallets_dir / f"{completed_wallet}.json"
            completed_wallet_path.write_text(json.dumps(completed_payload), encoding="utf-8")
            (output_dir / "selected_wallets.json").write_text(
                json.dumps([completed_payload["selection_record"]]),
                encoding="utf-8",
            )
            (output_dir / "screening_records.json").write_text(
                json.dumps([completed_payload["screening"]]),
                encoding="utf-8",
            )
            import_rows_path = output_dir / "smart_wallet_import_rows.json"
            import_rows_path.write_text(
                json.dumps(
                    [
                        {
                            "wallet": {"normalizedAddress": completed_wallet},
                            "userName": "already-done",
                            "metrics": {"pnl": 200, "volume": 2000},
                        },
                        {
                            "wallet": {"normalizedAddress": next_wallet},
                            "userName": "resume-next",
                            "metrics": {"pnl": 180, "volume": 1800},
                        },
                    ]
                ),
                encoding="utf-8",
            )
            (output_dir / "weather_events.json").write_text("[]", encoding="utf-8")
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["runtime"] = {
                "resume_existing_output": True,
                "smart_wallet_library_source_path": str(import_rows_path),
                "smart_wallet_library_process_all": True,
            }
            config["wallet_filter"]["target_count"] = 2
            config["wallet_filter"]["min_pnl"] = 0
            config["wallet_filter"]["min_volume"] = 0
            config["wallet_filter"]["min_traded_count"] = 0
            config["wallet_filter"]["min_weather_trade_ratio"] = 0

            original_read_json_file = analysis.read_json_file
            completed_wallet_reads: list[Path] = []

            def read_json_file_spy(path: Path) -> dict[str, Any]:
                if path == completed_wallet_path:
                    completed_wallet_reads.append(path)
                return original_read_json_file(path)

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", ResumeImportClient))
                stack.enter_context(patch.object(analysis, "read_json_file", side_effect=read_json_file_spy))
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

        self.assertEqual(result["selected_wallet_count"], 2)
        self.assertEqual(len(completed_wallet_reads), 1)

    def test_resume_index_backfills_completed_detail_missing_from_batch_flush(self) -> None:
        wallet_existing = "0xaaa0000000000000000000000000000000000000"
        wallet_missing = "0xbbb0000000000000000000000000000000000000"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            wallets_dir = output_dir / "wallets"
            wallets_dir.mkdir(parents=True)
            for wallet in (wallet_existing, wallet_missing):
                (wallets_dir / f"{wallet}.json").write_text(
                    json.dumps(
                        {
                            "wallet": wallet,
                            "screening": {"wallet": wallet, "selected": True},
                            "selection_record": {"wallet": wallet, "selected": True},
                            "metrics": {"leaderboard_pnl": 100, "trade_count": 3},
                        }
                    ),
                    encoding="utf-8",
                )
            (output_dir / "selected_wallets.json").write_text(
                json.dumps([{"wallet": wallet_existing, "selected": True}]),
                encoding="utf-8",
            )
            (output_dir / "screening_records.json").write_text(
                json.dumps([{"wallet": wallet_existing, "selected": True}]),
                encoding="utf-8",
            )

            selected_wallets, screening_records, completed_wallets = (
                analysis.load_existing_wallet_resume_index(output_dir)
            )

        self.assertEqual(completed_wallets, {wallet_existing, wallet_missing})
        self.assertEqual(
            [row["wallet"] for row in selected_wallets],
            [wallet_existing, wallet_missing],
        )
        self.assertEqual(
            [row["wallet"] for row in screening_records],
            [wallet_existing, wallet_missing],
        )

    def test_run_pipeline_prefilters_wallets_seen_in_history_registry_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["leaderboard"]["auto_extend_to_target"] = False
            seed_history_record(temp_path, WALLET)

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", FakePolymarketClient))
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 0)
            screening_records = json.loads(
                (output_dir / "screening_records.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(screening_records), 1)
            self.assertEqual(screening_records[0]["wallet"], WALLET)
            self.assertEqual(screening_records[0]["prefilter_stage"], "leaderboard")
            self.assertIn(
                analysis.HISTORY_ALREADY_FETCHED_REASON,
                screening_records[0]["reasons"],
            )

            history_record = json.loads(
                history_record_path(temp_path, WALLET).read_text(encoding="utf-8")
            )
            self.assertEqual(history_record["run_count"], 1)
            self.assertEqual(history_record["last_run_id"], "prior-run")
            self.assertEqual(history_record["last_status"], "selected")

            calls = [name for name, _kwargs in FakePolymarketClient.instances[0].calls]
            self.assertEqual(calls, ["leaderboard"])

    def test_run_pipeline_include_wallets_overrides_history_registry_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["wallet_filter"]["include_wallets"] = [WALLET.removeprefix("0x")]
            seed_history_record(temp_path, WALLET, run_count=3, last_status="screened_out")

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", FakePolymarketClient))
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(analysis, "progress", lambda *_args, **_kwargs: None, create=True)
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            screening_records = json.loads(
                (output_dir / "screening_records.json").read_text(encoding="utf-8")
            )
            self.assertTrue(all(
                analysis.HISTORY_ALREADY_FETCHED_REASON not in record["reasons"]
                for record in screening_records
            ))

            history_record = json.loads(
                history_record_path(temp_path, WALLET).read_text(encoding="utf-8")
            )
            self.assertEqual(history_record["wallet_address"], WALLET)
            self.assertEqual(history_record["user_name"], "smoke-weather")
            self.assertEqual(history_record["run_count"], 4)
            self.assertEqual(history_record["last_run_id"], "out")
            self.assertEqual(history_record["last_status"], "selected")

            calls = [name for name, _kwargs in FakePolymarketClient.instances[0].calls]
            self.assertEqual(
                calls,
                [
                    "leaderboard",
                    "events_keyset",
                    "activity",
                    "positions",
                    "closed_positions",
                ],
            )

    def test_run_pipeline_extends_leaderboard_until_target_count_is_met(self) -> None:
        wallet_fallback = "0xdef4560000000000000000000000000000000000"

        class ExtendingLeaderboardClient:
            instances: list["ExtendingLeaderboardClient"] = []

            def __init__(self, api_config: dict[str, Any]) -> None:
                self.api_config = api_config
                self.calls: list[tuple[str, dict[str, Any]]] = []
                type(self).instances.append(self)

            def fetch_leaderboard_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("leaderboard", kwargs))
                offset = kwargs["offset"]
                if offset == 0:
                    return [
                        {
                            "rank": 1,
                            "proxyWallet": WALLET,
                            "userName": "not-enough-trades",
                            "xUsername": "not_enough_trades",
                            "pnl": "150",
                            "vol": "3000",
                        }
                    ]
                if offset == 1:
                    return [
                        {
                            "rank": 2,
                            "proxyWallet": wallet_fallback,
                            "userName": "selected-after-extend",
                            "xUsername": "selected_after_extend",
                            "pnl": "160",
                            "vol": "3200",
                        }
                    ]
                return []

            def fetch_events_keyset_page(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(("events_keyset", kwargs))
                return {
                    "events": [
                        {
                            "id": "weather-event-1",
                            "slug": "rain-in-nyc",
                            "series": [{"title": "NYC Daily Weather"}],
                            "tags": [{"label": "Weather"}, {"label": "NYC"}],
                            "markets": [
                                {
                                    "conditionId": "cond-weather-yes",
                                    "slug": "rain-in-nyc-yes",
                                }
                            ],
                        }
                    ],
                    "next_cursor": None,
                }

            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("activity", kwargs))
                return [{"type": "REWARD", "usdcSize": "12.34"}] if kwargs["user"] == wallet_fallback else []

            def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("trades", kwargs))
                if kwargs["user"] != wallet_fallback:
                    return []
                return [
                    {
                        "asset": "rain-yes",
                        "side": "BUY",
                        "title": "NYC rain",
                        "outcome": "Yes",
                        "eventId": "weather-event-1",
                        "eventSlug": "rain-in-nyc",
                        "conditionId": "cond-weather-yes",
                        "slug": "rain-in-nyc-yes",
                        "timestamp": BASE_TS,
                        "size": "100",
                        "price": "0.40",
                        "usdcSize": "40",
                    },
                    {
                        "asset": "rain-yes",
                        "side": "SELL",
                        "title": "NYC rain",
                        "outcome": "Yes",
                        "eventId": "weather-event-1",
                        "eventSlug": "rain-in-nyc",
                        "conditionId": "cond-weather-yes",
                        "slug": "rain-in-nyc-yes",
                        "timestamp": BASE_TS + 48 * 3600,
                        "size": "50",
                        "price": "0.70",
                        "usdcSize": "35",
                    },
                    {
                        "asset": "rain-yes-late",
                        "side": "BUY",
                        "title": "NYC rain",
                        "outcome": "Yes",
                        "eventSlug": "rain-in-nyc",
                        "conditionId": "cond-weather-yes",
                        "timestamp": BASE_TS + 96 * 3600,
                        "size": "50",
                        "price": "0.50",
                        "usdcSize": "25",
                    },
                ]

            def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("positions", kwargs))
                return []

            def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("closed_positions", kwargs))
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(analysis, "PolymarketClient", ExtendingLeaderboardClient)
                )
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "progress",
                            lambda *_args, **_kwargs: None,
                            create=True,
                        )
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            selected_wallets = json.loads(
                (output_dir / "selected_wallets.json").read_text(encoding="utf-8")
            )
            self.assertEqual(selected_wallets[0]["wallet"], wallet_fallback)

            leaderboard_rows = json.loads(
                (output_dir / "leaderboard.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(leaderboard_rows), 2)

            leaderboard_calls = [
                kwargs
                for name, kwargs in ExtendingLeaderboardClient.instances[0].calls
                if name == "leaderboard"
            ]
            self.assertEqual(
                [(call["offset"], call["limit"]) for call in leaderboard_calls],
                [(0, 1), (1, 1)],
            )

    def test_run_pipeline_stops_auto_extend_when_next_page_repeats_wallets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = disable_light_first_gate(small_config(temp_path / "cache"))
            config["wallet_filter"]["target_count"] = 2

            with ExitStack() as stack:
                stack.enter_context(patch.object(analysis, "PolymarketClient", FakePolymarketClient))
                if not hasattr(analysis, "progress"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "progress",
                            lambda *_args, **_kwargs: None,
                            create=True,
                        )
                    )
                if not hasattr(analysis, "build_analysis_summary"):
                    stack.enter_context(
                        patch.object(
                            analysis,
                            "build_analysis_summary",
                            fallback_analysis_summary,
                            create=True,
                        )
                    )
                result = analysis.run_pipeline(config=config, output_dir=output_dir)

            self.assertEqual(result["selected_wallet_count"], 1)
            leaderboard_rows = json.loads(
                (output_dir / "leaderboard.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(leaderboard_rows), 1)

            leaderboard_calls = [
                kwargs
                for name, kwargs in FakePolymarketClient.instances[-1].calls
                if name == "leaderboard"
            ]
            self.assertEqual(
                [(call["offset"], call["limit"]) for call in leaderboard_calls],
                [(0, 1), (1, 1)],
            )


if __name__ == "__main__":
    unittest.main()
