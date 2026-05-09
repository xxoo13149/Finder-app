from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("configs/default_config.json")
DEFAULT_ANALYSIS_MODE = "standard"
WEEKLY_HIGH_PROFIT_MODE = "weekly_high_profit"
SMART_WALLET_LIBRARY_REFRESH_MODE = "smart_wallet_library_refresh"
RELAY_ANALYSIS_MODE = "relay_analysis"
CONFIG_SECTION_KEYS = (
    "api",
    "leaderboard",
    "wallet_filter",
    "pagination",
    "weather",
    "analysis",
    "history_registry",
    "history_provider",
    "history_ledger",
    "cloud_archive",
    "chain_validation",
    "runtime",
)
BUILTIN_ANALYSIS_MODE_PRESETS: dict[str, dict[str, Any]] = {
    WEEKLY_HIGH_PROFIT_MODE: {
        "leaderboard": {
            "time_period": "WEEK",
            "order_by": "PNL",
            "fetch_limit": 150,
            "max_fetch_limit": 500,
        },
        "wallet_filter": {
            "target_count": 10,
            "min_pnl": 25,
            "max_pnl": 5000,
            "min_volume": 500,
            "max_volume": 200000,
            "min_traded_count": 5,
            "max_traded_count": 120,
            "min_weather_trade_ratio": 0.2,
            "min_weather_notional_ratio": 0.45,
            "weather_focus_mode": "trade_or_notional",
        },
        "runtime": {
            "analysis_mode_label": "本周高盈利榜单",
        },
    },
    SMART_WALLET_LIBRARY_REFRESH_MODE: {
        "runtime": {
            "analysis_mode_label": "后台地址库回流",
        },
        "wallet_filter": {
            "activity_filter_mode": "all",
        },
    },
    RELAY_ANALYSIS_MODE: {
        "runtime": {
            "analysis_mode_label": "历史结果接力分析",
        },
        "wallet_filter": {
            "activity_filter_mode": "all",
        },
    },
}


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    return json.loads(config_path.read_text(encoding="utf-8"))


def clone_config(config: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(config, ensure_ascii=False))


def normalize_analysis_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ("", DEFAULT_ANALYSIS_MODE):
        return DEFAULT_ANALYSIS_MODE
    if normalized == WEEKLY_HIGH_PROFIT_MODE:
        return WEEKLY_HIGH_PROFIT_MODE
    if normalized == SMART_WALLET_LIBRARY_REFRESH_MODE:
        return SMART_WALLET_LIBRARY_REFRESH_MODE
    if normalized == RELAY_ANALYSIS_MODE:
        return RELAY_ANALYSIS_MODE
    return DEFAULT_ANALYSIS_MODE


def apply_analysis_mode(config: dict[str, Any], mode: Any) -> dict[str, Any]:
    normalized_mode = normalize_analysis_mode(mode)
    updated = clone_config(config)

    preset = BUILTIN_ANALYSIS_MODE_PRESETS.get(normalized_mode)
    if preset:
        updated = merge_config(updated, preset)

    custom_modes = updated.get("analysis_modes", {})
    custom_preset = custom_modes.get(normalized_mode) if isinstance(custom_modes, dict) else None
    if isinstance(custom_preset, dict):
        custom_sections = {
            key: value
            for key, value in custom_preset.items()
            if key in CONFIG_SECTION_KEYS and isinstance(value, dict)
        }
        if custom_sections:
            updated = merge_config(updated, custom_sections)

    runtime = updated.setdefault("runtime", {})
    runtime["analysis_mode"] = normalized_mode
    if normalized_mode == DEFAULT_ANALYSIS_MODE:
        runtime.setdefault("analysis_mode_label", "普通分析")
    return updated


def merge_config(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = clone_config(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = json.loads(json.dumps(value, ensure_ascii=False))
    return merged


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
    min_weather_notional_ratio: float | None = None,
    weather_focus_mode: str | None = None,
    activity_filter_mode: str | None = None,
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
    if min_weather_notional_ratio is not None:
        wallet_filter["min_weather_notional_ratio"] = min_weather_notional_ratio
    if weather_focus_mode:
        wallet_filter["weather_focus_mode"] = weather_focus_mode
    if activity_filter_mode:
        wallet_filter["activity_filter_mode"] = activity_filter_mode
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
