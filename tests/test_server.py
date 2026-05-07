from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.history_registry import wallet_history_registry_dir
from polymarket_weather_tool.history_ledger import history_ledger_table_path
from polymarket_weather_tool.server import (
    RunState,
    ServerState,
    build_cloud_archive_status,
    build_smart_pro_import_payload,
    build_cleanup_inventory,
    build_config_for_run,
    ensure_cleanup_path_allowed,
    perform_cleanup_delete,
    read_run_summary,
    smart_pro_config_status,
    sync_cloud_archive_run,
    sync_reusable_history_to_cloud,
    sync_run_to_smart_pro,
)
from polymarket_weather_tool.config import WEEKLY_HIGH_PROFIT_MODE


class ServerConfigTests(unittest.TestCase):
    def build_artifact_run(self, base: Path, run_id: str, *, with_wallets: bool = False) -> Path:
        output_dir = base / "artifacts" / run_id
        wallets_dir = output_dir / "wallets"
        wallets_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "analysis_summary.json").write_text("{}", encoding="utf-8")
        (output_dir / "report.txt").write_text("report", encoding="utf-8")
        (output_dir / "selected_wallets.json").write_text("[]", encoding="utf-8")
        (output_dir / "resolved_config.json").write_text("{}", encoding="utf-8")
        if with_wallets:
            (wallets_dir / "0xabc.json").write_text('{"wallet":"0xabc"}', encoding="utf-8")
            (output_dir / "leaderboard.json").write_text("{}", encoding="utf-8")
            (output_dir / "screening_records.json").write_text("[]", encoding="utf-8")
            (output_dir / "weather_events.json").write_text('{"payload":"large"}', encoding="utf-8")
            (output_dir / "errors.json").write_text("[]", encoding="utf-8")
            (output_dir / "progress.log").write_text("working", encoding="utf-8")
        return output_dir

    def build_smart_pro_sync_run(self, base: Path, run_id: str) -> tuple[Path, str, str]:
        first_wallet = "0xaaa0000000000000000000000000000000000000"
        second_wallet = "0xbbb0000000000000000000000000000000000000"
        output_dir = base / "artifacts" / run_id
        wallets_dir = output_dir / "wallets"
        wallets_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "selected_wallets.json").write_text(
            json.dumps(
                [
                    {
                        "wallet": first_wallet,
                        "user_name": "weather-pro",
                        "selected": True,
                        "labels": ["high frequency"],
                    },
                    {
                        "wallet": second_wallet,
                        "user_name": "storm-chaser",
                        "selected": True,
                        "labels": ["lottery"],
                    },
                ]
            ),
            encoding="utf-8",
        )
        (wallets_dir / f"{first_wallet}.json").write_text(
            json.dumps(
                {
                    "wallet": first_wallet,
                    "selection_record": {"wallet": first_wallet},
                    "label_evaluations": [{"key": "high_frequency_region", "matched": True}],
                    "finder_ai": {
                        "sourceName": "finder",
                        "runId": run_id,
                        "normalizedAddress": first_wallet,
                        "matched": True,
                        "wallet": {"address": first_wallet, "displayName": "weather-pro"},
                        "primarySignals": [{"key": "high_frequency_region", "label": "High frequency", "matched": True}],
                    },
                }
            ),
            encoding="utf-8",
        )
        (wallets_dir / f"{second_wallet}.json").write_text(
            json.dumps(
                {
                    "wallet": second_wallet,
                    "selection_record": {"wallet": second_wallet},
                    "label_evaluations": [{"key": "lottery_player", "matched": True}],
                    "finder_ai": {
                        "sourceName": "finder",
                        "runId": run_id,
                        "normalizedAddress": second_wallet,
                        "matched": True,
                        "wallet": {"address": second_wallet, "displayName": "storm-chaser"},
                        "primarySignals": [{"key": "lottery_player", "label": "Lottery player", "matched": True}],
                    },
                }
            ),
            encoding="utf-8",
        )
        return output_dir, first_wallet, second_wallet

    def build_wallet_registry_record(
        self,
        base: Path,
        wallet: str,
        *,
        user_name: str,
        run_count: int = 1,
        first_seen_at: str = "2026-04-27T00:00:00+00:00",
        last_seen_at: str = "2026-04-28T00:00:00+00:00",
    ) -> Path:
        registry_dir = wallet_history_registry_dir(base / "artifacts")
        registry_dir.mkdir(parents=True, exist_ok=True)
        record_path = registry_dir / f"{wallet.lower()}.json"
        record_path.write_text(
            json.dumps(
                {
                    "wallet_address": wallet.lower(),
                    "user_name": user_name,
                    "first_seen_at": first_seen_at,
                    "last_seen_at": last_seen_at,
                    "run_count": run_count,
                    "last_run_id": "polymarket-weather-20260428-010101Z-aaaaaa",
                    "last_status": "selected",
                }
            ),
            encoding="utf-8",
        )
        return record_path

    def write_run_resolved_config(self, output_dir: Path, payload: dict[str, Any]) -> None:
        (output_dir / "resolved_config.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_cloud_archive_manifest(self, output_dir: Path, payload: dict[str, Any]) -> None:
        (output_dir / "cloud_archive_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_default_config(self, root: Path, payload: dict[str, Any]) -> None:
        config_path = root / "configs" / "default_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_build_config_for_run_applies_numeric_filter_ranges_from_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ServerState(root=ROOT, artifacts_root=Path(temp_dir))
            run_state = RunState(
                run_id="range-override-test",
                status="queued",
                output_dir=str(Path(temp_dir) / "range-override-test"),
                created_at="2026-04-27T00:00:00+00:00",
                progress_log_path=str(Path(temp_dir) / "range-override-test" / "progress.log"),
            )

            config = build_config_for_run(
                state,
                {
                    "overrides": {
                        "min_pnl": "1.5",
                        "max_pnl": "200",
                        "min_volume": "10",
                        "max_volume": "40000",
                        "min_traded_count": "11",
                        "max_traded_count": "99",
                        "min_weather_trade_ratio": "0.55",
                        "max_fetch_limit": "250",
                    }
                },
                run_state,
            )

        self.assertEqual(config["wallet_filter"]["min_pnl"], 1.5)
        self.assertEqual(config["wallet_filter"]["max_pnl"], 200.0)
        self.assertEqual(config["wallet_filter"]["min_volume"], 10.0)
        self.assertEqual(config["wallet_filter"]["max_volume"], 40000.0)
        self.assertEqual(config["wallet_filter"]["min_traded_count"], 11)
        self.assertEqual(config["wallet_filter"]["max_traded_count"], 99)
        self.assertEqual(config["wallet_filter"]["min_weather_trade_ratio"], 0.55)
        self.assertEqual(config["leaderboard"]["max_fetch_limit"], 250)
        self.assertEqual(config["runtime"]["run_id"], run_state.run_id)
        self.assertEqual(config["runtime"]["progress_log_path"], run_state.progress_log_path)

    def test_build_config_for_run_applies_weekly_high_profit_mode_before_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ServerState(root=ROOT, artifacts_root=Path(temp_dir))
            run_state = RunState(
                run_id="weekly-mode-test",
                status="queued",
                output_dir=str(Path(temp_dir) / "weekly-mode-test"),
                created_at="2026-04-27T00:00:00+00:00",
                progress_log_path=str(Path(temp_dir) / "weekly-mode-test" / "progress.log"),
            )

            config = build_config_for_run(
                state,
                {
                    "analysis_mode": WEEKLY_HIGH_PROFIT_MODE,
                    "overrides": {
                        "target_count": "7",
                        "max_fetch_limit": "250",
                    },
                },
                run_state,
            )

        self.assertEqual(config["leaderboard"]["time_period"], "WEEK")
        self.assertEqual(config["leaderboard"]["order_by"], "PNL")
        self.assertEqual(config["leaderboard"]["fetch_limit"], 300)
        self.assertEqual(config["leaderboard"]["max_fetch_limit"], 250)
        self.assertEqual(config["wallet_filter"]["target_count"], 7)
        self.assertEqual(config["wallet_filter"]["min_pnl"], 25)
        self.assertEqual(config["wallet_filter"]["max_pnl"], 2000)
        self.assertEqual(config["wallet_filter"]["min_volume"], 500)
        self.assertEqual(config["wallet_filter"]["max_volume"], 1000000)
        self.assertEqual(config["wallet_filter"]["min_traded_count"], 5)
        self.assertEqual(config["wallet_filter"]["max_traded_count"], 2000)
        self.assertEqual(config["wallet_filter"]["min_weather_trade_ratio"], 0.2)
        self.assertEqual(config["wallet_filter"]["min_weather_notional_ratio"], 0.45)
        self.assertEqual(config["wallet_filter"]["weather_focus_mode"], "trade_or_notional")
        self.assertEqual(config["runtime"]["analysis_mode"], WEEKLY_HIGH_PROFIT_MODE)
        self.assertEqual(config["runtime"]["analysis_mode_label"], "本周高盈利榜单")
        self.assertEqual(config["runtime"]["run_id"], run_state.run_id)
        self.assertEqual(config["runtime"]["progress_log_path"], run_state.progress_log_path)

    def test_read_run_summary_backfills_core_labeled_wallet_count_for_legacy_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "legacy-run"
            wallets_dir = output_dir / "wallets"
            wallets_dir.mkdir(parents=True)

            (output_dir / "analysis_summary.json").write_text(
                json.dumps({"wallets_selected": 2, "label_counts": {"高频地区：Shanghai": 1}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (output_dir / "selected_wallets.json").write_text(
                json.dumps(
                    [
                        {"wallet": "0xaaa0000000000000000000000000000000000000"},
                        {"wallet": "0xbbb0000000000000000000000000000000000000"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (wallets_dir / "0xaaa0000000000000000000000000000000000000.json").write_text(
                json.dumps(
                    {
                        "wallet": "0xaaa0000000000000000000000000000000000000",
                        "label_evaluations": [
                            {"key": "high_frequency_region", "matched": True},
                            {"key": "liquidity_player", "matched": False},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (wallets_dir / "0xbbb0000000000000000000000000000000000000.json").write_text(
                json.dumps(
                    {
                        "wallet": "0xbbb0000000000000000000000000000000000000",
                        "labels": [{"key": "normal_active"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = read_run_summary(output_dir)

        self.assertEqual(summary["wallets_selected"], 2)
        self.assertEqual(summary["wallets_core_labeled"], 1)

    def test_build_cleanup_inventory_groups_runs_outputs_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa", with_wallets=True)
            self.build_artifact_run(root, "codex-smoke", with_wallets=True)
            (root / "output" / "playwright").mkdir(parents=True)
            (root / "output" / "playwright" / "shot.png").write_bytes(b"png")
            (root / "frontend" / "test-results" / "chromium").mkdir(parents=True)
            (root / "frontend" / "test-results" / "chromium" / "trace.zip").write_bytes(b"zip")
            (root / ".cache" / "polymarket-weather-tool").mkdir(parents=True)
            (root / ".cache" / "polymarket-weather-tool" / "cache.json").write_text("{}", encoding="utf-8")
            (root / ".cache" / "runtime" / "logs").mkdir(parents=True)
            (root / ".cache" / "runtime" / "logs" / "api.out.log").write_text("ok", encoding="utf-8")
            (root / "src" / "pkg" / "__pycache__").mkdir(parents=True)
            (root / "src" / "pkg" / "__pycache__" / "module.pyc").write_bytes(b"pyc")
            self.build_wallet_registry_record(
                root,
                "0xabc0000000000000000000000000000000000000",
                user_name="weather-pro",
                run_count=3,
            )
            self.build_wallet_registry_record(
                root,
                "0xdef0000000000000000000000000000000000000",
                user_name="storm-chaser",
                run_count=1,
                last_seen_at="2026-04-26T00:00:00+00:00",
            )

            inventory = build_cleanup_inventory(ServerState(root=root, artifacts_root=artifacts_root))

        sections = {section["key"]: section for section in inventory["sections"]}
        self.assertEqual(sections["analysis_runs"]["count"], 1)
        self.assertEqual(sections["diagnostic_runs"]["count"], 1)
        self.assertEqual(sections["wallet_registry"]["count"], 2)
        self.assertEqual(sections["temp_outputs"]["count"], 2)
        self.assertEqual(sections["runtime_storage"]["count"], 3)
        self.assertEqual(
            sorted(item["path"] for item in sections["temp_outputs"]["items"]),
            ["frontend/test-results/chromium", "output/playwright"],
        )
        analysis_item = sections["analysis_runs"]["items"][0]
        self.assertGreater(analysis_item.get("detail_prunable_bytes", 0), 0)
        wallet_registry_items = {
            item["wallet_address"]: item for item in sections["wallet_registry"]["items"]
        }
        self.assertEqual(
            wallet_registry_items["0xabc0000000000000000000000000000000000000"]["user_name"],
            "weather-pro",
        )
        self.assertEqual(
            wallet_registry_items["0xabc0000000000000000000000000000000000000"]["run_count"],
            3,
        )

        actions = {action["key"]: action for action in inventory["actions"]}
        self.assertEqual(actions["delete_diagnostic_records"]["target_count"], 1)
        self.assertEqual(actions["delete_temp_outputs"]["target_count"], 2)
        self.assertGreater(actions["clear_runtime_storage"]["target_count"], 0)
        self.assertGreater(actions["clear_api_cache"]["size_bytes"], 0)
        self.assertGreater(actions["clear_runtime_logs"]["target_count"], 0)
        self.assertGreater(actions["prune_run_details"]["target_count"], 0)
        self.assertEqual(actions["clear_wallet_registry"]["target_count"], 2)

    def test_build_cleanup_inventory_includes_cloud_archive_manifest_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa", with_wallets=True)
            self.write_cloud_archive_manifest(
                output_dir,
                {
                    "run_id": "polymarket-weather-20260428-010101Z-aaaaaa",
                    "status": "archived",
                    "backend": "cloudflare",
                    "configured": True,
                    "archived_at": "2026-05-06T12:00:00+00:00",
                    "document_count": 7,
                },
            )

            inventory = build_cleanup_inventory(ServerState(root=root, artifacts_root=artifacts_root))

        sections = {section["key"]: section for section in inventory["sections"]}
        analysis_item = sections["analysis_runs"]["items"][0]
        self.assertEqual(analysis_item["archive_status"], "archived")
        self.assertEqual(analysis_item["archived_document_count"], 7)
        self.assertEqual(analysis_item["archive_backend"], "cloudflare")
        self.assertIn("runs", inventory["cloud_archive"])
        self.assertEqual(inventory["cloud_archive"]["backend"], "cloudflare")
        self.assertEqual(inventory["cloud_archive"]["runs"][0]["run_id"], "polymarket-weather-20260428-010101Z-aaaaaa")
        self.assertEqual(inventory["cloud_archive"]["runs"][0]["archive_status"], "archived")
        self.assertEqual(inventory["cloud_archive"]["runs"][0]["archive_backend"], "cloudflare")

    def test_build_cloud_archive_status_lists_run_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa")
            self.write_cloud_archive_manifest(
                output_dir,
                {
                    "run_id": "polymarket-weather-20260428-010101Z-aaaaaa",
                    "status": "archived",
                    "backend": "cloudflare",
                    "configured": True,
                    "archived_at": "2026-05-06T12:00:00+00:00",
                    "document_count": 3,
                },
            )

            status = build_cloud_archive_status(ServerState(root=root, artifacts_root=artifacts_root))

        self.assertIn("runs", status)
        self.assertIn("history_registry", status)
        self.assertIn("history_ledger", status)
        self.assertEqual(status["backend"], "cloudflare")
        self.assertEqual(len(status["runs"]), 1)
        self.assertEqual(status["runs"][0]["run_id"], "polymarket-weather-20260428-010101Z-aaaaaa")
        self.assertEqual(status["runs"][0]["archived_document_count"], 3)
        self.assertEqual(status["runs"][0]["archive_backend"], "cloudflare")

    def test_sync_reusable_history_to_cloud_pushes_local_registry_and_ledger_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            self.write_default_config(
                root,
                {
                    "history_registry": {
                        "enabled": True,
                        "backend": "local",
                        "cloudflare_account_id": "account-id",
                        "cloudflare_d1_database_id": "database-id",
                        "cloudflare_api_token": "api-token",
                    },
                    "history_ledger": {
                        "enabled": True,
                        "backend": "local",
                        "cloudflare_account_id": "account-id",
                        "cloudflare_d1_database_id": "database-id",
                        "cloudflare_api_token": "api-token",
                    },
                },
            )
            self.build_wallet_registry_record(
                root,
                "0xabc0000000000000000000000000000000000000",
                user_name="weather-pro",
            )
            trade_path = history_ledger_table_path(artifacts_root, "trades")
            gap_path = history_ledger_table_path(artifacts_root, "gaps")
            trade_path.parent.mkdir(parents=True, exist_ok=True)
            trade_path.write_text(
                json.dumps(
                    [
                        {
                            "record_key": "trade-1",
                            "wallet_address": "0xabc0000000000000000000000000000000000000",
                            "run_id": "seed-run",
                            "snapshot_scope": "full",
                            "history_scope": "full_history",
                            "event_timestamp": 1777000000,
                            "payload": {"id": "trade-1"},
                            "updated_at": "2026-05-06T00:00:00+00:00",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            gap_path.write_text(
                json.dumps(
                    [
                        {
                            "gap_key": "gap-1",
                            "wallet_address": "0xabc0000000000000000000000000000000000000",
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
                ),
                encoding="utf-8",
            )

            with patch(
                "polymarket_weather_tool.history_registry.cloudflare_d1_upsert_rows",
                side_effect=lambda _config, _table, *, rows, on_conflict: [dict(row) for row in rows],
            ) as registry_upsert, patch(
                "polymarket_weather_tool.history_ledger.cloudflare_d1_upsert_rows",
                side_effect=lambda _config, _table, *, rows, on_conflict: [dict(row) for row in rows],
            ) as ledger_upsert:
                result = sync_reusable_history_to_cloud(
                    ServerState(root=root, artifacts_root=artifacts_root)
                )

        self.assertEqual(result["history_registry"]["status"], "synced")
        self.assertEqual(result["history_registry"]["backend"], "cloudflare")
        self.assertEqual(result["history_registry"]["record_count"], 1)
        self.assertEqual(result["history_ledger"]["status"], "synced")
        self.assertEqual(result["history_ledger"]["backend"], "cloudflare")
        self.assertEqual(result["history_ledger"]["trade_count"], 1)
        self.assertTrue(
            any(call.args[1] == "wallet_registry" for call in registry_upsert.call_args_list)
        )
        self.assertTrue(
            any(call.args[1] == "wallet_trade_ledger" for call in ledger_upsert.call_args_list)
        )
        self.assertTrue(
            any(call.args[1] == "wallet_history_gaps" for call in ledger_upsert.call_args_list)
        )

    def test_sync_cloud_archive_run_writes_manifest_for_existing_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa", with_wallets=True)
            self.write_run_resolved_config(
                output_dir,
                {
                    "cloud_archive": {
                        "enabled": False,
                    }
                },
            )

            result = sync_cloud_archive_run(
                ServerState(root=root, artifacts_root=artifacts_root),
                "polymarket-weather-20260428-010101Z-aaaaaa",
            )

            manifest = json.loads((output_dir / "cloud_archive_manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["run_id"], "polymarket-weather-20260428-010101Z-aaaaaa")
        self.assertEqual(result["manifest"]["status"], "disabled")
        self.assertEqual(result["manifest"]["backend"], "cloudflare")
        self.assertIn("reusable_history", result)
        self.assertEqual(result["reusable_history"]["history_registry"]["backend"], "cloudflare")
        self.assertEqual(manifest["status"], "disabled")
        self.assertEqual(manifest["backend"], "cloudflare")

    def test_perform_cleanup_delete_prunes_run_details_but_keeps_summary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa", with_wallets=True)
            state = ServerState(root=root, artifacts_root=artifacts_root)

            result = perform_cleanup_delete(state, action_key="prune_run_details")

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_count"], 1)
            self.assertGreater(result["deleted_bytes"], 0)
            self.assertTrue((output_dir / "analysis_summary.json").exists())
            self.assertTrue((output_dir / "report.txt").exists())
            self.assertTrue((output_dir / "selected_wallets.json").exists())
            self.assertTrue((output_dir / "resolved_config.json").exists())
            self.assertFalse((output_dir / "wallets").exists())
            self.assertFalse((output_dir / "leaderboard.json").exists())
            self.assertFalse((output_dir / "screening_records.json").exists())
            self.assertFalse((output_dir / "weather_events.json").exists())
            self.assertFalse((output_dir / "progress.log").exists())

    def test_perform_cleanup_delete_aborts_when_cloud_archive_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa", with_wallets=True)
            self.write_run_resolved_config(
                output_dir,
                {
                    "cloud_archive": {
                        "enabled": True,
                        "backend": "cloudflare",
                        "archive_before_cleanup": True,
                        "cloudflare_account_id": "account-id",
                        "cloudflare_d1_database_id": "database-id",
                        "cloudflare_api_token": "api-token",
                    }
                },
            )
            state = ServerState(root=root, artifacts_root=artifacts_root)

            with patch(
                "polymarket_weather_tool.server.cloud_archive_module.archive_run_outputs",
                return_value={"status": "failed", "document_count": 0},
            ):
                with self.assertRaisesRegex(ValueError, "Cloud archive prep failed"):
                    perform_cleanup_delete(
                        state,
                        item_ids=["run:polymarket-weather-20260428-010101Z-aaaaaa"],
                    )

            self.assertTrue(output_dir.exists())
            self.assertTrue((output_dir / "wallets").exists())

    def test_perform_cleanup_delete_single_item_returns_refreshed_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa")
            state = ServerState(root=root, artifacts_root=artifacts_root)

            result = perform_cleanup_delete(
                state,
                item_ids=["run:polymarket-weather-20260428-010101Z-aaaaaa"],
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_item_ids"], ["run:polymarket-weather-20260428-010101Z-aaaaaa"])
            self.assertEqual(result["deleted_run_ids"], ["polymarket-weather-20260428-010101Z-aaaaaa"])
            self.assertFalse(output_dir.exists())
            sections = {section["key"]: section for section in result["inventory"]["sections"]}
            self.assertEqual(sections["analysis_runs"]["count"], 0)

    def test_perform_cleanup_delete_prunes_selected_run_details_without_removing_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            output_dir = self.build_artifact_run(root, "polymarket-weather-20260428-010101Z-aaaaaa", with_wallets=True)
            state = ServerState(root=root, artifacts_root=artifacts_root)

            result = perform_cleanup_delete(
                state,
                item_ids=["run:polymarket-weather-20260428-010101Z-aaaaaa"],
                operation="prune",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_count"], 1)
            self.assertEqual(result["deleted_run_ids"], [])
            self.assertTrue(output_dir.exists())
            self.assertTrue((output_dir / "analysis_summary.json").exists())
            self.assertTrue((output_dir / "report.txt").exists())
            self.assertTrue((output_dir / "selected_wallets.json").exists())
            self.assertTrue((output_dir / "resolved_config.json").exists())
            self.assertFalse((output_dir / "wallets").exists())
            self.assertFalse((output_dir / "leaderboard.json").exists())
            sections = {section["key"]: section for section in result["inventory"]["sections"]}
            self.assertEqual(sections["analysis_runs"]["count"], 1)

    def test_perform_cleanup_delete_removes_wallet_registry_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            record_path = self.build_wallet_registry_record(
                root,
                "0xabc0000000000000000000000000000000000000",
                user_name="weather-pro",
                run_count=2,
            )
            other_record_path = self.build_wallet_registry_record(
                root,
                "0xdef0000000000000000000000000000000000000",
                user_name="storm-chaser",
            )
            state = ServerState(root=root, artifacts_root=artifacts_root)

            result = perform_cleanup_delete(
                state,
                item_ids=[f"wallet_registry:{record_path.relative_to(root).as_posix()}"],
            )

            self.assertTrue(result["ok"])
            self.assertEqual(
                result["deleted_item_ids"],
                [f"wallet_registry:{record_path.relative_to(root).as_posix()}"],
            )
            self.assertFalse(record_path.exists())
            self.assertTrue(other_record_path.exists())
            sections = {section["key"]: section for section in result["inventory"]["sections"]}
            self.assertEqual(sections["wallet_registry"]["count"], 1)
            self.assertEqual(
                sections["wallet_registry"]["items"][0]["wallet_address"],
                "0xdef0000000000000000000000000000000000000",
            )

    def test_clear_wallet_registry_action_removes_all_wallet_history_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            first_record = self.build_wallet_registry_record(
                root,
                "0xabc0000000000000000000000000000000000000",
                user_name="weather-pro",
            )
            second_record = self.build_wallet_registry_record(
                root,
                "0xdef0000000000000000000000000000000000000",
                user_name="storm-chaser",
            )

            result = perform_cleanup_delete(
                ServerState(root=root, artifacts_root=artifacts_root),
                action_key="clear_wallet_registry",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_count"], 2)
            self.assertFalse(first_record.exists())
            self.assertFalse(second_record.exists())
            sections = {section["key"]: section for section in result["inventory"]["sections"]}
            self.assertEqual(sections["wallet_registry"]["count"], 0)

    def test_perform_cleanup_delete_rejects_pruning_non_analysis_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            self.build_artifact_run(root, "codex-smoke", with_wallets=True)
            state = ServerState(root=root, artifacts_root=artifacts_root)

            with self.assertRaisesRegex(ValueError, "Detailed pruning only supports formal analysis history items."):
                perform_cleanup_delete(
                    state,
                    item_ids=["run:codex-smoke"],
                    operation="prune",
                )

    def test_perform_cleanup_delete_rejects_locked_run_and_action_skips_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            queued_dir = self.build_artifact_run(root, "codex-smoke-queued")
            finished_dir = self.build_artifact_run(root, "codex-smoke-finished")
            state = ServerState(root=root, artifacts_root=artifacts_root)
            state.runs["codex-smoke-queued"] = RunState(
                run_id="codex-smoke-queued",
                status="queued",
                output_dir=str(queued_dir),
                created_at="2026-04-28T00:00:00+00:00",
                progress_log_path=str(queued_dir / "progress.log"),
            )

            with self.assertRaisesRegex(ValueError, "Running tasks cannot be deleted yet."):
                perform_cleanup_delete(state, item_ids=["run:codex-smoke-queued"])

            result = perform_cleanup_delete(state, action_key="delete_diagnostic_records")

            self.assertTrue(result["ok"])
            self.assertTrue(queued_dir.exists())
            self.assertFalse(finished_dir.exists())
            self.assertEqual(result["deleted_run_ids"], ["codex-smoke-finished"])

    def test_delete_diagnostic_records_keeps_temp_outputs_and_delete_temp_outputs_removes_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            diagnostic_dir = self.build_artifact_run(root, "codex-smoke-finished")
            output_dir = root / "output" / "playwright"
            output_dir.mkdir(parents=True)
            (output_dir / "shot.png").write_bytes(b"png")
            test_results_root = root / "frontend" / "test-results"
            test_result_dir = test_results_root / "chromium"
            test_result_dir.mkdir(parents=True)
            (test_result_dir / "trace.zip").write_bytes(b"zip")
            state = ServerState(root=root, artifacts_root=artifacts_root)

            diagnostic_result = perform_cleanup_delete(state, action_key="delete_diagnostic_records")

            self.assertTrue(diagnostic_result["ok"])
            self.assertFalse(diagnostic_dir.exists())
            self.assertTrue(output_dir.exists())
            self.assertTrue(test_result_dir.exists())
            self.assertEqual(diagnostic_result["deleted_run_ids"], ["codex-smoke-finished"])

            temp_output_result = perform_cleanup_delete(state, action_key="delete_temp_outputs")

            self.assertTrue(temp_output_result["ok"])
            self.assertFalse(output_dir.exists())
            self.assertFalse(test_result_dir.exists())
            self.assertTrue((root / "output").exists())
            self.assertTrue(test_results_root.exists())
            sections = {section["key"]: section for section in temp_output_result["inventory"]["sections"]}
            self.assertEqual(sections["temp_outputs"]["count"], 0)

    def test_clear_runtime_storage_removes_cache_logs_and_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            api_cache = root / ".cache" / "polymarket-weather-tool"
            runtime_logs = root / ".cache" / "runtime" / "logs"
            python_cache = root / "tests" / "__pycache__"
            api_cache.mkdir(parents=True)
            runtime_logs.mkdir(parents=True)
            python_cache.mkdir(parents=True)
            (api_cache / "cache.json").write_text("{}", encoding="utf-8")
            (runtime_logs / "api.log").write_text("log", encoding="utf-8")
            (python_cache / "test_server.pyc").write_bytes(b"pyc")

            result = perform_cleanup_delete(
                ServerState(root=root, artifacts_root=artifacts_root),
                action_key="clear_runtime_storage",
            )

            self.assertTrue(result["ok"])
            self.assertFalse(api_cache.exists())
            self.assertFalse(runtime_logs.exists())
            self.assertFalse(python_cache.exists())
            sections = {section["key"]: section for section in result["inventory"]["sections"]}
            self.assertEqual(sections["runtime_storage"]["count"], 0)

    def test_cleanup_path_guard_rejects_non_cleanup_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts_root = root / "artifacts"
            artifacts_root.mkdir(parents=True)
            source_file = root / "src" / "polymarket_weather_tool" / "server.py"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("print('do not delete')", encoding="utf-8")
            frontend_test_results_root = root / "frontend" / "test-results"
            frontend_test_results_child = frontend_test_results_root / "chromium"
            frontend_test_results_child.mkdir(parents=True)
            state = ServerState(root=root, artifacts_root=artifacts_root)

            with self.assertRaisesRegex(ValueError, "outside cleanup-safe roots"):
                ensure_cleanup_path_allowed(state, source_file)
            with self.assertRaisesRegex(ValueError, "refusing to delete cleanup root"):
                ensure_cleanup_path_allowed(state, frontend_test_results_root)
            self.assertEqual(
                ensure_cleanup_path_allowed(state, frontend_test_results_child),
                frontend_test_results_child.resolve(),
            )

    def test_build_smart_pro_import_payload_filters_requested_wallets_and_reads_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir, first_wallet, second_wallet = self.build_smart_pro_sync_run(root, "sync-run")

            payload = build_smart_pro_import_payload(
                output_dir,
                "sync-run",
                requested_wallets=[second_wallet],
                filters={"tag": "lottery"},
            )

        self.assertEqual(payload["runId"], "sync-run")
        self.assertEqual(payload["sourceName"], "Finder-app:sync-run")
        self.assertEqual(payload["filters"], {"tag": "lottery"})
        self.assertEqual(len(payload["wallets"]), 1)
        self.assertEqual(payload["wallets"][0]["row"]["wallet"], second_wallet)
        self.assertEqual(payload["wallets"][0]["detail"]["wallet"], second_wallet)
        self.assertEqual(payload["wallets"][0]["finderAi"]["normalizedAddress"], second_wallet)
        self.assertTrue(payload["wallets"][0]["finderAi"]["matched"])
        self.assertNotEqual(payload["wallets"][0]["row"]["wallet"], first_wallet)

    def test_build_smart_pro_import_payload_compacts_large_detail_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir, first_wallet, _ = self.build_smart_pro_sync_run(root, "sync-run")
            selected_wallets_path = output_dir / "selected_wallets.json"
            selected_wallets = json.loads(selected_wallets_path.read_text(encoding="utf-8"))
            selected_wallets[0]["raw_positions"] = [{"market": "x", "notes": "z" * 4000}]
            selected_wallets_path.write_text(json.dumps(selected_wallets), encoding="utf-8")

            wallet_path = output_dir / "wallets" / f"{first_wallet}.json"
            wallet_detail = json.loads(wallet_path.read_text(encoding="utf-8"))
            wallet_detail["raw_transactions"] = [{"hash": "0x1", "payload": "x" * 12000}]
            wallet_detail["evidence_summary"] = {
                "headline": "high conviction",
                "main_region": "Shanghai",
                "latest_evidence_date": "2026-04-28",
                "suggest_watchlist": True,
                "full_report": "y" * 5000,
            }
            wallet_detail["label_evaluations"] = [
                {
                    "key": "high_frequency_region",
                    "matched": True,
                    "display_name": "High frequency",
                    "reason": "picked",
                    "details": {"region": "Shanghai", "city": "Shanghai", "reason": "burst", "huge": "q" * 9000},
                    "evidence": {"reason": "evidence kept", "blob": "w" * 9000},
                }
            ]
            wallet_detail["structured_materials"] = {
                "summary": {"headline": "high conviction", "source_excerpt": "s" * 5000},
                "records": {"trade_samples": [{"market_title": "x", "notes": "k" * 9000}]},
            }
            wallet_detail["finder_ai"]["layeredInput"] = {"L2": {"sourceExcerpt": "s" * 5000}}
            wallet_detail["finder_ai"]["aiBriefShort"] = "同步短摘要"
            wallet_detail["finder_ai"]["aiBriefNote"] = "这是同步给 Smart Pro 的 AI 简报。"
            wallet_detail["finder_ai"]["providerMeta"] = {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "promptVersion": "finder-weather-brief-v3",
                "generatedAt": "2026-05-05T00:00:00+00:00",
                "inputHash": "sha256:test",
                "cacheKey": "cache|" + ("m" * 2000),
                "generationScope": "brief",
                "outputSchemaVersion": "finder-ai-v1",
            }
            wallet_detail["finder_ai"]["briefGeneration"] = {
                "enabled": True,
                "cacheKey": "cache|" + ("z" * 5000),
                "gate": {"eligible": True, "reason": "ready"},
            }
            wallet_path.write_text(json.dumps(wallet_detail), encoding="utf-8")

            payload = build_smart_pro_import_payload(output_dir, "sync-run", requested_wallets=[first_wallet])

        compact_wallet = payload["wallets"][0]
        compact_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        raw_bytes = len(json.dumps({"wallets": [{"row": selected_wallets[0], "detail": wallet_detail}]}, ensure_ascii=False).encode("utf-8"))

        self.assertLess(compact_bytes, raw_bytes)
        self.assertNotIn("raw_positions", compact_wallet["row"])
        self.assertNotIn("raw_transactions", compact_wallet["detail"])
        self.assertEqual(compact_wallet["detail"]["evidence_summary"]["headline"], "high conviction")
        self.assertNotIn("full_report", compact_wallet["detail"]["evidence_summary"])
        self.assertEqual(compact_wallet["detail"]["label_evaluations"][0]["details"]["region"], "Shanghai")
        self.assertNotIn("huge", compact_wallet["detail"]["label_evaluations"][0]["details"])
        self.assertEqual(compact_wallet["detail"]["label_evaluations"][0]["evidence"]["reason"], "evidence kept")
        self.assertNotIn("structured_materials", compact_wallet["detail"])
        self.assertNotIn("layeredInput", compact_wallet["finderAi"])
        self.assertNotIn("briefGeneration", compact_wallet["finderAi"])
        self.assertEqual(compact_wallet["finderAi"]["aiBriefShort"], "同步短摘要")
        self.assertEqual(compact_wallet["finderAi"]["aiBriefNote"], "这是同步给 Smart Pro 的 AI 简报。")
        self.assertEqual(compact_wallet["finderAi"]["wallet"]["address"], first_wallet)
        self.assertEqual(compact_wallet["finderAi"]["wallet"]["displayName"], "weather-pro")
        self.assertEqual(compact_wallet["finderAi"]["primarySignals"][0]["key"], "high_frequency_region")
        self.assertEqual(
            compact_wallet["finderAi"]["providerMeta"]["generatedAt"],
            "2026-05-05T00:00:00+00:00",
        )

    def test_smart_pro_config_status_reports_missing_token_without_exposing_secrets(self) -> None:
        env_keys = [
            "SMART_PRO_BASE_URL",
            "SMART_PRO_URL",
            "SMART_PRO_FINDER_TOKEN",
            "SMART_PRO_SYNC_TOKEN",
            "FINDER_SYNC_TOKEN",
            "SMART_PRO_FINDER_COMMIT_PATH",
            "SMART_PRO_SYNC_TIMEOUT_SECONDS",
            "SMART_PRO_ACCESS_CLIENT_ID",
            "SMART_PRO_ACCESS_CLIENT_SECRET",
        ]
        old_env = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / ".env").write_text(
                    "SMART_PRO_BASE_URL=https://smart.example\nSMART_PRO_SYNC_TIMEOUT_SECONDS=120\n",
                    encoding="utf-8",
                )
                payload = smart_pro_config_status(root)

            self.assertFalse(payload["configured"])
            self.assertEqual(payload["base_url"], "https://smart.example")
            self.assertEqual(payload["commit_path"], "/api/finder/import/commit")
            self.assertEqual(payload["timeout_seconds"], 120)
            self.assertFalse(payload["token_configured"])
            self.assertFalse(payload["access_service_token_configured"])
            self.assertEqual(payload["errors"], ["SMART_PRO_FINDER_TOKEN is not configured in .env"])
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_sync_run_to_smart_pro_posts_commit_payload_with_env_token(self) -> None:
        env_keys = [
            "SMART_PRO_BASE_URL",
            "SMART_PRO_URL",
            "SMART_PRO_FINDER_TOKEN",
            "SMART_PRO_SYNC_TOKEN",
            "FINDER_SYNC_TOKEN",
            "SMART_PRO_FINDER_COMMIT_PATH",
            "SMART_PRO_SYNC_TIMEOUT_SECONDS",
            "SMART_PRO_ACCESS_CLIENT_ID",
            "SMART_PRO_ACCESS_CLIENT_SECRET",
        ]
        old_env = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                artifacts_root = root / "artifacts"
                artifacts_root.mkdir(parents=True)
                _, first_wallet, _ = self.build_smart_pro_sync_run(root, "sync-run")
                (root / ".env").write_text(
                    "SMART_PRO_BASE_URL=https://smart.example\nSMART_PRO_FINDER_TOKEN=test-token\n",
                    encoding="utf-8",
                )
                captured: dict[str, object] = {}

                def fake_post(
                    url: str,
                    token: str,
                    payload: dict[str, object],
                    timeout_seconds: int,
                    *,
                    extra_headers: dict[str, str] | None = None,
                ) -> dict[str, object]:
                    captured["url"] = url
                    captured["token"] = token
                    captured["payload"] = payload
                    captured["timeout_seconds"] = timeout_seconds
                    captured["extra_headers"] = extra_headers or {}
                    return {
                        "ok": True,
                        "data": {
                            "totalRows": 1,
                            "validRows": 1,
                            "commit": {
                                "createdCount": 1,
                                "updatedCount": 0,
                                "failedRows": [],
                            },
                        },
                    }

                result = sync_run_to_smart_pro(
                    ServerState(root=root, artifacts_root=artifacts_root),
                    {"run_id": "sync-run", "wallets": [first_wallet]},
                    post_json=fake_post,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["sent_count"], 1)
            self.assertGreater(result["payload_bytes"], 0)
            self.assertEqual(result["summary"]["createdCount"], 1)
            self.assertEqual(captured["url"], "https://smart.example/api/finder/import/commit")
            self.assertEqual(captured["token"], "test-token")
            self.assertEqual(captured["timeout_seconds"], 90)
            self.assertEqual(captured["extra_headers"], {})
            self.assertEqual(captured["payload"]["wallets"][0]["row"]["wallet"], first_wallet)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_sync_run_to_smart_pro_splits_large_wallet_lists_into_batches(self) -> None:
        env_keys = [
            "SMART_PRO_BASE_URL",
            "SMART_PRO_URL",
            "SMART_PRO_FINDER_TOKEN",
            "SMART_PRO_SYNC_TOKEN",
            "FINDER_SYNC_TOKEN",
            "SMART_PRO_FINDER_COMMIT_PATH",
            "SMART_PRO_SYNC_TIMEOUT_SECONDS",
            "SMART_PRO_ACCESS_CLIENT_ID",
            "SMART_PRO_ACCESS_CLIENT_SECRET",
        ]
        old_env = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                artifacts_root = root / "artifacts"
                artifacts_root.mkdir(parents=True)
                output_dir = artifacts_root / "sync-run"
                wallets_dir = output_dir / "wallets"
                wallets_dir.mkdir(parents=True, exist_ok=True)

                wallets: list[str] = []
                rows: list[dict[str, object]] = []
                for index in range(7):
                    wallet = f"0x{index + 1:040x}"
                    wallets.append(wallet)
                    rows.append({"wallet": wallet, "selected": True, "user_name": f"user-{index}"})
                    (wallets_dir / f"{wallet}.json").write_text(
                        json.dumps({"wallet": wallet, "selection_record": {"wallet": wallet}}),
                        encoding="utf-8",
                    )

                (output_dir / "selected_wallets.json").write_text(json.dumps(rows), encoding="utf-8")
                (root / ".env").write_text(
                    "SMART_PRO_BASE_URL=https://smart.example\nSMART_PRO_FINDER_TOKEN=test-token\n",
                    encoding="utf-8",
                )

                calls: list[dict[str, object]] = []

                def fake_post(
                    url: str,
                    token: str,
                    payload: dict[str, object],
                    timeout_seconds: int,
                    *,
                    extra_headers: dict[str, str] | None = None,
                ) -> dict[str, object]:
                    calls.append(
                        {
                            "url": url,
                            "token": token,
                            "timeout_seconds": timeout_seconds,
                            "wallet_count": len(payload["wallets"]),
                        }
                    )
                    count = len(payload["wallets"])
                    return {
                        "ok": True,
                        "data": {
                            "totalRows": count,
                            "validRows": count,
                            "commit": {
                                "createdCount": 0,
                                "updatedCount": count,
                                "failedRows": [],
                            },
                        },
                    }

                result = sync_run_to_smart_pro(
                    ServerState(root=root, artifacts_root=artifacts_root),
                    {"run_id": "sync-run", "wallets": wallets},
                    post_json=fake_post,
                )

            self.assertEqual([call["wallet_count"] for call in calls], [5, 2])
            self.assertEqual(result["sent_count"], 7)
            self.assertEqual(result["batch_count"], 2)
            self.assertEqual(result["summary"]["updatedCount"], 7)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
