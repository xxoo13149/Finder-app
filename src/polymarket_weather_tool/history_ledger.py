from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from .cloudflare_backend import (
    CloudflareD1Config,
    CloudflareD1RequestError,
    coerce_bool,
    normalize_backend_choice,
    cloudflare_d1_select_rows,
    cloudflare_d1_upsert_rows,
)


UTC = timezone.utc
HISTORY_LEDGER_DIRNAME = "_history_ledger"
DEFAULT_HISTORY_LEDGER_BACKEND = "local"
DEFAULT_HISTORY_LEDGER_TRADE_TABLE = "wallet_trade_ledger"
DEFAULT_HISTORY_LEDGER_OPERATION_TABLE = "wallet_operation_ledger"
DEFAULT_HISTORY_LEDGER_GAP_TABLE = "wallet_history_gaps"
DEFAULT_HISTORY_LEDGER_TIMEOUT_SECONDS = 20.0
DEFAULT_HISTORY_LEDGER_QUERY_PAGE_SIZE = 1000
DEFAULT_HISTORY_LEDGER_MAX_LOCAL_TABLE_BYTES = 128 * 1024 * 1024
LOCAL_LEDGER_TABLE_TOO_LARGE_REASON = "local_ledger_table_too_large"
LOCAL_LEDGER_FILENAMES = {
    "trades": "wallet_trade_ledger.json",
    "operations": "wallet_operation_ledger.json",
    "gaps": "wallet_history_gaps.json",
}
LOCAL_LEDGER_KEY_FIELDS = {
    "trades": "record_key",
    "operations": "record_key",
    "gaps": "gap_key",
}
LEDGER_LOCK = Lock()


def history_ledger_dir(artifacts_root: Path) -> Path:
    return artifacts_root / HISTORY_LEDGER_DIRNAME


def history_ledger_table_path(artifacts_root: Path, kind: str) -> Path:
    file_name = LOCAL_LEDGER_FILENAMES[kind]
    return history_ledger_dir(artifacts_root) / file_name


def normalize_wallet_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("0x"):
        text = f"0x{text}"
    return text


