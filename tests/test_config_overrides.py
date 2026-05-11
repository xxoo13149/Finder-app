from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.config import apply_overrides


class ConfigOverrideTests(unittest.TestCase):
    def test_default_config_uses_requested_day_leaderboard_and_filter_ranges(self) -> None:
        config = json.loads((ROOT / "configs" / "default_config.json").read_text(encoding="utf-8"))
        label_keys = [str(rule.get("key")) for rule in config.get("labels", [])]

        self.assertEqual(config["leaderboard"]["time_period"], "DAY")
        self.assertTrue(config["leaderboard"]["auto_extend_to_target"])
        self.assertEqual(config["leaderboard"]["max_fetch_limit"], 300)
        self.assertEqual(config["wallet_filter"]["min_pnl"], 0.01)
        self.assertEqual(config["wallet_filter"]["max_pnl"], 200)
        self.assertEqual(config["wallet_filter"]["min_volume"], 0)
        self.assertEqual(config["wallet_filter"]["max_volume"], 40000)
        self.assertEqual(config["wallet_filter"]["min_traded_count"], 11)
        self.assertEqual(config["wallet_filter"]["max_traded_count"], 200)
        self.assertEqual(config["wallet_filter"]["min_weather_trade_ratio"], 0.5)
        self.assertEqual(config["weather"]["max_events"], 100000)
        self.assertEqual(
            config["analysis_modes"]["weekly_high_profit"]["weather"]["max_events"],
            100000,
        )
        self.assertEqual(config["analysis"]["regional_frequency_min_day_ratio"], 0.4)
        self.assertEqual(config["analysis"]["regional_win_rate_min_trade_count"], 3)
        self.assertTrue(config["analysis"]["lightweight_batch_cleanup_enabled"])
        self.assertTrue(config["analysis"]["gc_after_wallet_batch"])
        self.assertEqual(config["analysis"]["finder_ai_concurrency"], 2)
        self.assertEqual(config["analysis"]["falcon_metrics_concurrency"], 2)
        self.assertEqual(config["analysis"]["wallet_screening_lookahead_multiplier"], 3)
        self.assertFalse(config["history_registry"]["replicate_to_cloudflare"])
        self.assertFalse(config["history_ledger"]["replicate_to_cloudflare"])
        self.assertFalse(config["history_ledger"]["compact_gap_payloads_after_batch"])
        self.assertTrue(config["history_ledger"]["compact_gap_payloads_after_run"])
        self.assertFalse(config["history_ledger"]["persist_screening_snapshots"])
        self.assertEqual(
            label_keys,
            [
                "normal_active",
                "low_active",
                "new_wallet",
                "hidden_expert_new_wallet",
                "early_positioning",
            ],
        )

    def test_apply_overrides_supports_runtime_controls(self) -> None:
        config = {
            "api": {"use_cache": True},
            "analysis": {"concurrent_wallets": 4},
            "leaderboard": {"fetch_limit": 100},
            "pagination": {"max_offset": 10000},
            "wallet_filter": {"target_count": 10},
            "weather": {"max_events": 1000},
        }

        updated = apply_overrides(
            config,
            min_pnl=1.25,
            max_pnl=150.0,
            min_volume=500.0,
            max_volume=20000.0,
            min_traded_count=12,
            max_traded_count=88,
            min_weather_trade_ratio=0.65,
            max_weather_events=25,
            max_fetch_limit=250,
            max_wallet_offset=500,
            concurrent_wallets=2,
            verbose=True,
            enable_chain_validation=True,
            chain_api_key_env="TEST_POLYGONSCAN_KEY",
        )

        self.assertEqual(updated["weather"]["max_events"], 25)
        self.assertEqual(updated["pagination"]["max_offset"], 500)
        self.assertEqual(updated["analysis"]["concurrent_wallets"], 2)
        self.assertEqual(updated["wallet_filter"]["min_pnl"], 1.25)
        self.assertEqual(updated["wallet_filter"]["max_pnl"], 150.0)
        self.assertEqual(updated["wallet_filter"]["min_volume"], 500.0)
        self.assertEqual(updated["wallet_filter"]["max_volume"], 20000.0)
        self.assertEqual(updated["wallet_filter"]["min_traded_count"], 12)
        self.assertEqual(updated["wallet_filter"]["max_traded_count"], 88)
        self.assertEqual(updated["wallet_filter"]["min_weather_trade_ratio"], 0.65)
        self.assertEqual(updated["leaderboard"]["max_fetch_limit"], 250)
        self.assertTrue(updated["runtime"]["verbose"])
        self.assertTrue(updated["chain_validation"]["enabled"])
        self.assertEqual(updated["chain_validation"]["api_key_envs"], ["TEST_POLYGONSCAN_KEY"])
        self.assertEqual(config["weather"]["max_events"], 1000)
        self.assertEqual(config["wallet_filter"], {"target_count": 10})
        self.assertNotIn("runtime", config)


if __name__ == "__main__":
    unittest.main()
