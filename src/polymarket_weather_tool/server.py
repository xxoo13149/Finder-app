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
)


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
            if isinstance(item, Mapping) and str(item.get("key") or "") in core_label_keys:
                return True
    return False


def count_core_labeled_wallets(output_dir: Path) -> int:
    selected_wallets = read_json_file(output_dir / "selected_wallets.json", []) or []
    if isinstance(selected_wallets, list) and selected_wallets:
        matched_by_wallet: dict[str, bool] = {}
        for item in selected_wallets:
            if not isinstance(item, Mapping):
                continue
            wallet = str(item.get("wallet") or "").strip().lower()
            if not wallet or wallet in matched_by_wallet:
                continue
            wallet_path = ensure_under(output_dir / "wallets", output_dir / "wallets" / f"{wallet}.json")
            matched_by_wallet[wallet] = wallet_payload_has_core_label(read_json_file(wallet_path, {}))
        return sum(
            1
            for item in selected_wallets
            if isinstance(item, Mapping)
            and matched_by_wallet.get(str(item.get("wallet") or "").strip().lower(), False)
        )

    wallets_dir = output_dir / "wallets"
    if not wallets_dir.exists():
        return 0

    count = 0
    for wallet_path in wallets_dir.glob("*.json"):
        if wallet_payload_has_core_label(read_json_file(wallet_path, {})):
            count += 1
    return count


def read_run_summary(output_dir: Path) -> dict[str, Any]:
    payload = read_json_file(output_dir / "analysis_summary.json", {})
    summary = dict(payload) if isinstance(payload, Mapping) else {}
    if "wallets_selected" not in summary:
        selected_wallets = read_json_file(output_dir / "selected_wallets.json", []) or []
        summary["wallets_selected"] = len(selected_wallets) if isinstance(selected_wallets, list) else 0
    if "wallets_core_labeled" not in summary:
        summary["wallets_core_labeled"] = count_core_labeled_wallets(output_dir)
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


def selected_wallet_rows(output_dir: Path) -> list[dict[str, Any]]:
    rows = read_json_file(output_dir / "selected_wallets.json", []) or []
    if not isinstance(rows, list):
        raise ValueError("selected_wallets.json must be a JSON array")
    return [dict(item) for item in rows if isinstance(item, Mapping)]


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
            )
        ):
            runs.append(path.name)
    return sorted(runs, key=lambda name: (artifacts_root / name).stat().st_mtime, reverse=True)


def infer_artifact_status(output_dir: Path) -> str:
    if (output_dir / "analysis_summary.json").exists() and (output_dir / "report.txt").exists():
        return "succeeded"
    if (output_dir / "errors.json").exists():
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
            "created_at": datetime.fromtimestamp(output_dir.stat().st_mtime, tz=UTC).isoformat(
                timespec="seconds"
            ),
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
    if include_files:
        payload["files"] = list_artifact_files(output_dir)
    return payload


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
        note = (
            "Deleting this will remove the full analysis output, report, wallet evidence, and source attachments."
            if not is_diagnostic_run_name(run_id)
            else "Development or diagnostic artifacts can usually be cleaned up safely."
        )
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
                if str(item.get("item_type") or "") == "analysis_run" and not bool(item.get("locked")):
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


def prepare_smart_wallet_import_for_run(
    state: ServerState,
    body: Mapping[str, Any],
    run_state: RunState,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    runtime = config.setdefault("runtime", {})
    analysis_mode = str(runtime.get("analysis_mode") or "").strip().lower()
    if analysis_mode != SMART_WALLET_LIBRARY_REFRESH_MODE:
        return None

    import_payload = body.get("smart_wallet_import")
    if not isinstance(import_payload, Mapping):
        raise ValueError("Smart wallet library refresh requires smart_wallet_import data.")

    rows = normalize_import_wallet_rows(import_payload.get("payload"))
    if not rows:
        raise ValueError("No valid wallet records could be parsed from the smart wallet JSON payload.")

    source_file_name = str(import_payload.get("file_name") or "smart-wallet-export.json").strip()
    output_dir = ensure_under(state.artifacts_root, Path(run_state.output_dir))
    rows_path = output_dir / SMART_WALLET_IMPORT_ROWS_FILENAME
    summary_path = output_dir / SMART_WALLET_IMPORT_SUMMARY_FILENAME

    summary = materialize_smart_wallet_library(
        state.artifacts_root,
        rows,
        source_file_name=source_file_name,
    )
    write_json_file(rows_path, rows)
    write_json_file(summary_path, summary)

    runtime["smart_wallet_library_source_path"] = str(rows_path)
    runtime["smart_wallet_library_summary_path"] = str(summary_path)
    runtime["smart_wallet_library_file_name"] = source_file_name
    runtime["smart_wallet_library_wallet_count"] = int(summary.get("wallet_count") or len(rows))
    runtime["smart_wallet_library_skip_history_registry"] = True
    runtime["smart_wallet_library_skip_numeric_prefilter"] = True
    runtime["smart_wallet_library_process_all"] = True
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
                prepare_smart_wallet_import_for_run(self.app_state, body, run_state, config)
                with self.app_state.lock:
                    self.app_state.runs[run_state.run_id] = run_state
                run_in_background(self.app_state, run_state, config)
                self.send_json(public_run_record(self.app_state, run_state.run_id), status=HTTPStatus.ACCEPTED)
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
            except OSError:
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
        wallets = read_json_file(output_dir / "selected_wallets.json", []) or []
        offset = int((query.get("offset") or ["0"])[0])
        limit = int((query.get("limit") or [str(len(wallets) or 50)])[0])
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        return {"items": wallets[offset : offset + limit], "total": len(wallets), "offset": offset, "limit": limit}

    def send_artifact(self, output_dir: Path, raw_path: str) -> None:
        if not raw_path:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "path is required")
            return
        artifact_path = ensure_under(output_dir, output_dir / raw_path)
        if artifact_path.is_dir():
            self.send_json({"items": list_artifact_files(artifact_path)})
            return
        if not artifact_path.exists():
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
