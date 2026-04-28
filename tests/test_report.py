from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.report import build_report, format_trade


def report_config() -> dict[str, Any]:
    return {
        "wallet_filter": {
            "target_count": 1,
            "min_pnl": 0.01,
            "max_pnl": 200,
            "max_volume": 40000,
            "min_traded_count": 11,
            "max_traded_count": 99,
            "min_weather_trade_ratio": 0.5,
        },
        "analysis": {
            "regional_frequency_min_day_ratio": 0.4,
            "regional_win_rate_min_trade_count": 3,
            "concurrent_wallets": 4,
        },
        "leaderboard": {"category": "WEATHER", "time_period": "DAY", "order_by": "PNL", "fetch_limit": 100},
        "weather": {"max_events": 1000},
        "chain_validation": {"enabled": False},
    }


def base_metrics() -> dict[str, Any]:
    return {
        "leaderboard_pnl": 120.5,
        "leaderboard_volume": 4_500,
        "trade_count": 8,
        "weather_trade_ratio": 0.625,
        "weather_notional_ratio": 0.5,
        "distinct_event_count": 3,
        "trades_per_active_day": 1.6,
        "median_trade_notional": 42,
        "closed_position_win_rate": 0.25,
        "reward_total_usdc": 3.5,
        "dominant_region": "NYC",
        "dominant_region_trade_ratio": 0.75,
        "best_region_trade_count": 3,
        "audit_profit_summary": {
            "trade_liquidity_profit": 18.0,
            "trade_liquidity_profit_multiple": 1.18,
            "final_settlement_profit": 30.0,
            "final_settlement_profit_multiple": 1.30,
            "unified_profit": 48.0,
            "unified_profit_multiple": 1.24,
        },
    }


def wallet_result(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "wallet": "0xabc",
        "leaderboard_entry": {
            "userName": "weather-pro",
            "xUsername": None,
            "rank": 7,
        },
        "selection_record": {
            "wallet": "0xabc",
            "user_name": "weather-pro",
            "pnl": 120.5,
            "volume": 4_500,
            "main_region": "NYC",
            "highest_burst": 2.5,
            "recent_evidence_date": "2026-04-21",
        },
        "metrics": metrics,
        "labels": [{"key": "high_frequency_region", "display_name": "High frequency region"}],
        "label_evaluations": [
            {
                "key": "high_frequency_region",
                "display_name": "High frequency region: NYC",
                "matched": True,
                "reason": "NYC accounts for 6/8 trades.",
                "facts": {
                    "city": "NYC",
                    "ratio": 0.75,
                    "numerator": 6,
                    "denominator": 8,
                    "count_mode": "region_day",
                    "raw_trade_count": 8,
                    "conditions": [
                        {
                            "field": "dominant_region_trade_ratio",
                            "op": ">=",
                            "value": 0.4,
                            "actual": 0.75,
                            "matched": True,
                        }
                    ],
                },
                "records": [
                    {
                        "type": "evidence",
                        "source": "regional_trade_summary.regions",
                        "region": "NYC",
                        "date": "2026-04-21",
                        "trade_count": 6,
                    }
                ],
            },
            {
                "key": "high_daily_region_profit",
                "display_name": "High burst",
                "matched": False,
                "reason": "Best city-day has sell/buy multiple 1.00x, below the core burst threshold.",
                "facts": {
                    "city": "NYC",
                    "date": "2026-04-21",
                    "multiple": 1.0,
                    "buy_amount": 100,
                    "sell_amount": 100,
                },
                "records": [{"type": "counterevidence", "source": "metrics"}],
            },
            {
                "key": "regional_high_win_rate",
                "display_name": "Regional high win rate",
                "matched": False,
                "reason": "NYC has 1/3 positive-return days (33.33%), below the core win-rate threshold.",
                "facts": {
                    "city": "NYC",
                    "ratio": 1 / 3,
                    "numerator": 1,
                    "denominator": 3,
                },
                "records": [{"type": "counterevidence", "source": "metrics"}],
            },
            {
                "key": "lottery_player",
                "display_name": "Lottery player",
                "matched": False,
                "reason": "Low-chip trades are 1/8 (12.50%), below the core lottery threshold.",
                "facts": {
                    "ratio": 0.125,
                    "numerator": 1,
                    "denominator": 8,
                },
                "records": [{"type": "counterevidence", "source": "metrics"}],
            },
            {
                "key": "split_player",
                "display_name": "Split player",
                "matched": False,
                "reason": "chain validation disabled.",
                "facts": {
                    "average_chip_cost": 0,
                    "numerator": 0,
                    "denominator": 2,
                },
                "records": [{"type": "counterevidence", "source": "metrics"}],
            },
            {
                "key": "liquidity_player",
                "display_name": "Liquidity player",
                "matched": False,
                "reason": "Swap ratio 20.00%; sell-dominant city-days 0/2 (0.00%), below the core liquidity threshold.",
                "facts": {
                    "city": "NYC",
                    "date": "2026-04-21",
                    "swap_ratio": 0.2,
                    "ratio": 0,
                    "numerator": 0,
                    "denominator": 2,
                },
                "records": [{"type": "counterevidence", "source": "metrics"}],
            },
        ],
        "operation_audit": {
            "complete": False,
            "collection_status": {
                "trades": {"stop_reason": "max_offset_reached"},
                "activity": {"stop_reason": "last_page_partial"},
            },
            "profit_summary": metrics["audit_profit_summary"],
        },
        "strategy_notes": ["Insufficient deep history for a stronger read."],
        "profile": {
            "average_buy_price": {
                "weighted_average_price": 0.42,
                "priced_buy_count": 3,
            },
            "city_distribution": {
                "cities": [
                    {
                        "city": "NYC",
                        "trade_count": 6,
                        "positive_return_days": 2,
                        "total_trade_days": 3,
                        "positive_return_day_ratio": 2 / 3,
                        "buy_amount": 100,
                        "sell_amount": 180,
                        "net_trade_cashflow": 80,
                        "realized_pnl": 30,
                    }
                ]
            },
            "top_cities": {
                "by_buy_amount": [{"city": "NYC", "buy_amount": 100}],
                "by_realized_pnl": [{"city": "NYC", "realized_pnl": 30}],
            },
            "buy_price_distribution": {
                "buckets": [{"min": 0.25, "max": 0.5, "count": 3}]
            },
            "closed_position_pnl": {
                "total_realized_pnl": 30,
                "win_rate": 0.5,
                "profit_multiple": 1.3,
            },
        },
        "top_trades": [
            {
                "side": "BUY",
                "title": "Will it rain tomorrow?",
                "outcome": "Yes",
                "size": 50,
                "price": 0.25,
            }
        ],
        "top_positions": [],
        "top_closed_positions": [],
    }


