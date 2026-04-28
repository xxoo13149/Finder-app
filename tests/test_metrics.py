from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.metrics import (
    condition_matches,
    cost_basis_distribution,
    filter_records,
    high_temperature_early_entry_summary,
    liquidity_player_summary,
    low_chip_cost_summary,
    profile_summary,
    profit_multiple,
    recent_activity_summary,
    regional_day_win_rate_summary,
    regional_daily_profit_summary,
    regional_trade_summary,
    record_matches_tags,
    tag_match_summary,
    trade_frequency_summary,
    wallet_age_summary,
    win_rate_summary,
)


class MetricsTests(unittest.TestCase):
    def assertHasKeys(self, mapping: dict[str, object], keys: set[str]) -> None:
        self.assertTrue(keys.issubset(mapping), msg=f"missing keys: {sorted(keys - set(mapping))}")

    def test_profile_summary_builds_price_city_and_closed_pnl_views(self) -> None:
        profile = profile_summary(
            [
                {"_region": "NYC", "side": "BUY", "price": "0.25", "size": "100", "usdcSize": "25"},
                {"_region": "NYC", "side": "SELL", "price": "0.60", "size": "50", "usdcSize": "30"},
                {"city": "Austin", "side": "BUY", "size": "100", "usdcSize": "80"},
                {"side": "BUY", "price": "0.50", "size": "10"},
            ],
            [
                {"_region": "NYC", "title": "NYC win", "realizedPnl": "10", "totalBought": "50"},
                {"city": "Austin", "title": "Austin loss", "realizedPnl": "-5", "totalBought": "20"},
                {"title": "Missing pnl", "realizedPnl": "", "totalBought": "5"},
            ],
            region_fields=("_region", "city", "region"),
        )

        average = profile["average_buy_price"]
        self.assertEqual(average["total_buy_count"], 3)
        self.assertEqual(average["priced_buy_count"], 3)
        self.assertAlmostEqual(average["weighted_average_price"], 110 / 210)

        city_distribution = profile["city_distribution"]
        self.assertEqual(city_distribution["total_trade_count"], 4)
        self.assertEqual(city_distribution["unknown_city_trade_count"], 1)
        self.assertEqual(city_distribution["cities"][0]["city"], "NYC")
        self.assertAlmostEqual(city_distribution["cities"][0]["net_trade_cashflow"], 5.0)

        self.assertEqual(profile["top_cities"]["by_buy_amount"][0]["city"], "Austin")
        self.assertEqual(profile["top_cities"]["by_realized_pnl"][0]["city"], "NYC")
        self.assertEqual(profile["buy_price_distribution"]["priced_count"], 3)
        self.assertAlmostEqual(profile["buy_price_distribution"]["max_cost_basis"], 0.8)

        closed_pnl = profile["closed_position_pnl"]
        self.assertEqual(closed_pnl["closed_position_count"], 3)
        self.assertEqual(closed_pnl["resolved_pnl_count"], 2)
        self.assertEqual(closed_pnl["missing_pnl_count"], 1)
        self.assertAlmostEqual(closed_pnl["total_realized_pnl"], 5.0)
        self.assertEqual(closed_pnl["top_winning_positions"][0]["city"], "NYC")
        self.assertEqual(closed_pnl["top_losing_positions"][0]["city"], "Austin")

    def test_profit_multiple_handles_strings_profit_and_zero_cost(self) -> None:
        self.assertEqual(profit_multiple("100", "250"), 2.5)
        self.assertEqual(profit_multiple("100", profit="50"), 1.5)
        self.assertEqual(profit_multiple("0", "250"), 0.0)
        self.assertEqual(profit_multiple("", "250"), 0.0)

    def test_win_rate_summary_handles_missing_and_string_numbers(self) -> None:
        summary = win_rate_summary(
            [
                {"realizedPnl": "10.5"},
                {"realizedPnl": "-2"},
                {"realizedPnl": 0},
                {},
            ]
        )

        self.assertEqual(summary["total_count"], 4)
        self.assertEqual(summary["resolved_count"], 3)
        self.assertEqual(summary["missing_count"], 1)
        self.assertEqual(summary["win_count"], 1)
        self.assertEqual(summary["loss_count"], 1)
        self.assertEqual(summary["push_count"], 1)
        self.assertAlmostEqual(summary["win_rate"], 1 / 3)
        self.assertAlmostEqual(summary["total_pnl"], 8.5)

    def test_cost_basis_distribution_buckets_weighted_costs(self) -> None:
        summary = cost_basis_distribution(
            [
                {"avgPrice": "0.20", "size": "10"},
                {"price": 0.65, "usdcSize": "13", "size": "20"},
                {"avgPrice": "1.10", "size": 5},
                {"title": "missing price"},
                {"avgPrice": "not-a-number", "size": 5},
            ]
        )

        self.assertEqual(summary["total_count"], 5)
        self.assertEqual(summary["priced_count"], 3)
        self.assertEqual(summary["missing_price_count"], 2)
        self.assertEqual(summary["buckets"][0]["count"], 1)
        self.assertEqual(summary["buckets"][2]["count"], 1)
        self.assertEqual(summary["overflow"]["count"], 1)
        self.assertAlmostEqual(summary["total_size"], 35.0)
        self.assertAlmostEqual(summary["total_cost"], 20.5)
        self.assertAlmostEqual(summary["weighted_average_cost"], 20.5 / 35.0)

    def test_trade_frequency_summary_handles_epoch_iso_and_empty(self) -> None:
        summary = trade_frequency_summary(
            [
                {"timestamp": "1704067200"},
                {"timestamp": "2024-01-01T01:00:00Z"},
                {"timestamp": 1704240000},
                {},
            ]
        )

        self.assertEqual(summary["total_count"], 4)
        self.assertEqual(summary["timestamp_count"], 3)
        self.assertEqual(summary["missing_timestamp_count"], 1)
        self.assertEqual(summary["active_day_count"], 2)
        self.assertEqual(summary["by_day"]["2024-01-01"], 2)
        self.assertEqual(summary["by_day"]["2024-01-03"], 1)
        self.assertEqual(summary["by_hour_utc"][0], 2)

        empty = trade_frequency_summary([])
        self.assertEqual(empty["total_count"], 0)
        self.assertEqual(empty["trades_per_active_day"], 0.0)
        self.assertIsNone(empty["first_timestamp"])

    def test_filter_records_supports_numeric_text_and_nested_fields(self) -> None:
        records = [
            {"market": {"slug": "rain-nyc"}, "pnl": "12", "side": "BUY"},
            {"market": {"slug": "heat-la"}, "pnl": "-3", "side": "SELL"},
            {"pnl": "", "side": "BUY"},
        ]

        selected = filter_records(
            records,
            [
                {"field": "pnl", "op": ">=", "value": "0"},
                {"field": "market.slug", "op": "contains", "value": "rain"},
            ],
        )

        self.assertEqual(selected, [records[0]])
        self.assertTrue(condition_matches(records[2], {"field": "market.slug", "op": "missing"}))
        self.assertFalse(condition_matches(records[2], {"field": "pnl", "op": ">=", "value": 0}))
        self.assertTrue(condition_matches(records[0], {"field": "side", "op": "in", "value": ["BUY"]}))

    def test_tag_matching_uses_structured_tags_and_searchable_fields(self) -> None:
        records = [
            {
                "tags": [{"slug": "weather"}, {"label": "rainfall"}],
                "title": "NYC rain total",
            },
            {"tagSlug": "crypto", "title": "Bitcoin range"},
            {"eventSlug": "daily-temperature-in-austin"},
        ]

        self.assertTrue(record_matches_tags(records[0], "weather"))
        self.assertTrue(record_matches_tags(records[2], ["temperature"]))
        self.assertFalse(record_matches_tags(records[1], ["weather"]))
        self.assertEqual(
            filter_records(records, [{"op": "tag_matches", "value": "weather"}]),
            [records[0]],
        )

        summary = tag_match_summary(records, ["weather", "temperature"])
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["per_tag"]["weather"], 1)
        self.assertEqual(summary["per_tag"]["temperature"], 1)

    def test_regional_trade_summary_identifies_dominant_and_balanced_regions(self) -> None:
        dominant = regional_trade_summary(
            [{"region": "Manila"} for _ in range(7)]
            + [{"region": "Helsinki"} for _ in range(3)]
        )

        self.assertEqual(dominant["dominant_region"], "Manila")
        self.assertAlmostEqual(dominant["dominant_region_trade_ratio"], 0.7)
        self.assertTrue(dominant["matched_high_frequency_region"])
        self.assertFalse(dominant["is_balanced_without_dominant_region"])

        balanced = regional_trade_summary(
            [{"region": "Manila"} for _ in range(4)]
            + [{"region": "Helsinki"} for _ in range(3)]
            + [{"region": "Austin"} for _ in range(3)]
        )

        self.assertAlmostEqual(balanced["dominant_region_trade_ratio"], 0.4)
        self.assertAlmostEqual(balanced["region_trade_ratio_spread"], 0.1)
        self.assertFalse(balanced["matched_high_frequency_region"])
        self.assertTrue(balanced["is_balanced_without_dominant_region"])

    def test_regional_trade_summary_can_group_by_region_day(self) -> None:
        summary = regional_trade_summary(
            [
                {"region": "Shanghai", "timestamp": "2026-04-13T01:00:00Z", "side": "BUY", "usdcSize": "15"},
                {"region": "Shanghai", "timestamp": "2026-04-13T02:00:00Z", "side": "SELL", "usdcSize": "40"},
                {"region": "Shanghai", "timestamp": "2026-04-14T01:00:00Z", "side": "BUY", "usdcSize": "10"},
                {"region": "Chongqing", "timestamp": "2026-04-15T01:00:00Z", "side": "BUY", "usdcSize": "12"},
                {"region": "Chongqing", "timestamp": "2026-04-15T02:00:00Z", "side": "SELL", "usdcSize": "18"},
                {"region": "Chongqing", "timestamp": "2026-04-15T03:00:00Z", "side": "SELL", "usdcSize": "22"},
            ],
            collapse_by_day=True,
            dominance_threshold=0.4,
        )

        self.assertEqual(summary["count_mode"], "region_day")
        self.assertEqual(summary["region_day_count"], 3)
        self.assertEqual(summary["dominant_region"], "Shanghai")
        self.assertEqual(summary["dominant_region_trade_count"], 2)
        self.assertEqual(summary["dominant_region_raw_trade_count"], 3)
        self.assertAlmostEqual(summary["dominant_region_trade_ratio"], 2 / 3)
        self.assertTrue(summary["matched_high_frequency_region"])

    def test_regional_daily_profit_summary_groups_all_temperature_ranges(self) -> None:
        summary = regional_daily_profit_summary(
            [
                {
                    "region": "Manila",
                    "timestamp": "2024-01-01T01:00:00Z",
                    "side": "BUY",
                    "usdcSize": "100",
                    "conditionId": "29c",
                },
                {
                    "region": "Manila",
                    "timestamp": "2024-01-01T02:00:00Z",
                    "side": "BUY",
                    "usdcSize": "50",
                    "conditionId": "30c",
                },
                {
                    "region": "Manila",
                    "timestamp": "2024-01-01T03:00:00Z",
                    "side": "SELL",
                    "usdcSize": "350",
                    "conditionId": "31c",
                },
                {
                    "region": "Helsinki",
                    "timestamp": "2024-01-01T03:00:00Z",
                    "side": "BUY",
                    "size": "100",
                    "price": "1",
                },
                {
                    "region": "Helsinki",
                    "timestamp": "2024-01-01T04:00:00Z",
                    "side": "SELL",
                    "size": "190",
                    "price": "1",
                },
            ]
        )

        self.assertTrue(summary["matched_high_daily_region_profit"])
        self.assertEqual(summary["qualified_count"], 1)
        self.assertEqual(summary["max_region"], "Manila")
        self.assertEqual(summary["max_date"], "2024-01-01")
        self.assertAlmostEqual(summary["max_profit_multiple"], 350 / 150)
        self.assertEqual(summary["qualified_region_days"][0]["trade_count"], 3)

    def test_regional_day_win_rate_summary_counts_positive_return_days(self) -> None:
        summary = regional_day_win_rate_summary(
            [
                {"region": "Manila", "timestamp": "2024-01-01T01:00:00Z", "side": "BUY", "usdcSize": "100"},
                {"region": "Manila", "timestamp": "2024-01-01T02:00:00Z", "side": "SELL", "usdcSize": "140"},
                {"region": "Manila", "timestamp": "2024-01-02T01:00:00Z", "side": "BUY", "usdcSize": "100"},
                {"region": "Manila", "timestamp": "2024-01-02T02:00:00Z", "side": "SELL", "usdcSize": "80"},
                {"region": "Manila", "timestamp": "2024-01-03T01:00:00Z", "side": "BUY", "usdcSize": "50"},
                {"region": "Manila", "timestamp": "2024-01-03T02:00:00Z", "side": "SELL", "usdcSize": "70"},
                {"region": "Helsinki", "timestamp": "2024-01-01T01:00:00Z", "side": "BUY", "usdcSize": "100"},
                {"region": "Helsinki", "timestamp": "2024-01-01T02:00:00Z", "side": "SELL", "usdcSize": "90"},
            ]
        )

        self.assertTrue(summary["matched_regional_high_win_rate"])
        self.assertEqual(summary["best_region"], "Manila")
        self.assertEqual(summary["best_positive_return_days"], 2)
        self.assertEqual(summary["best_total_trade_days"], 3)
        self.assertAlmostEqual(summary["best_positive_return_day_ratio"], 2 / 3)
        self.assertEqual(summary["best_trade_count"], 6)

        boundary = regional_day_win_rate_summary(
            [
                {"region": "Austin", "timestamp": "2024-01-01T01:00:00Z", "side": "SELL", "usdcSize": "10"},
                {"region": "Austin", "timestamp": "2024-01-02T01:00:00Z", "side": "SELL", "usdcSize": "10"},
                {"region": "Austin", "timestamp": "2024-01-03T01:00:00Z", "side": "SELL", "usdcSize": "10"},
                {"region": "Austin", "timestamp": "2024-01-04T01:00:00Z", "side": "BUY", "usdcSize": "10"},
                {"region": "Austin", "timestamp": "2024-01-05T01:00:00Z", "side": "BUY", "usdcSize": "10"},
            ]
        )
        self.assertAlmostEqual(boundary["best_positive_return_day_ratio"], 0.6)
        self.assertTrue(boundary["matched_regional_high_win_rate"])
        self.assertEqual(boundary["best_trade_count"], 5)

    def test_regional_day_win_rate_summary_requires_minimum_trade_count(self) -> None:
        summary = regional_day_win_rate_summary(
            [
                {"region": "Guangzhou", "timestamp": "2024-01-01T01:00:00Z", "side": "BUY", "usdcSize": "100"},
                {"region": "Guangzhou", "timestamp": "2024-01-01T02:00:00Z", "side": "SELL", "usdcSize": "120"},
                {"region": "Austin", "timestamp": "2024-01-01T01:00:00Z", "side": "BUY", "usdcSize": "100"},
                {"region": "Austin", "timestamp": "2024-01-01T02:00:00Z", "side": "SELL", "usdcSize": "130"},
                {"region": "Austin", "timestamp": "2024-01-02T01:00:00Z", "side": "BUY", "usdcSize": "90"},
                {"region": "Austin", "timestamp": "2024-01-02T02:00:00Z", "side": "SELL", "usdcSize": "80"},
                {"region": "Austin", "timestamp": "2024-01-03T01:00:00Z", "side": "BUY", "usdcSize": "70"},
                {"region": "Austin", "timestamp": "2024-01-03T02:00:00Z", "side": "SELL", "usdcSize": "100"},
            ]
        )

        self.assertTrue(summary["matched_regional_high_win_rate"])
        self.assertEqual(summary["best_region"], "Austin")
        self.assertEqual(summary["best_trade_count"], 6)

        low_base_only = regional_day_win_rate_summary(
            [
                {"region": "Guangzhou", "timestamp": "2024-01-01T01:00:00Z", "side": "BUY", "usdcSize": "100"},
                {"region": "Guangzhou", "timestamp": "2024-01-01T02:00:00Z", "side": "SELL", "usdcSize": "120"},
            ]
        )

        self.assertFalse(low_base_only["matched_regional_high_win_rate"])
        self.assertEqual(low_base_only["best_region"], "Guangzhou")
        self.assertEqual(low_base_only["best_trade_count"], 2)

    def test_low_chip_cost_summary_uses_all_trades_as_denominator(self) -> None:
        summary = low_chip_cost_summary(
            [
                {"price": "0.20", "region": "Beijing", "timestamp": "2026-04-13T01:00:00Z", "side": "BUY", "title": "Beijing high temp"},
                {"avgPrice": "0.29", "city": "Seoul", "timestamp": "2026-04-13T02:00:00Z", "side": "BUY", "slug": "seoul-high-temp"},
                {"chipCost": "25", "region": "Beijing", "timestamp": "2026-04-14T01:00:00Z", "side": "SELL", "conditionId": "cond-beijing"},
                {"costBasis": "40"},
                {},
            ]
        )

        self.assertEqual(summary["total_count"], 5)
        self.assertEqual(summary["low_chip_cost_count"], 3)
        self.assertAlmostEqual(summary["low_chip_cost_ratio"], 3 / 5)
        self.assertTrue(summary["matched_lottery_player"])
        self.assertEqual(summary["missing_cost_count"], 1)
        self.assertEqual(summary["top_low_chip_region"], "Beijing")
        self.assertEqual(summary["top_low_chip_region_count"], 2)
        self.assertEqual(
            set(summary["low_chip_records"][0]),
            {
                "region",
                "city",
                "date",
                "side",
                "chip_cost",
                "notional",
                "title",
                "slug",
                "condition_id",
            },
        )
        self.assertEqual(summary["low_chip_records"][0]["city"], "Beijing")
        self.assertEqual(summary["low_chip_records"][0]["date"], "2026-04-13")

        boundary = low_chip_cost_summary([{"price": "0.20"}, {"price": "0.80"}])
        self.assertAlmostEqual(boundary["low_chip_cost_ratio"], 0.5)
        self.assertFalse(boundary["matched_lottery_player"])

    def test_liquidity_player_summary_requires_low_swap_and_sell_dominant_days(self) -> None:
        trades = [
            {"region": "Manila", "timestamp": "2026-04-20T01:00:00Z", "side": "SELL"},
            {"region": "Manila", "timestamp": "2026-04-20T02:00:00Z", "side": "SELL"},
            {"region": "Manila", "timestamp": "2026-04-20T03:00:00Z", "side": "BUY"},
            {"region": "Manila", "timestamp": "2026-04-21T01:00:00Z", "side": "SELL"},
            {"region": "Manila", "timestamp": "2026-04-21T02:00:00Z", "side": "BUY"},
            {"region": "Austin", "timestamp": "2026-04-21T03:00:00Z", "side": "SELL"},
        ]

        summary = liquidity_player_summary(trades, activity_records=[])

        self.assertEqual(summary["swap_count"], 0)
        self.assertTrue(summary["low_swap_activity"])
        self.assertEqual(summary["unique_trade_day_count"], 2)
        self.assertEqual(summary["regional_trade_day_count"], 3)
        self.assertEqual(summary["sell_dominant_region_day_count"], 2)
        self.assertEqual(summary["sell_dominant_day_count"], 2)
        self.assertAlmostEqual(summary["sell_dominant_region_day_ratio"], 1.0)
        self.assertTrue(summary["matched_liquidity_player"])

        boundary = liquidity_player_summary(
            [{"region": "Austin", "timestamp": "2026-04-20T01:00:00Z", "side": "SELL"}]
            * 10,
            activity_records=[{"type": "SWAP"}],
        )
        self.assertAlmostEqual(boundary["swap_ratio"], 0.1)
        self.assertFalse(boundary["low_swap_activity"])
        self.assertFalse(boundary["matched_liquidity_player"])

    def test_recent_activity_summary_splits_normal_low_and_inactive(self) -> None:
        now = datetime(2026, 4, 25, tzinfo=timezone.utc)

        normal = recent_activity_summary(
            [{"timestamp": "2026-04-24T23:59:59Z"}],
            now=now,
        )
        self.assertEqual(normal["days_since_latest_trade"], 1)
        self.assertEqual(normal["activity_level"], "normal_active")
        self.assertTrue(normal["matched_recent_active"])

        low = recent_activity_summary(
            [{"timestamp": "2026-04-22T00:00:00Z"}],
            now=now,
        )
        self.assertEqual(low["days_since_latest_trade"], 3)
        self.assertEqual(low["activity_level"], "low_active")
        self.assertTrue(low["matched_recent_active"])

        inactive = recent_activity_summary(
            [{"timestamp": "2026-04-21T23:59:59Z"}],
            now=now,
        )
        self.assertEqual(inactive["days_since_latest_trade"], 4)
        self.assertEqual(inactive["activity_level"], "inactive")
        self.assertFalse(inactive["matched_recent_active"])

    def test_wallet_age_summary_splits_hidden_new_and_regular_new_wallets(self) -> None:
        now = datetime(2026, 4, 25, tzinfo=timezone.utc)

        hidden = wallet_age_summary("2026-04-20T00:00:00Z", now=now, source="createdAt")
        self.assertEqual(hidden["wallet_age_days"], 5)
        self.assertTrue(hidden["matched_hidden_new_wallet"])
        self.assertFalse(hidden["matched_new_wallet"])

        regular = wallet_age_summary("2026-04-01T00:00:00Z", now=now, source="createdAt")
        self.assertEqual(regular["wallet_age_days"], 24)
        self.assertFalse(regular["matched_hidden_new_wallet"])
        self.assertTrue(regular["matched_new_wallet"])

        hidden_boundary = wallet_age_summary("2026-04-15T00:00:00Z", now=now)
        self.assertEqual(hidden_boundary["wallet_age_days"], 10)
        self.assertFalse(hidden_boundary["matched_hidden_new_wallet"])
        self.assertTrue(hidden_boundary["matched_new_wallet"])

        new_boundary = wallet_age_summary("2026-02-24T00:00:00Z", now=now)
        self.assertEqual(new_boundary["wallet_age_days"], 60)
        self.assertFalse(new_boundary["matched_new_wallet"])

        missing = wallet_age_summary(None, now=now)
        self.assertEqual(missing["status"], "missing_registration_date")
        self.assertFalse(missing["matched_new_wallet"])

    def test_high_temperature_early_entry_summary_counts_off_day_buys(self) -> None:
        summary = high_temperature_early_entry_summary(
            [
                {
                    "title": "NYC highest temperature on April 25, 2026",
                    "region": "NYC",
                    "timestamp": "2026-04-20T10:00:00Z",
                    "side": "BUY",
                    "_market_date": "2026-04-25",
                    "usdcSize": "20",
                },
                {
                    "title": "NYC highest temperature on April 25, 2026",
                    "region": "NYC",
                    "timestamp": "2026-04-21T10:00:00Z",
                    "side": "BUY",
                    "_market_date": "2026-04-25",
                    "usdcSize": "30",
                },
                {
                    "title": "NYC highest temperature on April 25, 2026",
                    "region": "NYC",
                    "timestamp": "2026-04-25T10:00:00Z",
                    "side": "BUY",
                    "_market_date": "2026-04-25",
                    "usdcSize": "40",
                },
                {
                    "title": "NYC highest temperature on April 25, 2026",
                    "region": "NYC",
                    "timestamp": "2026-04-25T11:00:00Z",
                    "side": "SELL",
                    "_market_date": "2026-04-25",
                },
                {
                    "title": "NYC rain on April 25, 2026",
                    "region": "NYC",
                    "timestamp": "2026-04-20T10:00:00Z",
                    "side": "BUY",
                    "_market_date": "2026-04-25",
                },
            ]
        )

        self.assertEqual(summary["high_temperature_buy_count"], 3)
        self.assertEqual(summary["analyzed_buy_count"], 3)
        self.assertEqual(summary["off_day_buy_count"], 2)
        self.assertEqual(summary["same_day_buy_count"], 1)
        self.assertAlmostEqual(summary["off_day_buy_ratio"], 2 / 3)
        self.assertEqual(summary["buy_before_market_day_count"], 2)
        self.assertTrue(summary["matched_early_positioning"])
        self.assertEqual(
            set(summary["off_day_buy_records"][0]),
            {
                "region",
                "title",
                "slug",
                "condition_id",
                "buy_datetime",
                "buy_date",
                "high_temperature_date",
                "day_difference",
                "off_day",
                "notional",
            },
        )
        self.assertEqual(summary["off_day_buy_records"][0]["region"], "NYC")
        self.assertEqual(summary["off_day_buy_records"][0]["buy_date"], "2026-04-20")
        self.assertEqual(summary["off_day_buy_records"][0]["high_temperature_date"], "2026-04-25")
        self.assertEqual(summary["top_off_day_buy_records"][0]["region"], "NYC")
        self.assertEqual(summary["top_off_day_buy_records"][0]["high_temperature_date"], "2026-04-25")

        boundary = high_temperature_early_entry_summary(
            [
                {
                    "title": "Austin highest temperature",
                    "timestamp": "2026-04-24T10:00:00Z",
                    "side": "BUY",
                    "_market_date": "2026-04-25",
                },
                {
                    "title": "Austin highest temperature",
                    "timestamp": "2026-04-25T10:00:00Z",
                    "side": "BUY",
                    "_market_date": "2026-04-25",
                },
            ]
        )
        self.assertAlmostEqual(boundary["off_day_buy_ratio"], 0.5)
        self.assertFalse(boundary["matched_early_positioning"])

    def test_high_daily_region_profit_evidence_shape_is_stable(self) -> None:
        summary = regional_daily_profit_summary(
            [
                {"region": "Shanghai", "timestamp": "2026-04-13T01:00:00Z", "side": "BUY", "usdcSize": "15"},
                {"region": "Shanghai", "timestamp": "2026-04-13T02:00:00Z", "side": "SELL", "usdcSize": "973"},
                {"region": "Chengdu", "timestamp": "2026-04-12T01:00:00Z", "side": "BUY", "usdcSize": "29"},
                {"region": "Chengdu", "timestamp": "2026-04-12T02:00:00Z", "side": "SELL", "usdcSize": "224"},
            ]
        )

        self.assertTrue(summary["matched_high_daily_region_profit"])
        self.assertEqual(summary["max_region"], "Shanghai")
        self.assertEqual(summary["max_date"], "2026-04-13")
        self.assertAlmostEqual(summary["max_profit_multiple"], 973 / 15)
        self.assertEqual(
            set(summary["qualified_region_days"][0]),
            {
                "region",
                "date",
                "trade_count",
                "buy_trade_count",
                "sell_trade_count",
                "buy_amount",
                "sell_amount",
                "profit_multiple",
            },
        )

    def test_audit_facing_metric_summaries_keep_required_fields(self) -> None:
        summaries = {
            "regional_trade_summary": regional_trade_summary(
                [{"region": "NYC"}, {"region": "NYC"}, {"region": "Austin"}]
            ),
            "regional_daily_profit_summary": regional_daily_profit_summary(
                [
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T01:00:00Z",
                        "side": "BUY",
                        "usdcSize": "10",
                    },
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T02:00:00Z",
                        "side": "SELL",
                        "usdcSize": "30",
                    },
                ]
            ),
            "regional_day_win_rate_summary": regional_day_win_rate_summary(
                [
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T01:00:00Z",
                        "side": "BUY",
                        "usdcSize": "10",
                    },
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T02:00:00Z",
                        "side": "SELL",
                        "usdcSize": "30",
                    },
                ]
            ),
            "low_chip_cost_summary": low_chip_cost_summary(
                [
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T01:00:00Z",
                        "side": "BUY",
                        "price": "0.20",
                        "usdcSize": "20",
                    }
                ]
            ),
            "liquidity_player_summary": liquidity_player_summary(
                [
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T01:00:00Z",
                        "side": "SELL",
                    },
                    {
                        "region": "NYC",
                        "timestamp": "2026-04-13T02:00:00Z",
                        "side": "BUY",
                    },
                ],
                activity_records=[],
            ),
            "high_temperature_early_entry_summary": high_temperature_early_entry_summary(
                [
                    {
                        "title": "NYC highest temperature on April 25, 2026",
                        "region": "NYC",
                        "timestamp": "2026-04-20T10:00:00Z",
                        "side": "BUY",
                        "_market_date": "2026-04-25",
                        "usdcSize": "20",
                    }
                ]
            ),
        }

        required_keys = {
            "regional_trade_summary": {
                "regions",
                "dominant_region",
                "dominant_region_trade_count",
                "dominant_region_trade_ratio",
                "matched_high_frequency_region",
            },
            "regional_daily_profit_summary": {
                "qualified_region_days",
                "max_region",
                "max_date",
                "max_profit_multiple",
                "matched_high_daily_region_profit",
            },
            "regional_day_win_rate_summary": {
                "qualified_regions",
                "best_region",
                "best_positive_return_days",
                "best_total_trade_days",
                "matched_regional_high_win_rate",
            },
            "low_chip_cost_summary": {
                "low_chip_records",
                "top_low_chip_region",
                "top_low_chip_region_count",
                "low_chip_cost_ratio",
                "matched_lottery_player",
            },
            "liquidity_player_summary": {
                "sell_dominant_region_days",
                "top_sell_dominant_region",
                "top_sell_dominant_date",
                "sell_dominant_region_day_ratio",
                "matched_liquidity_player",
            },
            "high_temperature_early_entry_summary": {
                "off_day_buy_records",
                "top_off_day_buy_records",
                "off_day_buy_ratio",
                "buy_before_market_day_count",
                "matched_early_positioning",
            },
        }

        for name, summary in summaries.items():
            with self.subTest(summary=name):
                self.assertHasKeys(summary, required_keys[name])


if __name__ == "__main__":
    unittest.main()
