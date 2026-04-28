from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HISTORY_REGISTRY_DIRNAME = "_wallet_registry"


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


def list_wallet_history_records(artifacts_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    registry_dir = wallet_history_registry_dir(artifacts_root)
    if not registry_dir.exists():
        return []

    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(registry_dir.glob("*.json")):
        if not path.is_file():
            continue
        record = dict(read_wallet_history_record(path))
        wallet_address = normalize_wallet_address(record.get("wallet_address") or path.stem)
        if not wallet_address:
            continue
        run_count = decode_int(record.get("run_count"))
        record["wallet_address"] = wallet_address
        record["user_name"] = str(record.get("user_name") or "")
        record["x_username"] = str(record.get("x_username") or "")
        record["first_seen_at"] = str(record.get("first_seen_at") or "")
        record["last_seen_at"] = str(record.get("last_seen_at") or "")
        record["last_run_id"] = str(record.get("last_run_id") or "")
        record["last_status"] = str(record.get("last_status") or "")
        record["run_count"] = run_count if run_count > 0 else 1
        records.append((path, record))
    return records
