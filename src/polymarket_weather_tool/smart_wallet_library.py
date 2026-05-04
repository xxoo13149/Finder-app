from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


UTC = timezone.utc
SMART_WALLET_LIBRARY_DIRNAME = "_smart_wallet_library"
WALLET_PROFILE_DIRNAME = "wallet_profile"
WALLET_HISTORY_DIRNAME = "wallet_history"
SMART_WALLET_IMPORT_ROWS_FILENAME = "smart_wallet_import_rows.json"
SMART_WALLET_IMPORT_SUMMARY_FILENAME = "smart_wallet_import_summary.json"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    return ""


def to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("0x"):
        text = f"0x{text}"
    return text


def read_json_file(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def smart_wallet_library_dir(artifacts_root: Path) -> Path:
    return artifacts_root / SMART_WALLET_LIBRARY_DIRNAME


def smart_wallet_profile_dir(artifacts_root: Path) -> Path:
    return smart_wallet_library_dir(artifacts_root) / WALLET_PROFILE_DIRNAME


def smart_wallet_history_dir(artifacts_root: Path) -> Path:
    return smart_wallet_library_dir(artifacts_root) / WALLET_HISTORY_DIRNAME


def smart_wallet_profile_path(artifacts_root: Path, normalized_address: str) -> Path:
    return smart_wallet_profile_dir(artifacts_root) / f"{normalized_address}.json"


def smart_wallet_history_path(artifacts_root: Path, normalized_address: str) -> Path:
    return smart_wallet_history_dir(artifacts_root) / f"{normalized_address}.json"


def safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def compact_text_list(values: Any, *, limit: int = 12, max_length: int = 240) -> list[str]:
    results: list[str] = []
    for item in safe_list(values):
        if isinstance(item, Mapping):
            text = (
                text_value(item.get("text"))
                or text_value(item.get("title"))
                or text_value(item.get("label"))
                or text_value(item.get("summary"))
                or text_value(item.get("reason"))
            )
        else:
            text = text_value(item)
        if not text:
            continue
        results.append(text[:max_length])
        if len(results) >= limit:
            break
    return results


def candidate_wallet_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    if isinstance(payload, Mapping):
        for key in ("wallets", "items", "records", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]

        for key in ("data", "payload", "result"):
            nested = candidate_wallet_rows(payload.get(key))
            if nested:
                return nested

        if any(key in payload for key in ("wallet", "address", "normalizedAddress", "normalized_address")):
            return [dict(payload)]

    return []


def wallet_master_record(row: Mapping[str, Any]) -> dict[str, Any]:
    wallet = safe_mapping(row.get("wallet"))
    if wallet:
        return wallet
    return {
        "id": row.get("id"),
        "address": row.get("address") or row.get("wallet_address"),
        "normalizedAddress": row.get("normalizedAddress") or row.get("normalized_address"),
        "displayName": row.get("displayName") or row.get("display_name"),
        "alias": row.get("alias"),
        "sourceType": row.get("sourceType") or row.get("source_type"),
        "updatedAt": row.get("updatedAt") or row.get("updated_at"),
        "deletedAt": row.get("deletedAt") or row.get("deleted_at"),
    }


def normalized_address_from_row(row: Mapping[str, Any]) -> str:
    wallet = wallet_master_record(row)
    return normalize_address(
        wallet.get("normalizedAddress")
        or wallet.get("normalized_address")
        or wallet.get("address")
        or row.get("normalizedAddress")
        or row.get("normalized_address")
        or row.get("address")
        or row.get("wallet_address")
    )


def coerce_labels(values: Any) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for item in safe_list(values):
        if not isinstance(item, Mapping):
            text = text_value(item)
            if text:
                labels.append({"kind": "tag", "value": text, "source": "smart_pro"})
            continue
        label = safe_mapping(item)
        kind = text_value(label.get("kind")) or "tag"
        value = (
            text_value(label.get("value"))
            or text_value(label.get("display_name"))
            or text_value(label.get("displayName"))
            or text_value(label.get("name"))
            or text_value(label.get("title"))
        )
        if not value:
            continue
        labels.append(
            {
                **label,
                "kind": kind,
                "value": value,
                "source": text_value(label.get("source")) or "smart_pro",
                "updatedAt": text_value(label.get("updatedAt") or label.get("updated_at")),
            }
        )
    return labels


def coerce_records(values: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in safe_list(values):
        if not isinstance(item, Mapping):
            continue
        payload = dict(item)
        payload_id = text_value(payload.get("id"))
        if payload_id:
            payload["id"] = payload_id
        records.append(payload)
    return records


def merge_labels(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for label in [*coerce_labels(existing), *coerce_labels(incoming)]:
        key = (
            text_value(label.get("kind")).lower(),
            text_value(label.get("value")).lower(),
            text_value(label.get("source")).lower(),
        )
        current = merged.get(key, {})
        if text_value(label.get("updatedAt")) and text_value(current.get("updatedAt")):
            if text_value(label.get("updatedAt")) < text_value(current.get("updatedAt")):
                continue
        merged[key] = {**current, **label}
    return list(merged.values())


def merge_records_by_id(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []

    for record in [*coerce_records(existing), *coerce_records(incoming)]:
        record_id = text_value(record.get("id"))
        if not record_id:
            record_key = json.dumps(record, ensure_ascii=False, sort_keys=True)
            if record_key not in {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in anonymous}:
                anonymous.append(record)
            continue
        merged[record_id] = {**merged.get(record_id, {}), **record}

    return [*merged.values(), *anonymous]


def pick_user_name(row: Mapping[str, Any]) -> str:
    wallet = wallet_master_record(row)
    source_meta = safe_mapping(row.get("sourceMeta") or row.get("source_meta"))
    return (
        text_value(row.get("userName"))
        or text_value(row.get("user_name"))
        or text_value(source_meta.get("userName"))
        or text_value(source_meta.get("username"))
        or text_value(wallet.get("displayName"))
        or text_value(wallet.get("display_name"))
        or text_value(wallet.get("alias"))
        or text_value(row.get("summaryText"))
    )


def pick_x_username(row: Mapping[str, Any]) -> str:
    source_meta = safe_mapping(row.get("sourceMeta") or row.get("source_meta"))
    return (
        text_value(row.get("xUsername"))
        or text_value(row.get("x_username"))
        or text_value(source_meta.get("xUsername"))
        or text_value(source_meta.get("x_username"))
        or text_value(source_meta.get("twitter"))
    )


def normalize_import_wallet_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    normalized_address = normalized_address_from_row(row)
    if not normalized_address:
        return None

    wallet = wallet_master_record(row)
    wallet_payload = {
        "id": text_value(wallet.get("id")),
        "address": text_value(wallet.get("address")) or normalized_address,
        "normalizedAddress": normalized_address,
        "displayName": text_value(wallet.get("displayName") or wallet.get("display_name")),
        "alias": text_value(wallet.get("alias")),
        "sourceType": text_value(wallet.get("sourceType") or wallet.get("source_type")) or "smart_pro",
        "updatedAt": text_value(wallet.get("updatedAt") or wallet.get("updated_at")),
        "deletedAt": text_value(wallet.get("deletedAt") or wallet.get("deleted_at")),
    }

    return {
        "wallet": wallet_payload,
        "userName": pick_user_name(row),
        "xUsername": pick_x_username(row),
        "labels": coerce_labels(row.get("labels")),
        "notes": coerce_records(row.get("notes")),
        "auditLogs": coerce_records(row.get("auditLogs") or row.get("audit_logs")),
        "importBatch": safe_mapping(row.get("importBatch") or row.get("import_batch")),
        "summaryText": text_value(row.get("summaryText") or row.get("summary_text")),
        "highlights": compact_text_list(row.get("highlights")),
        "statusBadges": compact_text_list(row.get("statusBadges") or row.get("status_badges")),
        "sourceMeta": safe_mapping(row.get("sourceMeta") or row.get("source_meta")),
        "metrics": safe_mapping(row.get("metrics")),
        "trades": safe_list(row.get("trades")),
        "positions": safe_list(row.get("positions")),
        "alerts": safe_list(row.get("alerts")),
    }


def merge_import_wallet_rows(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    current_wallet = safe_mapping(current.get("wallet"))
    incoming_wallet = safe_mapping(incoming.get("wallet"))
    merged_wallet = {
        **current_wallet,
        **{key: value for key, value in incoming_wallet.items() if value not in (None, "")},
    }
    if text_value(current_wallet.get("updatedAt")) and text_value(incoming_wallet.get("updatedAt")):
        if text_value(incoming_wallet.get("updatedAt")) < text_value(current_wallet.get("updatedAt")):
            merged_wallet = current_wallet

    return {
        "wallet": merged_wallet,
        "userName": text_value(incoming.get("userName")) or text_value(current.get("userName")),
        "xUsername": text_value(incoming.get("xUsername")) or text_value(current.get("xUsername")),
        "labels": merge_labels(current.get("labels"), incoming.get("labels")),
        "notes": merge_records_by_id(current.get("notes"), incoming.get("notes")),
        "auditLogs": merge_records_by_id(current.get("auditLogs"), incoming.get("auditLogs")),
        "importBatch": {**safe_mapping(current.get("importBatch")), **safe_mapping(incoming.get("importBatch"))},
        "summaryText": text_value(incoming.get("summaryText")) or text_value(current.get("summaryText")),
        "highlights": [*compact_text_list(current.get("highlights")), *compact_text_list(incoming.get("highlights"))][:12],
        "statusBadges": [*compact_text_list(current.get("statusBadges")), *compact_text_list(incoming.get("statusBadges"))][:12],
        "sourceMeta": {**safe_mapping(current.get("sourceMeta")), **safe_mapping(incoming.get("sourceMeta"))},
        "metrics": {**safe_mapping(current.get("metrics")), **safe_mapping(incoming.get("metrics"))},
        "trades": safe_list(incoming.get("trades")) or safe_list(current.get("trades")),
        "positions": safe_list(incoming.get("positions")) or safe_list(current.get("positions")),
        "alerts": safe_list(incoming.get("alerts")) or safe_list(current.get("alerts")),
    }


def normalize_import_wallet_rows(payload: Any) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in candidate_wallet_rows(payload):
        normalized = normalize_import_wallet_row(item)
        if not normalized:
            continue
        wallet_address = normalized["wallet"]["normalizedAddress"]
        if wallet_address in rows:
            rows[wallet_address] = merge_import_wallet_rows(rows[wallet_address], normalized)
        else:
            rows[wallet_address] = normalized
    return list(rows.values())


def summarize_import_wallet_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    label_count = sum(len(coerce_labels(row.get("labels"))) for row in rows)
    note_count = sum(len(coerce_records(row.get("notes"))) for row in rows)
    audit_count = sum(len(coerce_records(row.get("auditLogs"))) for row in rows)
    deleted_count = sum(1 for row in rows if text_value(safe_mapping(row.get("wallet")).get("deletedAt")))
    return {
        "wallet_count": len(rows),
        "label_count": label_count,
        "note_count": note_count,
        "audit_log_count": audit_count,
        "deleted_wallet_count": deleted_count,
    }


def build_import_profile_record(row: Mapping[str, Any], *, imported_at: str, source_file_name: str) -> dict[str, Any]:
    wallet = safe_mapping(row.get("wallet"))
    normalized_address = text_value(wallet.get("normalizedAddress"))
    return {
        "wallet": wallet,
        "wallet_address": normalized_address,
        "normalized_address": normalized_address,
        "user_name": text_value(row.get("userName")),
        "x_username": text_value(row.get("xUsername")),
        "labels": coerce_labels(row.get("labels")),
        "notes": coerce_records(row.get("notes")),
        "auditLogs": coerce_records(row.get("auditLogs")),
        "importBatch": safe_mapping(row.get("importBatch")),
        "summaryText": text_value(row.get("summaryText")),
        "highlights": compact_text_list(row.get("highlights")),
        "statusBadges": compact_text_list(row.get("statusBadges")),
        "sourceMeta": safe_mapping(row.get("sourceMeta")),
        "metrics": safe_mapping(row.get("metrics")),
        "trades": safe_list(row.get("trades")),
        "positions": safe_list(row.get("positions")),
        "alerts": safe_list(row.get("alerts")),
        "lastImportedAt": imported_at,
        "sourceFileName": source_file_name,
    }


def merge_profile_records(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = {
        **safe_mapping(existing),
        **safe_mapping(incoming),
    }
    merged["wallet"] = {
        **safe_mapping(existing.get("wallet")),
        **safe_mapping(incoming.get("wallet")),
    }
    merged["labels"] = merge_labels(existing.get("labels"), incoming.get("labels"))
    merged["notes"] = merge_records_by_id(existing.get("notes"), incoming.get("notes"))
    merged["auditLogs"] = merge_records_by_id(existing.get("auditLogs"), incoming.get("auditLogs"))
    merged["importBatch"] = {
        **safe_mapping(existing.get("importBatch")),
        **safe_mapping(incoming.get("importBatch")),
    }
    merged["sourceMeta"] = {
        **safe_mapping(existing.get("sourceMeta")),
        **safe_mapping(incoming.get("sourceMeta")),
    }
    merged["metrics"] = {
        **safe_mapping(existing.get("metrics")),
        **safe_mapping(incoming.get("metrics")),
    }
    for key in ("summaryText", "user_name", "x_username", "lastImportedAt", "sourceFileName"):
        merged[key] = text_value(incoming.get(key)) or text_value(existing.get(key))
    for key in ("trades", "positions", "alerts"):
        merged[key] = safe_list(incoming.get(key)) or safe_list(existing.get(key))
    merged["highlights"] = [*compact_text_list(existing.get("highlights")), *compact_text_list(incoming.get("highlights"))][:12]
    merged["statusBadges"] = [*compact_text_list(existing.get("statusBadges")), *compact_text_list(incoming.get("statusBadges"))][:12]
    return merged


def append_history_snapshot(existing: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    history = {
        "walletAddress": text_value(existing.get("walletAddress")) or text_value(snapshot.get("walletAddress")),
        "snapshots": safe_list(existing.get("snapshots")),
        "analysisRuns": safe_list(existing.get("analysisRuns")),
        "updatedAt": text_value(snapshot.get("importedAt")),
    }
    snapshot_id = text_value(snapshot.get("snapshotId"))
    snapshots = [dict(item) for item in history["snapshots"] if isinstance(item, Mapping)]
    if snapshot_id and not any(text_value(item.get("snapshotId")) == snapshot_id for item in snapshots):
        snapshots.append(dict(snapshot))
    history["snapshots"] = snapshots
    return history


def build_history_snapshot(row: Mapping[str, Any], *, imported_at: str, source_file_name: str) -> dict[str, Any]:
    wallet = safe_mapping(row.get("wallet"))
    normalized_address = text_value(wallet.get("normalizedAddress"))
    snapshot_suffix = (
        text_value(wallet.get("updatedAt"))
        or text_value(safe_mapping(row.get("importBatch")).get("id"))
        or imported_at
    )
    return {
        "snapshotId": f"{source_file_name}:{normalized_address}:{snapshot_suffix}",
        "walletAddress": normalized_address,
        "importedAt": imported_at,
        "sourceFileName": source_file_name,
        "wallet": wallet,
        "userName": text_value(row.get("userName")),
        "xUsername": text_value(row.get("xUsername")),
        "labels": coerce_labels(row.get("labels")),
        "notes": coerce_records(row.get("notes")),
        "auditLogs": coerce_records(row.get("auditLogs")),
        "importBatch": safe_mapping(row.get("importBatch")),
        "summaryText": text_value(row.get("summaryText")),
        "highlights": compact_text_list(row.get("highlights")),
        "statusBadges": compact_text_list(row.get("statusBadges")),
        "sourceMeta": safe_mapping(row.get("sourceMeta")),
        "metrics": safe_mapping(row.get("metrics")),
        "trades": safe_list(row.get("trades")),
        "positions": safe_list(row.get("positions")),
        "alerts": safe_list(row.get("alerts")),
    }


def materialize_smart_wallet_library(
    artifacts_root: Path,
    rows: list[dict[str, Any]],
    *,
    source_file_name: str,
    imported_at: str | None = None,
) -> dict[str, Any]:
    imported_timestamp = imported_at or now_iso()
    created_profiles = 0
    updated_profiles = 0

    for row in rows:
        normalized_address = text_value(safe_mapping(row.get("wallet")).get("normalizedAddress"))
        if not normalized_address:
            continue

        profile_path = smart_wallet_profile_path(artifacts_root, normalized_address)
        history_path = smart_wallet_history_path(artifacts_root, normalized_address)

        incoming_profile = build_import_profile_record(
            row,
            imported_at=imported_timestamp,
            source_file_name=source_file_name,
        )
        existing_profile = safe_mapping(read_json_file(profile_path, {}))
        if existing_profile:
            merged_profile = merge_profile_records(existing_profile, incoming_profile)
            updated_profiles += 1
        else:
            merged_profile = incoming_profile
            created_profiles += 1
        write_json_file(profile_path, merged_profile)

        history_snapshot = build_history_snapshot(
            row,
            imported_at=imported_timestamp,
            source_file_name=source_file_name,
        )
        existing_history = safe_mapping(read_json_file(history_path, {}))
        write_json_file(history_path, append_history_snapshot(existing_history, history_snapshot))

    summary = summarize_import_wallet_rows(rows)
    return {
        **summary,
        "created_profiles": created_profiles,
        "updated_profiles": updated_profiles,
        "source_file_name": source_file_name,
        "imported_at": imported_timestamp,
    }


def leaderboard_entries_from_import_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        wallet = safe_mapping(row.get("wallet"))
        metrics = safe_mapping(row.get("metrics"))
        entries.append(
            {
                "rank": index,
                "proxyWallet": text_value(wallet.get("normalizedAddress")),
                "userName": text_value(row.get("userName")),
                "xUsername": text_value(row.get("xUsername")),
                "pnl": to_float(
                    metrics.get("unified_profit")
                    or metrics.get("unifiedProfit")
                    or metrics.get("pnl")
                    or metrics.get("profit")
                ),
                "vol": to_float(
                    metrics.get("screening_volume")
                    or metrics.get("trade_volume")
                    or metrics.get("tradeVolume")
                    or metrics.get("volume")
                    or metrics.get("total_trade_notional")
                ),
                "importedSummaryText": text_value(row.get("summaryText")),
                "importedHighlights": compact_text_list(row.get("highlights")),
                "importedStatusBadges": compact_text_list(row.get("statusBadges")),
                "importedLabels": [text_value(item.get("value")) for item in coerce_labels(row.get("labels"))][:12],
                "importedDeletedAt": text_value(wallet.get("deletedAt")),
                "sourceMeta": safe_mapping(row.get("sourceMeta")),
            }
        )
    return entries


def load_import_wallet_rows(path: Path) -> list[dict[str, Any]]:
    payload = read_json_file(path, [])
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, Mapping)]
