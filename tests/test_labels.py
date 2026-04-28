from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.labels import (
    CORE_LABEL_KEYS,
    evaluate_label_evaluations,
    evaluate_labels,
)


class LabelTests(unittest.TestCase):
    def test_core_label_evaluations_include_positive_and_negative_records(self) -> None:
        evaluations = evaluate_label_evaluations(
            {
                "trade_count": 10,
                "dominant_region": "Manila",
                "dominant_region_trade_count": 3,
                "dominant_region_trade_ratio": 0.3,
                "regional_trade_summary": {
                    "total_count": 10,
                    "regions": [
                        {"region": "Manila", "trade_count": 3, "trade_ratio": 0.3},
                        {"region": "Austin", "trade_count": 7, "trade_ratio": 0.7},
                    ]
                },
                "max_region_daily_profit_region": "Manila",
                "max_region_daily_profit_date": "2026-04-21",
                "max_region_daily_profit_multiple": 2.5,
                "max_region_daily_profit_buy_amount": 100,
                "max_region_daily_profit_sell_amount": 250,
                "regional_daily_profit_summary": {
                    "qualified_region_days": [
                        {
                            "region": "Manila",
                            "date": "2026-04-21",
                            "buy_amount": 100,
                            "sell_amount": 250,
                            "profit_multiple": 2.5,
                        }
                    ]
                },
            },
            [],
        )

        self.assertEqual([item["key"] for item in evaluations], list(CORE_LABEL_KEYS))
        for item in evaluations:
            self.assertIn("matched", item)
            self.assertIn("reason", item)
            self.assertIn("facts", item)
            self.assertIn("records", item)
            self.assertTrue(item["records"])

        high_frequency = evaluations[0]
        self.assertFalse(high_frequency["matched"])
        self.assertFalse(high_frequency["facts"]["conditions"][0]["matched"])
        self.assertEqual(high_frequency["records"][0]["type"], "counterevidence")

        high_burst = evaluations[1]
        self.assertTrue(high_burst["matched"])
        self.assertEqual(high_burst["records"][0]["type"], "evidence")
        self.assertEqual(high_burst["facts"]["city"], "Manila")

    def test_core_label_evaluations_negative_schema_is_complete(self) -> None:
        evaluations = evaluate_label_evaluations(
            {
                "trade_count": 5,
                "dominant_region": "NYC",
                "dominant_region_trade_count": 1,
                "dominant_region_trade_ratio": 0.2,
                "regional_trade_summary": {
                    "total_count": 5,
                    "regions": [{"region": "NYC", "trade_count": 1, "trade_ratio": 0.2}]
                },
                "max_region_daily_profit_region": "Austin",
                "max_region_daily_profit_date": "2026-04-22",
                "max_region_daily_profit_multiple": 1.2,
                "max_region_daily_profit_buy_amount": 100,
                "max_region_daily_profit_sell_amount": 120,
                "regional_daily_profit_summary": {
                    "region_days": [
                        {
                            "region": "Austin",
                            "date": "2026-04-22",
                            "buy_amount": 100,
                            "sell_amount": 120,
                            "profit_multiple": 1.2,
                        }
                    ]
                },
                "best_region_win_rate_region": "Austin",
                "best_region_positive_return_days": 1,
                "best_region_total_trade_days": 3,
                "best_region_positive_return_day_ratio": 1 / 3,
                "best_region_trade_count": 2,
                "regional_day_win_rate_summary": {
                    "regions": [
                        {
                            "region": "Austin",
                            "positive_return_days": 1,
                            "total_trade_days": 3,
                            "positive_return_day_ratio": 1 / 3,
                            "trade_count": 2,
                        }
                    ]
                },
                "low_chip_cost_trade_count": 2,
                "low_chip_cost_trade_ratio": 0.4,
                "low_chip_cost_threshold": 30,
                "low_chip_cost_summary": {
                    "low_chip_records": [{"region": "NYC", "chip_cost": 20}]
                },
                "split_avg_chip_cost": 8,
                "split_avg_chip_cost_target": 5,
                "split_avg_chip_cost_tolerance": 0.5,
                "split_avg_chip_cost_matched": False,
                "split_chain_verified": False,
                "chain_validation_status": "no_split_evidence",
                "split_evidence_count": 0,
                "required_split_evidence_count": 2,
                "split_player_validation_passed": False,
                "liquidity_swap_count": 1,
                "liquidity_swap_ratio": 0.2,
                "liquidity_low_swap_activity": False,
                "liquidity_sell_dominant_region_day_count": 2,
                "liquidity_regional_trade_day_count": 5,
                "liquidity_sell_dominant_region_day_ratio": 0.4,
                "liquidity_player_matched": False,
                "liquidity_player_summary": {
                    "region_days": [{"region": "NYC", "date": "2026-04-22"}]
                },
            },
            [],
        )

        self.assertTrue(all(not item["matched"] for item in evaluations))
        for item in evaluations:
            self.assertTrue(item["reason"])
            self.assertEqual(item["records"][0]["type"], "counterevidence")
            self.assertIn("source", item["records"][0])
            for condition in item["facts"]["conditions"]:
                self.assertEqual(
                    {"group", "field", "op", "value", "actual", "matched"},
                    set(condition),
                )

        split = next(item for item in evaluations if item["key"] == "split_player")
        self.assertIn("no_split_evidence", split["reason"])
        self.assertIn("0/2", split["reason"])
        lottery = next(item for item in evaluations if item["key"] == "lottery_player")
        self.assertEqual(lottery["facts"]["numerator"], 2)
        self.assertEqual(lottery["facts"]["denominator"], 5)

    def test_label_templates_render_metric_values_after_match(self) -> None:
        labels = evaluate_labels(
            {
                "dominant_region": "Manila",
                "dominant_region_trade_ratio": 0.7,
            },
            [
                {
                    "key": "high_frequency_region",
                    "display_name": "High frequency region: {dominant_region}",
                    "description": "Trades concentrate in {dominant_region}.",
                    "all": [
                        {
                            "field": "dominant_region_trade_ratio",
                            "op": ">",
                            "value": 0.6,
                        }
                    ],
                }
            ],
        )

        self.assertEqual(labels[0]["key"], "high_frequency_region")
        self.assertEqual(labels[0]["display_name"], "High frequency region: Manila")
        self.assertEqual(labels[0]["description"], "Trades concentrate in Manila.")

    def test_strict_greater_than_boundary_does_not_match(self) -> None:
        labels = evaluate_labels(
            {"dominant_region_trade_ratio": 0.6},
            [
                {
                    "key": "high_frequency_region",
                    "display_name": "High frequency region",
                    "all": [
                        {
                            "field": "dominant_region_trade_ratio",
                            "op": ">",
                            "value": 0.6,
                        }
                    ],
                }
            ],
        )

        self.assertEqual(labels, [])

    def test_disabled_rules_do_not_match(self) -> None:
        labels = evaluate_labels(
            {"weather_notional_ratio": 1.0},
            [
                {
                    "key": "weather_specialist",
                    "display_name": "Weather specialist",
                    "enabled": False,
                    "all": [
                        {
                            "field": "weather_notional_ratio",
                            "op": ">=",
                            "value": 0.5,
                        }
                    ],
                }
            ],
        )

        self.assertEqual(labels, [])

    def test_new_rule_boundaries_match_requested_operators(self) -> None:
        labels = evaluate_labels(
            {
                "best_region_win_rate_region": "Manila",
                "best_region_positive_return_day_ratio": 0.6,
                "low_chip_cost_trade_ratio": 0.5,
            },
            [
                {
                    "key": "regional_high_win_rate",
                    "display_name": "High win rate: {best_region_win_rate_region}",
                    "all": [
                        {
                            "field": "best_region_positive_return_day_ratio",
                            "op": ">=",
                            "value": 0.6,
                        }
                    ],
                },
                {
                    "key": "lottery_player",
                    "display_name": "Lottery player",
                    "all": [
                        {
                            "field": "low_chip_cost_trade_ratio",
                            "op": ">",
                            "value": 0.5,
                        }
                    ],
                },
            ],
        )

        self.assertEqual([label["key"] for label in labels], ["regional_high_win_rate"])
        self.assertEqual(labels[0]["display_name"], "High win rate: Manila")

    def test_core_regional_high_win_rate_keeps_min_trade_guard_when_rule_is_overridden(self) -> None:
        evaluations = evaluate_label_evaluations(
            {
                "best_region_win_rate_region": "Guangzhou",
                "best_region_positive_return_days": 1,
                "best_region_total_trade_days": 1,
                "best_region_positive_return_day_ratio": 1.0,
                "best_region_trade_count": 2,
                "regional_day_win_rate_summary": {
                    "regions": [
                        {
                            "region": "Guangzhou",
                            "positive_return_days": 1,
                            "total_trade_days": 1,
                            "positive_return_day_ratio": 1.0,
                            "trade_count": 2,
                        }
                    ]
                },
            },
            [
                {
                    "key": "regional_high_win_rate",
                    "display_name": "High win rate: {best_region_win_rate_region}",
                    "all": [
                        {
                            "field": "best_region_positive_return_day_ratio",
                            "op": ">=",
                            "value": 0.6,
                        }
                    ],
                }
            ],
        )

        regional = next(item for item in evaluations if item["key"] == "regional_high_win_rate")
        self.assertFalse(regional["matched"])
        self.assertEqual(regional["facts"]["trade_count"], 2)
        self.assertEqual(regional["facts"]["min_trade_count"], 3)
        self.assertIn("地区交易 2 笔", regional["reason"])

    def test_split_player_label_requires_verified_metric(self) -> None:
        rules = [
            {
                "key": "split_player",
                "display_name": "Split player",
                "all": [
                    {
                        "field": "split_player_validation_passed",
                        "op": "==",
                        "value": True,
                    }
                ],
            }
        ]

        self.assertEqual(evaluate_labels({"split_player_validation_passed": False}, rules), [])
        labels = evaluate_labels({"split_player_validation_passed": True}, rules)
        self.assertEqual(labels[0]["key"], "split_player")

    def test_liquidity_and_activity_labels_match_declared_metrics(self) -> None:
        rules = [
            {
                "key": "liquidity_player",
                "display_name": "Liquidity player",
                "all": [
                    {
                        "field": "liquidity_player_matched",
                        "op": "==",
                        "value": True,
                    }
                ],
            },
            {
                "key": "normal_active",
                "display_name": "Active: normal",
                "all": [
                    {
                        "field": "activity_level",
                        "op": "==",
                        "value": "normal_active",
                    }
                ],
            },
            {
                "key": "low_active",
                "display_name": "Active: low",
                "all": [
                    {
                        "field": "activity_level",
                        "op": "==",
                        "value": "low_active",
                    }
                ],
            },
        ]

        labels = evaluate_labels(
            {"liquidity_player_matched": True, "activity_level": "low_active"},
            rules,
        )

        self.assertEqual([label["key"] for label in labels], ["liquidity_player", "low_active"])

    def test_new_wallet_and_early_positioning_labels_match_only_full_conditions(self) -> None:
        rules = [
            {
                "key": "new_wallet",
                "display_name": "New wallet",
                "all": [
                    {
                        "field": "new_wallet_matched",
                        "op": "==",
                        "value": True,
                    }
                ],
            },
            {
                "key": "hidden_expert_new_wallet",
                "display_name": "Hidden expert new wallet",
                "all": [
                    {
                        "field": "hidden_new_wallet_matched",
                        "op": "==",
                        "value": True,
                    }
                ],
            },
            {
                "key": "early_positioning",
                "display_name": "Early positioning",
                "all": [
                    {
                        "field": "high_temp_early_positioning_matched",
                        "op": "==",
                        "value": True,
                    }
                ],
            },
        ]

        labels = evaluate_labels(
            {
                "new_wallet_matched": True,
                "hidden_new_wallet_matched": False,
                "high_temp_early_positioning_matched": True,
            },
            rules,
        )
        self.assertEqual([label["key"] for label in labels], ["new_wallet", "early_positioning"])

        hidden_labels = evaluate_labels(
            {
                "new_wallet_matched": False,
                "hidden_new_wallet_matched": True,
                "high_temp_early_positioning_matched": False,
            },
            rules,
        )
        self.assertEqual([label["key"] for label in hidden_labels], ["hidden_expert_new_wallet"])

    def test_matched_labels_include_frontend_evidence(self) -> None:
        labels = evaluate_labels(
            {
                "dominant_region": "Manila",
                "dominant_region_trade_count": 7,
                "dominant_region_trade_ratio": 0.7,
                "trade_count": 10,
                "regional_trade_summary": {
                    "regions": [
                        {"region": "Manila", "trade_count": 7, "trade_ratio": 0.7}
                    ]
                },
            },
            [
                {
                    "key": "high_frequency_region",
                    "display_name": "High frequency region: {dominant_region}",
                    "all": [
                        {
                            "field": "dominant_region_trade_ratio",
                            "op": ">",
                            "value": 0.6,
                        }
                    ],
                }
            ],
        )

        evidence = labels[0]["evidence"]
        self.assertTrue(evidence["matched"])
        self.assertIn("7/10", evidence["reason"])
        self.assertEqual(evidence["details"]["region"], "Manila")
        self.assertEqual(evidence["details"]["city"], "Manila")
        self.assertEqual(evidence["details"]["numerator"], 7)
        self.assertEqual(evidence["details"]["denominator"], 10)
        self.assertEqual(evidence["details"]["conditions"][0]["actual"], 0.7)

    def test_lottery_and_early_positioning_evidence_exposes_top_records(self) -> None:
        labels = evaluate_labels(
            {
                "trade_count": 4,
                "low_chip_cost_trade_count": 3,
                "low_chip_cost_trade_ratio": 0.75,
                "low_chip_cost_summary": {
                    "threshold": 30,
                    "top_low_chip_region": "Austin",
                    "top_low_chip_region_count": 2,
                    "top_low_chip_region_ratio": 2 / 3,
                    "low_chip_records": [
                        {"region": "Austin", "date": "2026-04-20", "chip_cost": 12}
                    ],
                },
                "high_temp_off_day_buy_count": 2,
                "high_temp_analyzed_buy_count": 3,
                "high_temp_off_day_buy_ratio": 2 / 3,
                "high_temperature_early_entry_summary": {
                    "top_off_day_buy_records": [
                        {
                            "region": "NYC",
                            "buy_date": "2026-04-20",
                            "high_temperature_date": "2026-04-25",
                        }
                    ]
                },
                "high_temp_early_positioning_matched": True,
            },
            [
                {
                    "key": "lottery_player",
                    "display_name": "Lottery player",
                    "all": [
                        {
                            "field": "low_chip_cost_trade_ratio",
                            "op": ">",
                            "value": 0.5,
                        }
                    ],
                },
                {
                    "key": "early_positioning",
                    "display_name": "Early positioning",
                    "all": [
                        {
                            "field": "high_temp_early_positioning_matched",
                            "op": "==",
                            "value": True,
                        }
                    ],
                },
            ],
        )

        lottery_details = labels[0]["evidence"]["details"]
        self.assertEqual(lottery_details["top_low_chip_region"], "Austin")
        self.assertEqual(lottery_details["numerator"], 3)
        self.assertEqual(lottery_details["denominator"], 4)
        self.assertEqual(lottery_details["top_low_chip_records"][0]["chip_cost"], 12)

        early_details = labels[1]["evidence"]["details"]
        self.assertEqual(early_details["region"], "NYC")
        self.assertEqual(early_details["date"], "2026-04-25")
        self.assertEqual(early_details["top_off_day_records"][0]["buy_date"], "2026-04-20")

    def test_label_evidence_templates_can_render_city_date_multiple_and_reason(self) -> None:
        labels = evaluate_labels(
            {
                "max_region_daily_profit_region": "Shanghai",
                "max_region_daily_profit_date": "2026-04-13",
                "max_region_daily_profit_multiple": 64.9,
                "high_daily_region_profit_reason": "sell/buy notional exceeded threshold",
            },
            [
                {
                    "key": "high_daily_region_profit",
                    "display_name": "High burst: {max_region_daily_profit_region}",
                    "description": (
                        "city={max_region_daily_profit_region}; "
                        "date={max_region_daily_profit_date}; "
                        "multiple={max_region_daily_profit_multiple}; "
                        "reason={high_daily_region_profit_reason}"
                    ),
                    "all": [
                        {
                            "field": "max_region_daily_profit_multiple",
                            "op": ">",
                            "value": 2,
                        }
                    ],
                }
            ],
        )

        self.assertEqual(labels[0]["key"], "high_daily_region_profit")
        self.assertIn("city=Shanghai", labels[0]["description"])
        self.assertIn("date=2026-04-13", labels[0]["description"])
        self.assertIn("multiple=64.9", labels[0]["description"])
        self.assertIn("reason=sell/buy notional exceeded threshold", labels[0]["description"])

    def test_legacy_report_core_label_logic_remains_expressible(self) -> None:
        rules = [
            {
                "key": "high_frequency_region",
                "display_name": "High frequency region: {dominant_region}",
                "all": [{"field": "dominant_region_trade_ratio", "op": ">", "value": 0.6}],
            },
            {
                "key": "high_daily_region_profit",
                "display_name": "High burst: {max_region_daily_profit_region}",
                "all": [{"field": "max_region_daily_profit_multiple", "op": ">", "value": 2}],
            },
            {
                "key": "lottery_player",
                "display_name": "Lottery player",
                "all": [{"field": "low_chip_cost_trade_ratio", "op": ">", "value": 0.5}],
            },
            {
                "key": "split_player",
                "display_name": "Split player",
                "all": [{"field": "split_player_validation_passed", "op": "==", "value": True}],
            },
        ]

        tokyo_labels = evaluate_labels(
            {
                "dominant_region": "Tokyo",
                "dominant_region_trade_ratio": 0.792,
                "max_region_daily_profit_region": "Singapore",
                "max_region_daily_profit_multiple": 2.4,
                "low_chip_cost_trade_ratio": 0.166,
                "split_player_validation_passed": False,
            },
            rules,
        )
        self.assertEqual(
            [label["key"] for label in tokyo_labels],
            ["high_frequency_region", "high_daily_region_profit"],
        )
        self.assertEqual(tokyo_labels[0]["display_name"], "High frequency region: Tokyo")

        dtyb_labels = evaluate_labels(
            {
                "dominant_region_trade_ratio": 0.101,
                "max_region_daily_profit_region": "Shanghai",
                "max_region_daily_profit_multiple": 64.9,
                "low_chip_cost_trade_ratio": 0.967,
                "split_player_validation_passed": True,
            },
            rules,
        )
        self.assertEqual(
            [label["key"] for label in dtyb_labels],
            ["high_daily_region_profit", "lottery_player", "split_player"],
        )


if __name__ == "__main__":
    unittest.main()
