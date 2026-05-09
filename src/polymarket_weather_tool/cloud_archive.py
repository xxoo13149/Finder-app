from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .cloudflare_backend import (
    CloudflareD1Config,
    CloudflareD1RequestError,
    cloudflare_d1_select_rows,
    cloudflare_d1_upsert_rows,
)


UTC = timezone.utc
RUN_ARCHIVE_MANIFEST_FILENAME = "cloud_archive_manifest.json"
DEFAULT_CLOUD_ARCHIVE_DOCUMENTS_TABLE = "archived_documents"
DEFAULT_CLOUD_ARCHIVE_TIMEOUT_SECONDS = 20.0
RUN_REUSABLE_JSON_FILENAMES = (
    "resolved_config.json",
    "analysis_summary.json",
    "selected_wallets.json",
    "leaderboard.json",
    "screening_records.json",
    "weather_events.json",
    "errors.json",
    "smart_wallet_import_rows.json",
    "smart_wallet_import_summary.json",
    "relay_import_rows.json",
    "relay_import_summary.json",
)


def resolve_cloud_archive_settings(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    section = config.get("cloud_archive", {}) if isinstance(config, Mapping) else {}
    if not isinstance(section, Mapping):
        section = {}

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
    backend = str(section.get("backend") or "cloudflare").strip().lower() or "cloudflare"

    return {
        "enabled": bool(section.get("enabled", False)),
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
        "documents_table": str(
            section.get("documents_table") or DEFAULT_CLOUD_ARCHIVE_DOCUMENTS_TABLE
        ).strip()
        or DEFAULT_CLOUD_ARCHIVE_DOCUMENTS_TABLE,
        "timeout_seconds": max(
            1.0,
            float(section.get("timeout_seconds", DEFAULT_CLOUD_ARCHIVE_TIMEOUT_SECONDS)),
        ),
        "archive_run_outputs": bool(section.get("archive_run_outputs", True)),
        "archive_before_cleanup": bool(section.get("archive_before_cleanup", True)),
    }


@dataclass
class CloudArchiveStore:
    settings: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings["enabled"]
            and self.settings["backend"] == "cloudflare"
            and self.settings["cloudflare_account_id"]
            and self.settings["cloudflare_d1_database_id"]
            and (
                self.settings["cloudflare_api_token"]
                or (
                    self.settings["cloudflare_email"]
                    and self.settings["cloudflare_global_api_key"]
                )
            )
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings["enabled"]),
            "backend": str(self.settings["backend"]),
            "configured": bool(self.enabled),
            "documents_table": str(self.settings["documents_table"]),
            "archive_run_outputs": bool(self.settings["archive_run_outputs"]),
            "archive_before_cleanup": bool(self.settings["archive_before_cleanup"]),
        }

    def archive_run_outputs(self, output_dir: Path, *, run_id: str) -> dict[str, Any]:
        manifest = {
            "run_id": run_id,
            "backend": str(self.settings["backend"]),
            "configured": bool(self.enabled),
            "status": "skipped",
            "archived_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "document_count": 0,
            "documents": [],
        }
        if not self.enabled or not bool(self.settings["archive_run_outputs"]):
            manifest["status"] = "disabled"
            return manifest

        documents: list[dict[str, Any]] = []
        for file_name in RUN_REUSABLE_JSON_FILENAMES:
            file_path = output_dir / file_name
            if not file_path.exists() or not file_path.is_file():
                continue
            document = self.archive_json_file(
                file_path,
                document_type=file_name.removesuffix(".json"),
                document_key=f"runs/{run_id}/{file_name.removesuffix('.json')}",
                metadata={
                    "run_id": run_id,
                    "source_path": file_name,
                    "artifact_scope": "run_output",
                },
            )
            if document:
                documents.append(document)

        wallets_dir = output_dir / "wallets"
        if wallets_dir.exists():
            for wallet_path in sorted(wallets_dir.glob("*.json")):
                document = self.archive_json_file(
                    wallet_path,
                    document_type="wallet_analysis",
                    document_key=f"runs/{run_id}/wallets/{wallet_path.stem}",
                    metadata={
                        "run_id": run_id,
                        "wallet_address": wallet_path.stem,
                        "source_path": f"wallets/{wallet_path.name}",
                        "artifact_scope": "wallet_output",
                    },
                )
                if document:
                    documents.append(document)

        manifest["status"] = "archived"
        manifest["document_count"] = len(documents)
        manifest["documents"] = documents
        return manifest

    def archive_json_file(
        self,
        path: Path,
        *,
        document_type: str,
        document_key: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        return self.archive_json_document(
            document_type=document_type,
            document_key=document_key,
            payload=payload,
            metadata=dict(metadata or {}),
        )

    def archive_json_document(
        self,
        *,
        document_type: str,
        document_key: str,
        payload: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        content_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        record = {
            "document_type": str(document_type),
            "document_key": str(document_key),
            "run_id": str((metadata or {}).get("run_id") or ""),
            "wallet_address": str((metadata or {}).get("wallet_address") or ""),
            "source_path": str((metadata or {}).get("source_path") or ""),
            "content_sha256": content_sha256,
            "payload": payload,
            "metadata": dict(metadata or {}),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        rows = cloudflare_d1_upsert_rows(
            self._cloudflare_config(),
            self.settings["documents_table"],
            rows=[record],
            on_conflict="document_key",
        )
        stored = dict(rows[0]) if rows else dict(record)
        return {
            "document_type": str(stored.get("document_type") or document_type),
            "document_key": str(stored.get("document_key") or document_key),
            "wallet_address": str(stored.get("wallet_address") or ""),
            "source_path": str(stored.get("source_path") or ""),
            "content_sha256": str(stored.get("content_sha256") or content_sha256),
        }

    def load_latest_wallet_analysis(self, wallet_address: str) -> dict[str, Any] | None:
        normalized_wallet = str(wallet_address or "").strip().lower()
        if not self.enabled or not normalized_wallet:
            return None
        rows = cloudflare_d1_select_rows(
            self._cloudflare_config(),
            self.settings["documents_table"],
            columns=["document_key", "payload", "updated_at", "wallet_address", "metadata"],
            filters={
                "document_type": "eq.wallet_analysis",
                "wallet_address": f"eq.{normalized_wallet}",
            },
            order="updated_at.desc",
            limit=1,
        )
        if not rows:
            return None
        payload = rows[0].get("payload")
        if not isinstance(payload, Mapping):
            return None
        return {
            **dict(payload),
            "cloud_fallback": {
                "status": "loaded",
                "document_key": str(rows[0].get("document_key") or ""),
                "updated_at": str(rows[0].get("updated_at") or ""),
                "wallet_address": str(rows[0].get("wallet_address") or normalized_wallet),
            },
        }

    def _cloudflare_config(self) -> CloudflareD1Config:
        return CloudflareD1Config.from_settings(self.settings)


def create_cloud_archive_store(config: Mapping[str, Any] | None = None) -> CloudArchiveStore:
    return CloudArchiveStore(settings=resolve_cloud_archive_settings(config))


def run_archive_manifest_path(output_dir: Path) -> Path:
    return output_dir / RUN_ARCHIVE_MANIFEST_FILENAME


def read_run_archive_manifest(output_dir: Path) -> dict[str, Any]:
    manifest_path = run_archive_manifest_path(output_dir)
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_run_archive_manifest(output_dir: Path, manifest: Mapping[str, Any]) -> None:
    manifest_path = run_archive_manifest_path(output_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(dict(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def archive_run_outputs(
    output_dir: Path,
    *,
    run_id: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    store = create_cloud_archive_store(config)
    manifest = store.archive_run_outputs(output_dir, run_id=run_id)
    if manifest:
        write_run_archive_manifest(output_dir, manifest)
    return manifest


def cloud_archive_status(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return create_cloud_archive_store(config).status()


def load_latest_wallet_analysis(
    wallet_address: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return create_cloud_archive_store(config).load_latest_wallet_analysis(wallet_address)
