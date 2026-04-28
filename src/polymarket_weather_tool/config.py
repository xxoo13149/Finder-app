from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("configs/default_config.json")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    return json.loads(config_path.read_text(encoding="utf-8"))


def clone_config(config: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(config, ensure_ascii=False))


def apply_overrides(
    config: dict[str, Any],
    *,
    target_count: int | None = None,
    min_pnl: float | None = None,
    max_pnl: float | None = None,
    min_volume: float | None = None,
    max_volume: float | None = None,
    min_traded_count: int | None = None,
    max_traded_count: int | None = None,
    min_weather_trade_ratio: float | None = None,
    fetch_limit: int | None = None,
    max_fetch_limit: int | None = None,
    max_weather_events: int | None = None,
    max_wallet_offset: int | None = None,
    concurrent_wallets: int | None = None,
    verbose: bool | None = None,
    use_cache: bool | None = None,
    enable_chain_validation: bool | None = None,
    chain_api_key_env: str | None = None,
) -> dict[str, Any]:
    updated = clone_config(config)
    wallet_filter = updated.setdefault("wallet_filter", {})
    if target_count is not None:
        wallet_filter["target_count"] = target_count
    if min_pnl is not None:
        wallet_filter["min_pnl"] = min_pnl
    if max_pnl is not None:
        wallet_filter["max_pnl"] = max_pnl
    if min_volume is not None:
        wallet_filter["min_volume"] = min_volume
    if max_volume is not None:
        wallet_filter["max_volume"] = max_volume
    if min_traded_count is not None:
        wallet_filter["min_traded_count"] = min_traded_count
    if max_traded_count is not None:
        wallet_filter["max_traded_count"] = max_traded_count
    if min_weather_trade_ratio is not None:
        wallet_filter["min_weather_trade_ratio"] = min_weather_trade_ratio
    if fetch_limit is not None:
        updated.setdefault("leaderboard", {})["fetch_limit"] = fetch_limit
    if max_fetch_limit is not None:
        updated.setdefault("leaderboard", {})["max_fetch_limit"] = max_fetch_limit
    if max_weather_events is not None:
        updated.setdefault("weather", {})["max_events"] = max_weather_events
    if max_wallet_offset is not None:
        updated.setdefault("pagination", {})["max_offset"] = max_wallet_offset
    if concurrent_wallets is not None:
        updated.setdefault("analysis", {})["concurrent_wallets"] = concurrent_wallets
    if verbose is not None:
        updated.setdefault("runtime", {})["verbose"] = verbose
    if use_cache is not None:
        updated.setdefault("api", {})["use_cache"] = use_cache
    if enable_chain_validation is not None:
        updated.setdefault("chain_validation", {})["enabled"] = enable_chain_validation
    if chain_api_key_env:
        updated.setdefault("chain_validation", {})["api_key_envs"] = [chain_api_key_env]
    return updated