def resolve_history_ledger_settings(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    section = config.get("history_ledger", {}) if isinstance(config, Mapping) else {}
    if not isinstance(section, Mapping):
        section = {}

    backend = normalize_backend_choice(
        section.get("backend") or os.environ.get("HISTORY_LEDGER_BACKEND"),
        default=DEFAULT_HISTORY_LEDGER_BACKEND,
        allowed={"local", "cloudflare"},
    )

    account_id_env = (
        str(section.get("cloudflare_account_id_env") or "CLOUDFLARE_ACCOUNT_ID").strip()
        or "CLOUDFLARE_ACCOUNT_ID"
    )
    database_id_env = (
        str(section.get("cloudflare_d1_database_id_env") or "CLOUDFLARE_D1_DATABASE_ID").strip()
        or "CLOUDFLARE_D1_DATABASE_ID"
    )
    api_token_env = (
        str(section.get("cloudflare_api_token_env") or "CLOUDFLARE_API_TOKEN").strip()
        or "CLOUDFLARE_API_TOKEN"
    )
    email_env = (
        str(section.get("cloudflare_email_env") or "CLOUDFLARE_EMAIL").strip()
        or "CLOUDFLARE_EMAIL"
    )
    global_api_key_env = (
        str(section.get("cloudflare_global_api_key_env") or "CLOUDFLARE_GLOBAL_API_KEY").strip()
        or "CLOUDFLARE_GLOBAL_API_KEY"
    )

    return {
        "enabled": coerce_bool(section.get("enabled", True), True),
        "backend": backend,
        "cloudflare_account_id": str(
            section.get("cloudflare_account_id") or os.environ.get(account_id_env) or ""
        ).strip(),
        "cloudflare_d1_database_id": str(
            section.get("cloudflare_d1_database_id") or os.environ.get(database_id_env) or ""
        ).strip(),
        "cloudflare_api_token": str(
            section.get("cloudflare_api_token") or os.environ.get(api_token_env) or ""
        ).strip(),
        "cloudflare_email": str(
            section.get("cloudflare_email") or os.environ.get(email_env) or ""
        ).strip(),
        "cloudflare_global_api_key": str(
            section.get("cloudflare_global_api_key") or os.environ.get(global_api_key_env) or ""
        ).strip(),
        "trade_table": str(section.get("trade_table") or DEFAULT_HISTORY_LEDGER_TRADE_TABLE).strip()
        or DEFAULT_HISTORY_LEDGER_TRADE_TABLE,
        "operation_table": str(
            section.get("operation_table") or DEFAULT_HISTORY_LEDGER_OPERATION_TABLE
        ).strip()
        or DEFAULT_HISTORY_LEDGER_OPERATION_TABLE,
        "gap_table": str(section.get("gap_table") or DEFAULT_HISTORY_LEDGER_GAP_TABLE).strip()
        or DEFAULT_HISTORY_LEDGER_GAP_TABLE,
        "timeout_seconds": max(
            1.0,
            float(section.get("timeout_seconds", DEFAULT_HISTORY_LEDGER_TIMEOUT_SECONDS)),
        ),
        "fallback_to_local_on_error": coerce_bool(
            section.get("fallback_to_local_on_error", True),
            True,
        ),
        "read_fallback_enabled": coerce_bool(section.get("read_fallback_enabled", True), True),
        "read_cloud_fallback_enabled": coerce_bool(
            section.get("read_cloud_fallback_enabled", True),
            True,
        ),
        "replicate_to_cloudflare": coerce_bool(
            section.get("replicate_to_cloudflare", False),
            False,
        ),
        "compact_gap_payloads_after_batch": coerce_bool(
            section.get("compact_gap_payloads_after_batch", True),
            True,
        ),
        "compact_gap_payloads_after_run": coerce_bool(
            section.get("compact_gap_payloads_after_run", True),
            True,
        ),
        "persist_screening_snapshots": coerce_bool(
            section.get("persist_screening_snapshots", True),
            True,
        ),
        "persist_trades": coerce_bool(section.get("persist_trades", True), True),
        "persist_operations": coerce_bool(section.get("persist_operations", True), True),
        "persist_gaps": coerce_bool(section.get("persist_gaps", True), True),
        "query_page_size": max(
            1,
            int(section.get("query_page_size", DEFAULT_HISTORY_LEDGER_QUERY_PAGE_SIZE)),
        ),
        "max_local_table_bytes": max(
            0,
            int(
                section.get(
                    "max_local_table_bytes",
                    DEFAULT_HISTORY_LEDGER_MAX_LOCAL_TABLE_BYTES,
                )
            ),
        ),
    }


def create_history_ledger_store(
    artifacts_root: Path | None,
    config: Mapping[str, Any] | None = None,
) -> HistoryLedgerStore:
    return HistoryLedgerStore(
        artifacts_root=artifacts_root.resolve() if isinstance(artifacts_root, Path) else None,
        settings=resolve_history_ledger_settings(config),
    )


@dataclass
class HistoryLedgerStore:
    artifacts_root: Path | None
    settings: dict[str, Any]
    _local_gap_rows_cache: list[dict[str, Any]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def status(self) -> dict[str, Any]:
        primary_backend = "cloudflare" if self._should_use_cloudflare() else "local"
        payload = {
            "enabled": bool(self.enabled),
            "backend": str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
            "primary_backend": primary_backend,
            "cloudflare_configured": bool(self._is_cloudflare_configured()),
            "replicate_to_cloudflare": bool(self.settings.get("replicate_to_cloudflare", False)),
            "read_fallback_enabled": bool(self.settings.get("read_fallback_enabled", True)),
            "read_cloud_fallback_enabled": bool(
                self.settings.get("read_cloud_fallback_enabled", True)
            ),
            "fallback_to_local_on_error": bool(
                self.settings.get("fallback_to_local_on_error", True)
            ),
            "compact_gap_payloads_after_batch": bool(
                self.settings.get("compact_gap_payloads_after_batch", True)
            ),
            "compact_gap_payloads_after_run": bool(
                self.settings.get("compact_gap_payloads_after_run", True)
            ),
            "persist_screening_snapshots": bool(
                self.settings.get("persist_screening_snapshots", True)
            ),
            "max_local_table_bytes": int(
                self.settings.get(
                    "max_local_table_bytes",
                    DEFAULT_HISTORY_LEDGER_MAX_LOCAL_TABLE_BYTES,
                )
                or 0
            ),
        }
        self._add_local_skip_fields(
            payload,
            self._local_tables_exceeding_size_limit(("trades", "operations", "gaps")),
        )
        return payload

    def persist_wallet_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        wallet: str | None = None,
        run_id: str = "",
        snapshot_scope: str = "",
    ) -> dict[str, Any]:
        normalized_wallet = normalize_wallet_address(wallet or snapshot.get("wallet"))
        normalized_scope = str(snapshot_scope or snapshot.get("snapshot_scope") or "").strip() or "full"
        if not self.enabled:
            return self._status(
                status="disabled",
                backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
                wallet=normalized_wallet,
                snapshot_scope=normalized_scope,
                reason="history_ledger_disabled",
            )
        if not normalized_wallet:
            return self._status(
                status="skipped",
                backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
                wallet=normalized_wallet,
                snapshot_scope=normalized_scope,
                reason="wallet_missing",
            )

        primary_cloudflare = self._should_use_cloudflare()
        replicate_cloudflare = self._should_replicate_to_cloudflare()
        local_only_write = (
            self.artifacts_root is not None
            and not primary_cloudflare
            and not replicate_cloudflare
        )
        early_skipped_local_tables: list[str] = []

        def should_build_rows(kind: str, enabled: bool) -> bool:
            if not enabled:
                return False
            if local_only_write and self._local_table_exceeds_size_limit(kind):
                early_skipped_local_tables.append(kind)
                return False
            return True

        persist_trades = bool(self.settings.get("persist_trades", True))
        persist_operations = bool(self.settings.get("persist_operations", True))
        persist_gaps = bool(self.settings.get("persist_gaps", True))
        trade_rows = (
            build_trade_ledger_rows(
                snapshot,
                wallet_address=normalized_wallet,
                run_id=run_id,
                snapshot_scope=normalized_scope,
            )
            if should_build_rows("trades", persist_trades)
            else []
        )
        operation_rows = (
            build_operation_ledger_rows(
                snapshot,
                wallet_address=normalized_wallet,
                run_id=run_id,
                snapshot_scope=normalized_scope,
            )
            if should_build_rows("operations", persist_operations)
            else []
        )
        gap_rows = (
            build_gap_ledger_rows(
                snapshot,
                wallet_address=normalized_wallet,
                run_id=run_id,
                snapshot_scope=normalized_scope,
            )
            if should_build_rows("gaps", persist_gaps)
            else []
        )
        trade_count = len(trade_rows)
        operation_count = len(operation_rows)
        gap_count = len(gap_rows)
        if "trades" in early_skipped_local_tables:
            trades = snapshot.get("trades", [])
            trade_count = len(trades) if isinstance(trades, list) else 0
        if "operations" in early_skipped_local_tables:
            operation_audit = (
                snapshot.get("operation_audit", {})
                if isinstance(snapshot.get("operation_audit", {}), Mapping)
                else {}
            )
            operation_records = operation_audit.get("records", [])
            operation_count = (
                sum(
                    1
                    for record in operation_records
                    if isinstance(record, Mapping)
                    and str(record.get("operation") or "").strip().lower() not in {"", "trade"}
                )
                if isinstance(operation_records, list)
                else 0
            )
        if "gaps" in early_skipped_local_tables:
            collection_status = (
                snapshot.get("collection_status", {})
                if isinstance(snapshot.get("collection_status", {}), Mapping)
                else {}
            )
            gap_count = len(collection_status)

        if self._should_use_cloudflare():
            try:
                self._persist_cloudflare_rows(trade_rows, operation_rows, gap_rows)
                return self._status(
                    status="persisted",
                    backend="cloudflare",
                    wallet=normalized_wallet,
                    snapshot_scope=normalized_scope,
                    trade_count=trade_count,
                    operation_count=operation_count,
                    gap_count=gap_count,
                )
            except CloudflareD1RequestError as exc:
                if not self._should_fallback_local():
                    raise
                if self.artifacts_root is None:
                    return self._status(
                        status="failed",
                        backend="cloudflare",
                        wallet=normalized_wallet,
                        snapshot_scope=normalized_scope,
                        trade_count=trade_count,
                        operation_count=operation_count,
                        gap_count=gap_count,
                        error=str(exc),
                    )

        if self.artifacts_root is None:
            if self._should_replicate_to_cloudflare():
                try:
                    self._persist_cloudflare_rows(trade_rows, operation_rows, gap_rows)
                except CloudflareD1RequestError as exc:
                    return self._status(
                        status="failed",
                        backend="cloudflare",
                        wallet=normalized_wallet,
                        snapshot_scope=normalized_scope,
                        trade_count=trade_count,
                        operation_count=operation_count,
                        gap_count=gap_count,
                        error=str(exc),
                    )
                return self._status(
                    status="persisted",
                    backend="cloudflare",
                    wallet=normalized_wallet,
                    snapshot_scope=normalized_scope,
                    trade_count=trade_count,
                    operation_count=operation_count,
                    gap_count=gap_count,
                )
            return self._status(
                status="disabled",
                backend="local",
                wallet=normalized_wallet,
                snapshot_scope=normalized_scope,
                trade_count=trade_count,
                operation_count=operation_count,
                gap_count=gap_count,
                reason="artifacts_root_missing",
            )

        attempted_local_tables = [
            kind
            for kind, rows in (
                ("trades", trade_rows),
                ("operations", operation_rows),
                ("gaps", gap_rows),
            )
            if rows
        ]
        skipped_local_tables = merge_local_skipped_tables(
            early_skipped_local_tables,
            [
                kind
                for kind, rows in (
                    ("trades", trade_rows),
                    ("operations", operation_rows),
                    ("gaps", gap_rows),
                )
                if rows and self._local_table_exceeds_size_limit(kind)
            ],
        )
        attempted_local_tables = merge_local_skipped_tables(
            attempted_local_tables,
            early_skipped_local_tables,
        )
        self._persist_local_rows("trades", trade_rows)
        self._persist_local_rows("operations", operation_rows)
        self._persist_local_rows("gaps", gap_rows)
        all_local_writes_skipped = bool(attempted_local_tables) and set(
            attempted_local_tables
        ).issubset(set(skipped_local_tables))
        status_payload = self._status(
            status="skipped" if all_local_writes_skipped else "persisted",
            backend="local",
            wallet=normalized_wallet,
            snapshot_scope=normalized_scope,
            trade_count=trade_count,
            operation_count=operation_count,
            gap_count=gap_count,
            reason=(
                LOCAL_LEDGER_TABLE_TOO_LARGE_REASON if all_local_writes_skipped else ""
            ),
        )
        if skipped_local_tables:
            self._add_local_skip_fields(status_payload, skipped_local_tables)
        if self._should_replicate_to_cloudflare():
            try:
                self._persist_cloudflare_rows(trade_rows, operation_rows, gap_rows)
            except CloudflareD1RequestError as exc:
                status_payload["replica_backend"] = "cloudflare"
                status_payload["replica_status"] = "failed"
                status_payload["replica_error"] = str(exc)
            else:
                status_payload["replica_backend"] = "cloudflare"
                status_payload["replica_status"] = "persisted"
        return status_payload

    def load_complete_trade_fallback(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str = "",
        range_start: int | None = None,
        range_end: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        normalized_wallet = normalize_wallet_address(wallet)
        normalized_history_scope = str(history_scope or "").strip().lower() or "aggregate"
        normalized_snapshot_scope = str(snapshot_scope or "").strip() or "full"
        if not self.enabled or not bool(self.settings.get("read_fallback_enabled", True)):
            return self._trade_fallback_status(
                status="disabled",
                backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
                wallet=normalized_wallet,
                history_scope=normalized_history_scope,
                snapshot_scope=normalized_snapshot_scope,
                reason="history_ledger_read_fallback_disabled",
            )
        if not normalized_wallet:
            return self._trade_fallback_status(
                status="skipped",
                backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
                wallet=normalized_wallet,
                history_scope=normalized_history_scope,
                snapshot_scope=normalized_snapshot_scope,
                reason="wallet_missing",
            )

        read_backends = self._trade_read_backends()
        errors: list[str] = []
        skipped_local_tables: list[str] = []
        for backend in read_backends:
            if backend == "local":
                backend_skipped_tables = self._local_tables_exceeding_size_limit(
                    ("gaps", "trades")
                )
                if backend_skipped_tables:
                    skipped_local_tables = merge_local_skipped_tables(
                        skipped_local_tables,
                        backend_skipped_tables,
                    )
                    continue
            try:
                coverage = self._find_complete_trade_coverage_for_backend(
                    backend,
                    wallet=normalized_wallet,
                    history_scope=normalized_history_scope,
                    snapshot_scope=normalized_snapshot_scope,
                    range_start=range_start,
                    range_end=range_end,
                )
            except CloudflareD1RequestError as exc:
                errors.append(str(exc))
                continue
            if coverage is None:
                continue

            coverage_scope = (
                str(coverage.get("history_scope") or normalized_history_scope).strip()
                or normalized_history_scope
            )
            coverage_snapshot_scope = (
                str(coverage.get("snapshot_scope") or normalized_snapshot_scope).strip()
                or normalized_snapshot_scope
            )
            try:
                trade_rows = self._load_trade_rows_for_backend(
                    backend,
                    wallet=normalized_wallet,
                    history_scope=coverage_scope,
                    snapshot_scope=coverage_snapshot_scope,
                    range_start=range_start,
                    range_end=range_end,
                )
            except CloudflareD1RequestError as exc:
                errors.append(str(exc))
                continue
            if not trade_rows:
                continue

            total_count = len(trade_rows)
            truncated = bool(limit is not None and total_count > max(0, int(limit)))
            if limit is not None:
                trade_rows = trade_rows[: max(0, int(limit))]
            records = [annotate_loaded_trade_payload(row.get("payload")) for row in trade_rows]
            coverage_complete = bool(coverage.get("complete", False))
            payload = {
                "status": "loaded",
                "backend": backend,
                "wallet": normalized_wallet,
                "history_scope": coverage_scope,
                "snapshot_scope": coverage_snapshot_scope,
                "range_start": to_optional_int(coverage.get("range_start")),
                "range_end": to_optional_int(coverage.get("range_end")),
                "complete": coverage_complete and not truncated,
                "coverage_complete": coverage_complete,
                "truncated": truncated,
                "records": records,
                "record_count": len(records),
                "total_record_count": total_count,
                "status_payload": {
                    "complete": coverage_complete,
                    "stop_reason": "history_ledger_trade_history_complete",
                    "collection_mode": "history_ledger",
                    "source_section": "history_ledger",
                    "history_scope": coverage_scope,
                    "snapshot_scope": coverage_snapshot_scope,
                    "source": backend,
                    "trades_complete": coverage_complete,
                    "operations_complete": False,
                    "trade_record_count": total_count,
                    "range_start": to_optional_int(coverage.get("range_start")),
                    "range_end": to_optional_int(coverage.get("range_end")),
                },
                "error": "",
            }
            self._add_local_skip_fields(payload, skipped_local_tables)
            return payload

        status = "skipped" if skipped_local_tables else "missing"
        if read_backends and read_backends[0] == "cloudflare" and errors and not self._should_fallback_local():
            status = "failed"
        reason = (
            LOCAL_LEDGER_TABLE_TOO_LARGE_REASON
            if skipped_local_tables
            else "no_complete_trade_coverage"
        )
        payload = self._trade_fallback_status(
            status=status,
            backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
            wallet=normalized_wallet,
            history_scope=normalized_history_scope,
            snapshot_scope=normalized_snapshot_scope,
            range_start=range_start,
            range_end=range_end,
            reason=reason,
            error=errors[0] if errors else "",
        )
        self._add_local_skip_fields(payload, skipped_local_tables)
        return payload

    def load_complete_operation_fallback(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str = "",
        limit: int | None = None,
    ) -> dict[str, Any]:
        normalized_wallet = normalize_wallet_address(wallet)
        normalized_history_scope = str(history_scope or "").strip().lower() or "full_history"
        normalized_snapshot_scope = str(snapshot_scope or "").strip() or "full"
        if not self.enabled or not bool(self.settings.get("read_fallback_enabled", True)):
            return self._operation_fallback_status(
                status="disabled",
                backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
                wallet=normalized_wallet,
                history_scope=normalized_history_scope,
                snapshot_scope=normalized_snapshot_scope,
                reason="history_ledger_read_fallback_disabled",
            )
        if not normalized_wallet:
            return self._operation_fallback_status(
                status="skipped",
                backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
                wallet=normalized_wallet,
                history_scope=normalized_history_scope,
                snapshot_scope=normalized_snapshot_scope,
                reason="wallet_missing",
            )

        read_backends = self._trade_read_backends()
        errors: list[str] = []
        skipped_local_tables: list[str] = []
        for backend in read_backends:
            if backend == "local":
                backend_skipped_tables = self._local_tables_exceeding_size_limit(
                    ("gaps", "operations")
                )
                if backend_skipped_tables:
                    skipped_local_tables = merge_local_skipped_tables(
                        skipped_local_tables,
                        backend_skipped_tables,
                    )
                    continue
            try:
                coverage = self._find_complete_operation_coverage_for_backend(
                    backend,
                    wallet=normalized_wallet,
                    history_scope=normalized_history_scope,
                    snapshot_scope=normalized_snapshot_scope,
                )
            except CloudflareD1RequestError as exc:
                errors.append(str(exc))
                continue
            if coverage is None:
                continue

            coverage_scope = (
                str(coverage.get("history_scope") or normalized_history_scope).strip()
                or normalized_history_scope
            )
            coverage_snapshot_scope = (
                str(coverage.get("snapshot_scope") or normalized_snapshot_scope).strip()
                or normalized_snapshot_scope
            )
            try:
                operation_rows = self._load_operation_rows_for_backend(
                    backend,
                    wallet=normalized_wallet,
                    history_scope=coverage_scope,
                    snapshot_scope=coverage_snapshot_scope,
                )
            except CloudflareD1RequestError as exc:
                errors.append(str(exc))
                continue
            if not operation_rows:
                continue

            total_count = len(operation_rows)
            truncated = bool(limit is not None and total_count > max(0, int(limit)))
            if limit is not None:
                operation_rows = operation_rows[: max(0, int(limit))]
            records = [
                annotate_loaded_operation_payload(row.get("payload"))
                for row in operation_rows
            ]
            coverage_complete = bool(operation_coverage_complete(coverage))
            payload = {
                "status": "loaded",
                "backend": backend,
                "wallet": normalized_wallet,
                "history_scope": coverage_scope,
                "snapshot_scope": coverage_snapshot_scope,
                "complete": coverage_complete and not truncated,
                "coverage_complete": coverage_complete,
                "truncated": truncated,
                "records": records,
                "record_count": len(records),
                "total_record_count": total_count,
                "status_payload": {
                    "complete": coverage_complete,
                    "stop_reason": "history_ledger_operation_history_complete",
                    "collection_mode": "history_ledger",
                    "source_section": "history_ledger_operations",
                    "history_scope": coverage_scope,
                    "snapshot_scope": coverage_snapshot_scope,
                    "source": backend,
                    "trades_complete": False,
                    "operations_complete": coverage_complete,
                    "operation_record_count": total_count,
                },
                "error": "",
            }
            self._add_local_skip_fields(payload, skipped_local_tables)
            return payload

        status = "skipped" if skipped_local_tables else "missing"
        if read_backends and read_backends[0] == "cloudflare" and errors and not self._should_fallback_local():
            status = "failed"
        reason = (
            LOCAL_LEDGER_TABLE_TOO_LARGE_REASON
            if skipped_local_tables
            else "no_complete_operation_coverage"
        )
        payload = self._operation_fallback_status(
            status=status,
            backend=str(self.settings.get("backend") or DEFAULT_HISTORY_LEDGER_BACKEND),
            wallet=normalized_wallet,
            history_scope=normalized_history_scope,
            snapshot_scope=normalized_snapshot_scope,
            reason=reason,
            error=errors[0] if errors else "",
        )
        self._add_local_skip_fields(payload, skipped_local_tables)
        return payload

    def sync_local_to_cloudflare(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "backend": "cloudflare",
                "reason": "history_ledger_disabled",
            }
        if self.artifacts_root is None:
            return {
                "status": "disabled",
                "backend": "cloudflare",
                "reason": "artifacts_root_missing",
            }
        if not self._is_cloudflare_configured():
            return {
                "status": "disabled",
                "backend": "cloudflare",
                "reason": "cloudflare_not_configured",
            }

        skipped_tables = self._local_tables_exceeding_size_limit(
            ("trades", "operations", "gaps")
        )
        trade_rows = (
            []
            if "trades" in skipped_tables
            else read_local_ledger_rows(history_ledger_table_path(self.artifacts_root, "trades"))
        )
        operation_rows = (
            []
            if "operations" in skipped_tables
            else read_local_ledger_rows(history_ledger_table_path(self.artifacts_root, "operations"))
        )
        gap_rows = (
            []
            if "gaps" in skipped_tables
            else read_local_ledger_rows(history_ledger_table_path(self.artifacts_root, "gaps"))
        )
        try:
            self._persist_cloudflare_rows(trade_rows, operation_rows, gap_rows)
        except CloudflareD1RequestError as exc:
            payload = {
                "status": "failed",
                "backend": "cloudflare",
                "trade_count": len(trade_rows),
                "operation_count": len(operation_rows),
                "gap_count": len(gap_rows),
                "error": str(exc),
            }
            self._add_local_skip_fields(payload, skipped_tables, include_legacy_alias=True)
            return payload
        payload = {
            "status": "synced",
            "backend": "cloudflare",
            "trade_count": len(trade_rows),
            "operation_count": len(operation_rows),
            "gap_count": len(gap_rows),
        }
        self._add_local_skip_fields(payload, skipped_tables, include_legacy_alias=True)
        return payload

    def compact_local_gap_payloads(self, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "reason": "history_ledger_disabled",
                "updated_count": 0,
                "removed_record_lists": 0,
            }
        if self.artifacts_root is None:
            return {
                "status": "skipped",
                "reason": "artifacts_root_missing",
                "updated_count": 0,
                "removed_record_lists": 0,
            }
        if not force and not bool(self.settings.get("compact_gap_payloads_after_batch", True)):
            return {
                "status": "disabled",
                "reason": "gap_payload_compaction_disabled",
                "updated_count": 0,
                "removed_record_lists": 0,
            }

        path = history_ledger_table_path(self.artifacts_root, "gaps")
        if not path.exists():
            return {
                "status": "skipped",
                "reason": "gap_ledger_missing",
                "updated_count": 0,
                "removed_record_lists": 0,
            }
        if self._local_table_exceeds_size_limit("gaps"):
            return {
                "status": "skipped",
                "reason": LOCAL_LEDGER_TABLE_TOO_LARGE_REASON,
                "local_skipped_tables": ["gaps"],
                "updated_count": 0,
                "removed_record_lists": 0,
            }

        with LEDGER_LOCK:
            rows = read_local_ledger_rows(path)
            changed = False
            updated_count = 0
            removed_record_lists = 0
            compacted_rows: list[dict[str, Any]] = []
            for row in rows:
                next_row = dict(row)
                payload = next_row.get("payload")
                if isinstance(payload, Mapping):
                    compact_payload, removed_count = compact_ledger_status_with_count(payload)
                    if removed_count:
                        next_row["payload"] = compact_payload
                        changed = True
                        updated_count += 1
                        removed_record_lists += removed_count
                compacted_rows.append(next_row)

            if changed:
                path.write_text(
                    json.dumps(compacted_rows, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )

        return {
            "status": "compacted" if changed else "unchanged",
            "updated_count": updated_count,
            "removed_record_lists": removed_record_lists,
        }

    def _persist_cloudflare_rows(
        self,
        trade_rows: list[dict[str, Any]],
        operation_rows: list[dict[str, Any]],
        gap_rows: list[dict[str, Any]],
    ) -> None:
        self._persist_cloudflare_table_rows(
            self.settings["trade_table"],
            trade_rows,
            on_conflict="record_key",
        )
        self._persist_cloudflare_table_rows(
            self.settings["operation_table"],
            operation_rows,
            on_conflict="record_key",
        )
        self._persist_cloudflare_table_rows(
            self.settings["gap_table"],
            gap_rows,
            on_conflict="gap_key",
        )

    def _persist_cloudflare_table_rows(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
    ) -> None:
        if not rows:
            return
        config = self._cloudflare_config()
        page_size = max(
            1,
            min(
                int(self.settings.get("query_page_size", DEFAULT_HISTORY_LEDGER_QUERY_PAGE_SIZE)),
                500,
            ),
        )
        for offset in range(0, len(rows), page_size):
            cloudflare_d1_upsert_rows(
                config,
                table,
                rows=rows[offset : offset + page_size],
                on_conflict=on_conflict,
            )

    def _persist_local_rows(self, kind: str, rows: list[dict[str, Any]]) -> None:
        if self.artifacts_root is None or not rows:
            return
        path = history_ledger_table_path(self.artifacts_root, kind)
        if self._local_table_exceeds_size_limit(kind):
            return
        key_field = LOCAL_LEDGER_KEY_FIELDS[kind]
        with LEDGER_LOCK:
            if self._local_table_exceeds_size_limit(kind):
                return
            existing_rows = read_local_ledger_rows(path)
            rows_by_key = {
                str(row.get(key_field) or ""): dict(row)
                for row in existing_rows
                if isinstance(row, Mapping) and str(row.get(key_field) or "")
            }
            for row in rows:
                row_key = str(row.get(key_field) or "")
                if not row_key:
                    continue
                rows_by_key[row_key] = dict(row)
            merged_rows = sorted(
                rows_by_key.values(),
                key=lambda item: (
                    str(item.get("wallet_address") or ""),
                    str(item.get("event_timestamp") or ""),
                    str(item.get(key_field) or ""),
                ),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(merged_rows, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    def _find_complete_trade_coverage(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> dict[str, Any] | None:
        if self._should_use_cloudflare():
            rows = self._load_gap_rows_cloudflare(wallet=wallet, section_name="trades")
            return choose_trade_coverage_row(
                rows,
                wallet=wallet,
                history_scope=history_scope,
                snapshot_scope=snapshot_scope,
                range_start=range_start,
                range_end=range_end,
            )
        return self._find_complete_trade_coverage_local(
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _find_complete_trade_coverage_for_backend(
        self,
        backend: str,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> dict[str, Any] | None:
        if backend == "cloudflare":
            return choose_trade_coverage_row(
                self._load_gap_rows_cloudflare(wallet=wallet, section_name="trades"),
                wallet=wallet,
                history_scope=history_scope,
                snapshot_scope=snapshot_scope,
                range_start=range_start,
                range_end=range_end,
            )
        return self._find_complete_trade_coverage_local(
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _find_complete_operation_coverage_for_backend(
        self,
        backend: str,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
    ) -> dict[str, Any] | None:
        if backend == "cloudflare":
            rows = [
                *self._load_gap_rows_cloudflare(wallet=wallet, section_name="history_provider"),
                *self._load_gap_rows_cloudflare(
                    wallet=wallet,
                    section_name="history_ledger_operations",
                ),
            ]
            return choose_operation_coverage_row(
                rows,
                wallet=wallet,
                history_scope=history_scope,
                snapshot_scope=snapshot_scope,
            )
        return self._find_complete_operation_coverage_local(
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
        )

    def _find_complete_operation_coverage_local(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
    ) -> dict[str, Any] | None:
        rows = [
            *self._load_gap_rows_local(wallet=wallet, section_name="history_provider"),
            *self._load_gap_rows_local(wallet=wallet, section_name="history_ledger_operations"),
        ]
        return choose_operation_coverage_row(
            rows,
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
        )

    def _find_complete_trade_coverage_local(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> dict[str, Any] | None:
        rows = self._load_gap_rows_local(wallet=wallet, section_name="trades")
        return choose_trade_coverage_row(
            rows,
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _load_trade_rows(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> list[dict[str, Any]]:
        if self._should_use_cloudflare():
            return self._load_trade_rows_cloudflare(
                wallet=wallet,
                history_scope=history_scope,
                snapshot_scope=snapshot_scope,
                range_start=range_start,
                range_end=range_end,
            )
        return self._load_trade_rows_local(
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _load_trade_rows_for_backend(
        self,
        backend: str,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> list[dict[str, Any]]:
        if backend == "cloudflare":
            return self._load_trade_rows_cloudflare(
                wallet=wallet,
                history_scope=history_scope,
                snapshot_scope=snapshot_scope,
                range_start=range_start,
                range_end=range_end,
            )
        return self._load_trade_rows_local(
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _load_operation_rows_for_backend(
        self,
        backend: str,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
    ) -> list[dict[str, Any]]:
        if backend == "cloudflare":
            return self._load_operation_rows_cloudflare(
                wallet=wallet,
                history_scope=history_scope,
                snapshot_scope=snapshot_scope,
            )
        return self._load_operation_rows_local(
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
        )

    def _load_trade_rows_local(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> list[dict[str, Any]]:
        if self.artifacts_root is None:
            return []
        if self._local_table_exceeds_size_limit("trades"):
            return []
        rows = read_local_ledger_rows(history_ledger_table_path(self.artifacts_root, "trades"))
        return filter_trade_rows(
            rows,
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _load_trade_rows_cloudflare(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None,
        range_end: int | None,
    ) -> list[dict[str, Any]]:
        rows = self._select_cloudflare_rows(
            table=self.settings["trade_table"],
            columns=[
                "record_key",
                "wallet_address",
                "run_id",
                "snapshot_scope",
                "history_scope",
                "event_timestamp",
                "payload",
                "updated_at",
            ],
            filters={"wallet_address": f"eq.{wallet}"},
            order="event_timestamp.desc",
        )
        return filter_trade_rows(
            rows,
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
            range_start=range_start,
            range_end=range_end,
        )

    def _load_operation_rows_local(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
    ) -> list[dict[str, Any]]:
        if self.artifacts_root is None:
            return []
        if self._local_table_exceeds_size_limit("operations"):
            return []
        rows = read_local_ledger_rows(history_ledger_table_path(self.artifacts_root, "operations"))
        return filter_operation_rows(
            rows,
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
        )

    def _load_operation_rows_cloudflare(
        self,
        *,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
    ) -> list[dict[str, Any]]:
        rows = self._select_cloudflare_rows(
            table=self.settings["operation_table"],
            columns=[
                "record_key",
                "wallet_address",
                "run_id",
                "snapshot_scope",
                "history_scope",
                "operation_type",
                "event_timestamp",
                "payload",
                "updated_at",
            ],
            filters={"wallet_address": f"eq.{wallet}"},
            order="event_timestamp.desc",
        )
        return filter_operation_rows(
            rows,
            wallet=wallet,
            history_scope=history_scope,
            snapshot_scope=snapshot_scope,
        )

    def _load_gap_rows_local(self, *, wallet: str, section_name: str) -> list[dict[str, Any]]:
        if self.artifacts_root is None:
            return []
        if self._local_table_exceeds_size_limit("gaps"):
            return []
        if self._local_gap_rows_cache is None:
            self._local_gap_rows_cache = read_local_ledger_rows(
                history_ledger_table_path(self.artifacts_root, "gaps")
            )
        rows = self._local_gap_rows_cache
        return [
            dict(row)
            for row in rows
            if str(row.get("wallet_address") or "").strip().lower() == wallet
            and str(row.get("section_name") or "").strip() == section_name
        ]

    def _load_gap_rows_cloudflare(self, *, wallet: str, section_name: str) -> list[dict[str, Any]]:
        return self._select_cloudflare_rows(
            table=self.settings["gap_table"],
            columns=[
                "gap_key",
                "wallet_address",
                "run_id",
                "snapshot_scope",
                "section_name",
                "history_scope",
                "collection_mode",
                "stop_reason",
                "complete",
                "range_start",
                "range_end",
                "payload",
                "updated_at",
            ],
            filters={
                "wallet_address": f"eq.{wallet}",
                "section_name": f"eq.{section_name}",
            },
            order="updated_at.desc",
        )

    def _select_cloudflare_rows(
        self,
        *,
        table: str,
        columns: list[str],
        filters: Mapping[str, str],
        order: str,
    ) -> list[dict[str, Any]]:
        config = self._cloudflare_config()
        rows: list[dict[str, Any]] = []
        offset = 0
        page_size = max(1, int(self.settings.get("query_page_size", DEFAULT_HISTORY_LEDGER_QUERY_PAGE_SIZE)))
        while True:
            page = cloudflare_d1_select_rows(
                config,
                table,
                columns=columns,
                filters=filters,
                order=order,
                limit=page_size,
                offset=offset,
            )
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
        return rows

    def _local_table_exceeds_size_limit(self, kind: str) -> bool:
        if self.artifacts_root is None:
            return False
        limit = int(
            self.settings.get(
                "max_local_table_bytes",
                DEFAULT_HISTORY_LEDGER_MAX_LOCAL_TABLE_BYTES,
            )
            or 0
        )
        if limit <= 0:
            return False
        path = history_ledger_table_path(self.artifacts_root, kind)
        try:
            return path.exists() and path.stat().st_size > limit
        except OSError:
            return False

    def _local_tables_exceeding_size_limit(self, kinds: tuple[str, ...]) -> list[str]:
        return [kind for kind in kinds if self._local_table_exceeds_size_limit(kind)]

    def _add_local_skip_fields(
        self,
        payload: dict[str, Any],
        skipped_tables: list[str],
        *,
        include_legacy_alias: bool = False,
    ) -> None:
        if not skipped_tables:
            return
        payload["local_skipped_tables"] = list(skipped_tables)
        if include_legacy_alias:
            payload["skipped_local_tables"] = list(skipped_tables)
        payload["local_skip_reason"] = LOCAL_LEDGER_TABLE_TOO_LARGE_REASON

    def _should_use_cloudflare(self) -> bool:
        return bool(
            self.enabled
            and self.settings.get("backend") == "cloudflare"
            and self.settings.get("cloudflare_account_id")
            and self.settings.get("cloudflare_d1_database_id")
            and self._has_cloudflare_auth()
        )

    def _is_cloudflare_configured(self) -> bool:
        return bool(
            self.settings.get("cloudflare_account_id")
            and self.settings.get("cloudflare_d1_database_id")
            and self._has_cloudflare_auth()
        )

    def _has_cloudflare_auth(self) -> bool:
        return bool(
            self.settings.get("cloudflare_api_token")
            or (
                self.settings.get("cloudflare_email")
                and self.settings.get("cloudflare_global_api_key")
            )
        )

    def _should_replicate_to_cloudflare(self) -> bool:
        return bool(
            self.enabled
            and self.settings.get("backend") != "cloudflare"
            and self.settings.get("replicate_to_cloudflare", False)
            and self._is_cloudflare_configured()
        )

    def _should_read_cloudflare_fallback(self) -> bool:
        return bool(
            self.enabled
            and self.settings.get("backend") != "cloudflare"
            and self.settings.get("read_cloud_fallback_enabled", True)
            and self._is_cloudflare_configured()
        )

    def _trade_read_backends(self) -> list[str]:
        if self._should_use_cloudflare():
            backends = ["cloudflare"]
            if self._should_fallback_local():
                backends.append("local")
            return backends
        backends = ["local"]
        if self._should_read_cloudflare_fallback():
            backends.append("cloudflare")
        return backends

    def _should_fallback_local(self) -> bool:
        return bool(self.settings.get("fallback_to_local_on_error", True))

    def _cloudflare_config(self) -> CloudflareD1Config:
        return CloudflareD1Config.from_settings(self.settings)

    def _status(
        self,
        *,
        status: str,
        backend: str,
        wallet: str,
        snapshot_scope: str,
        trade_count: int = 0,
        operation_count: int = 0,
        gap_count: int = 0,
        reason: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        payload = {
            "status": status,
            "backend": backend,
            "wallet": wallet,
            "snapshot_scope": snapshot_scope,
            "trade_count": int(trade_count),
            "operation_count": int(operation_count),
            "gap_count": int(gap_count),
        }
        if reason:
            payload["reason"] = reason
        if error:
            payload["error"] = error
        return payload

    def _trade_fallback_status(
        self,
        *,
        status: str,
        backend: str,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        range_start: int | None = None,
        range_end: int | None = None,
        reason: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        payload = {
            "status": status,
            "backend": backend,
            "wallet": wallet,
            "history_scope": history_scope,
            "snapshot_scope": snapshot_scope,
            "range_start": range_start,
            "range_end": range_end,
            "records": [],
            "record_count": 0,
            "total_record_count": 0,
            "complete": False,
            "coverage_complete": False,
            "truncated": False,
            "status_payload": {},
        }
        if reason:
            payload["reason"] = reason
        if error:
            payload["error"] = error
        return payload

    def _operation_fallback_status(
        self,
        *,
        status: str,
        backend: str,
        wallet: str,
        history_scope: str,
        snapshot_scope: str,
        reason: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        payload = {
            "status": status,
            "backend": backend,
            "wallet": wallet,
            "history_scope": history_scope,
            "snapshot_scope": snapshot_scope,
            "records": [],
            "record_count": 0,
            "total_record_count": 0,
            "complete": False,
            "coverage_complete": False,
            "truncated": False,
            "status_payload": {},
        }
        if reason:
            payload["reason"] = reason
        if error:
            payload["error"] = error
        return payload


def read_local_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def choose_trade_coverage_row(
    rows: list[dict[str, Any]],
    *,
    wallet: str,
    history_scope: str,
    snapshot_scope: str,
    range_start: int | None,
    range_end: int | None,
) -> dict[str, Any] | None:
    acceptable_scopes = normalized_trade_coverage_scopes(history_scope)
    normalized_snapshot_scope = str(snapshot_scope or "").strip().lower()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("wallet_address") or "").strip().lower() != wallet:
            continue
        if str(row.get("section_name") or "").strip() != "trades":
            continue
        if not bool(row.get("complete", False)):
            continue
        row_scope = str(row.get("history_scope") or "").strip().lower()
        if row_scope not in acceptable_scopes:
            continue
        row_snapshot_scope = str(row.get("snapshot_scope") or "").strip().lower()
        if normalized_snapshot_scope and row_scope not in {"aggregate", "full_history"}:
            if row_snapshot_scope and row_snapshot_scope != normalized_snapshot_scope:
                continue
        if row_scope == "screening_window":
            row_range_start = to_optional_int(row.get("range_start"))
            row_range_end = to_optional_int(row.get("range_end"))
            if range_start is None or range_end is None:
                continue
            if row_range_start is None or row_range_end is None:
                continue
            if row_range_start > range_start or row_range_end < range_end:
                continue
        candidates.append(dict(row))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("run_id") or ""),
            str(item.get("gap_key") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def choose_operation_coverage_row(
    rows: list[dict[str, Any]],
    *,
    wallet: str,
    history_scope: str,
    snapshot_scope: str,
) -> dict[str, Any] | None:
    acceptable_scopes = normalized_operation_coverage_scopes(history_scope)
    normalized_snapshot_scope = str(snapshot_scope or "").strip().lower()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("wallet_address") or "").strip().lower() != wallet:
            continue
        if str(row.get("section_name") or "").strip() not in {
            "history_provider",
            "history_ledger_operations",
        }:
            continue
        if not operation_coverage_complete(row):
            continue
        row_scope = str(row.get("history_scope") or "").strip().lower()
        if row_scope not in acceptable_scopes:
            continue
        row_snapshot_scope = str(row.get("snapshot_scope") or "").strip().lower()
        if normalized_snapshot_scope and row_snapshot_scope and row_snapshot_scope != normalized_snapshot_scope:
            continue
        candidates.append(dict(row))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("run_id") or ""),
            str(item.get("gap_key") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def operation_coverage_complete(row: Mapping[str, Any]) -> bool:
    payload = row.get("payload", {}) if isinstance(row.get("payload", {}), Mapping) else {}
    return bool(
        row.get("complete", False)
        or payload.get("operations_complete", False)
        or payload.get("operation_history_complete", False)
    )


def normalized_trade_coverage_scopes(history_scope: str) -> set[str]:
    normalized_scope = str(history_scope or "").strip().lower() or "aggregate"
    if normalized_scope in {"aggregate", "full_history"}:
        return {"aggregate", "full_history"}
    return {normalized_scope}


def normalized_operation_coverage_scopes(history_scope: str) -> set[str]:
    normalized_scope = str(history_scope or "").strip().lower() or "full_history"
    if normalized_scope in {"aggregate", "full_history"}:
        return {"aggregate", "full_history"}
    return {normalized_scope}


def filter_trade_rows(
    rows: list[dict[str, Any]],
    *,
    wallet: str,
    history_scope: str,
    snapshot_scope: str,
    range_start: int | None,
    range_end: int | None,
) -> list[dict[str, Any]]:
    acceptable_scopes = normalized_trade_coverage_scopes(history_scope)
    normalized_snapshot_scope = str(snapshot_scope or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("wallet_address") or "").strip().lower() != wallet:
            continue
        row_scope = str(row.get("history_scope") or "").strip().lower()
        if row_scope not in acceptable_scopes:
            continue
        row_snapshot_scope = str(row.get("snapshot_scope") or "").strip().lower()
        if normalized_snapshot_scope and row_scope not in {"aggregate", "full_history"}:
            if row_snapshot_scope and row_snapshot_scope != normalized_snapshot_scope:
                continue
        event_timestamp = to_optional_int(row.get("event_timestamp"))
        if row_scope == "screening_window":
            if event_timestamp is None:
                continue
            if range_start is not None and event_timestamp < range_start:
                continue
            if range_end is not None and event_timestamp > range_end:
                continue
        filtered.append(dict(row))
    filtered.sort(
        key=lambda item: (
            to_optional_int(item.get("event_timestamp")) or 0,
            str(item.get("record_key") or ""),
        ),
        reverse=True,
    )
    return filtered


def filter_operation_rows(
    rows: list[dict[str, Any]],
    *,
    wallet: str,
    history_scope: str,
    snapshot_scope: str,
) -> list[dict[str, Any]]:
    acceptable_scopes = normalized_operation_coverage_scopes(history_scope)
    normalized_snapshot_scope = str(snapshot_scope or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("wallet_address") or "").strip().lower() != wallet:
            continue
        row_scope = str(row.get("history_scope") or "").strip().lower()
        if row_scope not in acceptable_scopes:
            continue
        row_snapshot_scope = str(row.get("snapshot_scope") or "").strip().lower()
        if normalized_snapshot_scope and row_snapshot_scope and row_snapshot_scope != normalized_snapshot_scope:
            continue
        filtered.append(dict(row))
    filtered.sort(
        key=lambda item: (
            to_optional_int(item.get("event_timestamp")) or 0,
            str(item.get("record_key") or ""),
        ),
        reverse=True,
    )
    return filtered


def annotate_loaded_trade_payload(payload: Any) -> dict[str, Any]:
    row = dict(payload) if isinstance(payload, Mapping) else {}
    row["_history_ledger"] = True
    row["_history_ledger_source"] = "history_ledger"
    return row


def annotate_loaded_operation_payload(payload: Any) -> dict[str, Any]:
    row = dict(payload) if isinstance(payload, Mapping) else {}
    row["_history_ledger"] = True
    row["_history_ledger_source"] = "history_ledger"
    row["_audit_source"] = "history_ledger.operation"
    return row


def build_trade_ledger_rows(
    snapshot: Mapping[str, Any],
    *,
    wallet_address: str,
    run_id: str,
    snapshot_scope: str,
) -> list[dict[str, Any]]:
    trades = snapshot.get("trades", [])
    if not isinstance(trades, list):
        return []
    collection_status = (
        snapshot.get("collection_status", {})
        if isinstance(snapshot.get("collection_status", {}), Mapping)
        else {}
    )
    trade_status = (
        collection_status.get("trades", {})
        if isinstance(collection_status.get("trades", {}), Mapping)
        else {}
    )
    history_scope = str(trade_status.get("history_scope") or "aggregate").strip() or "aggregate"
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for record in trades:
        if not isinstance(record, Mapping):
            continue
        payload = dict(record)
        event_timestamp = normalize_timestamp_value(
            first_non_empty_value(
                record,
                ("timestamp", "createdAt", "created_at", "timeStamp", "time"),
            )
        )
        identity = compact_dict(
            {
                "transaction_hash": first_non_empty_text(
                    record,
                    ("transactionHash", "txHash", "hash"),
                ),
                "record_id": first_non_empty_text(record, ("id", "tradeId", "tradeID")),
                "event_timestamp": event_timestamp,
                "condition_id": first_non_empty_text(
                    record,
                    ("conditionId", "eventSlug", "slug"),
                ),
                "asset_id": first_non_empty_text(
                    record,
                    ("asset", "tokenId", "makerAssetId", "takerAssetId"),
                ),
                "side": first_non_empty_text(record, ("side", "type")),
                "size": normalize_scalar_text(first_non_empty_value(record, ("size", "shares", "amount"))),
                "usdc_size": normalize_scalar_text(
                    first_non_empty_value(record, ("usdcSize", "value", "notional"))
                ),
            }
        )
        if len(identity) <= 2:
            identity["payload"] = payload
        record_key = stable_hash(
            {
                "record_type": "trade",
                "wallet_address": wallet_address,
                "identity": identity,
            }
        )
        rows.append(
            {
                "record_key": record_key,
                "wallet_address": wallet_address,
                "run_id": str(run_id or ""),
                "snapshot_scope": snapshot_scope,
                "history_scope": history_scope,
                "event_timestamp": event_timestamp,
                "payload": payload,
                "updated_at": updated_at,
            }
        )
    return rows


def build_operation_ledger_rows(
    snapshot: Mapping[str, Any],
    *,
    wallet_address: str,
    run_id: str,
    snapshot_scope: str,
) -> list[dict[str, Any]]:
    operation_audit = (
        snapshot.get("operation_audit", {})
        if isinstance(snapshot.get("operation_audit", {}), Mapping)
        else {}
    )
    records = operation_audit.get("records", [])
    if not isinstance(records, list):
        return []
    collection_status = (
        snapshot.get("collection_status", {})
        if isinstance(snapshot.get("collection_status", {}), Mapping)
        else {}
    )
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        operation_type = str(record.get("operation") or "").strip().lower()
        if not operation_type or operation_type == "trade":
            continue
        payload = dict(record)
        event_timestamp = normalize_timestamp_value(record.get("timestamp"))
        identity = compact_dict(
            {
                "operation_type": operation_type,
                "transaction_hash": first_non_empty_text(
                    record,
                    ("transaction_hash", "transactionHash", "txHash", "hash"),
                ),
                "event_timestamp": event_timestamp,
                "source": first_non_empty_text(record, ("source", "verification")),
                "market": first_non_empty_text(record, ("market", "title")),
                "notional": normalize_scalar_text(record.get("notional")),
                "profit_amount": normalize_scalar_text(record.get("profit_amount")),
            }
        )
        if len(identity) <= 2:
            identity["payload"] = payload
        record_key = stable_hash(
            {
                "record_type": "operation",
                "wallet_address": wallet_address,
                "identity": identity,
            }
        )
        rows.append(
            {
                "record_key": record_key,
                "wallet_address": wallet_address,
                "run_id": str(run_id or ""),
                "snapshot_scope": snapshot_scope,
                "history_scope": infer_operation_history_scope(
                    record,
                    collection_status=collection_status,
                    snapshot_scope=snapshot_scope,
                ),
                "operation_type": operation_type,
                "event_timestamp": event_timestamp,
                "payload": payload,
                "updated_at": updated_at,
            }
        )
    return rows


def build_gap_ledger_rows(
    snapshot: Mapping[str, Any],
    *,
    wallet_address: str,
    run_id: str,
    snapshot_scope: str,
) -> list[dict[str, Any]]:
    collection_status = (
        snapshot.get("collection_status", {})
        if isinstance(snapshot.get("collection_status", {}), Mapping)
        else {}
    )
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for section_name, raw_status in collection_status.items():
        if not isinstance(raw_status, Mapping):
            continue
        status = compact_collection_status_for_ledger(raw_status)
        history_scope = str(status.get("history_scope") or snapshot_scope or "aggregate").strip()
        range_start = to_optional_int(status.get("range_start"))
        range_end = to_optional_int(status.get("range_end"))
        gap_key = stable_hash(
            {
                "wallet_address": wallet_address,
                "run_id": str(run_id or ""),
                "snapshot_scope": snapshot_scope,
                "section_name": str(section_name),
                "complete": bool(status.get("complete", True)),
                "stop_reason": str(status.get("stop_reason") or ""),
                "collection_mode": str(status.get("collection_mode") or ""),
                "history_scope": history_scope,
                "range_start": range_start,
                "range_end": range_end,
            }
        )
        rows.append(
            {
                "gap_key": gap_key,
                "wallet_address": wallet_address,
                "run_id": str(run_id or ""),
                "snapshot_scope": snapshot_scope,
                "section_name": str(section_name),
                "history_scope": history_scope,
                "collection_mode": str(status.get("collection_mode") or ""),
                "stop_reason": str(status.get("stop_reason") or ""),
                "complete": bool(status.get("complete", True)),
                "range_start": range_start,
                "range_end": range_end,
                "payload": status,
                "updated_at": updated_at,
            }
        )
    return rows


def compact_collection_status_for_ledger(status: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): compact_ledger_status_value(value)
        for key, value in status.items()
        if str(key) != "records"
    }


def compact_ledger_status_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return compact_collection_status_for_ledger(value)
    if isinstance(value, list):
        return [compact_ledger_status_value(item) for item in value]
    return value


def compact_ledger_status_with_count(status: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    removed_count = 0
    result: dict[str, Any] = {}
    for key, value in status.items():
        if str(key) == "records":
            removed_count += 1
            continue
        compact_value, nested_removed_count = compact_ledger_status_value_with_count(value)
        result[str(key)] = compact_value
        removed_count += nested_removed_count
    return result, removed_count


def compact_ledger_status_value_with_count(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        return compact_ledger_status_with_count(value)
    if isinstance(value, list):
        compacted_values: list[Any] = []
        removed_count = 0
        for item in value:
            compacted_item, item_removed_count = compact_ledger_status_value_with_count(item)
            compacted_values.append(compacted_item)
            removed_count += item_removed_count
        return compacted_values, removed_count
    return value, 0


def infer_operation_history_scope(
    record: Mapping[str, Any],
    *,
    collection_status: Mapping[str, Any],
    snapshot_scope: str,
) -> str:
    source = str(record.get("source") or "").strip().lower()
    if source.startswith("chain_validation"):
        return "chain_validation"
    if source == "closed_positions":
        status = (
            collection_status.get("closed_positions", {})
            if isinstance(collection_status.get("closed_positions", {}), Mapping)
            else {}
        )
        return str(status.get("history_scope") or snapshot_scope or "aggregate")
    if source == "activity":
        status = (
            collection_status.get("activity", {})
            if isinstance(collection_status.get("activity", {}), Mapping)
            else {}
        )
        return str(status.get("history_scope") or snapshot_scope or "aggregate")
    if source == "history_provider":
        status = (
            collection_status.get("history_provider", {})
            if isinstance(collection_status.get("history_provider", {}), Mapping)
            else {}
        )
        return str(status.get("history_scope") or "full_history")
    return str(snapshot_scope or "aggregate")


def first_non_empty_value(source: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return ""


def first_non_empty_text(source: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    value = first_non_empty_value(source, keys)
    return str(value).strip() if value not in (None, "") else ""


def compact_dict(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in source.items()
        if value not in (None, "", [], {}, ())
    }


def merge_local_skipped_tables(
    existing_tables: list[str],
    next_tables: list[str],
) -> list[str]:
    merged: list[str] = []
    for table in [*existing_tables, *next_tables]:
        if table not in merged:
            merged.append(table)
    return merged


def to_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def normalize_timestamp_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(float(value))
    text = str(value).strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def normalize_scalar_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
