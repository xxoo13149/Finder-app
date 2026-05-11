from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.analysis import (
    DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
    POSITIONS_CONVERTED_TOPIC0,
    WeatherIndex,
    address_to_topic,
    analyze_wallet,
    build_analysis_summary,
    fetch_optional_chain_validation,
    fetch_wallet_snapshot,
    normalize_positions_converted_logs,
)


WALLET = "0xabc1230000000000000000000000000000000000"


def audit_scaffold_config() -> dict[str, Any]:
    return {
        "analysis": {
            "concurrent_wallets": 1,
            "current_datetime": "2026-04-27T00:00:00+00:00",
            "hidden_new_wallet_days": 10,
            "long_dated_threshold_days": 90,
            "new_wallet_days": 60,
            "normal_active_days": 1,
            "position_size_threshold": 0.1,
            "recent_active_days": 3,
            "top_closed_positions_in_report": 3,
            "top_positions_in_report": 3,
            "top_trades_in_report": 3,
        },
        "chain_validation": {"enabled": False},
        "labels": [],
        "pagination": {"page_size": 10, "max_offset": 0},
        "wallet_filter": {
            "exclude_wallets": [],
            "include_wallets": [],
            "min_pnl": 0,
            "min_traded_count": 0,
            "min_volume": 0,
            "target_count": 1,
        },
    }


class SnapshotFixtureClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        return [
            {"type": "TRADE", "usdcSize": "5"},
            {"type": "REWARD", "usdcSize": "1.25"},
            {"type": "YIELD", "usdcSize": "0.75"},
        ]

    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {
                "conditionId": "cond-open",
                "currentValue": "25",
                "avgPrice": "0.40",
                "size": "50",
                "endDate": "2026-06-01T00:00:00Z",
            }
        ]
        return records[offset : offset + limit]

    def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("closed_positions", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {
                "conditionId": "cond-closed",
                "realizedPnl": "3",
                "totalBought": "10",
                "endDate": "2026-04-30T00:00:00Z",
            }
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


class PartitionRecoverySnapshotClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.activity_records = [
            {
                "type": "TRADE",
                "timestamp": 300,
                "transactionHash": "0xt2",
                "conditionId": "cond-b",
                "eventSlug": "sun-in-la",
                "slug": "sun-in-la-yes",
                "asset": "asset-b",
                "side": "SELL",
                "size": "5",
                "price": "0.70",
                "usdcSize": "3.5",
                "title": "LA sun",
            },
            {
                "type": "REWARD",
                "timestamp": 200,
                "transactionHash": "0xr1",
                "conditionId": "cond-r",
                "eventSlug": "reward-event",
                "slug": "reward-event",
                "usdcSize": "1.25",
                "title": "Reward",
            },
            {
                "type": "TRADE",
                "timestamp": 100,
                "transactionHash": "0xt1",
                "conditionId": "cond-a",
                "eventSlug": "rain-in-nyc",
                "slug": "rain-in-nyc-yes",
                "asset": "asset-a",
                "side": "BUY",
                "size": "10",
                "price": "0.40",
                "usdcSize": "4",
                "title": "NYC rain",
            },
        ]
        self.trade_records = [
            dict(record)
            for record in self.activity_records
            if str(record.get("type") or "").upper() == "TRADE"
        ]

    def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("activity", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        activity_type = str(kwargs.get("activity_type") or "").upper()
        start = kwargs.get("start")
        end = kwargs.get("end")
        records = self.trade_records if activity_type == "TRADE" else self.activity_records
        if start is None and end is None:
            if offset == 0:
                return [dict(records[0])]
            raise_terminal_http_400("activity", limit=limit, offset=offset)
        filtered = [
            dict(record)
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
            return [dict(self.trade_records[0])]
        raise_terminal_http_400("trades", limit=limit, offset=offset)

    def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("positions", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {
                "conditionId": "cond-open",
                "currentValue": "25",
                "avgPrice": "0.40",
                "size": "50",
                "endDate": "2026-06-01T00:00:00Z",
            }
        ]
        return records[offset : offset + limit]

    def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("closed_positions", kwargs))
        limit = int(kwargs["limit"])
        offset = int(kwargs["offset"])
        records = [
            {
                "conditionId": "cond-closed",
                "realizedPnl": "3",
                "totalBought": "10",
                "endDate": "2026-04-30T00:00:00Z",
            }
        ]
        return records[offset : offset + limit]


class AuditUpgradeScaffoldTests(unittest.TestCase):
    def assertHasKeys(self, mapping: dict[str, Any], keys: set[str]) -> None:
        self.assertTrue(keys.issubset(mapping), msg=f"missing keys: {sorted(keys - set(mapping))}")

    def assertSnapshotCoreShape(self, snapshot: dict[str, Any]) -> None:
        self.assertEqual(snapshot["wallet"], WALLET)
        for key in ("activity", "trades", "rewards", "positions", "closed_positions"):
            with self.subTest(section=key):
                self.assertIn(key, snapshot)
                self.assertIsInstance(snapshot[key], list)
        self.assertIn("chain_validation", snapshot)
        self.assertIsInstance(snapshot["chain_validation"], dict)
        self.assertHasKeys(
            snapshot["chain_validation"],
            {
                "status",
                "reason",
                "split_evidence_count",
                "evidence",
            },
        )

    def assertChainValidationShape(self, result: dict[str, Any]) -> None:
        self.assertHasKeys(
            result,
            {
                "status",
                "reason",
                "first_transaction_datetime",
                "first_transaction_hash",
                "split_evidence_count",
                "evidence",
            },
        )
        self.assertIsInstance(result["evidence"], list)

    def test_fetch_wallet_snapshot_preserves_prefetched_trades_and_core_sections(self) -> None:
        client = SnapshotFixtureClient()
        prefetched_trades = [
            {
                "asset": "rain-yes",
                "conditionId": "cond-open",
                "eventSlug": "rain-in-nyc",
                "price": "0.40",
                "side": "BUY",
                "size": "50",
                "timestamp": 1_777_000_000,
                "usdcSize": "20",
            }
        ]

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            WALLET,
            audit_scaffold_config(),
            prefetched_trades=prefetched_trades,
        )

        self.assertSnapshotCoreShape(snapshot)
        self.assertEqual(snapshot["trades"], prefetched_trades)
        self.assertEqual([record["type"] for record in snapshot["rewards"]], ["REWARD", "YIELD"])
        self.assertEqual([name for name, _kwargs in client.calls], ["activity", "positions", "closed_positions"])
        self.assertNotIn("size_threshold", client.calls[1][1])

    def test_fetch_wallet_snapshot_continues_full_activity_from_prefetched_page(self) -> None:
        class PrefetchedActivityContinuationClient(SnapshotFixtureClient):
            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("activity", kwargs))
                limit = int(kwargs["limit"])
                offset = int(kwargs["offset"])
                if offset == 2:
                    return [
                        {
                            "type": "TRADE",
                            "timestamp": 100,
                            "transactionHash": "0xt2",
                            "conditionId": "cond-older",
                            "eventSlug": "rain-in-nyc",
                            "slug": "rain-in-nyc-no",
                            "asset": "asset-older",
                            "side": "SELL",
                            "size": "7",
                            "price": "0.55",
                            "usdcSize": "3.85",
                            "title": "NYC rain older",
                        }
                    ][:limit]
                raise AssertionError(f"unexpected offset {offset}")

        client = PrefetchedActivityContinuationClient()
        config = audit_scaffold_config()
        config["pagination"] = {"page_size": 2, "max_offset": 10}

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            WALLET,
            config,
            prefetched_activity_page={
                "records": [
                    {
                        "type": "TRADE",
                        "timestamp": 300,
                        "transactionHash": "0xt1",
                        "conditionId": "cond-recent",
                        "eventSlug": "rain-in-nyc",
                        "slug": "rain-in-nyc-yes",
                        "asset": "asset-recent",
                        "side": "BUY",
                        "size": "10",
                        "price": "0.40",
                        "usdcSize": "4",
                        "title": "NYC rain recent",
                    },
                    {
                        "type": "REWARD",
                        "timestamp": 200,
                        "transactionHash": "0xr1",
                        "conditionId": "cond-reward",
                        "eventSlug": "reward-event",
                        "slug": "reward-event",
                        "usdcSize": "1.25",
                        "title": "Reward",
                    },
                ],
                "page_count": 1,
                "next_offset": 2,
            },
        )

        self.assertSnapshotCoreShape(snapshot)
        self.assertEqual(
            [kwargs["offset"] for name, kwargs in client.calls if name == "activity"],
            [2],
        )
        self.assertEqual(len(snapshot["activity"]), 3)
        self.assertEqual(
            [record["transactionHash"] for record in snapshot["trades"]],
            ["0xt1", "0xt2"],
        )
        self.assertEqual([record["type"] for record in snapshot["rewards"]], ["REWARD"])
        self.assertEqual(snapshot["collection_status"]["activity"]["page_count"], 2)
        self.assertEqual(snapshot["collection_status"]["activity"]["record_count"], 3)
        self.assertEqual(snapshot["collection_status"]["activity"]["collection_mode"], "aggregate")

    def test_fetch_wallet_snapshot_continues_full_trades_from_prefetched_page(self) -> None:
        class PrefetchedTradesContinuationClient(SnapshotFixtureClient):
            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("activity", kwargs))
                limit = int(kwargs["limit"])
                offset = int(kwargs["offset"])
                records = [
                    {"type": "REWARD", "usdcSize": "1.25"},
                    {"type": "YIELD", "usdcSize": "0.75"},
                ]
                return records[offset : offset + limit]

            def fetch_trades_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                self.calls.append(("trades", kwargs))
                limit = int(kwargs["limit"])
                offset = int(kwargs["offset"])
                if offset == 2:
                    return [
                        {
                            "timestamp": 100,
                            "transactionHash": "0xt3",
                            "conditionId": "cond-older",
                            "eventSlug": "rain-in-nyc",
                            "slug": "rain-in-nyc-no",
                            "asset": "asset-older",
                            "side": "SELL",
                            "size": "7",
                            "price": "0.55",
                            "usdcSize": "3.85",
                            "title": "NYC rain older",
                        }
                    ][:limit]
                raise AssertionError(f"unexpected offset {offset}")

        client = PrefetchedTradesContinuationClient()
        config = audit_scaffold_config()
        config["pagination"] = {"page_size": 2, "max_offset": 10}

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            WALLET,
            config,
            prefetched_trades_page={
                "records": [
                    {
                        "timestamp": 300,
                        "transactionHash": "0xt1",
                        "conditionId": "cond-recent",
                        "eventSlug": "rain-in-nyc",
                        "slug": "rain-in-nyc-yes",
                        "asset": "asset-recent",
                        "side": "BUY",
                        "size": "10",
                        "price": "0.40",
                        "usdcSize": "4",
                        "title": "NYC rain recent",
                    },
                    {
                        "timestamp": 200,
                        "transactionHash": "0xt2",
                        "conditionId": "cond-mid",
                        "eventSlug": "rain-in-nyc",
                        "slug": "rain-in-nyc-flat",
                        "asset": "asset-mid",
                        "side": "BUY",
                        "size": "8",
                        "price": "0.45",
                        "usdcSize": "3.6",
                        "title": "NYC rain mid",
                    },
                ],
                "page_count": 1,
                "next_offset": 2,
            },
        )

        self.assertSnapshotCoreShape(snapshot)
        self.assertEqual(
            [kwargs["offset"] for name, kwargs in client.calls if name == "trades"],
            [2],
        )
        self.assertEqual(
            [record["transactionHash"] for record in snapshot["trades"]],
            ["0xt1", "0xt2", "0xt3"],
        )
        self.assertEqual(snapshot["collection_status"]["trades"]["page_count"], 2)
        self.assertEqual(snapshot["collection_status"]["trades"]["record_count"], 3)
        self.assertEqual(snapshot["collection_status"]["trades"]["collection_mode"], "aggregate")
        self.assertEqual([record["type"] for record in snapshot["rewards"]], ["REWARD", "YIELD"])

    def test_fetch_wallet_snapshot_fetches_auxiliary_pages_concurrently(self) -> None:
        barrier = threading.Barrier(2)

        class ConcurrentAuxiliaryPagesClient(SnapshotFixtureClient):
            def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                barrier.wait(timeout=1)
                return super().fetch_positions_page(**kwargs)

            def fetch_closed_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                barrier.wait(timeout=1)
                return super().fetch_closed_positions_page(**kwargs)

        client = ConcurrentAuxiliaryPagesClient()

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            WALLET,
            audit_scaffold_config(),
        )

        self.assertSnapshotCoreShape(snapshot)
        call_names = [name for name, _kwargs in client.calls]
        self.assertIn("positions", call_names)
        self.assertIn("closed_positions", call_names)

    def test_fetch_wallet_snapshot_overlaps_activity_and_auxiliary_pages(self) -> None:
        barrier = threading.Barrier(2)

        class ConcurrentActivityAuxiliaryClient(SnapshotFixtureClient):
            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                if kwargs.get("start") is None and not kwargs.get("activity_type"):
                    barrier.wait(timeout=1)
                return super().fetch_activity_page(**kwargs)

            def fetch_positions_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                barrier.wait(timeout=1)
                return super().fetch_positions_page(**kwargs)

        client = ConcurrentActivityAuxiliaryClient()

        snapshot = fetch_wallet_snapshot(
            client,  # type: ignore[arg-type]
            WALLET,
            audit_scaffold_config(),
        )

        self.assertSnapshotCoreShape(snapshot)
        call_names = [name for name, _kwargs in client.calls]
        self.assertIn("activity", call_names)
        self.assertIn("positions", call_names)

    def test_fetch_wallet_snapshot_overlaps_chain_validation_with_full_snapshot_fetches(self) -> None:
        barrier = threading.Barrier(2)
        chain_started = threading.Event()

        class ConcurrentChainValidationClient(SnapshotFixtureClient):
            def fetch_activity_page(self, **kwargs: Any) -> list[dict[str, Any]]:
                if kwargs.get("start") is None and not kwargs.get("activity_type"):
                    barrier.wait(timeout=1)
                return super().fetch_activity_page(**kwargs)

        def fake_chain_validation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            chain_started.set()
            barrier.wait(timeout=1)
            return {"status": "verified", "reason": "parallel chain validation"}

        client = ConcurrentChainValidationClient()
        config = audit_scaffold_config()
        config["chain_validation"] = {"enabled": True}

        with patch(
            "polymarket_weather_tool.analysis.fetch_optional_chain_validation",
            side_effect=fake_chain_validation,
        ):
            snapshot = fetch_wallet_snapshot(
                client,  # type: ignore[arg-type]
                WALLET,
                config,
            )

        self.assertTrue(chain_started.is_set())
        self.assertEqual(snapshot["chain_validation"]["status"], "verified")

    def test_fetch_wallet_snapshot_recovers_activity_and_trades_from_time_partitions(self) -> None:
        client = PartitionRecoverySnapshotClient()
        config = audit_scaffold_config()
        config["pagination"] = {"page_size": 1, "max_offset": 10}

        with patch("polymarket_weather_tool.analysis.current_partition_end_epoch", return_value=400):
            snapshot = fetch_wallet_snapshot(
                client,  # type: ignore[arg-type]
                WALLET,
                config,
            )

        self.assertSnapshotCoreShape(snapshot)
        self.assertEqual(
            [record["transactionHash"] for record in snapshot["trades"]],
            ["0xt2", "0xt1"],
        )
        self.assertEqual([record["type"] for record in snapshot["rewards"]], ["REWARD"])
        self.assertTrue(snapshot["collection_status"]["activity"]["complete"])
        self.assertTrue(snapshot["collection_status"]["trades"]["complete"])
        self.assertEqual(
            snapshot["collection_status"]["activity"]["collection_mode"],
            "partition_recovery",
        )
        self.assertEqual(
            snapshot["collection_status"]["trades"]["collection_mode"],
            "activity_projection",
        )
        self.assertEqual(
            snapshot["collection_status"]["trades"]["stop_reason"],
            "projected_from_activity",
        )
        self.assertTrue(snapshot["operation_audit"]["complete"])
        self.assertFalse(
            any(name == "trades" for name, _kwargs in client.calls)
        )
        self.assertFalse(
            any(
                name == "activity" and str(kwargs.get("activity_type") or "").upper() == "TRADE"
                for name, kwargs in client.calls
            )
        )

    def test_chain_validation_empty_states_keep_audit_ready_shape(self) -> None:
        disabled = fetch_optional_chain_validation(
            object(),  # type: ignore[arg-type]
            WALLET,
            {"chain_validation": {"enabled": False}},
        )
        missing_api_key = fetch_optional_chain_validation(
            object(),  # type: ignore[arg-type]
            WALLET,
            {"chain_validation": {"enabled": True}},
        )

        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(missing_api_key["status"], "missing_api_key")
        self.assertChainValidationShape(disabled)
        self.assertChainValidationShape(missing_api_key)

    def test_normalize_positions_converted_logs_filters_non_matching_records(self) -> None:
        valid_topic1 = address_to_topic(WALLET)
        logs = [
            {
                "address": DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
                "topics": [
                    POSITIONS_CONVERTED_TOPIC0,
                    valid_topic1,
                    "0x" + "ab" * 32,
                    hex(7),
                ],
                "data": hex(500),
                "transactionHash": "0xmatch",
                "blockNumber": hex(123),
                "timeStamp": hex(1_700_000_000),
                "logIndex": hex(1),
            },
            {
                "address": "0x0000000000000000000000000000000000000001",
                "topics": [POSITIONS_CONVERTED_TOPIC0, valid_topic1, "0x" + "ab" * 32, hex(8)],
                "data": hex(500),
            },
            {
                "address": DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
                "topics": ["0xdeadbeef", valid_topic1, "0x" + "ab" * 32, hex(9)],
                "data": hex(500),
            },
            {
                "address": DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
                "topics": [
                    POSITIONS_CONVERTED_TOPIC0,
                    address_to_topic("0xdef4560000000000000000000000000000000000"),
                    "0x" + "ab" * 32,
                    hex(10),
                ],
                "data": hex(500),
            },
            {
                "address": DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
                "topics": [POSITIONS_CONVERTED_TOPIC0, valid_topic1],
                "data": hex(500),
            },
        ]

        evidence = normalize_positions_converted_logs(
            logs,
            WALLET,
            DEFAULT_NEG_RISK_ADAPTER_ADDRESS,
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["transaction_hash"], "0xmatch")
        self.assertEqual(evidence[0]["index_set"], 7)
        self.assertHasKeys(
            evidence[0],
            {
                "transaction_hash",
                "block_number",
                "timestamp",
                "stakeholder",
                "market_id",
                "index_set",
                "amount",
                "log_index",
            },
        )

    def test_analyze_wallet_exposes_raw_snapshot_counts_for_audit(self) -> None:
        snapshot = {
            "wallet": WALLET,
            "activity": [{"type": "REWARD", "usdcSize": "1.25"}],
            "trades": [
                {
                    "asset": "rain-yes",
                    "conditionId": "cond-open",
                    "eventSlug": "rain-in-nyc",
                    "price": "0.40",
                    "side": "BUY",
                    "size": "50",
                    "timestamp": 1_777_000_000,
                    "usdcSize": "20",
                }
            ],
            "rewards": [{"type": "REWARD", "usdcSize": "1.25"}],
            "positions": [
                {
                    "conditionId": "cond-open",
                    "currentValue": "25",
                    "avgPrice": "0.40",
                    "size": "50",
                    "endDate": "2026-06-01T00:00:00Z",
                }
            ],
            "closed_positions": [
                {
                    "conditionId": "cond-closed",
                    "realizedPnl": "3",
                    "totalBought": "10",
                    "endDate": "2026-04-30T00:00:00Z",
                }
            ],
        }

        wallet_result = analyze_wallet(
            wallet=WALLET,
            leaderboard_entry={"proxyWallet": WALLET, "pnl": "10", "vol": "50"},
            snapshot=snapshot,
            weather_index=WeatherIndex(set(), set(), set(), set(), {}),
            config=audit_scaffold_config(),
        )

        self.assertEqual(
            wallet_result["raw_counts"],
            {
                "activity_count": 1,
                "trade_count": 1,
                "reward_count": 1,
                "position_count": 1,
                "closed_position_count": 1,
                "operation_record_count": 2,
            },
        )

    def test_analyze_wallet_snapshot_audit_block_is_reserved_for_mainline_upgrade(self) -> None:
        snapshot = {
            "wallet": WALLET,
            "activity": [],
            "trades": [],
            "rewards": [],
            "positions": [],
            "closed_positions": [],
        }
        wallet_result = analyze_wallet(
            wallet=WALLET,
            leaderboard_entry={"proxyWallet": WALLET, "pnl": "0", "vol": "0"},
            snapshot=snapshot,
            weather_index=WeatherIndex(set(), set(), set(), set(), {}),
            config=audit_scaffold_config(),
        )

        snapshot_audit = wallet_result.get("snapshot_audit")
        if not isinstance(snapshot_audit, dict):
            self.skipTest("waiting for mainline snapshot audit block")

        self.assertHasKeys(
            snapshot_audit,
            {
                "status",
                "sections",
                "missing_sections",
                "chain_validation_status",
            },
        )
        self.assertTrue(
            {"activity", "trades", "rewards", "positions", "closed_positions", "chain_validation"}.issubset(
                set(snapshot_audit["sections"])
            )
        )

    def test_build_analysis_summary_audit_rollups_are_reserved_for_mainline_upgrade(self) -> None:
        summary = build_analysis_summary(
            leaderboard=[{"proxyWallet": WALLET}],
            weather_events=[],
            screening_records=[{"wallet": WALLET, "selected": True}],
            wallet_results=[
                {
                    "wallet": WALLET,
                    "leaderboard_entry": {"rank": 1},
                    "labels": [],
                    "metrics": {
                        "leaderboard_pnl": 10.0,
                        "weather_notional_ratio": 0.5,
                        "closed_position_win_rate": 1.0,
                        "closed_profit_multiple": 1.3,
                        "trades_per_active_day": 1.0,
                        "trade_count": 1,
                    },
                }
            ],
            errors=[],
        )

        audit_rollup = summary.get("audit_rollup")
        if not isinstance(audit_rollup, dict):
            self.skipTest("waiting for mainline audit rollups in analysis summary")

        self.assertHasKeys(
            audit_rollup,
            {
                "wallets_with_complete_snapshots",
                "chain_validation_status_counts",
                "missing_snapshot_sections",
            },
        )

    def test_build_analysis_summary_counts_only_core_labeled_wallets_for_funnel(self) -> None:
        summary = build_analysis_summary(
            leaderboard=[{"proxyWallet": WALLET}],
            weather_events=[],
            screening_records=[{"wallet": WALLET, "selected": True}],
            wallet_results=[
                {
                    "wallet": WALLET,
                    "leaderboard_entry": {"rank": 1},
                    "labels": [{"key": "normal_active", "display_name": "正常活跃"}],
                    "label_evaluations": [
                        {"key": "high_frequency_region", "matched": False},
                        {"key": "high_daily_region_profit", "matched": False},
                    ],
                    "metrics": {
                        "leaderboard_pnl": 10.0,
                        "weather_notional_ratio": 0.5,
                        "closed_position_win_rate": 1.0,
                        "closed_profit_multiple": 1.3,
                        "trades_per_active_day": 1.0,
                        "trade_count": 1,
                    },
                },
                {
                    "wallet": "0xdef4560000000000000000000000000000000000",
                    "leaderboard_entry": {"rank": 2},
                    "labels": [{"key": "high_frequency_region", "display_name": "高频地区：上海"}],
                    "label_evaluations": [
                        {"key": "high_frequency_region", "matched": True},
                        {"key": "high_daily_region_profit", "matched": False},
                    ],
                    "metrics": {
                        "leaderboard_pnl": 12.0,
                        "weather_notional_ratio": 0.6,
                        "closed_position_win_rate": 0.5,
                        "closed_profit_multiple": 1.1,
                        "trades_per_active_day": 1.5,
                        "trade_count": 2,
                    },
                },
            ],
            errors=[],
        )

        self.assertEqual(summary.get("wallets_selected"), 2)
        self.assertEqual(summary.get("wallets_core_labeled"), 1)


if __name__ == "__main__":
    unittest.main()
