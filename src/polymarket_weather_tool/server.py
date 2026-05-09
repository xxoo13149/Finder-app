from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, unquote, urlparse

from . import cloud_archive as cloud_archive_module
from . import history_ledger as history_ledger_module
from .config import (
    DEFAULT_CONFIG_PATH,
    RELAY_ANALYSIS_MODE,
    SMART_WALLET_LIBRARY_REFRESH_MODE,
    apply_analysis_mode,
    apply_overrides,
    load_config,
)
from .env import load_project_env
from .finder_ai_contract import compact_finder_ai_result
from .history_registry import create_history_registry, list_wallet_history_records
from .labels import CORE_LABEL_KEYS
from .smart_wallet_library import (
    SMART_WALLET_IMPORT_ROWS_FILENAME,
    SMART_WALLET_IMPORT_SUMMARY_FILENAME,
    materialize_smart_wallet_library,
    normalize_import_wallet_rows,
    summarize_import_wallet_rows,
)

RELAY_IMPORT_ROWS_FILENAME = "relay_import_rows.json"
RELAY_IMPORT_SUMMARY_FILENAME = "relay_import_summary.json"
WEATHER_FETCH_SUMMARY_FILENAME = "weather_fetch_summary.json"
RELAY_CORE_LABEL_FILTERS = {"all", "core", "non_core"}
RELAY_DEEPSEEK_FILTERS = {"all", "completed", "incomplete"}


UTC = timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = PROJECT_ROOT / "artifacts"
RUNTIME_STATE_RELATIVE_PATH = Path(".cache") / "runtime" / "launcher.json"
ALLOWED_BROWSER_ORIGINS = {
    "http://localhost:41873",
    "http://127.0.0.1:41873",
    "http://localhost:41874",
    "http://127.0.0.1:41874",
}
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
DIAGNOSTIC_RUN_TOKENS = ("smoke", "codex", "browser-", "ui-api", "test-fast")
RUN_DETAIL_PRUNE_FILES = (
    "leaderboard.json",
    "screening_records.json",
    "weather_events.json",
    "errors.json",
    "progress.log",
)
RUN_DETAIL_PRUNE_DIRS = ("wallets",)
SMART_PRO_DEFAULT_COMMIT_PATH = "/api/finder/import/commit"
SMART_PRO_DEFAULT_TIMEOUT_SECONDS = 90
SMART_PRO_MAX_SYNC_WALLETS = 500
SMART_PRO_SYNC_BATCH_SIZE = 5
WALLET_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SELECTED_WALLET_SNAPSHOT_LOCK = threading.Lock()
FULL_DETAIL_LIST_WALLET_LIMIT = 50
DIAGNOSTIC_DETAIL_WALLET_LIMIT = 20
DIAGNOSTIC_DETAIL_TOTAL_BYTES_LIMIT = 25 * 1024 * 1024


@dataclass
class RunState:
    run_id: str
    status: str
    output_dir: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    progress_log_path: str | None = None
    traceback: str | None = None


