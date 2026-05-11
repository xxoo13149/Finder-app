from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from http.client import IncompleteRead
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from .cloudflare_backend import (
    CloudflareD1Config,
    CloudflareD1RequestError,
    coerce_bool,
    normalize_backend_choice,
    cloudflare_d1_delete_rows,
    cloudflare_d1_select_rows,
    cloudflare_d1_upsert_rows,
)


UTC = timezone.utc
HISTORY_REGISTRY_DIRNAME = "_wallet_registry"
DEFAULT_HISTORY_REGISTRY_BACKEND = "local"
DEFAULT_HISTORY_REGISTRY_WALLET_TABLE = "wallet_registry"
DEFAULT_HISTORY_REGISTRY_TIMEOUT_SECONDS = 20.0
REGISTRY_RECORD_FIELDS = (
    "wallet_address",
    "user_name",
    "x_username",
    "first_seen_at",
    "last_seen_at",
    "last_run_id",
    "last_status",
    "run_count",
)
REGISTRY_LOCK = Lock()
CLOUDFLARE_TRANSIENT_ERRORS = (CloudflareD1RequestError, TimeoutError, IncompleteRead, OSError)
INCOMPLETE_HISTORY_STATUSES = {
    "selected_pending",
    "selected_pending_hydration",
    "hydration_pending",
}


def wallet_history_registry_dir(artifacts_root: Path) -> Path:
    return artifacts_root / HISTORY_REGISTRY_DIRNAME


def normalize_wallet_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("0x"):
        text = f"0x{text}"
    return text


