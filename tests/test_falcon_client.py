from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.falcon_client import (
    FALCON_LIFETIME_AGENT_ID,
    FALCON_PROFIT_AND_LOSS_AGENT_ID,
    FALCON_WALLET_360_AGENT_ID,
    falcon_display_metrics_for_wallet,
)


class FalconClientTests(unittest.TestCase):
    def test_display_metrics_fetches_falcon_sources_concurrently(self) -> None:
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        started_agent_ids: list[int] = []
        paginations_by_agent_id: dict[int, dict[str, object]] = {}

        def fake_falcon_post(**kwargs: object) -> dict[str, object]:
            agent_id = int(kwargs["agent_id"])
            with lock:
                started_agent_ids.append(agent_id)
                paginations_by_agent_id[agent_id] = dict(kwargs.get("pagination") or {})
            barrier.wait(timeout=1)
            if agent_id == FALCON_LIFETIME_AGENT_ID:
                return {
                    "data": {
                        "results": [
                            {
                                "total_pnl": "123.45",
                                "roi_pct": "0.67",
                                "total_trades": "10",
                                "total_invested": "500",
                                "last_updated": "2026-05-10",
                            }
                        ]
                    }
                }
            if agent_id == FALCON_WALLET_360_AGENT_ID:
                return {
                    "data": {
                        "results": [
                            {
                                "win_rate": "0.8",
                                "winning_trades": "8",
                                "losing_trades": "2",
                                "date_range_end": "2026-05-10",
                            }
                        ]
                    }
                }
            if agent_id == FALCON_PROFIT_AND_LOSS_AGENT_ID:
                return {"data": {"results": [{"wins": "7", "losses": "3"}]}}
            raise AssertionError(f"unexpected agent id {agent_id}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "falcon": {
                    "enabled": True,
                    "token_env": "FALCON_TEST_TOKEN",
                    "cache_dir": temp_dir,
                }
            }
            with patch.dict(os.environ, {"FALCON_TEST_TOKEN": "token"}):
                with patch(
                    "polymarket_weather_tool.falcon_client.falcon_post",
                    side_effect=fake_falcon_post,
                ):
                    metrics = falcon_display_metrics_for_wallet(
                        "0xABC",
                        config=config,
                        now_date="2026-05-10",
                    )

        self.assertEqual(
            set(started_agent_ids),
            {
                FALCON_LIFETIME_AGENT_ID,
                FALCON_WALLET_360_AGENT_ID,
                FALCON_PROFIT_AND_LOSS_AGENT_ID,
            },
        )
        self.assertEqual(metrics["wallet"], "0xabc")
        self.assertEqual(metrics["total_pnl"], 123.45)
        self.assertEqual(metrics["total_roi"], 0.67)
        self.assertEqual(metrics["win_rate"], 0.8)
        self.assertEqual(metrics["total_trades"], 10)
        self.assertEqual(metrics["total_invested"], 500.0)
        self.assertEqual(metrics["wins"], 8)
        self.assertEqual(metrics["losses"], 2)
        self.assertEqual(
            paginations_by_agent_id[FALCON_WALLET_360_AGENT_ID],
            {"limit": 5, "offset": 0},
        )


if __name__ == "__main__":
    unittest.main()
