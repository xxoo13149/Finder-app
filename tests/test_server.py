from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.history_registry import wallet_history_registry_dir
from polymarket_weather_tool.server import (
    RunState,
    ServerState,
    build_cleanup_inventory,
    build_config_for_run,
    ensure_cleanup_path_allowed,
    perform_cleanup_delete,
    read_run_summary,
)


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

            with self.assertRaisesRegex(ValueError, "明细清理仅支持正式分析历史条目"):
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

            with self.assertRaisesRegex(ValueError, "不能删除|locked|运行"):
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


if __name__ == "__main__":
    unittest.main()