@dataclass
class ServerState:
    root: Path
    artifacts_root: Path
    runs: dict[str, RunState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    return f"polymarket-weather-{timestamp}-{uuid.uuid4().hex[:6]}"


def run_datetime_from_id(run_id: str) -> datetime | None:
    for pattern in (
        r"polymarket-weather-(\d{8})-(\d{6})Z",
        r"finder-latest-(\d{8})-(\d{6})",
        r"codex-verify-(\d{8})-(\d{6})Z",
    ):
        match = re.search(pattern, run_id)
        if not match:
            continue
        try:
            return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def run_created_datetime(run_id: str, output_dir: Path) -> datetime:
    parsed = run_datetime_from_id(run_id)
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(output_dir.stat().st_mtime, tz=UTC)


def resolve_under_root(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def ensure_under(parent: Path, child: Path) -> Path:
    parent = parent.resolve()
    child = child.resolve()
    if parent != child and parent not in child.parents:
        raise ValueError(f"path is outside allowed root: {child}")
    return child


def read_json_file(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return fallback


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_server_default_config(state: ServerState) -> dict[str, Any]:
    try:
        config_path = ensure_under(state.root, state.root / DEFAULT_CONFIG_PATH)
    except ValueError:
        return {}
    try:
        payload = load_config(config_path)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def read_run_resolved_config(output_dir: Path) -> dict[str, Any]:
    payload = read_json_file(output_dir / "resolved_config.json", {})
    return dict(payload) if isinstance(payload, Mapping) else {}


def history_registry_store(state: ServerState):
    return create_history_registry(state.artifacts_root, config=load_server_default_config(state))


def history_ledger_store(state: ServerState) -> history_ledger_module.HistoryLedgerStore:
    return history_ledger_module.create_history_ledger_store(
        state.artifacts_root,
        config=load_server_default_config(state),
    )


def cloud_archive_store(
    state: ServerState | None = None,
    config: Mapping[str, Any] | None = None,
) -> cloud_archive_module.CloudArchiveStore:
    resolved_config = config
    if resolved_config is None and state is not None:
        resolved_config = load_server_default_config(state)
    return cloud_archive_module.create_cloud_archive_store(resolved_config)


def summarize_run_archive_manifest(output_dir: Path) -> dict[str, Any]:
    manifest = cloud_archive_module.read_run_archive_manifest(output_dir)
    if not manifest:
        return {
            "archive_status": "missing",
            "archived_document_count": 0,
            "archived_at": "",
        }
    return {
        "archive_status": str(manifest.get("status") or "unknown"),
        "archived_document_count": int(manifest.get("document_count") or 0),
        "archived_at": str(manifest.get("archived_at") or ""),
        "archive_backend": str(manifest.get("backend") or ""),
        "archive_configured": bool(manifest.get("configured", False)),
    }


def wallet_payload_has_core_label(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False

    core_label_keys = set(CORE_LABEL_KEYS)
    evaluations = payload.get("label_evaluations")
    if isinstance(evaluations, list):
        for item in evaluations:
            if (
                isinstance(item, Mapping)
                and str(item.get("key") or "") in core_label_keys
                and bool(item.get("matched"))
            ):
                return True

    labels = payload.get("labels")
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, Mapping) and (
                bool(item.get("system_core")) or str(item.get("key") or "") in core_label_keys
            ):
                return True
    return False


def wallet_payload_core_label_keys(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []

    core_label_keys = set(CORE_LABEL_KEYS)
    keys: list[str] = []
    evaluations = payload.get("label_evaluations")
    if isinstance(evaluations, list):
        for item in evaluations:
            if (
                isinstance(item, Mapping)
                and str(item.get("key") or "") in core_label_keys
                and bool(item.get("matched"))
            ):
                keys.append(str(item.get("key")))

    labels = payload.get("labels")
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, Mapping) and (
                bool(item.get("system_core")) or str(item.get("key") or "") in core_label_keys
            ):
                keys.append(str(item.get("key") or "unknown_core_label"))

    deduped: list[str] = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return deduped


def count_core_labeled_wallets(output_dir: Path) -> int:
    try:
        rows = selected_wallet_file_rows(output_dir, repair=False)
    except ValueError:
        rows = []
    if rows:
        count = 0
        seen_wallets: set[str] = set()
        has_explicit_core_metadata = False
        for row in rows:
            wallet = normalized_wallet_file_name(finder_wallet_address(row))
            if not wallet or wallet in seen_wallets:
                continue
            seen_wallets.add(wallet)
            if "has_core_label" in row or "core_label_keys" in row:
                has_explicit_core_metadata = True
            if row_core_label_keys(row):
                count += 1
        if count or has_explicit_core_metadata or not should_load_diagnostic_wallet_details(output_dir):
            return count

        count = 0
        for wallet in seen_wallets:
            wallet_path = ensure_under(output_dir / "wallets", output_dir / "wallets" / f"{wallet}.json")
            if wallet_payload_has_core_label(read_json_file(wallet_path, {})):
                count += 1
        return count

    if should_load_diagnostic_wallet_details(output_dir):
        details = wallet_detail_payloads(output_dir)
        if details:
            return count_core_labeled_detail_payloads(details)

    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return 0

    count = 0
    for row in lightweight_summary_rows(output_dir):
        if row_core_label_keys(row):
            count += 1
    return count


def numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if number == number else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if number == number else None
    return None


def average_from_rows(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for value in (numeric_value(row.get(key)) for row in rows) if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def build_finder_ai_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "selected_wallets": len(rows),
        "finder_ai_present": 0,
        "eligible": 0,
        "generated": 0,
        "cached": 0,
        "fallback": 0,
        "failed": 0,
        "skipped": 0,
        "needs_review": 0,
        "has_conflict": 0,
    }
    for row in rows:
        status = str(row.get("ai_generation_status") or "").strip().lower()
        has_ai = bool(
            status
            or text_value(row.get("ai_brief_short"))
            or text_value(row.get("ai_strategy_focus"))
            or text_value(row.get("ai_generation_reason"))
        )
        if not has_ai:
            continue
        summary["finder_ai_present"] += 1
        if status == "generated":
            summary["generated"] += 1
        elif status == "cached":
            summary["cached"] += 1
        elif status == "fallback":
            summary["fallback"] += 1
        elif status == "failed":
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        if row.get("ai_needs_review"):
            summary["needs_review"] += 1
        if row.get("ai_has_conflict"):
            summary["has_conflict"] += 1
    return summary


def finder_ai_diagnostics_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = build_finder_ai_summary_from_rows(rows)
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in rows:
        status = str(row.get("ai_generation_status") or "").strip().lower()
        reason = text_value(row.get("ai_generation_reason"))
        if status:
            status_counts[status] += 1
        if reason:
            reason_counts[reason] += 1
    summary["status_counts"] = dict(status_counts)
    summary["reason_counts"] = dict(reason_counts)
    return summary


def hydration_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    history_scopes: Counter[str] = Counter()
    audit_incomplete = 0
    for row in rows:
        history_scope = text_value(row.get("history_scope"))
        if history_scope:
            history_scopes[history_scope] += 1
        if row.get("audit_complete") is False:
            audit_incomplete += 1
    skipped = sum(history_scopes.values())
    return {
        "completed": 0,
        "skipped": skipped,
        "failed": 0,
        "unknown": 0,
        "status_counts": {"skipped": skipped} if skipped else {},
        "reason_counts": {"analysis_audit_incomplete": audit_incomplete} if audit_incomplete else {},
        "history_scopes": dict(history_scopes),
    }


def wallet_rank_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "wallet": row.get("wallet"),
            "rank": row.get("rank"),
            "user_name": row.get("user_name") or row.get("userName") or row.get("username"),
            "x_username": row.get("x_username") or row.get("xUsername"),
            "pnl": row.get("pnl"),
            "closed_profit_multiple": row.get("closed_profit_multiple"),
            "closed_position_win_rate": row.get("closed_position_win_rate"),
            "trades_per_active_day": row.get("trades_per_active_day"),
            "trade_count": row.get("trade_count"),
        }.items()
        if value not in (None, "")
    }


def lightweight_summary_rows(output_dir: Path) -> list[dict[str, Any]]:
    selected_rows = selected_wallet_file_rows(output_dir, repair=False)
    detail_wallets = wallet_detail_file_wallets(output_dir)
    can_load_details = should_load_diagnostic_wallet_details(output_dir)
    if selected_rows:
        if can_load_details:
            return selected_wallet_rows(output_dir)
        return [row for row in selected_rows if row.get("selected") is not False]
    if can_load_details:
        return detail_rows_for_wallets(output_dir, detail_wallets)
    return [wallet_stub_row(wallet) for wallet in detail_wallets[:10]]


def build_lightweight_summary(output_dir: Path) -> dict[str, Any]:
    rows = lightweight_summary_rows(output_dir)
    labels = Counter(
        str(label)
        for row in rows
        for label in (row.get("labels") if isinstance(row.get("labels"), list) else [])
        if str(label).strip()
    )
    average_keys = (
        "weather_notional_ratio",
        "closed_position_win_rate",
        "closed_profit_multiple",
        "trades_per_active_day",
    )
    averages = {
        key: value
        for key in average_keys
        if (value := average_from_rows(rows, key)) is not None
    }
    pnl_rows = [
        row
        for row in rows
        if normalized_wallet_file_name(str(row.get("wallet") or ""))
    ]
    top_by_pnl = sorted(
        pnl_rows,
        key=lambda row: numeric_value(row.get("pnl")) if numeric_value(row.get("pnl")) is not None else float("-inf"),
        reverse=True,
    )[:10]
    top_by_frequency = sorted(
        pnl_rows,
        key=lambda row: numeric_value(row.get("trades_per_active_day"))
        if numeric_value(row.get("trades_per_active_day")) is not None
        else float("-inf"),
        reverse=True,
    )[:10]
    return {
        "label_counts": dict(labels.most_common()),
        "averages": averages,
        "top_wallets_by_pnl": [wallet_rank_summary(row) for row in top_by_pnl],
        "top_wallets_by_frequency": [wallet_rank_summary(row) for row in top_by_frequency],
        "finder_ai_summary": build_finder_ai_summary_from_rows(rows),
    }


def read_smart_wallet_import_summary(output_dir: Path) -> dict[str, Any]:
    payload = (
        read_json_file(output_dir / SMART_WALLET_IMPORT_SUMMARY_FILENAME, None)
        or read_json_file(output_dir / RELAY_IMPORT_SUMMARY_FILENAME, {})
        or {}
    )
    return dict(payload) if isinstance(payload, Mapping) else {}


def progress_weather_event_count(output_dir: Path) -> int | None:
    progress_path = output_dir / "progress.log"
    if not progress_path.exists():
        return None
    try:
        text = progress_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    matches = re.findall(r"Indexed\s+(\d+)\s+weather events", text)
    if not matches:
        return None
    return int(matches[-1])


def run_weather_diagnostics(output_dir: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    config = read_run_resolved_config(output_dir)
    weather_config = config.get("weather") if isinstance(config.get("weather"), Mapping) else {}
    fetch_summary = read_json_file(output_dir / WEATHER_FETCH_SUMMARY_FILENAME, {})
    fetch_summary = dict(fetch_summary) if isinstance(fetch_summary, Mapping) else {}
    indexed = summary.get("weather_events_indexed")
    if not isinstance(indexed, int):
        progress_count = progress_weather_event_count(output_dir)
        indexed = progress_count if progress_count is not None else None
    max_events = weather_config.get("max_events") if isinstance(weather_config, Mapping) else None
    try:
        normalized_max_events = int(max_events) if max_events not in (None, "") else None
    except (TypeError, ValueError):
        normalized_max_events = None
    cap_hit = bool(
        isinstance(indexed, int)
        and isinstance(normalized_max_events, int)
        and normalized_max_events > 0
        and indexed >= normalized_max_events
    )
    shortfall_hint = ""
    coverage_note = ""
    stop_reason = "unknown"
    fetch_stop_reason = text_value(fetch_summary.get("stop_reason"))
    if fetch_stop_reason:
        stop_reason = "natural_end" if bool(fetch_summary.get("natural_end")) else fetch_stop_reason
        if bool(fetch_summary.get("natural_end")):
            shortfall_hint = "gamma_weather_tag_natural_end"
            coverage_note = (
                "Gamma weather tag 已自然到底；这里显示的是当前 Gamma weather 标签池的完整索引量，"
                "不是 max_events 配置过小或分页提前停止。交易记录自身的天气信号兜底仍会参与轻量筛选。"
            )
    elif cap_hit:
        stop_reason = "max_events_reached"
    elif isinstance(indexed, int) and isinstance(normalized_max_events, int) and indexed < normalized_max_events:
        if bool(weather_config.get("use_keyset", True)) and indexed > 0 and (output_dir / "weather_events.json").exists():
            stop_reason = "natural_end"
            shortfall_hint = "gamma_weather_tag_natural_end"
            coverage_note = (
                "Gamma weather tag 已自然到底；这里显示的是当前 Gamma weather 标签池的完整索引量，"
                "不是 max_events 配置过小或分页提前停止。交易记录自身的天气信号兜底仍会参与轻量筛选。"
            )
        else:
            stop_reason = "below_cap_unknown"
            shortfall_hint = "tag_natural_end_or_filter_scope"
            coverage_note = (
                "天气事件索引没有打满上限，通常表示 Gamma weather tag 已自然到底或 tag 范围偏窄；"
                "交易记录自身的天气信号兜底仍会参与轻量筛选。"
            )
    return {
        "indexed": indexed if isinstance(indexed, int) else None,
        "max": normalized_max_events,
        "tag_id": weather_config.get("tag_id") if isinstance(weather_config, Mapping) else None,
        "tag_slug": text_value(weather_config.get("tag_slug")) if isinstance(weather_config, Mapping) else "",
        "fetch_mode": "keyset" if bool(weather_config.get("use_keyset", True)) else "offset",
        "reused_existing": progress_contains(output_dir, "Loading existing weather events for resumed run"),
        "cap_hit": cap_hit,
        "stop_reason": stop_reason,
        "fetch_stop_reason": fetch_stop_reason,
        "page_count": int(fetch_summary.get("page_count")) if isinstance(fetch_summary.get("page_count"), int) else None,
        "last_page_size": int(fetch_summary.get("last_page_size")) if isinstance(fetch_summary.get("last_page_size"), int) else None,
        "terminal_next_cursor_present": (
            bool(fetch_summary.get("terminal_next_cursor_present"))
            if "terminal_next_cursor_present" in fetch_summary
            else None
        ),
        "natural_end": bool(fetch_summary.get("natural_end")) if fetch_summary else stop_reason == "natural_end",
        "shortfall_hint": shortfall_hint,
        "coverage_note": coverage_note,
        "trading_fallback_enabled": True,
    }


def progress_contains(output_dir: Path, needle: str) -> bool:
    progress_path = output_dir / "progress.log"
    if not progress_path.exists():
        return False
    try:
        return needle in progress_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def should_load_diagnostic_wallet_details(output_dir: Path) -> bool:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return False
    wallet_paths = [path for path in wallets_dir.glob("*.json") if path.is_file()]
    if len(wallet_paths) > DIAGNOSTIC_DETAIL_WALLET_LIMIT:
        return False
    total_size = 0
    for wallet_path in wallet_paths:
        try:
            total_size += wallet_path.stat().st_size
        except OSError:
            continue
        if total_size > DIAGNOSTIC_DETAIL_TOTAL_BYTES_LIMIT:
            return False
    return True


def should_load_hydration_wallet_details(output_dir: Path) -> bool:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return False
    wallet_paths = [path for path in wallets_dir.glob("*.json") if path.is_file()]
    if len(wallet_paths) > DIAGNOSTIC_DETAIL_WALLET_LIMIT:
        return False
    for wallet_path in wallet_paths:
        try:
            if wallet_path.stat().st_size > DIAGNOSTIC_DETAIL_TOTAL_BYTES_LIMIT:
                return False
        except OSError:
            continue
    return True


def wallet_detail_payloads(output_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for wallet_path in sorted(wallets_dir.glob("*.json")):
        if limit is not None and len(payloads) >= limit:
            break
        payload = read_json_file(wallet_path, {}) or {}
        if isinstance(payload, Mapping):
            payloads.append(dict(payload))
    return payloads


def wallet_hydration_payloads(output_dir: Path) -> list[dict[str, Any]]:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return []
    wallet_paths = [path for path in sorted(wallets_dir.glob("*.json")) if path.is_file()]
    if not should_load_hydration_wallet_details(output_dir):
        return []

    payloads: list[dict[str, Any]] = []
    for wallet_path in wallet_paths:
        payload = read_json_file(wallet_path, {}) or {}
        if not isinstance(payload, Mapping):
            continue
        payloads.append(
            {
                "deep_hydration": payload.get("deep_hydration"),
                "metrics": payload.get("metrics"),
            }
        )
    return payloads


def core_label_key_counts_from_details(details: list[dict[str, Any]]) -> Counter[str]:
    core_keys = set(CORE_LABEL_KEYS)
    counts: Counter[str] = Counter()
    for detail in details:
        seen: set[str] = set()
        evaluations = detail.get("label_evaluations")
        if isinstance(evaluations, list):
            for item in evaluations:
                if (
                    isinstance(item, Mapping)
                    and str(item.get("key") or "") in core_keys
                    and bool(item.get("matched"))
                ):
                    seen.add(str(item.get("key")))
        labels = detail.get("labels")
        if isinstance(labels, list):
            for item in labels:
                if isinstance(item, Mapping) and (
                    bool(item.get("system_core")) or str(item.get("key") or "") in core_keys
                ):
                    key = str(item.get("key") or "").strip()
                    if key:
                        seen.add(key)
        counts.update(seen)
    return counts


def core_label_key_counts_from_rows(rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        keys = row_core_label_keys(row)
        counts.update(keys)
    return counts


def row_core_label_keys(row: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in row.get("core_label_keys") if isinstance(row.get("core_label_keys"), list) else []:
        text = str(key or "").strip()
        if text:
            keys.add(text)
    if bool(row.get("has_core_label")) and not keys:
        keys.add("unknown_core_label")
    if numeric_value(row.get("dominant_region_trade_ratio")) is not None and float(row.get("dominant_region_trade_ratio") or 0) >= 0.4:
        keys.add("high_frequency_region")
    if numeric_value(row.get("max_region_daily_profit_multiple")) is not None and float(row.get("max_region_daily_profit_multiple") or 0) > 2:
        keys.add("high_daily_region_profit")
    if (
        numeric_value(row.get("best_region_positive_return_day_ratio")) is not None
        and float(row.get("best_region_positive_return_day_ratio") or 0) >= 0.6
        and int(float(row.get("best_region_trade_count") or 0)) >= 3
    ):
        keys.add("regional_high_win_rate")
    if numeric_value(row.get("low_chip_cost_trade_ratio")) is not None and float(row.get("low_chip_cost_trade_ratio") or 0) > 0.5:
        keys.add("lottery_player")
    if row.get("split_player_validation_passed") is True:
        keys.add("split_player")
    if row.get("liquidity_player_matched") is True:
        keys.add("liquidity_player")
    return keys


def run_hydration_summary(details: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    history_scopes: Counter[str] = Counter()
    for detail in details:
        hydration = detail.get("deep_hydration") if isinstance(detail.get("deep_hydration"), Mapping) else {}
        status = text_value(hydration.get("status")) or "unknown"
        reason = text_value(hydration.get("reason"))
        status_counts[status] += 1
        if reason:
            reason_counts[reason] += 1
        metrics = detail.get("metrics") if isinstance(detail.get("metrics"), Mapping) else {}
        history_scope = text_value(metrics.get("history_scope"))
        if history_scope:
            history_scopes[history_scope] += 1
    return {
        "completed": status_counts.get("completed", 0),
        "skipped": status_counts.get("skipped", 0),
        "failed": status_counts.get("failed", 0),
        "unknown": status_counts.get("unknown", 0),
        "status_counts": dict(status_counts),
        "reason_counts": dict(reason_counts),
        "history_scopes": dict(history_scopes),
    }


def run_finder_ai_diagnostics(details: list[dict[str, Any]], fallback_summary: Mapping[str, Any]) -> dict[str, Any]:
    summary = dict(fallback_summary)
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    eligible = 0
    present = 0
    for detail in details:
        finder_ai = detail.get("finder_ai") if isinstance(detail.get("finder_ai"), Mapping) else {}
        if not finder_ai:
            continue
        present += 1
        brief_generation = (
            finder_ai.get("briefGeneration")
            if isinstance(finder_ai.get("briefGeneration"), Mapping)
            else {}
        )
        gate = (
            brief_generation.get("gate")
            if isinstance(brief_generation.get("gate"), Mapping)
            else {}
        )
        if gate.get("eligible"):
            eligible += 1
        status = text_value(brief_generation.get("status")) or "missing_status"
        reason = text_value(brief_generation.get("reason"))
        status_counts[status] += 1
        if reason:
            reason_counts[reason] += 1
    if present:
        summary["finder_ai_present"] = present
        summary["eligible"] = eligible
    summary["status_counts"] = dict(status_counts)
    summary["reason_counts"] = dict(reason_counts)
    return summary


def run_pipeline_diagnostics(output_dir: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    details = wallet_detail_payloads(output_dir) if should_load_diagnostic_wallet_details(output_dir) else []
    rows = lightweight_summary_rows(output_dir) if not details else []
    hydration_details = details or (wallet_hydration_payloads(output_dir) if should_load_hydration_wallet_details(output_dir) else [])
    core_counts = core_label_key_counts_from_details(details)
    if not details:
        core_counts = core_label_key_counts_from_rows(rows)
    computed_core_wallets = count_core_labeled_detail_payloads(details) if details else 0
    if not details:
        computed_core_wallets = sum(1 for row in rows if row_core_label_keys(row))
    core_wallets = computed_core_wallets
    if not core_wallets and "wallets_core_labeled" in summary:
        core_wallets = int(summary.get("wallets_core_labeled") or 0)
    finder_ai_summary = (
        summary.get("finder_ai_summary")
        if isinstance(summary.get("finder_ai_summary"), Mapping)
        else {}
    )
    return {
        "weather_events": run_weather_diagnostics(output_dir, summary),
        "core_labels": {
            "wallets": core_wallets,
            "by_key": dict(core_counts),
        },
        "hydration": run_hydration_summary(hydration_details) if hydration_details else hydration_summary_from_rows(rows),
        "finder_ai": (
            run_finder_ai_diagnostics(details, finder_ai_summary)
            if details
            else finder_ai_diagnostics_from_rows(rows)
        ),
        "detail_diagnostics_source": (
            "wallet_details"
            if details
            else "hydration_wallet_details"
            if hydration_details
            else "lightweight_rows"
        ),
    }


def count_core_labeled_detail_payloads(details: list[dict[str, Any]]) -> int:
    return sum(1 for detail in details if wallet_payload_has_core_label(detail))


def error_count(output_dir: Path) -> int:
    payload = read_json_file(output_dir / "errors.json", []) or []
    return len(payload) if isinstance(payload, list) else 0


def read_run_summary(output_dir: Path) -> dict[str, Any]:
    payload = read_json_file(output_dir / "analysis_summary.json", {})
    summary = dict(payload) if isinstance(payload, Mapping) else {}
    lightweight = build_lightweight_summary(output_dir)
    import_summary = read_smart_wallet_import_summary(output_dir)
    selected_count = selected_wallet_count(output_dir)
    import_wallet_count = int(import_summary.get("wallet_count") or 0)
    if "wallets_selected" not in summary:
        summary["wallets_selected"] = selected_count
    if "wallets_screened" not in summary:
        summary["wallets_screened"] = import_wallet_count or selected_count
    if "leaderboard_rows_fetched" not in summary:
        summary["leaderboard_rows_fetched"] = import_wallet_count or selected_count
    if "weather_events_indexed" not in summary:
        indexed_events = progress_weather_event_count(output_dir)
        if indexed_events is not None:
            summary["weather_events_indexed"] = indexed_events
    if "errors" not in summary:
        summary["errors"] = error_count(output_dir)
    if "wallets_core_labeled" not in summary:
        summary["wallets_core_labeled"] = (
            count_core_labeled_wallets(output_dir)
            if (output_dir / "analysis_summary.json").exists()
            else 0
        )
    for key in ("label_counts", "averages", "top_wallets_by_pnl", "top_wallets_by_frequency", "finder_ai_summary"):
        if key not in summary:
            summary[key] = lightweight[key]
    summary["diagnostics"] = run_pipeline_diagnostics(output_dir, summary)
    return summary


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    return ""


def compact_text_list(values: Any, *, limit: int = 6, max_length: int = 180) -> list[str]:
    items = values if isinstance(values, list) else []
    results: list[str] = []
    for item in items:
        text = text_value(item)
        if not text and isinstance(item, Mapping):
            text = (
                text_value(item.get("text"))
                or text_value(item.get("note"))
                or text_value(item.get("reason"))
                or text_value(item.get("summary"))
                or text_value(item.get("title"))
            )
        if not text:
            continue
        results.append(text[:max_length])
        if len(results) >= limit:
            break
    return results


def compact_mapping(source: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    payload: dict[str, Any] = {}
    for key in keys:
        if key not in source:
            continue
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        payload[key] = value
    return payload


def compact_finder_label_record(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = compact_mapping(
        item,
        (
            "key",
            "matched",
            "display_name",
            "name",
            "title",
            "reason",
            "description",
        ),
    )
    details_source = item.get("details") if isinstance(item.get("details"), Mapping) else item.get("facts")
    details = compact_mapping(details_source if isinstance(details_source, Mapping) else None, ("region", "city", "reason"))
    if details:
        payload["details"] = details
    evidence = compact_mapping(item.get("evidence") if isinstance(item.get("evidence"), Mapping) else None, ("reason",))
    if evidence:
        payload["evidence"] = evidence
    return payload


def compact_finder_row_for_import(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = compact_mapping(
        row,
        (
            "wallet",
            "address",
            "proxyWallet",
            "user_name",
            "userName",
            "username",
            "x_username",
            "xUsername",
            "pnl",
            "volume",
            "trade_count",
            "weather_trade_ratio",
            "weather_notional_ratio",
            "closed_position_win_rate",
            "closed_profit_multiple",
            "main_region",
            "dominant_region",
            "highest_burst",
            "highest_burst_date",
            "max_region_daily_profit_multiple",
            "recent_evidence_date",
            "selected",
            "watchlist",
            "suggest_watchlist",
            "recommend_watchlist",
            "first_seen_at",
            "firstSeenAt",
        ),
    )
    labels = compact_text_list(row.get("labels"), limit=20, max_length=80)
    if labels:
        payload["labels"] = labels
    reasons = compact_text_list(row.get("reasons"), limit=6, max_length=180)
    if reasons:
        payload["reasons"] = reasons
    return payload


def wallet_address_from_import_row(row: Mapping[str, Any]) -> str:
    wallet = row.get("wallet") if isinstance(row.get("wallet"), Mapping) else {}
    return normalized_wallet_file_name(
        text_value(
            wallet.get("normalizedAddress")
            or wallet.get("normalized_address")
            or wallet.get("address")
            or row.get("normalizedAddress")
            or row.get("normalized_address")
            or row.get("address")
            or row.get("wallet_address")
        ).lower()
    ) or ""


def relay_import_row_from_wallet_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    address = normalized_wallet_file_name(finder_wallet_address(row))
    if not address:
        return None
    labels = [
        {"kind": "tag", "value": label, "source": "finder"}
        for label in compact_text_list(row.get("labels"), limit=20, max_length=80)
    ]
    return {
        "wallet": {
            "address": address,
            "normalizedAddress": address,
            "displayName": text_value(row.get("user_name") or row.get("userName") or row.get("username")),
            "sourceType": "finder_relay",
        },
        "userName": text_value(row.get("user_name") or row.get("userName") or row.get("username")),
        "xUsername": text_value(row.get("x_username") or row.get("xUsername")),
        "labels": labels,
        "summaryText": text_value(row.get("ai_strategy_focus") or row.get("ai_brief_short")),
        "highlights": compact_text_list([row.get("ai_brief_short"), row.get("ai_generation_reason")]),
        "metrics": {
            key: value
            for key, value in {
                "pnl": row.get("pnl"),
                "volume": row.get("volume"),
                "trade_count": row.get("trade_count"),
                "weather_trade_ratio": row.get("weather_trade_ratio"),
                "weather_notional_ratio": row.get("weather_notional_ratio"),
                "closed_position_win_rate": row.get("closed_position_win_rate"),
                "closed_profit_multiple": row.get("closed_profit_multiple"),
            }.items()
            if value not in (None, "")
        },
        "sourceMeta": {
            "source": text_value(row.get("source")) or "finder_relay",
            "rank": row.get("rank"),
        },
    }


def relay_import_row_from_leaderboard_entry(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    address = normalized_wallet_file_name(
        text_value(
            entry.get("proxyWallet")
            or entry.get("wallet")
            or entry.get("address")
            or entry.get("wallet_address")
        ).lower()
    )
    if not address:
        return None
    labels = [
        {"kind": "tag", "value": label, "source": "finder"}
        for label in compact_text_list(entry.get("importedLabels"), limit=12, max_length=80)
    ]
    return {
        "wallet": {
            "address": address,
            "normalizedAddress": address,
            "displayName": text_value(entry.get("userName") or entry.get("user_name") or entry.get("username")),
            "sourceType": "finder_relay",
        },
        "userName": text_value(entry.get("userName") or entry.get("user_name") or entry.get("username")),
        "xUsername": text_value(entry.get("xUsername") or entry.get("x_username")),
        "labels": labels,
        "summaryText": text_value(entry.get("importedSummaryText")),
        "highlights": compact_text_list(entry.get("importedHighlights")),
        "metrics": {
            key: value
            for key, value in {
                "pnl": entry.get("pnl"),
                "volume": entry.get("vol") or entry.get("volume"),
            }.items()
            if value not in (None, "")
        },
        "sourceMeta": {
            **(dict(entry.get("sourceMeta")) if isinstance(entry.get("sourceMeta"), Mapping) else {}),
            "source": "finder_leaderboard",
            "rank": entry.get("rank"),
        },
    }


def relay_source_import_rows(output_dir: Path) -> tuple[list[dict[str, Any]], str]:
    for filename, source_type in (
        (SMART_WALLET_IMPORT_ROWS_FILENAME, "smart_wallet_import_rows"),
        (RELAY_IMPORT_ROWS_FILENAME, "relay_import_rows"),
    ):
        path = output_dir / filename
        if not path.exists():
            continue
        payload = read_json_file(path, [])
        rows = normalize_import_wallet_rows(payload)
        if rows:
            return rows, source_type

    leaderboard_payload = read_json_file(output_dir / "leaderboard.json", [])
    if isinstance(leaderboard_payload, list):
        rows = [
            row
            for row in (relay_import_row_from_leaderboard_entry(item) for item in leaderboard_payload if isinstance(item, Mapping))
            if row is not None
        ]
        rows = normalize_import_wallet_rows(rows)
        if rows:
            return rows, "leaderboard"

    rows = [
        row
        for row in (relay_import_row_from_wallet_row(item) for item in selected_wallet_rows(output_dir))
        if row is not None
    ]
    rows = normalize_import_wallet_rows(rows)
    return rows, "selected_wallets"


def source_meta_core_label_keys(row: Mapping[str, Any]) -> list[str]:
    source_meta = row.get("sourceMeta") if isinstance(row.get("sourceMeta"), Mapping) else {}
    raw_keys = source_meta.get("coreLabelKeys") or source_meta.get("core_label_keys") or row.get("coreLabelKeys") or row.get("core_label_keys")
    keys = compact_text_list(raw_keys, limit=12, max_length=80)
    core_keys = set(CORE_LABEL_KEYS)
    return [key for key in keys if key in core_keys]


def import_row_has_source_core_label(row: Mapping[str, Any]) -> bool:
    source_meta = row.get("sourceMeta") if isinstance(row.get("sourceMeta"), Mapping) else {}
    if bool(source_meta.get("hasCoreLabel") or source_meta.get("has_core_label") or row.get("hasCoreLabel") or row.get("has_core_label")):
        return True
    if source_meta_core_label_keys(row):
        return True
    return False


def is_deepseek_completed_status(status: Any) -> bool:
    return text_value(status).lower() in {"generated", "cached"}


def relay_candidate_import_row(
    source_row: Mapping[str, Any],
    detail_row: Mapping[str, Any] | None,
    *,
    source_run_id: str,
    source_pool: str,
) -> dict[str, Any] | None:
    address = wallet_address_from_import_row(source_row)
    if not address:
        return None
    row = dict(source_row)
    wallet = dict(row.get("wallet")) if isinstance(row.get("wallet"), Mapping) else {}
    wallet["address"] = text_value(wallet.get("address")) or address
    wallet["normalizedAddress"] = address
    wallet["sourceType"] = text_value(wallet.get("sourceType")) or "finder_relay"
    row["wallet"] = wallet

    source_meta = dict(row.get("sourceMeta")) if isinstance(row.get("sourceMeta"), Mapping) else {}
    source_meta.update(
        {
            "source": "finder_run_relay",
            "sourceRunId": source_run_id,
            "sourcePool": source_pool,
            "detailAvailable": bool(detail_row),
        }
    )
    if detail_row:
        ai_status = text_value(detail_row.get("ai_generation_status"))
        ai_reason = text_value(detail_row.get("ai_generation_reason"))
        core_label_keys = compact_text_list(detail_row.get("core_label_keys"), limit=12, max_length=80)
        source_meta.update(
            {
                "aiGenerationStatus": ai_status,
                "aiGenerationReason": ai_reason,
                "hasCoreLabel": bool(detail_row.get("has_core_label") or core_label_keys),
                "coreLabelKeys": core_label_keys,
            }
        )
        metrics = dict(row.get("metrics")) if isinstance(row.get("metrics"), Mapping) else {}
        for key in (
            "pnl",
            "volume",
            "trade_count",
            "weather_trade_ratio",
            "weather_notional_ratio",
            "closed_position_win_rate",
            "closed_profit_multiple",
            "main_region",
            "dominant_region",
            "highest_burst",
            "highest_burst_date",
            "recent_evidence_date",
        ):
            if key not in metrics and detail_row.get(key) not in (None, ""):
                metrics[key] = detail_row.get(key)
        if ai_status:
            metrics["ai_generation_status"] = ai_status
        if ai_reason:
            metrics["ai_generation_reason"] = ai_reason
        row["metrics"] = metrics
        if not text_value(row.get("summaryText")):
            row["summaryText"] = text_value(detail_row.get("ai_strategy_focus") or detail_row.get("ai_brief_short"))
        highlights = compact_text_list(row.get("highlights"))
        for value in (detail_row.get("ai_brief_short"), detail_row.get("ai_generation_reason")):
            text = text_value(value)
            if text and text not in highlights:
                highlights.append(text[:180])
        row["highlights"] = highlights[:12]
    else:
        source_meta.setdefault("aiGenerationStatus", "")
        source_meta.setdefault("aiGenerationReason", "")
        source_meta.setdefault("hasCoreLabel", import_row_has_source_core_label(row))
        source_meta.setdefault("coreLabelKeys", source_meta_core_label_keys(row))
    row["sourceMeta"] = source_meta
    return row


def relay_candidate_has_core_label(source_row: Mapping[str, Any], detail_row: Mapping[str, Any] | None) -> bool:
    if detail_row:
        core_label_keys = detail_row.get("core_label_keys") if isinstance(detail_row.get("core_label_keys"), list) else []
        return bool(detail_row.get("has_core_label") or core_label_keys)
    return import_row_has_source_core_label(source_row)


def relay_candidate_ai_status(source_row: Mapping[str, Any], detail_row: Mapping[str, Any] | None) -> str:
    if detail_row:
        return text_value(detail_row.get("ai_generation_status"))
    metrics = source_row.get("metrics") if isinstance(source_row.get("metrics"), Mapping) else {}
    source_meta = source_row.get("sourceMeta") if isinstance(source_row.get("sourceMeta"), Mapping) else {}
    return text_value(
        source_meta.get("aiGenerationStatus")
        or source_meta.get("ai_generation_status")
        or metrics.get("ai_generation_status")
    )


def build_relay_import_payload_from_run(
    output_dir: Path,
    source_run_id: str,
    *,
    core_label_filter: str = "all",
    deepseek_filter: str = "all",
) -> dict[str, Any]:
    core_filter = text_value(core_label_filter).lower() or "all"
    deepseek = text_value(deepseek_filter).lower() or "all"
    if core_filter not in RELAY_CORE_LABEL_FILTERS:
        raise ValueError("core_label_filter must be all, core, or non_core")
    if deepseek not in RELAY_DEEPSEEK_FILTERS:
        raise ValueError("deepseek_filter must be all, completed, or incomplete")

    source_rows, source_pool = relay_source_import_rows(output_dir)
    detail_by_wallet = {
        finder_wallet_address(row): row
        for row in wallet_detail_rows(output_dir)
        if finder_wallet_address(row)
    }
    wallets: list[dict[str, Any]] = []
    source_total = 0
    completed_count = 0
    incomplete_count = 0
    core_count = 0
    non_core_count = 0

    for source_row in source_rows:
        address = wallet_address_from_import_row(source_row)
        if not address:
            continue
        source_total += 1
        detail_row = detail_by_wallet.get(address)
        has_core_label = relay_candidate_has_core_label(source_row, detail_row)
        ai_status = relay_candidate_ai_status(source_row, detail_row)
        completed = is_deepseek_completed_status(ai_status)
        if completed:
            completed_count += 1
        else:
            incomplete_count += 1
        if has_core_label:
            core_count += 1
        else:
            non_core_count += 1
        if core_filter == "core" and not has_core_label:
            continue
        if core_filter == "non_core" and has_core_label:
            continue
        if deepseek == "completed" and not completed:
            continue
        if deepseek == "incomplete" and completed:
            continue
        relay_row = relay_candidate_import_row(
            source_row,
            detail_row,
            source_run_id=source_run_id,
            source_pool=source_pool,
        )
        if relay_row is not None:
            wallets.append(relay_row)

    file_name = f"finder-relay-{source_run_id}-{core_filter}-{deepseek}.json"
    payload = {
        "source": "finder_run_relay",
        "sourceRunId": source_run_id,
        "sourcePool": source_pool,
        "coreLabelFilter": core_filter,
        "deepSeekFilter": deepseek,
        "generatedAt": now_iso(),
        "wallets": wallets,
    }
    summary = {
        **summarize_import_wallet_rows(wallets),
        "source_run_id": source_run_id,
        "source_pool": source_pool,
        "source_total": source_total,
        "matched_count": len(wallets),
        "deepseek_completed_count": completed_count,
        "deepseek_incomplete_count": incomplete_count,
        "core_labeled_count": core_count,
        "non_core_count": non_core_count,
        "core_label_filter": core_filter,
        "deepseek_filter": deepseek,
    }
    return {
        "payload": payload,
        "file_name": file_name,
        "summary": summary,
        "source_total": source_total,
        "matched_count": len(wallets),
        "completed_count": completed_count,
        "incomplete_count": incomplete_count,
        "core_labeled_count": core_count,
        "non_core_count": non_core_count,
    }


def build_relay_import_payload_for_request(
    state: ServerState,
    body: Mapping[str, Any],
    *,
    default_source_run_id: str = "",
) -> dict[str, Any]:
    source_run_id = text_value(body.get("source_run_id") or body.get("sourceRunId") or default_source_run_id)
    if not source_run_id:
        raise ValueError("source_run_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", source_run_id):
        raise ValueError("source_run_id may only contain letters, numbers, dots, underscores, and dashes")
    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / source_run_id)
    if not output_dir.exists():
        raise ValueError(f"source run does not exist: {source_run_id}")
    return build_relay_import_payload_from_run(
        output_dir,
        source_run_id,
        core_label_filter=text_value(body.get("core_label_filter") or body.get("coreLabelFilter") or "all"),
        deepseek_filter=text_value(
            body.get("deepseek_filter")
            or body.get("deepSeekFilter")
            or body.get("deep_seek_filter")
            or "all"
        ),
    )


def compact_finder_detail_for_import(detail: Mapping[str, Any]) -> dict[str, Any]:
    payload = compact_mapping(detail, ("wallet", "address"))
    for key in ("selection_record", "screening"):
        nested = compact_mapping(detail.get(key) if isinstance(detail.get(key), Mapping) else None, ("wallet", "address", "proxyWallet", "proxy_wallet"))
        if nested:
            payload[key] = nested

    leaderboard_entry = compact_mapping(
        detail.get("leaderboard_entry") if isinstance(detail.get("leaderboard_entry"), Mapping) else None,
        ("userName", "user_name", "xUsername", "x_username"),
    )
    if leaderboard_entry:
        payload["leaderboard_entry"] = leaderboard_entry

    profile = compact_mapping(
        detail.get("profile") if isinstance(detail.get("profile"), Mapping) else None,
        ("userName", "user_name", "xUsername", "x_username"),
    )
    if profile:
        payload["profile"] = profile

    evidence_summary = compact_mapping(
        detail.get("evidence_summary") if isinstance(detail.get("evidence_summary"), Mapping) else None,
        ("headline", "main_region", "latest_evidence_date", "suggest_watchlist"),
    )
    if evidence_summary:
        payload["evidence_summary"] = evidence_summary

    metrics = compact_mapping(detail.get("metrics") if isinstance(detail.get("metrics"), Mapping) else None, ("unified_profit",))
    if metrics:
        payload["metrics"] = metrics

    strategy_notes = compact_text_list(detail.get("strategy_notes"), limit=6, max_length=180)
    if strategy_notes:
        payload["strategy_notes"] = strategy_notes

    label_evaluations = [
        compact_finder_label_record(item)
        for item in (detail.get("label_evaluations") if isinstance(detail.get("label_evaluations"), list) else [])
        if isinstance(item, Mapping)
    ]
    label_evaluations = [item for item in label_evaluations if item]
    if label_evaluations:
        payload["label_evaluations"] = label_evaluations[:24]

    detail_labels = [
        compact_finder_label_record(item)
        for item in (detail.get("labels") if isinstance(detail.get("labels"), list) else [])
        if isinstance(item, Mapping)
    ]
    detail_labels = [item for item in detail_labels if item]
    if detail_labels:
        payload["labels"] = detail_labels[:24]

    return payload


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def format_byte_count(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.2f} {units[unit_index]}"


def chunk_items(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[index : index + size] for index in range(0, len(items), size)]


def finder_wallet_address(row: Mapping[str, Any], detail: Mapping[str, Any] | None = None) -> str:
    detail = detail or {}
    nested_sources = (
        detail.get("selection_record"),
        detail.get("screening"),
        detail.get("leaderboard_entry"),
        detail.get("profile"),
    )
    candidates = [
        row.get("wallet"),
        row.get("address"),
        row.get("proxyWallet"),
        detail.get("wallet"),
        detail.get("address"),
    ]
    for source in nested_sources:
        if isinstance(source, Mapping):
            candidates.extend(
                [
                    source.get("wallet"),
                    source.get("address"),
                    source.get("proxyWallet"),
                    source.get("proxy_wallet"),
                ]
            )
    for candidate in candidates:
        value = text_value(candidate).lower()
        if value:
            return value
    return ""


def normalized_wallet_file_name(address: str) -> str | None:
    value = address.strip().lower()
    return value if WALLET_ADDRESS_RE.match(value) else None


def label_names_from_detail(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if isinstance(item, str):
            label = item.strip()
        elif isinstance(item, Mapping):
            label = (
                text_value(item.get("display_name"))
                or text_value(item.get("displayName"))
                or text_value(item.get("label"))
                or text_value(item.get("name"))
                or text_value(item.get("key"))
            )
        else:
            label = ""
        if label:
            labels.append(label)
    return labels


def first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def wallet_row_from_detail(detail: Mapping[str, Any], fallback_wallet: str = "") -> dict[str, Any]:
    selection = first_mapping(detail.get("selection_record"))
    screening = first_mapping(detail.get("screening"))
    leaderboard_entry = first_mapping(detail.get("leaderboard_entry"))
    profile = first_mapping(detail.get("profile"))
    metrics = first_mapping(detail.get("metrics"))

    wallet = finder_wallet_address(selection, detail) or finder_wallet_address(screening, detail)
    if not wallet:
        wallet = fallback_wallet.strip().lower()

    row: dict[str, Any] = {}
    for source in (selection, screening):
        for key, value in source.items():
            if key not in row and value is not None:
                row[str(key)] = value

    row["wallet"] = wallet
    if "rank" not in row and leaderboard_entry.get("rank") is not None:
        row["rank"] = leaderboard_entry.get("rank")
    if "user_name" not in row:
        row["user_name"] = (
            text_value(selection.get("user_name"))
            or text_value(screening.get("user_name"))
            or text_value(leaderboard_entry.get("userName"))
            or text_value(leaderboard_entry.get("user_name"))
            or text_value(profile.get("userName"))
            or text_value(profile.get("user_name"))
        )
    if "x_username" not in row:
        row["x_username"] = (
            text_value(selection.get("x_username"))
            or text_value(screening.get("x_username"))
            or text_value(leaderboard_entry.get("xUsername"))
            or text_value(leaderboard_entry.get("x_username"))
            or text_value(profile.get("xUsername"))
            or text_value(profile.get("x_username"))
        )
    for key, source_key in (
        ("pnl", "pnl"),
        ("pnl", "leaderboard_pnl"),
        ("volume", "volume"),
        ("trade_count", "trade_count"),
        ("weather_trade_ratio", "weather_trade_ratio"),
        ("weather_notional_ratio", "weather_notional_ratio"),
        ("closed_position_win_rate", "closed_position_win_rate"),
        ("closed_profit_multiple", "closed_profit_multiple"),
        ("main_region", "main_region"),
        ("dominant_region", "dominant_region"),
        ("highest_burst", "highest_burst"),
        ("highest_burst_date", "highest_burst_date"),
        ("recent_evidence_date", "recent_evidence_date"),
    ):
        if key not in row and metrics.get(source_key) is not None:
            row[key] = metrics.get(source_key)

    labels = row.get("labels")
    if not isinstance(labels, list) or not labels:
        labels = label_names_from_detail(detail.get("labels"))
    row["labels"] = labels if isinstance(labels, list) else []
    core_label_keys = wallet_payload_core_label_keys(detail)
    row["has_core_label"] = bool(core_label_keys)
    row["core_label_keys"] = core_label_keys
    finder_ai = detail.get("finder_ai") if isinstance(detail.get("finder_ai"), Mapping) else {}
    if finder_ai:
        brief_generation = finder_ai.get("briefGeneration") if isinstance(finder_ai.get("briefGeneration"), Mapping) else {}
        row.setdefault("ai_strategy_focus", text_value(finder_ai.get("strategyFocus")))
        row.setdefault("ai_brief_short", text_value(finder_ai.get("aiBriefShort")))
        row.setdefault("ai_needs_review", bool(finder_ai.get("needsReview")))
        row.setdefault("ai_has_conflict", bool(finder_ai.get("hasConflict")))
        row.setdefault("ai_evidence_level", text_value(finder_ai.get("evidenceLevel")))
        row.setdefault("ai_generation_status", text_value(brief_generation.get("status")))
        row.setdefault("ai_generation_reason", text_value(brief_generation.get("reason")))
    if "selected" not in row:
        row["selected"] = bool(screening.get("selected", True))
    row["detail_available"] = True
    row["source"] = "wallet_detail"
    return row


def wallet_detail_rows(output_dir: Path) -> list[dict[str, Any]]:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return []

    rows: list[dict[str, Any]] = []
    for wallet_path in sorted(wallets_dir.glob("*.json")):
        fallback_wallet = wallet_path.stem.lower()
        if not normalized_wallet_file_name(fallback_wallet):
            continue
        detail = read_json_file(wallet_path, {}) or {}
        if not isinstance(detail, Mapping):
            continue
        row = wallet_row_from_detail(detail, fallback_wallet=fallback_wallet)
        if normalized_wallet_file_name(str(row.get("wallet") or "")):
            rows.append(row)
    return rows


def wallet_detail_file_wallets(output_dir: Path) -> list[str]:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return []
    wallets: list[str] = []
    for wallet_path in sorted(wallets_dir.glob("*.json")):
        wallet = normalized_wallet_file_name(wallet_path.stem.lower())
        if wallet:
            wallets.append(wallet)
    return wallets


def wallet_detail_row_for_wallet(output_dir: Path, wallet: str) -> dict[str, Any] | None:
    normalized = normalized_wallet_file_name(wallet)
    if not normalized:
        return None
    wallet_path = ensure_under(output_dir / "wallets", output_dir / "wallets" / f"{normalized}.json")
    detail = read_json_file(wallet_path, {}) or {}
    if not isinstance(detail, Mapping):
        return None
    row = wallet_row_from_detail(detail, fallback_wallet=normalized)
    if not normalized_wallet_file_name(str(row.get("wallet") or "")):
        return None
    return row


def detail_rows_for_wallets(output_dir: Path, wallets: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wallet in wallets:
        row = wallet_detail_row_for_wallet(output_dir, wallet)
        if row is not None:
            rows.append(row)
        else:
            rows.append(wallet_stub_row(wallet))
    return rows


def wallet_stub_row(wallet: str) -> dict[str, Any]:
    return {
        "wallet": wallet,
        "selected": True,
        "labels": [],
        "has_core_label": False,
        "core_label_keys": [],
        "detail_available": True,
        "source": "wallet_detail_file",
    }


def compact_wallet_row_for_index(row: Mapping[str, Any], *, source: str | None = None) -> dict[str, Any]:
    payload = compact_mapping(
        row,
        (
            "wallet",
            "address",
            "proxyWallet",
            "user_name",
            "userName",
            "username",
            "x_username",
            "xUsername",
            "rank",
            "pnl",
            "volume",
            "trade_count",
            "weather_trade_count",
            "weather_trade_ratio",
            "weather_notional_ratio",
            "closed_position_win_rate",
            "closed_profit_multiple",
            "median_trade_notional",
            "trades_per_active_day",
            "dominant_region",
            "main_region",
            "dominant_region_trade_ratio",
            "max_region_daily_profit_multiple",
            "highest_burst",
            "highest_burst_region",
            "highest_burst_date",
            "recent_evidence_date",
            "best_region_win_rate_region",
            "best_region_positive_return_day_ratio",
            "best_region_trade_count",
            "low_chip_cost_trade_ratio",
            "activity_level",
            "latest_trade_date",
            "days_since_latest_trade",
            "audit_complete",
            "screening_evidence_complete",
            "history_scope",
            "ai_strategy_focus",
            "ai_brief_short",
            "ai_needs_review",
            "ai_has_conflict",
            "ai_evidence_level",
            "ai_generation_status",
            "ai_generation_reason",
            "selected",
            "has_core_label",
        ),
    )
    labels = compact_text_list(row.get("labels"), limit=20, max_length=80)
    payload["labels"] = labels
    core_label_keys = compact_text_list(row.get("core_label_keys"), limit=12, max_length=80)
    payload["core_label_keys"] = core_label_keys
    payload["has_core_label"] = bool(row.get("has_core_label") or core_label_keys)
    reasons = compact_text_list(row.get("reasons"), limit=6, max_length=180)
    if reasons:
        payload["reasons"] = reasons
    payload["detail_available"] = bool(row.get("detail_available", True))
    payload_source = source or text_value(row.get("source"))
    if payload_source:
        payload["source"] = payload_source
    return payload


def compact_wallet_detail_row_for_index(output_dir: Path, wallet: str) -> dict[str, Any]:
    row = wallet_detail_row_for_wallet(output_dir, wallet)
    if row is None:
        return wallet_stub_row(wallet)
    return compact_wallet_row_for_index(row, source="selected_wallets")


def ensure_selected_wallet_snapshot(output_dir: Path) -> None:
    selected_wallets_path = output_dir / "selected_wallets.json"
    if selected_wallets_path.exists():
        return
    with SELECTED_WALLET_SNAPSHOT_LOCK:
        if selected_wallets_path.exists():
            return
        if not output_dir.exists() or not (output_dir / "wallets").exists():
            return
        if not ((output_dir / "progress.log").exists() or (output_dir / "resolved_config.json").exists()):
            return

        wallets = wallet_detail_file_wallets(output_dir)
        if not wallets:
            return

        rows = [compact_wallet_detail_row_for_index(output_dir, wallet) for wallet in wallets]
        rows = [row for row in rows if normalized_wallet_file_name(str(row.get("wallet") or ""))]
        if not rows:
            return

        try:
            write_json_file(selected_wallets_path, rows)
        except OSError:
            return


def wallet_detail_count(output_dir: Path) -> int:
    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return 0
    return sum(1 for path in wallets_dir.glob("*.json") if path.is_file())


def run_has_final_outputs(output_dir: Path) -> bool:
    return (output_dir / "analysis_summary.json").exists() and (output_dir / "report.txt").exists()


def run_can_resume(output_dir: Path, status: str | None = None) -> bool:
    if str(status or "") in {"queued", "running", "succeeded"}:
        return False
    if run_has_final_outputs(output_dir):
        return False
    return (output_dir / "resolved_config.json").exists() and (
        (output_dir / "progress.log").exists() or wallet_detail_count(output_dir) > 0
    )


def selected_wallet_file_rows(
    output_dir: Path,
    *,
    strict: bool = False,
    repair: bool = True,
) -> list[dict[str, Any]]:
    if repair:
        ensure_selected_wallet_snapshot(output_dir)
    rows = read_json_file(output_dir / "selected_wallets.json", []) or []
    if not isinstance(rows, list):
        if strict:
            raise ValueError("selected_wallets.json must be a JSON array")
        return []
    return [dict(item) for item in rows if isinstance(item, Mapping)]


def merge_wallet_row_with_detail(
    selected_row: Mapping[str, Any],
    detail_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not detail_row:
        return dict(selected_row)
    merged = dict(detail_row)
    for key, value in selected_row.items():
        if key in {"has_core_label", "core_label_keys"}:
            continue
        if value is None:
            continue
        if value == "" and merged.get(key) not in (None, ""):
            continue
        if isinstance(value, list) and not value and merged.get(key):
            continue
        merged[str(key)] = value
    merged["detail_available"] = True
    return merged


def selected_wallet_count(output_dir: Path) -> int:
    selected_rows = selected_wallet_file_rows(output_dir, repair=False)
    seen_wallets: set[str] = set()
    count = 0
    for row in selected_rows:
        wallet = finder_wallet_address(row)
        if wallet:
            seen_wallets.add(wallet)
        count += 1
    if selected_rows:
        return count
    detail_wallets = wallet_detail_file_wallets(output_dir)
    count += sum(1 for wallet in detail_wallets if wallet not in seen_wallets)
    return count


def selected_wallet_rows(output_dir: Path) -> list[dict[str, Any]]:
    selected_rows = selected_wallet_file_rows(output_dir)
    detail_rows = wallet_detail_rows(output_dir)
    if not selected_rows:
        return detail_rows
    if not detail_rows:
        return selected_rows

    detail_by_wallet = {
        finder_wallet_address(row): row
        for row in detail_rows
        if finder_wallet_address(row)
    }
    merged_rows: list[dict[str, Any]] = []
    seen_wallets: set[str] = set()
    for row in selected_rows:
        wallet = finder_wallet_address(row)
        detail_row = detail_by_wallet.get(wallet) if wallet else None
        if not detail_row:
            merged_rows.append(row)
            if wallet:
                seen_wallets.add(wallet)
            continue
        merged_rows.append(merge_wallet_row_with_detail(row, detail_row))
        seen_wallets.add(wallet)
    for row in detail_rows:
        wallet = finder_wallet_address(row)
        if wallet and wallet not in seen_wallets:
            merged_rows.append(row)
            seen_wallets.add(wallet)
    return merged_rows


def selected_wallet_rows_for_wallets(output_dir: Path, requested_wallets: list[str]) -> list[dict[str, Any]]:
    requested = [
        wallet
        for wallet in (normalized_wallet_file_name(value.strip().lower()) for value in requested_wallets)
        if wallet
    ]
    if not requested:
        return []
    selected_by_wallet = {
        finder_wallet_address(row): row
        for row in selected_wallet_file_rows(output_dir, repair=False)
        if finder_wallet_address(row)
    }
    rows: list[dict[str, Any]] = []
    seen_wallets: set[str] = set()
    for wallet in requested:
        if wallet in seen_wallets:
            continue
        seen_wallets.add(wallet)
        selected_row = selected_by_wallet.get(wallet)
        detail_row = wallet_detail_row_for_wallet(output_dir, wallet)
        if selected_row is not None:
            rows.append(merge_wallet_row_with_detail(selected_row, detail_row))
        elif detail_row is not None:
            rows.append(detail_row)
    return rows


def paginated_selected_wallet_rows(
    output_dir: Path,
    *,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    selected_rows = selected_wallet_file_rows(output_dir, repair=False)
    detail_wallets = wallet_detail_file_wallets(output_dir)
    if not selected_rows:
        total = len(detail_wallets)
        page_wallets = detail_wallets[offset : offset + limit]
        if total <= FULL_DETAIL_LIST_WALLET_LIMIT:
            return detail_rows_for_wallets(output_dir, page_wallets), total
        return ([wallet_stub_row(wallet) for wallet in page_wallets], total)

    if len(detail_wallets) <= FULL_DETAIL_LIST_WALLET_LIMIT:
        full_rows = selected_wallet_rows(output_dir)
        total = len(full_rows)
        return full_rows[offset : offset + limit], total

    selected_seen_wallets = {
        finder_wallet_address(row)
        for row in selected_rows
        if finder_wallet_address(row)
    }
    detail_only_wallets = [wallet for wallet in detail_wallets if wallet not in selected_seen_wallets]
    total = len(selected_rows) + len(detail_only_wallets)
    page_rows: list[dict[str, Any]] = []

    selected_start = min(offset, len(selected_rows))
    selected_end = min(offset + limit, len(selected_rows))
    detail_wallet_set = set(detail_wallets)
    for row in selected_rows[selected_start:selected_end]:
        wallet = finder_wallet_address(row)
        page_row = dict(row)
        if wallet and wallet in detail_wallet_set:
            page_row["detail_available"] = True
            page_row.setdefault("source", "selected_wallets")
        page_rows.append(page_row)

    remaining = limit - len(page_rows)
    if remaining > 0:
        detail_offset = max(0, offset - len(selected_rows))
        page_wallets = detail_only_wallets[detail_offset : detail_offset + remaining]
        page_rows.extend(wallet_stub_row(wallet) for wallet in page_wallets)
    return page_rows, total


def filter_wallet_rows(
    rows: list[dict[str, Any]],
    requested_wallets: list[str] | None,
) -> list[dict[str, Any]]:
    if not requested_wallets:
        return [row for row in rows if row.get("selected") is not False]

    requested = {value.strip().lower() for value in requested_wallets if value.strip()}
    return [row for row in rows if finder_wallet_address(row) in requested]


def read_wallet_detail_for_import(output_dir: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    address = normalized_wallet_file_name(finder_wallet_address(row))
    if not address:
        return {}
    wallet_path = ensure_under(output_dir / "wallets", output_dir / "wallets" / f"{address}.json")
    detail = read_json_file(wallet_path, {}) or {}
    return dict(detail) if isinstance(detail, Mapping) else {}


def build_smart_pro_import_payload(
    output_dir: Path,
    run_id: str,
    *,
    requested_wallets: list[str] | None = None,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if requested_wallets:
        rows = filter_wallet_rows(selected_wallet_rows_for_wallets(output_dir, requested_wallets), requested_wallets)
    else:
        rows = filter_wallet_rows(selected_wallet_rows(output_dir), requested_wallets)
    rows = rows[:SMART_PRO_MAX_SYNC_WALLETS]
    wallets: list[dict[str, Any]] = []
    for row in rows:
        detail = read_wallet_detail_for_import(output_dir, row)
        wallet_payload = {
            "row": compact_finder_row_for_import(row),
            "detail": compact_finder_detail_for_import(detail),
        }
        finder_ai = compact_finder_ai_result(detail.get("finder_ai") if isinstance(detail, Mapping) else None)
        if finder_ai:
            wallet_payload["finderAi"] = finder_ai
        wallets.append(wallet_payload)
    if not wallets:
        raise ValueError("no Finder wallets matched the current selection")

    payload: dict[str, Any] = {
        "runId": run_id,
        "sourceName": f"Finder-app:{run_id}",
        "wallets": wallets,
    }
    if filters:
        payload["filters"] = dict(filters)
    return payload


def smart_pro_base_url_from_env(root: Path) -> str:
    load_project_env(root)
    raw_url = (os.environ.get("SMART_PRO_BASE_URL") or os.environ.get("SMART_PRO_URL") or "").strip()
    if not raw_url:
        raise ValueError("SMART_PRO_BASE_URL is not configured in .env")

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("SMART_PRO_BASE_URL must be an http(s) URL")
    return raw_url.rstrip("/")


def smart_pro_token_from_env(root: Path) -> str:
    load_project_env(root)
    token = (
        os.environ.get("SMART_PRO_FINDER_TOKEN")
        or os.environ.get("SMART_PRO_SYNC_TOKEN")
        or os.environ.get("FINDER_SYNC_TOKEN")
        or ""
    ).strip()
    if not token:
        raise ValueError("SMART_PRO_FINDER_TOKEN is not configured in .env")
    return token


def smart_pro_commit_path_from_env(root: Path) -> str:
    load_project_env(root)
    path = (os.environ.get("SMART_PRO_FINDER_COMMIT_PATH") or SMART_PRO_DEFAULT_COMMIT_PATH).strip()
    return path if path.startswith("/") else f"/{path}"


def smart_pro_timeout_from_env(root: Path) -> int:
    load_project_env(root)
    raw_value = os.environ.get("SMART_PRO_SYNC_TIMEOUT_SECONDS")
    try:
        value = int(raw_value or SMART_PRO_DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        value = SMART_PRO_DEFAULT_TIMEOUT_SECONDS
    return max(10, min(300, value))


def smart_pro_access_headers_from_env(root: Path) -> dict[str, str]:
    load_project_env(root)
    client_id = text_value(os.environ.get("SMART_PRO_ACCESS_CLIENT_ID"))
    client_secret = text_value(os.environ.get("SMART_PRO_ACCESS_CLIENT_SECRET"))
    if not client_id or not client_secret:
        return {}
    return {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }


def smart_pro_config_status(root: Path) -> dict[str, Any]:
    load_project_env(root)
    raw_url = (os.environ.get("SMART_PRO_BASE_URL") or os.environ.get("SMART_PRO_URL") or "").strip()
    token = (
        os.environ.get("SMART_PRO_FINDER_TOKEN")
        or os.environ.get("SMART_PRO_SYNC_TOKEN")
        or os.environ.get("FINDER_SYNC_TOKEN")
        or ""
    ).strip()
    errors: list[str] = []
    base_url: str | None = None

    if not raw_url:
        errors.append("SMART_PRO_BASE_URL is not configured in .env")
    else:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("SMART_PRO_BASE_URL must be an http(s) URL")
        else:
            base_url = raw_url.rstrip("/")

    if not token:
        errors.append("SMART_PRO_FINDER_TOKEN is not configured in .env")

    return {
        "configured": not errors,
        "base_url": base_url,
        "commit_path": smart_pro_commit_path_from_env(root),
        "timeout_seconds": smart_pro_timeout_from_env(root),
        "token_configured": bool(token),
        "access_service_token_configured": bool(
            text_value(os.environ.get("SMART_PRO_ACCESS_CLIENT_ID"))
            and text_value(os.environ.get("SMART_PRO_ACCESS_CLIENT_SECRET"))
        ),
        "errors": errors,
    }


def post_json_to_smart_pro(
    url: str,
    token: str,
    payload: Mapping[str, Any],
    timeout_seconds: int,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    data = json_bytes(payload)
    headers = {
        "X-Finder-Sync-Token": token,
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
    }
    if extra_headers:
        headers.update({key: value for key, value in extra_headers.items() if text_value(value)})
    request = urlrequest.Request(
        url,
        data=data,
        method="POST",
        headers=headers,
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
            message = payload.get("error") if isinstance(payload, Mapping) else None
        except json.JSONDecodeError:
            message = None
        raise ValueError(message or f"SmartPro returned HTTP {exc.code}") from exc
    except urlerror.URLError as exc:
        hint = ""
        reason_text = text_value(exc.reason) or str(exc.reason)
        if "EOF occurred in violation of protocol" in reason_text:
            hint = f" while uploading {format_byte_count(len(data))}; Finder will need a smaller sync payload"
        raise ValueError(f"SmartPro request failed: {reason_text}{hint}") from exc

    try:
        result = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise ValueError("SmartPro returned a non-JSON response") from exc
    return dict(result) if isinstance(result, Mapping) else {"data": result}


def summarize_smart_pro_response(response: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), Mapping) else {}
    commit = data.get("commit") if isinstance(data.get("commit"), Mapping) else {}
    failed_rows = commit.get("failedRows") if isinstance(commit.get("failedRows"), list) else []
    return {
        "totalRows": data.get("totalRows"),
        "validRows": data.get("validRows"),
        "createdCount": commit.get("createdCount"),
        "updatedCount": commit.get("updatedCount"),
        "failedCount": len(failed_rows),
        "fallbackReason": data.get("fallbackReason"),
    }


def merge_smart_pro_sync_result(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    current_summary = current.get("summary") if isinstance(current.get("summary"), Mapping) else {}
    incoming_summary = incoming.get("summary") if isinstance(incoming.get("summary"), Mapping) else {}
    fallback_parts = [
        text_value(current_summary.get("fallbackReason")),
        text_value(incoming_summary.get("fallbackReason")),
    ]
    fallback_reasons = [item for item in dict.fromkeys(fallback_parts) if item]

    return {
        **incoming,
        "requested_count": (current.get("requested_count") or 0) + (incoming.get("requested_count") or 0),
        "sent_count": (current.get("sent_count") or 0) + (incoming.get("sent_count") or 0),
        "payload_bytes": (current.get("payload_bytes") or 0) + (incoming.get("payload_bytes") or 0),
        "batch_count": (current.get("batch_count") or 1) + (incoming.get("batch_count") or 1),
        "summary": {
            "totalRows": (current_summary.get("totalRows") or 0) + (incoming_summary.get("totalRows") or 0),
            "validRows": (current_summary.get("validRows") or 0) + (incoming_summary.get("validRows") or 0),
            "createdCount": (current_summary.get("createdCount") or 0) + (incoming_summary.get("createdCount") or 0),
            "updatedCount": (current_summary.get("updatedCount") or 0) + (incoming_summary.get("updatedCount") or 0),
            "failedCount": (current_summary.get("failedCount") or 0) + (incoming_summary.get("failedCount") or 0),
            "fallbackReason": " | ".join(fallback_reasons) if fallback_reasons else None,
        },
    }


def sync_run_to_smart_pro_once(
    state: ServerState,
    *,
    run_id: str,
    requested_wallets: list[str] | None,
    filters: Mapping[str, Any] | None,
    post_json,
) -> dict[str, Any]:
    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
    if not output_dir.exists():
        raise ValueError("run not found")

    smart_payload = build_smart_pro_import_payload(
        output_dir,
        run_id,
        requested_wallets=requested_wallets or None,
        filters=filters,
    )
    payload_bytes = len(json_bytes(smart_payload))
    base_url = smart_pro_base_url_from_env(state.root)
    commit_path = smart_pro_commit_path_from_env(state.root)
    endpoint_url = f"{base_url}{commit_path}"
    try:
        response = post_json(
            endpoint_url,
            smart_pro_token_from_env(state.root),
            smart_payload,
            smart_pro_timeout_from_env(state.root),
            extra_headers=smart_pro_access_headers_from_env(state.root),
        )
    except ValueError as exc:
        raise ValueError(
            f"{exc} (prepared {len(smart_payload['wallets'])} wallet(s), {format_byte_count(payload_bytes)})"
        ) from exc

    return {
        "ok": True,
        "run_id": run_id,
        "requested_count": len(requested_wallets) if requested_wallets else len(smart_payload["wallets"]),
        "sent_count": len(smart_payload["wallets"]),
        "payload_bytes": payload_bytes,
        "batch_count": 1,
        "smart_pro_base_url": base_url,
        "endpoint": commit_path,
        "smart_pro": response,
        "summary": summarize_smart_pro_response(response),
    }


def sync_run_to_smart_pro(
    state: ServerState,
    body: Mapping[str, Any],
    *,
    post_json=post_json_to_smart_pro,
) -> dict[str, Any]:
    run_id = text_value(body.get("run_id") or body.get("runId"))
    if not run_id:
        raise ValueError("run_id is required")

    requested_raw = body.get("wallets") or body.get("wallet_addresses") or body.get("walletAddresses")
    if requested_raw is not None and not isinstance(requested_raw, list):
        raise ValueError("wallets must be a list")
    requested_wallets = [text_value(item) for item in requested_raw or [] if text_value(item)]
    filters = body.get("filters") if isinstance(body.get("filters"), Mapping) else None
    if requested_wallets and len(requested_wallets) > SMART_PRO_SYNC_BATCH_SIZE:
        merged: dict[str, Any] | None = None
        for chunk in chunk_items(requested_wallets, SMART_PRO_SYNC_BATCH_SIZE):
            result = sync_run_to_smart_pro_once(
                state,
                run_id=run_id,
                requested_wallets=chunk,
                filters=filters,
                post_json=post_json,
            )
            merged = result if merged is None else merge_smart_pro_sync_result(merged, result)
        if merged is None:
            raise ValueError("no Finder wallets matched the current selection")
        return merged

    return sync_run_to_smart_pro_once(
        state,
        run_id=run_id,
        requested_wallets=requested_wallets or None,
        filters=filters,
        post_json=post_json,
    )


def runtime_state_path(root: Path) -> Path:
    return root / RUNTIME_STATE_RELATIVE_PATH


def read_runtime_state(root: Path) -> dict[str, Any]:
    payload = read_json_file(runtime_state_path(root), {})
    return payload if isinstance(payload, dict) else {}


def process_id_from(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            process_query_limited_information = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_process_tree(pid: int) -> bool:
    current_pid = os.getpid()
    if pid <= 0 or pid == current_pid:
        return False

    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def runtime_status(root: Path) -> dict[str, Any]:
    runtime = read_runtime_state(root)
    pid_keys = (
        "launcher_pid",
        "api_process_pid",
        "api_port_pid",
        "frontend_process_pid",
        "frontend_port_pid",
        "browser_pid",
    )
    processes = {
        key: {"pid": pid, "running": process_is_running(pid)}
        for key in pid_keys
        if (pid := process_id_from(runtime.get(key))) is not None
    }
    return {
        "ok": True,
        "root": str(root),
        "runtime_state_path": str(runtime_state_path(root)),
        "launched_at": runtime.get("launched_at"),
        "frontend_url": runtime.get("frontend_url", "http://127.0.0.1:41874"),
        "processes": processes,
    }


def runtime_identity(root: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "root": str(root),
        "frontend_url": "http://127.0.0.1:41874",
    }


def schedule_application_shutdown(server: ThreadingHTTPServer, root: Path) -> None:
    runtime = read_runtime_state(root)
    candidate_pids = [
        process_id_from(runtime.get(key))
        for key in (
            "browser_pid",
        )
    ]
    current_pid = os.getpid()
    unique_pids = []
    for pid in candidate_pids:
        if pid and pid != current_pid and pid not in unique_pids:
            unique_pids.append(pid)

    def worker() -> None:
        time.sleep(0.2)
        for pid in unique_pids:
            terminate_process_tree(pid)
            time.sleep(0.2)
        try:
            runtime_state_path(root).unlink(missing_ok=True)
        except OSError:
            pass
        server.shutdown()

    thread = threading.Thread(target=worker, name="polymarket-weather-shutdown", daemon=True)
    thread.start()


def read_progress(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if "\t" in line:
                timestamp, message = line.split("\t", 1)
            else:
                timestamp, message = "", line
            rows.append({"time": timestamp, "message": message})
    except OSError:
        return []
    return rows


def progress_view(rows: list[dict[str, str]], status: str) -> dict[str, Any]:
    if status == "succeeded":
        return {"phase": "Completed", "percent": 100}
    if status == "failed":
        return {"phase": "Failed", "percent": 100}
    if not rows:
        return {"phase": "Queued", "percent": 0 if status == "queued" else 5}

    message = rows[-1]["message"]
    percent = 8
    phase = message
    if "Fetching leaderboard" in message:
        percent = 10
    elif "Fetched" in message and "leaderboard" in message:
        percent = 22
    elif "Fetching weather events" in message:
        percent = 30
    elif "Indexed" in message and "weather" in message:
        percent = 42
    elif "Analyzing wallets" in message:
        percent = 55
        match = re.search(r"(\d+)-(\d+) of (\d+)", message)
        if match:
            end = int(match.group(2))
            total = max(1, int(match.group(3)))
            percent = min(96, 42 + round((end / total) * 52))
    return {"phase": phase, "percent": percent}


def parse_progress_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {
        "completed_wallets": 0,
        "failed_wallets": 0,
        "indexed_weather_events": 0,
        "loaded_wallet_rows": 0,
        "prefilter_kept": 0,
        "prefilter_total": 0,
        "current_batch_start": 0,
        "current_batch_end": 0,
        "current_batch_total": 0,
    }
    for row in rows:
        message = row.get("message", "")
        completed = re.search(r"Wallet completed (\d+) of \d+", message)
        if completed:
            counts["completed_wallets"] = max(counts["completed_wallets"], int(completed.group(1)))
        failed = re.search(r"Wallet failed (\d+):", message)
        if failed:
            counts["failed_wallets"] = max(counts["failed_wallets"], int(failed.group(1)))
        indexed = re.search(r"Indexed (\d+) weather events", message)
        if indexed:
            counts["indexed_weather_events"] = max(counts["indexed_weather_events"], int(indexed.group(1)))
        loaded = re.search(r"Loaded (\d+) imported wallet rows", message)
        if loaded:
            counts["loaded_wallet_rows"] = max(counts["loaded_wallet_rows"], int(loaded.group(1)))
        prefilter = re.search(r"Leaderboard prefilter kept (\d+) of (\d+) candidates", message)
        if prefilter:
            counts["prefilter_kept"] = int(prefilter.group(1))
            counts["prefilter_total"] = int(prefilter.group(2))
        batch = re.search(r"Analyzing wallets (\d+)-(\d+) of (\d+)", message)
        if batch:
            counts["current_batch_start"] = int(batch.group(1))
            counts["current_batch_end"] = int(batch.group(2))
            counts["current_batch_total"] = int(batch.group(3))
    return counts


def active_relay_source_summary(output_dir: Path) -> dict[str, Any]:
    summary = read_json_file(output_dir / RELAY_IMPORT_SUMMARY_FILENAME, {}) or {}
    if not isinstance(summary, Mapping):
        return {}
    return {
        key: summary.get(key)
        for key in (
            "wallet_count",
            "source_file_name",
            "source_type",
            "source_pool",
            "source_total",
            "matched_count",
            "deepseek_completed_count",
            "deepseek_incomplete_count",
            "core_labeled_count",
            "non_core_count",
            "core_label_filter",
            "deepseek_filter",
        )
        if summary.get(key) not in (None, "")
    }


def active_run_diagnostics(output_dir: Path, progress_rows: list[dict[str, str]]) -> dict[str, Any]:
    counts = parse_progress_counts(progress_rows)
    details = wallet_detail_count(output_dir)
    selected = selected_wallet_count(output_dir)
    diagnostics = {
        "progress_counts": counts,
        "wallets": {
            "selected_snapshot": selected,
            "detail_files": details,
            "completed_from_log": counts["completed_wallets"],
            "failed_from_log": counts["failed_wallets"],
            "current_batch_start": counts["current_batch_start"],
            "current_batch_end": counts["current_batch_end"],
            "current_batch_total": counts["current_batch_total"],
            "prefilter_kept": counts["prefilter_kept"],
            "prefilter_total": counts["prefilter_total"],
        },
        "weather_events": {
            "indexed_from_log": counts["indexed_weather_events"],
        },
    }
    relay_summary = active_relay_source_summary(output_dir)
    if relay_summary:
        diagnostics["relay_source"] = relay_summary
    return diagnostics


def file_metadata(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": path.relative_to(root).as_posix(),
        "type": "folder" if path.is_dir() else path.suffix.lstrip(".").lower() or "file",
        "size": stat.st_size if path.is_file() else None,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(timespec="seconds"),
    }


def artifact_run_ids(artifacts_root: Path) -> list[str]:
    if not artifacts_root.exists():
        return []
    runs = []
    for path in artifacts_root.iterdir():
        if not path.is_dir():
            continue
        if any(
            (path / name).exists()
            for name in (
                "resolved_config.json",
                "analysis_summary.json",
                "selected_wallets.json",
                "report.txt",
                "progress.log",
            )
        ) or any((path / "wallets").glob("*.json")):
            runs.append(path.name)
    def sort_timestamp(name: str) -> float:
        parsed = run_datetime_from_id(name)
        if parsed is not None:
            return parsed.timestamp()
        return (artifacts_root / name).stat().st_mtime

    return sorted(runs, key=sort_timestamp, reverse=True)


def infer_artifact_status(output_dir: Path) -> str:
    if (output_dir / "analysis_summary.json").exists() and (output_dir / "report.txt").exists():
        return "succeeded"
    if (output_dir / "errors.json").exists():
        return "partial"
    if (output_dir / "progress.log").exists() or wallet_detail_count(output_dir) > 0:
        return "partial"
    return "artifact"


def public_run_record(state: ServerState, run_id: str, *, include_files: bool = True) -> dict[str, Any]:
    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
    with state.lock:
        memory_state = state.runs.get(run_id)

    if memory_state:
        payload = asdict(memory_state)
    else:
        payload = {
            "run_id": run_id,
            "status": infer_artifact_status(output_dir),
            "output_dir": str(output_dir),
            "created_at": run_created_datetime(run_id, output_dir).isoformat(timespec="seconds"),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
            "progress_log_path": str(output_dir / "progress.log"),
            "traceback": None,
        }

    progress = read_progress(Path(payload["progress_log_path"]) if payload.get("progress_log_path") else None)
    payload["progress"] = progress
    payload.update(progress_view(progress, str(payload["status"])))
    payload["wallet_detail_count"] = wallet_detail_count(output_dir)
    payload["selected_wallet_count"] = selected_wallet_count(output_dir)
    if str(payload.get("status") or "") in {"queued", "running", "partial"}:
        payload["active_diagnostics"] = active_run_diagnostics(output_dir, progress)
    payload["resumable"] = run_can_resume(output_dir, str(payload.get("status") or ""))
    if include_files:
        try:
            summary = read_run_summary(output_dir)
        except Exception:
            summary = {}
        if summary:
            payload["summary"] = {
                key: summary.get(key)
                for key in (
                    "weather_events_indexed",
                    "wallets_screened",
                    "wallets_selected",
                    "wallets_core_labeled",
                    "finder_ai_summary",
                    "errors",
                    "diagnostics",
                )
                if key in summary
            }
        payload["files"] = list_artifact_files(output_dir)
    return payload


def fallback_public_run_record(state: ServerState, run_id: str, error: Exception) -> dict[str, Any]:
    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
    try:
        created_at = datetime.fromtimestamp(output_dir.stat().st_mtime, tz=UTC).isoformat(
            timespec="seconds"
        )
    except OSError:
        created_at = now_iso()
    return {
        "run_id": run_id,
        "status": "artifact",
        "output_dir": str(output_dir),
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": f"run metadata degraded: {error}",
        "progress_log_path": str(output_dir / "progress.log"),
        "traceback": None,
        "progress": [],
        "phase": "Metadata degraded",
        "percent": 5,
        "wallet_detail_count": 0,
        "selected_wallet_count": 0,
        "resumable": False,
        "metadata_error": str(error),
    }


def list_artifact_files(output_dir: Path) -> list[dict[str, Any]]:
    if not output_dir.exists():
        return []
    files = []
    for path in sorted(output_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name)):
        if path.name.startswith("."):
            continue
        files.append(file_metadata(path, output_dir))
    return files


def is_diagnostic_run_name(name: str) -> bool:
    lowered = name.strip().lower()
    return any(token in lowered for token in DIAGNOSTIC_RUN_TOKENS)


def path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            total += int(child.stat().st_size)
        except OSError:
            continue
    return total


def path_file_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def path_modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")


def build_cleanup_item(
    *,
    item_id: str,
    label: str,
    root: Path,
    path: Path,
    item_type: str,
    note: str,
    locked: bool = False,
    locked_reason: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    relative_path = ensure_under(root, path).relative_to(root).as_posix()
    item = {
        "id": item_id,
        "label": label,
        "path": relative_path,
        "item_type": item_type,
        "size_bytes": path_size_bytes(path),
        "file_count": path_file_count(path),
        "modified_at": path_modified_at(path),
        "note": note,
        "locked": locked,
    }
    if locked_reason:
        item["locked_reason"] = locked_reason
    if run_id:
        item["run_id"] = run_id
    if status:
        item["status"] = status
    if extra:
        item.update(extra)
    return item


def build_wallet_registry_items(state: ServerState) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record_path, record in list_wallet_history_records(state.artifacts_root):
        wallet_address = str(record.get("wallet_address") or "").strip().lower()
        if not wallet_address:
            continue
        user_name = str(record.get("user_name") or "").strip()
        label = user_name or wallet_address
        items.append(
            build_cleanup_item(
                item_id=f"wallet_registry:{ensure_under(state.root, record_path).relative_to(state.root).as_posix()}",
                label=label,
                root=state.root,
                path=record_path,
                item_type="wallet_registry_entry",
                note="Previously fetched wallet registry entry. Delete it to allow this wallet to enter future collection flows again.",
                extra={
                    "wallet_address": wallet_address,
                    "user_name": user_name,
                    "x_username": str(record.get("x_username") or "").strip(),
                    "first_seen_at": str(record.get("first_seen_at") or ""),
                    "last_seen_at": str(record.get("last_seen_at") or ""),
                    "run_count": int(record.get("run_count") or 0),
                    "last_run_id": str(record.get("last_run_id") or ""),
                    "last_status": str(record.get("last_status") or ""),
                },
            )
        )
    return sorted(
        items,
        key=lambda item: (
            str(item.get("last_seen_at") or item.get("modified_at") or ""),
            str(item.get("wallet_address") or ""),
        ),
        reverse=True,
    )


def build_wallet_registry_items_cloud_aware(state: ServerState) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    registry = history_registry_store(state)
    registry_status = {
        "storage_backend": str(registry.settings.get("backend") or "local"),
        "cloud_backed": bool(
            registry.settings.get("backend") == "cloudflare"
            and registry.settings.get("cloudflare_account_id")
            and registry.settings.get("cloudflare_d1_database_id")
            and registry.settings.get("cloudflare_api_token")
        ),
    }
    for record_path, record in list_wallet_history_records(state.artifacts_root):
        wallet_address = str(record.get("wallet_address") or "").strip().lower()
        if not wallet_address:
            continue
        user_name = str(record.get("user_name") or "").strip()
        label = user_name or wallet_address
        note = "Previously fetched wallet registry entry. Delete it to allow this wallet to enter future collection flows again."
        if registry_status["cloud_backed"]:
            note = f"{note} Cloudflare backup storage is configured."
        items.append(
            build_cleanup_item(
                item_id=f"wallet_registry:{ensure_under(state.root, record_path).relative_to(state.root).as_posix()}",
                label=label,
                root=state.root,
                path=record_path,
                item_type="wallet_registry_entry",
                note=note,
                extra={
                    "wallet_address": wallet_address,
                    "user_name": user_name,
                    "x_username": str(record.get("x_username") or "").strip(),
                    "first_seen_at": str(record.get("first_seen_at") or ""),
                    "last_seen_at": str(record.get("last_seen_at") or ""),
                    "run_count": int(record.get("run_count") or 0),
                    "last_run_id": str(record.get("last_run_id") or ""),
                    "last_status": str(record.get("last_status") or ""),
                    **registry_status,
                },
            )
        )
    return sorted(
        items,
        key=lambda item: (
            str(item.get("last_seen_at") or item.get("modified_at") or ""),
            str(item.get("wallet_address") or ""),
        ),
        reverse=True,
    )


def run_prunable_paths(output_dir: Path) -> list[Path]:
    if not run_has_final_outputs(output_dir):
        return []
    if run_can_resume(output_dir, infer_artifact_status(output_dir)):
        return []
    paths: list[Path] = []
    for name in RUN_DETAIL_PRUNE_FILES:
        candidate = output_dir / name
        if candidate.exists():
            paths.append(candidate)
    for name in RUN_DETAIL_PRUNE_DIRS:
        candidate = output_dir / name
        if candidate.exists():
            paths.append(candidate)
    return paths


def run_prunable_size_bytes(output_dir: Path) -> int:
    return sum(path_size_bytes(path) for path in run_prunable_paths(output_dir))


def build_cleanup_sections(state: ServerState) -> list[dict[str, Any]]:
    analysis_items: list[dict[str, Any]] = []
    diagnostic_items: list[dict[str, Any]] = []
    wallet_registry_items = build_wallet_registry_items_cloud_aware(state)
    output_items: list[dict[str, Any]] = []
    runtime_items: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()

    with state.lock:
        memory_run_ids = list(state.runs.keys())

    for run_id in memory_run_ids + artifact_run_ids(state.artifacts_root):
        if run_id in seen_run_ids:
            continue
        seen_run_ids.add(run_id)
        output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
        if not output_dir.exists():
            continue
        record = public_run_record(state, run_id, include_files=False)
        locked = str(record.get("status") or "") in {"queued", "running"}
        prunable_bytes = 0 if is_diagnostic_run_name(run_id) else run_prunable_size_bytes(output_dir)
        resumable = bool(record.get("resumable"))
        note = (
            "Deleting this will remove the full analysis output, report, wallet evidence, and source attachments."
            if not is_diagnostic_run_name(run_id)
            else "Development or diagnostic artifacts can usually be cleaned up safely."
        )
        if resumable:
            note = "This run can be resumed; wallet details already written here are protected from detail pruning."
        item = build_cleanup_item(
            item_id=f"run:{run_id}",
            label=run_id,
            root=state.root,
            path=output_dir,
            item_type="diagnostic_run" if is_diagnostic_run_name(run_id) else "analysis_run",
            note=note,
            locked=locked,
            locked_reason="Running tasks cannot be deleted yet." if locked else None,
            run_id=run_id,
            status=str(record.get("status") or "artifact"),
            extra={
                "resumable": resumable,
                "wallet_detail_count": int(record.get("wallet_detail_count") or 0),
                "selected_wallet_count": int(record.get("selected_wallet_count") or 0),
            },
        )
        item.update(summarize_run_archive_manifest(output_dir))
        if prunable_bytes:
            item["detail_prunable_bytes"] = prunable_bytes
        if is_diagnostic_run_name(run_id):
            diagnostic_items.append(item)
        else:
            analysis_items.append(item)

    output_root = state.root / "output"
    if output_root.exists():
        for child in sorted(output_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if child.name.startswith("."):
                continue
            note = "Temporary output, screenshot, or verification directory."
            if child.suffix == ".log":
                note = "Local development or debug log."
            output_items.append(
                build_cleanup_item(
                    item_id=f"output:{ensure_under(state.root, child).relative_to(state.root).as_posix()}",
                    label=child.name,
                    root=state.root,
                    path=child,
                    item_type="temp_output",
                    note=note,
                )
            )

    frontend_test_results_root = state.root / "frontend" / "test-results"
    if frontend_test_results_root.exists():
        for child in sorted(frontend_test_results_root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if child.name.startswith("."):
                continue
            output_items.append(
                build_cleanup_item(
                    item_id=f"output:{ensure_under(state.root, child).relative_to(state.root).as_posix()}",
                    label=child.name,
                    root=state.root,
                    path=child,
                    item_type="temp_output",
                    note="Frontend test output or screenshot directory.",
                )
            )

    api_cache_path = state.root / ".cache" / "polymarket-weather-tool"
    if api_cache_path.exists():
        runtime_items.append(
            build_cleanup_item(
                item_id="runtime:.cache/polymarket-weather-tool",
                label="Polymarket API cache",
                root=state.root,
                path=api_cache_path,
                item_type="runtime_cache",
                note="Cached API responses and derived runtime data.",
            )
        )
    for path in collect_runtime_log_paths(state.root):
        runtime_items.append(
            build_cleanup_item(
                item_id=f"runtime:{ensure_under(state.root, path).relative_to(state.root).as_posix()}",
                label=path.name,
                root=state.root,
                path=path,
                item_type="runtime_log",
                note="Launcher, API, or local development log output.",
            )
        )
    for path in collect_python_cache_paths(state.root):
        runtime_items.append(
            build_cleanup_item(
                item_id=f"runtime:{ensure_under(state.root, path).relative_to(state.root).as_posix()}",
                label=path.relative_to(state.root).as_posix(),
                root=state.root,
                path=path,
                item_type="python_cache",
                note="Python bytecode cache; safe to delete and rebuild automatically.",
            )
        )

    sections = [
        {
            "key": "analysis_runs",
            "label": "Historical analysis results",
            "description": "Final analysis artifacts that can be removed item by item.",
            "items": sorted(analysis_items, key=lambda item: str(item.get("modified_at") or ""), reverse=True),
        },
        {
            "key": "diagnostic_runs",
            "label": "Tests and diagnostics",
            "description": "Development-time smoke tests, validations, and diagnostic records.",
            "items": sorted(diagnostic_items, key=lambda item: str(item.get("modified_at") or ""), reverse=True),
        },
        {
            "key": "wallet_registry",
            "label": "Historical wallet registry",
            "description": "Deduplicated historical wallet-address records that can be removed per wallet.",
            "items": wallet_registry_items,
        },
        {
            "key": "temp_outputs",
            "label": "Temporary output and screenshots",
            "description": "Playwright screenshots, dev logs, and temporary output directories.",
            "items": sorted(output_items, key=lambda item: str(item.get("modified_at") or ""), reverse=True),
        },
        {
            "key": "runtime_storage",
            "label": "Runtime cache and logs",
            "description": "API cache, runtime logs, and Python bytecode cache.",
            "items": sorted(runtime_items, key=lambda item: str(item.get("modified_at") or ""), reverse=True),
        },
    ]
    for section in sections:
        items = section["items"]
        section["count"] = len(items)
        section["size_bytes"] = sum(int(item.get("size_bytes") or 0) for item in items)
    return sections


def build_cleanup_item_index(sections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for section in sections:
        for item in section.get("items", []):
            if isinstance(item, dict):
                index[str(item.get("id") or "")] = item
    return index


def cleanup_targets_for_item(
    state: ServerState,
    item: dict[str, Any],
    *,
    operation: str,
) -> tuple[list[Path], list[str]]:
    item_type = str(item.get("item_type") or "")
    if operation == "delete":
        relative_path = str(item.get("path") or "")
        run_id = str(item.get("run_id") or "")
        return [ensure_under(state.root, state.root / relative_path)], [run_id] if run_id else []

    if operation == "prune":
        if item_type != "analysis_run":
            raise ValueError("Detailed pruning only supports formal analysis history items.")
        run_id = str(item.get("run_id") or "")
        if not run_id:
            raise ValueError("The selected item is not linked to a formal analysis record.")
        output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
        targets = run_prunable_paths(output_dir)
        if not targets:
            raise ValueError("The selected analysis record has no prunable detail attachments.")
        return targets, []

    raise ValueError(f"Unknown cleanup operation: {operation}")


def collect_runtime_log_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    runtime_logs = root / ".cache" / "runtime" / "logs"
    if runtime_logs.exists():
        paths.append(runtime_logs)
    output_root = root / "output"
    if output_root.exists():
        for child in output_root.iterdir():
            if child.is_file() and child.suffix == ".log":
                paths.append(child)
    return paths


def collect_python_cache_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for base in (root / "src", root / "tests"):
        if not base.exists():
            continue
        for child in base.rglob("__pycache__"):
            if child.is_dir():
                paths.append(child)
    return paths


def build_cleanup_action_specs(
    state: ServerState,
    sections: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    item_index = build_cleanup_item_index(sections)
    diagnostic_paths = [
        ensure_under(state.root, state.root / str(item.get("path")))
        for item in item_index.values()
        if str(item.get("item_type") or "") == "diagnostic_run" and not bool(item.get("locked"))
    ]
    diagnostic_run_ids = [
        str(item.get("run_id") or "")
        for item in item_index.values()
        if str(item.get("item_type") or "") == "diagnostic_run" and not bool(item.get("locked"))
    ]
    temp_output_paths = [
        ensure_under(state.root, state.root / str(item.get("path")))
        for item in item_index.values()
        if str(item.get("item_type") or "") == "temp_output"
    ]
    wallet_registry_paths = [
        ensure_under(state.root, state.root / str(item.get("path")))
        for item in item_index.values()
        if str(item.get("item_type") or "") == "wallet_registry_entry"
    ]
    api_cache_path = state.root / ".cache" / "polymarket-weather-tool"
    runtime_cache_paths = [api_cache_path] if api_cache_path.exists() else []
    runtime_log_paths = collect_runtime_log_paths(state.root)
    python_cache_paths = collect_python_cache_paths(state.root)
    prunable_targets: list[Path] = []
    prunable_runs = 0
    for item in item_index.values():
        if str(item.get("item_type") or "") != "analysis_run" or bool(item.get("locked")):
            continue
        run_id = str(item.get("run_id") or "")
        if not run_id:
            continue
        output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
        targets = run_prunable_paths(output_dir)
        if targets:
            prunable_runs += 1
            prunable_targets.extend(targets)

    specs: dict[str, dict[str, Any]] = {
        "delete_diagnostic_records": {
            "key": "delete_diagnostic_records",
            "label": "Delete tests and diagnostics",
            "description": "Remove smoke test records, validation artifacts, screenshots, and temporary output.",
            "warning": "This removes development-time diagnostic records only and does not affect formal analysis history.",
            "paths": diagnostic_paths,
            "deleted_run_ids": diagnostic_run_ids,
            "target_count": len(diagnostic_paths),
        },
        "delete_temp_outputs": {
            "key": "delete_temp_outputs",
            "label": "Delete temporary output and screenshots",
            "description": "Remove temporary artifacts under output and frontend test results while keeping directory structure intact.",
            "warning": "This only removes temporary output and does not affect historical analysis results.",
            "paths": temp_output_paths,
            "deleted_run_ids": [],
            "target_count": len(temp_output_paths),
        },
        "clear_runtime_storage": {
            "key": "clear_runtime_storage",
            "label": "Clear runtime cache and logs",
            "description": "Remove API cache, runtime logs, and Python bytecode caches.",
            "warning": "This does not delete formal analysis results; caches will be rebuilt automatically later.",
            "paths": runtime_cache_paths + runtime_log_paths + python_cache_paths,
            "deleted_run_ids": [],
            "target_count": len(runtime_cache_paths) + len(runtime_log_paths) + len(python_cache_paths),
        },
        "clear_api_cache": {
            "key": "clear_api_cache",
            "label": "Clear API cache",
            "description": "Remove cached Polymarket API responses so the next run fetches fresh data.",
            "warning": "This does not delete analysis results, but the next run may be slower.",
            "paths": runtime_cache_paths,
            "deleted_run_ids": [],
            "target_count": len(runtime_cache_paths),
        },
        "clear_runtime_logs": {
            "key": "clear_runtime_logs",
            "label": "Clear runtime logs",
            "description": "Remove launcher, API, and frontend development logs.",
            "warning": "This is safe for releasing disk space and does not affect analysis outputs.",
            "paths": runtime_log_paths,
            "deleted_run_ids": [],
            "target_count": len(runtime_log_paths),
        },
        "clear_python_caches": {
            "key": "clear_python_caches",
            "label": "Clear Python caches",
            "description": "Remove Python __pycache__ directories.",
            "warning": "These caches are recreated automatically and are usually low risk to remove.",
            "paths": python_cache_paths,
            "deleted_run_ids": [],
            "target_count": len(python_cache_paths),
        },
        "prune_run_details": {
            "key": "prune_run_details",
            "label": "Prune analysis detail attachments",
            "description": "Keep report, summary, and selected wallets while deleting wallet details and source snapshots.",
            "warning": "Summaries and reports remain available, but wallet detail pages and deep evidence will be removed.",
            "paths": prunable_targets,
            "deleted_run_ids": [],
            "target_count": prunable_runs,
        },
        "clear_wallet_registry": {
            "key": "clear_wallet_registry",
            "label": "Clear wallet registry",
            "description": "Remove all historical wallet registry entries.",
            "warning": "After this, future analyses may collect those wallets again.",
            "paths": wallet_registry_paths,
            "deleted_run_ids": [],
            "target_count": len(wallet_registry_paths),
        },
    }
    for spec in specs.values():
        spec["size_bytes"] = sum(path_size_bytes(path) for path in spec["paths"])
    return specs


def build_cleanup_inventory(state: ServerState) -> dict[str, Any]:
    sections = build_cleanup_sections(state)
    action_specs = build_cleanup_action_specs(state, sections)
    actions = [
        {
            "key": spec["key"],
            "label": spec["label"],
            "description": spec["description"],
            "warning": spec["warning"],
            "target_count": spec["target_count"],
            "size_bytes": spec["size_bytes"],
        }
        for spec in action_specs.values()
    ]
    return {
        "generated_at": now_iso(),
        "sections": sections,
        "actions": actions,
        "cloud_archive": build_cloud_archive_status(state),
    }


def dedupe_cleanup_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in sorted((path.resolve() for path in paths if path.exists()), key=lambda item: (len(item.parts), str(item))):
        if path in seen:
            continue
        if any(parent == path or parent in path.parents for parent in unique):
            continue
        unique.append(path)
        seen.add(path)
    return unique


def ensure_cleanup_path_allowed(state: ServerState, path: Path) -> Path:
    target = ensure_under(state.root, path)
    cleanup_roots = (
        state.artifacts_root,
        state.root / "output",
        state.root / ".cache",
        state.root / "frontend" / "test-results",
    )
    for cleanup_root in cleanup_roots:
        cleanup_root = cleanup_root.resolve()
        if target == cleanup_root:
            raise ValueError(f"refusing to delete cleanup root: {target}")
        if cleanup_root in target.parents:
            return target

    python_cache_roots = (state.root / "src", state.root / "tests")
    if target.name == "__pycache__" and any(base.resolve() in target.parents for base in python_cache_roots):
        return target

    raise ValueError(f"path is outside cleanup-safe roots: {target}")


def remove_cleanup_path(state: ServerState, path: Path) -> int:
    target = ensure_cleanup_path_allowed(state, path)
    size_bytes = path_size_bytes(target)
    if not target.exists():
        return 0
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)
    return size_bytes


def ensure_analysis_run_archived_for_cleanup(state: ServerState, item: Mapping[str, Any]) -> dict[str, Any]:
    if str(item.get("item_type") or "") != "analysis_run":
        return {}
    run_id = str(item.get("run_id") or "").strip()
    if not run_id:
        return {}
    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
    run_config = read_run_resolved_config(output_dir)
    archive = cloud_archive_store(state, config=run_config)
    if not archive.enabled or not bool(archive.settings.get("archive_before_cleanup", True)):
        return {}
    manifest = cloud_archive_module.read_run_archive_manifest(output_dir)
    if str(manifest.get("status") or "") == "archived" and int(manifest.get("document_count") or 0) > 0:
        return manifest

    manifest = cloud_archive_module.archive_run_outputs(
        output_dir,
        run_id=run_id,
        config=run_config,
    )
    if str(manifest.get("status") or "") not in {"archived", "disabled"}:
        raise ValueError(f"Cloud archive prep failed before cleanup: {run_id}")
    return manifest


def perform_cleanup_delete(
    state: ServerState,
    *,
    item_ids: list[str] | None = None,
    action_key: str | None = None,
    operation: str = "delete",
) -> dict[str, Any]:
    operation = str(operation or "delete").strip().lower()
    if operation not in {"delete", "prune"}:
        raise ValueError(f"Unknown cleanup operation: {operation}")

    sections = build_cleanup_sections(state)
    item_index = build_cleanup_item_index(sections)
    registry = history_registry_store(state)
    deleted_run_ids: list[str] = []
    target_paths: list[Path] = []
    deleted_item_ids: list[str] = []
    affected_count = 0
    remote_deleted_count = 0

    if item_ids:
        for item_id in item_ids:
            item = item_index.get(item_id)
            if item is None:
                raise ValueError(f"Unknown cleanup item: {item_id}")
            if bool(item.get("locked")):
                raise ValueError(str(item.get("locked_reason") or "The selected item is locked and cannot be cleaned yet."))
            if str(item.get("item_type") or "") == "wallet_registry_entry":
                wallet_address = str(item.get("wallet_address") or "").strip().lower()
                if wallet_address and registry.delete_wallet(wallet_address):
                    remote_deleted_count += 1
                if operation == "delete":
                    deleted_item_ids.append(item_id)
                affected_count += 1
                continue
            ensure_analysis_run_archived_for_cleanup(state, item)
            item_paths, item_run_ids = cleanup_targets_for_item(
                state,
                item,
                operation=operation,
            )
            target_paths.extend(item_paths)
            deleted_run_ids.extend(item_run_ids)
            if operation == "delete":
                deleted_item_ids.append(item_id)
            affected_count += 1
    elif action_key:
        if operation != "delete":
            raise ValueError("Quick cleanup actions do not support extra operation modes.")
        if action_key == "clear_wallet_registry":
            remote_deleted_count += registry.clear()
            affected_count = remote_deleted_count
            return {
                "ok": True,
                "deleted_count": affected_count,
                "deleted_bytes": 0,
                "deleted_item_ids": [],
                "deleted_run_ids": [],
                "inventory": build_cleanup_inventory(state),
            }
        action_specs = build_cleanup_action_specs(state, sections)
        spec = action_specs.get(action_key)
        if spec is None:
            raise ValueError(f"Unknown cleanup action: {action_key}")
        if action_key == "prune_run_details":
            for item in item_index.values():
                if (
                    str(item.get("item_type") or "") == "analysis_run"
                    and not bool(item.get("locked"))
                    and not bool(item.get("resumable"))
                ):
                    ensure_analysis_run_archived_for_cleanup(state, item)
        target_paths.extend(spec["paths"])
        deleted_run_ids.extend(str(run_id) for run_id in spec.get("deleted_run_ids", []))
        affected_count = int(spec.get("target_count") or 0)
    else:
        raise ValueError("Cleanup requests must provide item_ids or action_key.")

    deleted_paths = dedupe_cleanup_paths(target_paths)
    removed_bytes = 0
    for path in deleted_paths:
        removed_bytes += remove_cleanup_path(state, path)

    if deleted_run_ids:
        with state.lock:
            for run_id in deleted_run_ids:
                run_state = state.runs.get(run_id)
                if run_state and run_state.status not in {"queued", "running"}:
                    state.runs.pop(run_id, None)

    return {
        "ok": True,
        "deleted_count": affected_count if affected_count else len(deleted_paths) + remote_deleted_count,
        "deleted_bytes": removed_bytes,
        "deleted_item_ids": deleted_item_ids,
        "deleted_run_ids": sorted({run_id for run_id in deleted_run_ids if run_id}),
        "inventory": build_cleanup_inventory(state),
    }


def build_cloud_archive_status(state: ServerState) -> dict[str, Any]:
    status = cloud_archive_store(state).status()
    status["history_registry"] = history_registry_store(state).status()
    status["history_ledger"] = history_ledger_store(state).status()
    manifests: list[dict[str, Any]] = []
    for run_id in artifact_run_ids(state.artifacts_root):
        output_dir = ensure_under(state.artifacts_root, state.artifacts_root / run_id)
        if not output_dir.exists():
            continue
        summary = summarize_run_archive_manifest(output_dir)
        manifests.append(
            {
                "run_id": run_id,
                **summary,
            }
        )
    status["runs"] = manifests
    return status


def sync_reusable_history_to_cloud(state: ServerState) -> dict[str, Any]:
    registry_status = history_registry_store(state).sync_local_to_cloudflare()
    ledger_status = history_ledger_store(state).sync_local_to_cloudflare()
    return {
        "history_registry": registry_status,
        "history_ledger": ledger_status,
    }


def sync_cloud_archive_run(state: ServerState, run_id: str) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id is required")
    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / normalized_run_id)
    if not output_dir.exists():
        raise ValueError(f"run output does not exist: {normalized_run_id}")
    run_config = read_run_resolved_config(output_dir)
    manifest = cloud_archive_module.archive_run_outputs(
        output_dir,
        run_id=normalized_run_id,
        config=run_config,
    )
    reusable_history = sync_reusable_history_to_cloud(state)
    return {
        "ok": True,
        "run_id": normalized_run_id,
        "manifest": manifest,
        "reusable_history": reusable_history,
        "cloud_archive": build_cloud_archive_status(state),
    }


def build_run_payload(state: ServerState, body: dict[str, Any]) -> RunState:
    run_id = str(body.get("run_id") or new_run_id()).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise ValueError("run_id may only contain letters, numbers, dots, underscores, and dashes")

    raw_output_dir = body.get("output_dir")
    output_dir = (
        resolve_under_root(state.root, str(raw_output_dir))
        if raw_output_dir
        else state.artifacts_root / run_id
    )
    ensure_under(state.artifacts_root, output_dir)

    progress_log_path = output_dir / "progress.log"
    return RunState(
        run_id=run_id,
        status="queued",
        output_dir=str(output_dir),
        created_at=now_iso(),
        progress_log_path=str(progress_log_path),
    )


def build_config_for_run(state: ServerState, body: dict[str, Any], run_state: RunState) -> dict[str, Any]:
    config_path = resolve_under_root(
        state.root,
        str(body.get("config_path") or DEFAULT_CONFIG_PATH),
    )
    ensure_under(state.root, config_path)
    load_project_env(state.root)
    config = load_config(config_path)
    config = apply_analysis_mode(config, body.get("analysis_mode"))
    overrides = dict(body.get("overrides") or {})
    config = apply_overrides(
        config,
        target_count=optional_int(overrides.get("target_count")),
        min_pnl=optional_number(overrides.get("min_pnl")),
        max_pnl=optional_number(overrides.get("max_pnl")),
        min_volume=optional_number(overrides.get("min_volume")),
        max_volume=optional_number(overrides.get("max_volume")),
        min_traded_count=optional_int(overrides.get("min_traded_count")),
        max_traded_count=optional_int(overrides.get("max_traded_count")),
        min_weather_trade_ratio=optional_number(overrides.get("min_weather_trade_ratio")),
        min_weather_notional_ratio=optional_number(overrides.get("min_weather_notional_ratio")),
        weather_focus_mode=optional_str(overrides.get("weather_focus_mode")),
        activity_filter_mode=optional_str(overrides.get("activity_filter_mode")),
        fetch_limit=optional_int(overrides.get("fetch_limit")),
        max_fetch_limit=optional_int(overrides.get("max_fetch_limit")),
        max_weather_events=optional_int(overrides.get("max_weather_events")),
        max_wallet_offset=optional_int(overrides.get("max_wallet_offset")),
        concurrent_wallets=optional_int(overrides.get("concurrent_wallets")),
        verbose=optional_bool(overrides.get("verbose")),
        use_cache=optional_bool(overrides.get("use_cache")),
        enable_chain_validation=optional_bool(overrides.get("enable_chain_validation")),
        chain_api_key_env=optional_str(overrides.get("chain_api_key_env")),
    )

    runtime = config.setdefault("runtime", {})
    runtime["analysis_mode"] = runtime.get("analysis_mode", "standard")
    runtime["run_id"] = run_state.run_id
    runtime["progress_log_path"] = run_state.progress_log_path
    return config


def build_resume_config_for_run(
    state: ServerState,
    run_id: str,
    output_dir: Path,
    run_state: RunState,
) -> dict[str, Any]:
    config = read_run_resolved_config(output_dir)
    if not config:
        raise ValueError("run cannot be resumed because resolved_config.json is missing or invalid")
    runtime = config.setdefault("runtime", {})
    runtime["run_id"] = run_id
    runtime["progress_log_path"] = run_state.progress_log_path
    runtime["resume_existing_output"] = True
    runtime["artifacts_root"] = str(state.artifacts_root.resolve())
    return config


def resume_existing_run(state: ServerState, run_id: str) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", normalized_run_id):
        raise ValueError("run_id may only contain letters, numbers, dots, underscores, and dashes")

    output_dir = ensure_under(state.artifacts_root, state.artifacts_root / normalized_run_id)
    if not output_dir.exists():
        raise ValueError(f"run output does not exist: {normalized_run_id}")

    with state.lock:
        existing_state = state.runs.get(normalized_run_id)
        existing_status = str(existing_state.status if existing_state else infer_artifact_status(output_dir))
        if existing_status in {"queued", "running"}:
            raise ValueError("run is already queued or running")
        if not run_can_resume(output_dir, existing_status):
            raise ValueError("run is not resumable")

    run_state = RunState(
        run_id=normalized_run_id,
        status="queued",
        output_dir=str(output_dir),
        created_at=now_iso(),
        progress_log_path=str(output_dir / "progress.log"),
    )
    config = build_resume_config_for_run(state, normalized_run_id, output_dir, run_state)
    with state.lock:
        existing_state = state.runs.get(normalized_run_id)
        if existing_state and existing_state.status in {"queued", "running"}:
            raise ValueError("run is already queued or running")
        state.runs[normalized_run_id] = run_state
    run_in_background(state, run_state, config)
    return public_run_record(state, normalized_run_id)


def prepare_import_wallet_source_for_run(
    state: ServerState,
    body: Mapping[str, Any],
    run_state: RunState,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    runtime = config.setdefault("runtime", {})
    analysis_mode = str(runtime.get("analysis_mode") or "").strip().lower()
    if analysis_mode not in {SMART_WALLET_LIBRARY_REFRESH_MODE, RELAY_ANALYSIS_MODE}:
        return None

    import_payload = body.get("wallet_import")
    if not isinstance(import_payload, Mapping):
        import_payload = body.get("smart_wallet_import")
    if not isinstance(import_payload, Mapping) and analysis_mode == RELAY_ANALYSIS_MODE:
        relay_import = body.get("relay_import")
        if isinstance(relay_import, Mapping):
            relay_payload = build_relay_import_payload_for_request(state, relay_import)
            if int(relay_payload.get("matched_count") or 0) <= 0:
                raise ValueError("No source wallets matched the relay filters.")
            import_payload = {
                "file_name": relay_payload.get("file_name"),
                "payload": relay_payload.get("payload"),
                "summary": relay_payload.get("summary"),
            }
    if not isinstance(import_payload, Mapping):
        if analysis_mode == RELAY_ANALYSIS_MODE:
            raise ValueError("Relay analysis requires relay_import or wallet_import data.")
        raise ValueError("Smart wallet library refresh requires wallet_import data.")

    rows = normalize_import_wallet_rows(import_payload.get("payload"))
    if not rows:
        if analysis_mode == RELAY_ANALYSIS_MODE:
            raise ValueError("No valid wallet records could be parsed from the relay wallet payload.")
        raise ValueError("No valid wallet records could be parsed from the smart wallet JSON payload.")

    default_file_name = "finder-relay-wallets.json" if analysis_mode == RELAY_ANALYSIS_MODE else "smart-wallet-export.json"
    source_file_name = str(import_payload.get("file_name") or default_file_name).strip()
    output_dir = ensure_under(state.artifacts_root, Path(run_state.output_dir))
    rows_filename = (
        RELAY_IMPORT_ROWS_FILENAME
        if analysis_mode == RELAY_ANALYSIS_MODE
        else SMART_WALLET_IMPORT_ROWS_FILENAME
    )
    summary_filename = (
        RELAY_IMPORT_SUMMARY_FILENAME
        if analysis_mode == RELAY_ANALYSIS_MODE
        else SMART_WALLET_IMPORT_SUMMARY_FILENAME
    )
    rows_path = output_dir / rows_filename
    summary_path = output_dir / summary_filename

    if analysis_mode == SMART_WALLET_LIBRARY_REFRESH_MODE:
        summary = materialize_smart_wallet_library(
            state.artifacts_root,
            rows,
            source_file_name=source_file_name,
        )
    else:
        relay_payload = import_payload.get("payload") if isinstance(import_payload.get("payload"), Mapping) else {}
        relay_summary = import_payload.get("summary") if isinstance(import_payload.get("summary"), Mapping) else {}
        summary = {
            **dict(relay_summary),
            **summarize_import_wallet_rows(rows),
            "source_file_name": source_file_name,
            "source_type": "finder_relay",
        }
        for key, value in {
            "source_run_id": text_value(relay_summary.get("source_run_id") or relay_payload.get("sourceRunId")),
            "source_pool": text_value(relay_summary.get("source_pool") or relay_payload.get("sourcePool")),
            "core_label_filter": text_value(relay_summary.get("core_label_filter") or relay_payload.get("coreLabelFilter")),
            "deepseek_filter": text_value(relay_summary.get("deepseek_filter") or relay_payload.get("deepSeekFilter")),
        }.items():
            if value:
                summary[key] = value
    write_json_file(rows_path, rows)
    write_json_file(summary_path, summary)

    wallet_count = int(summary.get("wallet_count") or len(rows))
    runtime["import_wallet_source_path"] = str(rows_path)
    runtime["import_wallet_summary_path"] = str(summary_path)
    runtime["import_wallet_file_name"] = source_file_name
    runtime["import_wallet_count"] = wallet_count
    runtime["import_wallet_skip_history_registry"] = True
    runtime["import_wallet_skip_numeric_prefilter"] = True
    runtime["import_wallet_process_all"] = True
    if analysis_mode == SMART_WALLET_LIBRARY_REFRESH_MODE:
        runtime["smart_wallet_library_source_path"] = str(rows_path)
        runtime["smart_wallet_library_summary_path"] = str(summary_path)
        runtime["smart_wallet_library_file_name"] = source_file_name
        runtime["smart_wallet_library_wallet_count"] = wallet_count
    if analysis_mode == RELAY_ANALYSIS_MODE:
        runtime["relay_source_path"] = str(rows_path)
        runtime["relay_source_file_name"] = source_file_name
        runtime["relay_wallet_count"] = wallet_count
    return summary


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def run_in_background(state: ServerState, run_state: RunState, config: dict[str, Any]) -> None:
    def target() -> None:
        from .analysis import run_pipeline

        with state.lock:
            run_state.status = "running"
            run_state.started_at = now_iso()
        try:
            result = run_pipeline(config=config, output_dir=Path(run_state.output_dir))
            with state.lock:
                run_state.status = "succeeded"
                run_state.result = result
                run_state.finished_at = now_iso()
        except Exception as exc:  # pragma: no cover - defensive HTTP boundary
            with state.lock:
                run_state.status = "failed"
                run_state.error = str(exc)
                run_state.traceback = traceback.format_exc()
                run_state.finished_at = now_iso()

    thread = threading.Thread(target=target, name=f"polymarket-weather-{run_state.run_id}", daemon=True)
    thread.start()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "PolymarketWeatherAPI/0.1"

    @property
    def app_state(self) -> ServerState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:
        if not self.request_origin_allowed():
            self.send_error_json(HTTPStatus.FORBIDDEN, "origin is not allowed")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
            query = parse_qs(parsed.query)
            if parts == ["api", "health"]:
                self.send_json({"ok": True, "time": now_iso()})
                return
            if parts == ["api", "system", "status"]:
                self.send_json(runtime_status(self.app_state.root))
                return
            if parts == ["api", "system", "identity"]:
                self.send_json(runtime_identity(self.app_state.root))
                return
            if parts == ["api", "smart-pro", "config"]:
                self.send_json(smart_pro_config_status(self.app_state.root))
                return
            if parts == ["api", "config", "default"]:
                config_path = ensure_under(self.app_state.root, self.app_state.root / DEFAULT_CONFIG_PATH)
                self.send_json(read_json_file(config_path, {}))
                return
            if parts == ["api", "history", "cleanup"]:
                self.send_json(build_cleanup_inventory(self.app_state))
                return
            if parts == ["api", "history", "cloud", "status"]:
                self.send_json(build_cloud_archive_status(self.app_state))
                return
            if parts == ["api", "runs"]:
                self.send_json({"items": self.handle_list_runs()})
                return
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                self.handle_run_get(parts[2:], query)
                return
            if parts and parts[0] == "api":
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            self.serve_frontend(parsed.path)
            return
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        try:
            if not self.request_origin_allowed():
                self.send_error_json(HTTPStatus.FORBIDDEN, "origin is not allowed")
                return
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
            body = self.read_json_body()
            if parts == ["api", "runs"]:
                run_state = build_run_payload(self.app_state, body)
                config = build_config_for_run(self.app_state, body, run_state)
                with self.app_state.lock:
                    if run_state.run_id in self.app_state.runs:
                        self.send_error_json(HTTPStatus.CONFLICT, "run_id already exists")
                        return
                Path(run_state.output_dir).mkdir(parents=True, exist_ok=True)
                prepare_import_wallet_source_for_run(self.app_state, body, run_state, config)
                with self.app_state.lock:
                    self.app_state.runs[run_state.run_id] = run_state
                run_in_background(self.app_state, run_state, config)
                self.send_json(public_run_record(self.app_state, run_state.run_id), status=HTTPStatus.ACCEPTED)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "relay-import":
                payload = build_relay_import_payload_for_request(
                    self.app_state,
                    body,
                    default_source_run_id=parts[2],
                )
                self.send_json(payload)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "resume":
                self.send_json(
                    resume_existing_run(self.app_state, parts[2]),
                    status=HTTPStatus.ACCEPTED,
                )
                return
            if parts == ["api", "system", "shutdown"]:
                self.send_json({"ok": True, "message": "application shutdown requested"})
                schedule_application_shutdown(self.server, self.app_state.root)  # type: ignore[arg-type]
                return
            if parts == ["api", "smart-pro", "import", "commit"]:
                self.send_json(sync_run_to_smart_pro(self.app_state, body))
                return
            if parts == ["api", "config", "default"]:
                self.update_default_config(body)
                return
            if parts == ["api", "history", "cleanup", "delete"]:
                item_ids_raw = body.get("item_ids") or []
                action_key = str(body.get("action_key") or "").strip() or None
                operation = str(body.get("operation") or "delete").strip().lower() or "delete"
                if item_ids_raw and not isinstance(item_ids_raw, list):
                    raise ValueError("item_ids must be a list")
                item_ids = [str(item).strip() for item in item_ids_raw if str(item).strip()] if isinstance(item_ids_raw, list) else []
                self.send_json(
                    perform_cleanup_delete(
                        self.app_state,
                        item_ids=item_ids or None,
                        action_key=action_key,
                        operation=operation,
                    )
                )
                return
            if parts == ["api", "history", "cloud", "sync"]:
                run_id = str(body.get("run_id") or "").strip()
                if run_id:
                    self.send_json(sync_cloud_archive_run(self.app_state, run_id))
                else:
                    self.send_json(
                        {
                            "ok": True,
                            **sync_reusable_history_to_cloud(self.app_state),
                            "cloud_archive": build_cloud_archive_status(self.app_state),
                        }
                    )
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PUT(self) -> None:
        self.do_POST()

    def handle_list_runs(self) -> list[dict[str, Any]]:
        items = []
        seen: set[str] = set()
        with self.app_state.lock:
            memory_run_ids = list(self.app_state.runs.keys())
        for run_id in memory_run_ids + artifact_run_ids(self.app_state.artifacts_root):
            if run_id in seen:
                continue
            seen.add(run_id)
            try:
                items.append(public_run_record(self.app_state, run_id, include_files=False))
            except Exception as exc:
                try:
                    items.append(fallback_public_run_record(self.app_state, run_id, exc))
                except Exception:
                    continue
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def handle_run_get(self, parts: list[str], query: dict[str, list[str]]) -> None:
        run_id = parts[0]
        output_dir = ensure_under(self.app_state.artifacts_root, self.app_state.artifacts_root / run_id)
        if not output_dir.exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, "run not found")
            return

        if len(parts) == 1:
            self.send_json(public_run_record(self.app_state, run_id))
            return
        if parts[1:] == ["summary"]:
            self.send_json(read_run_summary(output_dir))
            return
        if parts[1:] == ["wallets"]:
            self.send_json(self.wallets_response(output_dir, query))
            return
        if len(parts) == 3 and parts[1] == "wallets":
            wallet = parts[2].lower()
            wallet_path = ensure_under(output_dir / "wallets", output_dir / "wallets" / f"{wallet}.json")
            payload = read_json_file(wallet_path)
            if payload is None:
                self.send_error_json(HTTPStatus.NOT_FOUND, "wallet not found")
                return
            self.send_json(payload)
            return
        if parts[1:] == ["report"]:
            report_path = output_dir / "report.txt"
            if not report_path.exists():
                self.send_error_json(HTTPStatus.NOT_FOUND, "report not found")
                return
            self.send_text(report_path.read_text(encoding="utf-8"))
            return
        if parts[1:] == ["files"]:
            self.send_json({"items": list_artifact_files(output_dir)})
            return
        if parts[1:] == ["artifact"]:
            path_values = query.get("path") or [""]
            self.send_artifact(output_dir, path_values[0])
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def wallets_response(self, output_dir: Path, query: dict[str, list[str]]) -> dict[str, Any]:
        offset = int((query.get("offset") or ["0"])[0])
        limit = int((query.get("limit") or ["50"])[0])
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        wallets, total = paginated_selected_wallet_rows(output_dir, offset=offset, limit=limit)
        return {"items": wallets, "total": total, "offset": offset, "limit": limit}

    def send_artifact(self, output_dir: Path, raw_path: str) -> None:
        if not raw_path:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "path is required")
            return
        artifact_path = ensure_under(output_dir, output_dir / raw_path)
        if artifact_path.is_dir():
            self.send_json({"items": list_artifact_files(artifact_path)})
            return
        if not artifact_path.exists():
            if raw_path == "errors.json":
                self.send_json([])
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "artifact not found")
            return
        text = artifact_path.read_text(encoding="utf-8")
        content_type = "application/json" if artifact_path.suffix == ".json" else "text/plain; charset=utf-8"
        self.send_text(text, content_type=content_type)

    def update_default_config(self, body: dict[str, Any]) -> None:
        payload = body.get("config", body)
        if not isinstance(payload, dict):
            raise ValueError("config payload must be an object")
        config_path = ensure_under(self.app_state.root, self.app_state.root / DEFAULT_CONFIG_PATH)
        backup_path = config_path.with_suffix(".json.bak")
        if config_path.exists() and not backup_path.exists():
            backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        write_json_file(config_path, payload)
        self.send_json({"ok": True, "path": str(config_path)})

    def serve_frontend(self, raw_path: str) -> None:
        dist_root = FRONTEND_DIST.resolve()
        index_path = dist_root / "index.html"
        if not index_path.exists():
            self.send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "frontend build is missing; run npm run build in frontend",
            )
            return

        relative = unquote(raw_path.split("?", 1)[0]).lstrip("/")
        if not relative or relative.endswith("/"):
            target_path = index_path
        else:
            try:
                target_path = ensure_under(dist_root, dist_root / relative)
            except ValueError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not target_path.exists() or target_path.is_dir():
                target_path = index_path

        content_type = mimetypes.guess_type(str(target_path))[0] or "application/octet-stream"
        if target_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target_path.suffix in {".js", ".mjs"}:
            content_type = "text/javascript; charset=utf-8"
        elif target_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"

        try:
            data = target_path.read_bytes()
        except OSError as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return

        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if target_path == index_path:
            self.send_header("Cache-Control", "no-cache")
        elif "/assets/" in target_path.as_posix():
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def request_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return not origin or origin in ALLOWED_BROWSER_ORIGINS

    def send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_BROWSER_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "false")
        self.send_header("Vary", "Origin")

    def send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(
        self,
        text: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status=status)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[api] {self.address_string()} - {format % args}")


def create_server(host: str, port: int, root: Path = PROJECT_ROOT) -> ThreadingHTTPServer:
    root = root.resolve()
    artifacts_root = root / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), ApiHandler)
    server.app_state = ServerState(root=root, artifacts_root=artifacts_root)  # type: ignore[attr-defined]
    return server


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the local Polymarket Weather Tool API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=41874)
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    args = parser.parse_args()

    load_project_env(args.root)
    server = create_server(args.host, args.port, root=Path(args.root))
    print(f"Polymarket Weather API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