def decode_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    text = str(value)
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def read_wallet_history_record(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def wallet_history_record_is_complete(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping) or not record:
        return False
    status = str(record.get("last_status") or "").strip().lower()
    return status not in INCOMPLETE_HISTORY_STATUSES


def resolve_history_registry_settings(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    section = config.get("history_registry", {}) if isinstance(config, Mapping) else {}
    if not isinstance(section, Mapping):
        section = {}

    backend = normalize_backend_choice(
        section.get("backend") or os.environ.get("HISTORY_REGISTRY_BACKEND"),
        default=DEFAULT_HISTORY_REGISTRY_BACKEND,
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
        ).strip()
        or "",
        "cloudflare_api_token": str(
            section.get("cloudflare_api_token") or os.environ.get(api_token_env) or ""
        ).strip(),
        "cloudflare_email": str(
            section.get("cloudflare_email") or os.environ.get(email_env) or ""
        ).strip(),
        "cloudflare_global_api_key": str(
            section.get("cloudflare_global_api_key") or os.environ.get(global_api_key_env) or ""
        ).strip(),
        "wallet_registry_table": str(
            section.get("wallet_registry_table") or DEFAULT_HISTORY_REGISTRY_WALLET_TABLE
        ).strip()
        or DEFAULT_HISTORY_REGISTRY_WALLET_TABLE,
        "timeout_seconds": max(
            1.0,
            float(section.get("timeout_seconds", DEFAULT_HISTORY_REGISTRY_TIMEOUT_SECONDS)),
        ),
        "fallback_to_local_on_error": coerce_bool(
            section.get("fallback_to_local_on_error", True),
            True,
        ),
        "read_fallback_enabled": coerce_bool(
            section.get("read_fallback_enabled", section.get("read_cloud_fallback_enabled", True)),
            True,
        ),
        "read_cloud_fallback_enabled": coerce_bool(
            section.get("read_cloud_fallback_enabled", section.get("read_fallback_enabled", True)),
            True,
        ),
        "replicate_to_cloudflare": coerce_bool(
            section.get("replicate_to_cloudflare", False),
            False,
        ),
    }


def list_wallet_history_records(
    artifacts_root: Path,
    config: Mapping[str, Any] | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    return create_history_registry(artifacts_root, config=config).list_records()


def create_history_registry(
    artifacts_root: Path,
    config: Mapping[str, Any] | None = None,
) -> HistoryRegistry:
    return HistoryRegistry(
        artifacts_root=artifacts_root,
        settings=resolve_history_registry_settings(config),
    )


@dataclass
class HistoryRegistry:
    artifacts_root: Path
    settings: dict[str, Any]
    _records_by_wallet: dict[str, dict[str, Any]] = field(default_factory=dict)
    _loaded_all_records: bool = False

    def status(self) -> dict[str, Any]:
        primary_backend = "cloudflare" if self._should_use_cloudflare() else "local"
        return {
            "enabled": bool(self.settings["enabled"]),
            "backend": str(self.settings["backend"]),
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
        }

    def record_path(self, wallet: str) -> Path | None:
        normalized_wallet = normalize_wallet_address(wallet)
        if not normalized_wallet:
            return None
        return wallet_history_registry_dir(self.artifacts_root) / f"{normalized_wallet}.json"

    def contains(self, wallet: str) -> bool:
        normalized_wallet = normalize_wallet_address(wallet)
        if not normalized_wallet or not self.settings["enabled"]:
            return False
        if self._should_use_cloudflare():
            try:
                self._prime_cloudflare_cache()
                return wallet_history_record_is_complete(
                    self._records_by_wallet.get(normalized_wallet)
                )
            except CLOUDFLARE_TRANSIENT_ERRORS:
                if not self._should_fallback_local():
                    raise
        record_path = self.record_path(normalized_wallet)
        if record_path and record_path.exists():
            return wallet_history_record_is_complete(
                normalize_wallet_history_record(
                    read_wallet_history_record(record_path),
                    wallet_fallback=normalized_wallet,
                )
            )
        if self._should_read_cloudflare_fallback():
            try:
                self._prime_cloudflare_cache()
                return wallet_history_record_is_complete(
                    self._records_by_wallet.get(normalized_wallet)
                )
            except CLOUDFLARE_TRANSIENT_ERRORS:
                return False
        return False

    def upsert(
        self,
        *,
        wallet: str,
        leaderboard_entry: Mapping[str, Any],
        run_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        normalized_wallet = normalize_wallet_address(wallet)
        if not normalized_wallet or not self.settings["enabled"]:
            return None

        existing = self._load_existing_record(normalized_wallet)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        last_run_id = str(existing.get("last_run_id") or "").strip()
        run_count = decode_int(existing.get("run_count"))
        if last_run_id != run_id:
            run_count += 1

        record = normalize_wallet_history_record(
            {
                "wallet_address": normalized_wallet,
                "user_name": str(
                    leaderboard_entry.get("userName") or existing.get("user_name") or ""
                ),
                "x_username": str(
                    leaderboard_entry.get("xUsername") or existing.get("x_username") or ""
                ),
                "first_seen_at": str(existing.get("first_seen_at") or timestamp),
                "last_seen_at": timestamp,
                "last_run_id": run_id,
                "last_status": status,
                "run_count": run_count,
            },
            wallet_fallback=normalized_wallet,
        )
        if not record:
            return None

        if self._should_use_cloudflare():
            try:
                stored = self._upsert_cloudflare_record(record)
                self._remember_record(stored)
                return stored
            except CloudflareD1RequestError:
                if not self._should_fallback_local():
                    raise

        stored = self._write_local_record(record)
        self._remember_record(stored)
        if self._should_replicate_to_cloudflare():
            try:
                mirrored = self._upsert_cloudflare_record(record)
            except CloudflareD1RequestError:
                pass
            else:
                if mirrored:
                    stored = {**stored, "replica_backend": "cloudflare", "replica_status": "persisted"}
                else:
                    stored = {**stored, "replica_backend": "cloudflare", "replica_status": "persisted"}
        return stored

    def sync_local_to_cloudflare(self) -> dict[str, Any]:
        if not self.settings["enabled"]:
            return {
                "status": "disabled",
                "backend": "cloudflare",
                "reason": "history_registry_disabled",
            }
        if not self._is_cloudflare_configured():
            return {
                "status": "disabled",
                "backend": "cloudflare",
                "reason": "cloudflare_not_configured",
            }

        rows = [record for _path, record in self._list_local_records()]
        if not rows:
            return {"status": "synced", "backend": "cloudflare", "record_count": 0}
        try:
            self._upsert_cloudflare_records(rows)
        except CloudflareD1RequestError as exc:
            return {
                "status": "failed",
                "backend": "cloudflare",
                "record_count": len(rows),
                "error": str(exc),
            }
        return {"status": "synced", "backend": "cloudflare", "record_count": len(rows)}

    def delete_wallet(self, wallet: str) -> bool:
        normalized_wallet = normalize_wallet_address(wallet)
        if not normalized_wallet or not self.settings["enabled"]:
            return False
        deleted = False
        if self._should_use_cloudflare():
            try:
                deleted = bool(
                    cloudflare_d1_delete_rows(
                        self._cloudflare_config(),
                        self.settings["wallet_registry_table"],
                        filters={"wallet_address": f"eq.{normalized_wallet}"},
                    )
                )
            except CloudflareD1RequestError:
                if not self._should_fallback_local():
                    raise
            else:
                self._records_by_wallet.pop(normalized_wallet, None)
                return deleted

        record_path = self.record_path(normalized_wallet)
        if record_path and record_path.exists():
            record_path.unlink(missing_ok=True)
            self._records_by_wallet.pop(normalized_wallet, None)
            return True
        return deleted

    def clear(self) -> int:
        if not self.settings["enabled"]:
            return 0
        if self._should_use_cloudflare():
            try:
                deleted_rows = cloudflare_d1_delete_rows(
                    self._cloudflare_config(),
                    self.settings["wallet_registry_table"],
                    filters={"wallet_address": "not.is.null"},
                )
                deleted_count = int(
                    (deleted_rows[0] if deleted_rows else {}).get("deleted_count")
                    or len(deleted_rows)
                )
                self._records_by_wallet.clear()
                self._loaded_all_records = True
                return deleted_count
            except CloudflareD1RequestError:
                if not self._should_fallback_local():
                    raise

        registry_dir = wallet_history_registry_dir(self.artifacts_root)
        deleted_count = 0
        if registry_dir.exists():
            for path in registry_dir.glob("*.json"):
                if path.is_file():
                    path.unlink(missing_ok=True)
                    deleted_count += 1
        self._records_by_wallet.clear()
        self._loaded_all_records = False
        return deleted_count

    def list_records(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.settings["enabled"]:
            return []
        if self._should_use_cloudflare():
            try:
                self._prime_cloudflare_cache()
                return [
                    (self.record_path(wallet) or wallet_history_registry_dir(self.artifacts_root), dict(record))
                    for wallet, record in sorted(self._records_by_wallet.items())
                ]
            except CloudflareD1RequestError:
                if not self._should_fallback_local():
                    raise
        return self._list_local_records()

    def _should_use_cloudflare(self) -> bool:
        return bool(
            self.settings["enabled"]
            and self.settings["backend"] == "cloudflare"
            and self.settings["cloudflare_account_id"]
            and self.settings["cloudflare_d1_database_id"]
            and self._has_cloudflare_auth()
        )

    def _is_cloudflare_configured(self) -> bool:
        return bool(
            self.settings["cloudflare_account_id"]
            and self.settings["cloudflare_d1_database_id"]
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
            self.settings["enabled"]
            and self.settings.get("backend") != "cloudflare"
            and self.settings.get("replicate_to_cloudflare", False)
            and self._is_cloudflare_configured()
        )

    def _should_read_cloudflare_fallback(self) -> bool:
        return bool(
            self.settings["enabled"]
            and self.settings.get("backend") != "cloudflare"
            and self.settings.get("read_fallback_enabled", True)
            and self._is_cloudflare_configured()
        )

    def _should_fallback_local(self) -> bool:
        return bool(self.settings.get("fallback_to_local_on_error", True))

    def _cloudflare_config(self) -> CloudflareD1Config:
        return CloudflareD1Config.from_settings(self.settings)

    def _prime_cloudflare_cache(self) -> None:
        if self._loaded_all_records:
            return
        records: dict[str, dict[str, Any]] = {}
        offset = 0
        page_size = 1000
        while True:
            rows = cloudflare_d1_select_rows(
                self._cloudflare_config(),
                self.settings["wallet_registry_table"],
                columns=list(REGISTRY_RECORD_FIELDS),
                order="wallet_address.asc",
                limit=page_size,
                offset=offset,
            )
            if not rows:
                break
            for row in rows:
                normalized = normalize_wallet_history_record(row)
                wallet_address = str(normalized.get("wallet_address") or "")
                if wallet_address:
                    records[wallet_address] = normalized
            if len(rows) < page_size:
                break
            offset += page_size
        self._records_by_wallet = records
        self._loaded_all_records = True

    def _load_existing_record(self, wallet: str) -> dict[str, Any]:
        cached = self._records_by_wallet.get(wallet)
        if cached:
            return dict(cached)
        if self._should_use_cloudflare() or self._should_read_cloudflare_fallback():
            try:
                record = self._load_cloudflare_record(wallet)
            except CLOUDFLARE_TRANSIENT_ERRORS:
                if not self._should_fallback_local():
                    raise
            else:
                if record:
                    self._remember_record(record)
                    return record

        record_path = self.record_path(wallet)
        if record_path and record_path.exists():
            record = normalize_wallet_history_record(read_wallet_history_record(record_path), wallet_fallback=wallet)
            self._remember_record(record)
            return record
        return {}

    def _write_local_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        wallet_address = str(record.get("wallet_address") or "")
        record_path = self.record_path(wallet_address)
        if record_path is None:
            return {}
        record_path.parent.mkdir(parents=True, exist_ok=True)
        normalized = normalize_wallet_history_record(record, wallet_fallback=wallet_address)
        with REGISTRY_LOCK:
            record_path.write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return normalized

    def _upsert_cloudflare_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        rows = self._upsert_cloudflare_records([dict(record)])
        if rows:
            return normalize_wallet_history_record(rows[0], wallet_fallback=record.get("wallet_address"))
        return normalize_wallet_history_record(record, wallet_fallback=record.get("wallet_address"))

    def _upsert_cloudflare_records(self, rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        config = self._cloudflare_config()
        page_size = 500
        stored_rows: list[dict[str, Any]] = []
        for offset in range(0, len(rows), page_size):
            stored_rows.extend(
                cloudflare_d1_upsert_rows(
                    config,
                    self.settings["wallet_registry_table"],
                    rows=[dict(row) for row in rows[offset : offset + page_size]],
                    on_conflict="wallet_address",
                )
            )
        return stored_rows

    def _load_cloudflare_record(self, wallet: str) -> dict[str, Any]:
        rows = cloudflare_d1_select_rows(
            self._cloudflare_config(),
            self.settings["wallet_registry_table"],
            columns=list(REGISTRY_RECORD_FIELDS),
            filters={"wallet_address": f"eq.{wallet}"},
            limit=1,
        )
        if not rows:
            return {}
        return normalize_wallet_history_record(rows[0], wallet_fallback=wallet)

    def _list_local_records(self) -> list[tuple[Path, dict[str, Any]]]:
        registry_dir = wallet_history_registry_dir(self.artifacts_root)
        if not registry_dir.exists():
            return []

        records: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(registry_dir.glob("*.json")):
            if not path.is_file():
                continue
            record = normalize_wallet_history_record(read_wallet_history_record(path), wallet_fallback=path.stem)
            wallet_address = str(record.get("wallet_address") or "")
            if not wallet_address:
                continue
            self._remember_record(record)
            records.append((path, record))
        return records

    def _remember_record(self, record: Mapping[str, Any]) -> None:
        normalized = normalize_wallet_history_record(record)
        wallet_address = str(normalized.get("wallet_address") or "")
        if wallet_address:
            self._records_by_wallet[wallet_address] = normalized


def normalize_wallet_history_record(
    payload: Mapping[str, Any] | None,
    *,
    wallet_fallback: Any = "",
) -> dict[str, Any]:
    record = dict(payload) if isinstance(payload, Mapping) else {}
    wallet_address = normalize_wallet_address(record.get("wallet_address") or wallet_fallback)
    if not wallet_address:
        return {}
    run_count = decode_int(record.get("run_count"))
    return {
        "wallet_address": wallet_address,
        "user_name": str(record.get("user_name") or ""),
        "x_username": str(record.get("x_username") or ""),
        "first_seen_at": str(record.get("first_seen_at") or ""),
        "last_seen_at": str(record.get("last_seen_at") or ""),
        "last_run_id": str(record.get("last_run_id") or ""),
        "last_status": str(record.get("last_status") or ""),
        "run_count": run_count if run_count > 0 else 1,
    }