class ReportTests(unittest.TestCase):
    def test_format_trade_uses_size_price_when_usdc_size_is_missing(self) -> None:
        line = format_trade(
            {
                "side": "BUY",
                "title": "Will it snow?",
                "outcome": "No",
                "size": 123.45,
                "price": 0.4,
            }
        )

        self.assertEqual(line, "BUY | Will it snow? | No | $49.38 USDC")

    def test_build_report_renders_sections_and_dynamic_rule_sources(self) -> None:
        report = build_report(
            config=report_config(),
            leaderboard=[{"wallet": "0xabc"}],
            weather_events=[],
            wallet_results=[wallet_result(base_metrics())],
            errors=[],
        )

        self.assertIn("天气赛道交易占比下限：50.0%", report)
        self.assertIn("高频地区命中阈值：40.0%（底层按地区-日期样本统计）", report)
        self.assertIn("地区高胜率最小交易笔数：3", report)
        self.assertIn("按天气赛道的地区-日期样本统计；主地区占比 >= 0.4", report)
        self.assertIn("regional_daily_profit_summary.region_days", report)
        self.assertIn("liquidity_player_summary.region_days", report)
        self.assertIn("split_position_average_cost_summary + chain_validation.evidence", report)
        self.assertIn("NYC accounts for 6/8 trades.", report)
        self.assertIn("Best city-day has sell/buy multiple 1.00x", report)
        self.assertIn("Will it rain tomorrow? | Yes | $12.50 USDC", report)
        self.assertIn("| 地区 | 交易数 | 胜率 |", report)
        self.assertIn("66.7% (2/3)", report)

    def test_build_report_includes_audit_block(self) -> None:
        report = build_report(
            config=report_config(),
            leaderboard=[{"wallet": "0xabc"}],
            weather_events=[],
            wallet_results=[wallet_result(base_metrics())],
            errors=[],
        )

        self.assertIn("18.00", report)
        self.assertIn("30.00", report)
        self.assertIn("48.00", report)
        self.assertIn("max_offset_reached", report)
        self.assertIn("last_page_partial", report)


if __name__ == "__main__":
    unittest.main()
