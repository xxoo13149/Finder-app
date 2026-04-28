from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool import analysis


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
        return [
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

    def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("closed_positions", kwargs))
        end_date = (BASE_DT + timedelta(days=10)).isoformat()
        return [
            {"title": "A", "conditionId": "cond-weather-yes", "realizedPnl": "20", "totalBought": "50", "endDate": end_date},
            {"title": "B", "conditionId": "cond-other", "realizedPnl": "-5", "totalBought": "20", "endDate": end_date},
            {"title": "C", "conditionId": "cond-third", "realizedPnl": "1", "totalBought": "10", "endDate": end_date},
        ]


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
                    {"field": "closed_position_win_rate", "op": ">=", "value": 0.6},
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
        self.assertAlmostEqual(metrics["closed_position_win_rate"], 2 / 3)
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
            config = small_config(temp_path / "cache")

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

    def test_run_pipeline_prefilters_wallets_seen_in_history_registry_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_dir = temp_path / "out"
            config = small_config(temp_path / "cache")
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
            config = small_config(temp_path / "cache")
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
                    "trades",
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
            config = small_config(temp_path / "cache")

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


if __name__ == "__main__":
    unittest.main()
