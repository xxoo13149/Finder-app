from __future__ import annotations

from typing import Any, Callable, Mapping


DEFAULT_HISTORY_PROVIDER_SOURCE = "public_goldsky"
DEFAULT_HISTORY_PROVIDER_ORDERBOOK_URL = (
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/orderbook-subgraph/0.0.1/gn"
)
DEFAULT_HISTORY_PROVIDER_ACTIVITY_URL = (
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/activity-subgraph/0.0.4/gn"
)
DEFAULT_HISTORY_PROVIDER_POSITIONS_URL = (
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/positions-subgraph/0.0.7/gn"
)
DEFAULT_HISTORY_PROVIDER_PAGE_SIZE = 200
DEFAULT_HISTORY_PROVIDER_MAX_PAGES = 30
DEFAULT_HISTORY_PROVIDER_TOKEN_LOOKUP_CHUNK_SIZE = 100
DEFAULT_HISTORY_PROVIDER_ASSET_DECIMALS = 6
DEFAULT_HISTORY_PROVIDER_USDC_ASSET_ID = "0"


def history_provider_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    settings = config.get("history_provider", {}) if isinstance(config, Mapping) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "screening_fallback_enabled": bool(settings.get("screening_fallback_enabled", True)),
        "trade_probe_fallback_enabled": bool(settings.get("trade_probe_fallback_enabled", True)),
        "source": str(settings.get("source") or DEFAULT_HISTORY_PROVIDER_SOURCE),
        "orderbook_url": str(
            settings.get("orderbook_url") or DEFAULT_HISTORY_PROVIDER_ORDERBOOK_URL
        ),
        "activity_url": str(
            settings.get("activity_url") or DEFAULT_HISTORY_PROVIDER_ACTIVITY_URL
        ),
        "positions_url": str(
            settings.get("positions_url") or DEFAULT_HISTORY_PROVIDER_POSITIONS_URL
        ),
        "page_size": max(1, int(settings.get("page_size", DEFAULT_HISTORY_PROVIDER_PAGE_SIZE))),
        "max_pages_per_stream": max(
            1,
            int(settings.get("max_pages_per_stream", DEFAULT_HISTORY_PROVIDER_MAX_PAGES)),
        ),
        "token_lookup_chunk_size": max(
            1,
            int(
                settings.get(
                    "token_lookup_chunk_size",
                    DEFAULT_HISTORY_PROVIDER_TOKEN_LOOKUP_CHUNK_SIZE,
                )
            ),
        ),
        "asset_decimals": max(
            0,
            int(settings.get("asset_decimals", DEFAULT_HISTORY_PROVIDER_ASSET_DECIMALS)),
        ),
        "usdc_asset_id": str(
            settings.get("usdc_asset_id") or DEFAULT_HISTORY_PROVIDER_USDC_ASSET_ID
        ).strip(),
        "always_for_full_snapshot": bool(settings.get("always_for_full_snapshot", False)),
        "fetch_when_trades_incomplete": bool(
            settings.get("fetch_when_trades_incomplete", True)
        ),
        "fetch_when_activity_incomplete": bool(
            settings.get("fetch_when_activity_incomplete", True)
        ),
    }


def history_provider_fetch_plan(
    *,
    config: Mapping[str, Any],
    snapshot_scope: str,
    trades_page: Mapping[str, Any],
    activity_page: Mapping[str, Any],
) -> dict[str, bool]:
    if snapshot_scope != "full":
        return {
            "enabled": False,
            "need_trade_history": False,
            "need_operations": False,
        }
    settings = history_provider_settings(config)
    if not settings["enabled"]:
        return {
            "enabled": False,
            "need_trade_history": False,
            "need_operations": False,
        }
    if settings["always_for_full_snapshot"]:
        return {
            "enabled": True,
            "need_trade_history": True,
            "need_operations": True,
        }
    need_trade_history = bool(settings["fetch_when_trades_incomplete"]) and not bool(
        trades_page.get("complete", True)
    )
    need_operations = bool(settings["fetch_when_activity_incomplete"]) and not bool(
        activity_page.get("complete", True)
    )
    return {
        "enabled": need_trade_history or need_operations,
        "need_trade_history": need_trade_history,
        "need_operations": need_operations,
    }


def should_fetch_screening_history_provider_trades(
    *,
    config: Mapping[str, Any],
    snapshot_scope: str,
    trades_page: Mapping[str, Any],
) -> bool:
    if snapshot_scope != "screening":
        return False
    settings = history_provider_settings(config)
    if not settings["enabled"] or not settings["screening_fallback_enabled"]:
        return False
    return not bool(trades_page.get("complete", True))


def should_fetch_trade_probe_history_provider(config: Mapping[str, Any]) -> bool:
    settings = history_provider_settings(config)
    return bool(settings["enabled"] and settings["trade_probe_fallback_enabled"])


def build_full_history_bundle(
    *,
    settings: Mapping[str, Any],
    wallet: str,
    need_trade_history: bool,
    need_operations: bool,
    fetch_order_fills: Callable[..., dict[str, Any]],
    fetch_activity_operations: Callable[..., dict[str, Any]],
    fetch_token_condition_lookup: Callable[..., dict[str, Any]],
    graph_order_fill_asset_id: Callable[..., str],
    convert_order_fills_to_trade_records: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    if need_trade_history:
        order_fills_page = fetch_order_fills(
            wallet=wallet,
            settings=settings,
        )
        token_lookup_page = fetch_token_condition_lookup(
            token_ids=sorted(
                {
                    graph_order_fill_asset_id(record, settings=settings)
                    for record in order_fills_page.get("records", [])
                    if isinstance(record, Mapping)
                    and graph_order_fill_asset_id(record, settings=settings)
                }
            ),
            settings=settings,
        )
        trade_records = convert_order_fills_to_trade_records(
            wallet=wallet,
            fills=order_fills_page.get("records", []),
            token_lookup_page=token_lookup_page,
            settings=settings,
        )
        trades_complete = bool(order_fills_page.get("complete", False)) and bool(
            token_lookup_page.get("complete", False)
        )
    else:
        order_fills_page = skipped_provider_collection_page("order_fills")
        token_lookup_page = skipped_provider_collection_page("token_conditions")
        trade_records = []
        trades_complete = True

    if need_operations:
        operation_page = fetch_activity_operations(
            wallet=wallet,
            settings=settings,
        )
        operations_complete = bool(operation_page.get("complete", False))
    else:
        operation_page = skipped_provider_collection_page("activity_operations")
        operations_complete = True

    if trades_complete and operations_complete and (need_trade_history or need_operations):
        status_stop_reason = "graphql_history_provider_complete"
    elif need_trade_history and not bool(order_fills_page.get("complete", False)):
        status_stop_reason = str(order_fills_page.get("stop_reason") or "")
    elif need_trade_history and not bool(token_lookup_page.get("complete", False)):
        status_stop_reason = str(token_lookup_page.get("stop_reason") or "")
    else:
        status_stop_reason = str(operation_page.get("stop_reason") or "")
    status = {
        "complete": trades_complete and operations_complete,
        "stop_reason": status_stop_reason,
        "collection_mode": "graphql_history_provider",
        "source_section": "history_provider",
        "history_scope": "full_history",
        "source": settings["source"],
        "trades_complete": trades_complete,
        "operations_complete": operations_complete,
        "operations_attempted": need_operations,
        "trades_attempted": need_trade_history,
        "order_fill_count": len(order_fills_page.get("records", [])),
        "trade_record_count": len(trade_records),
        "operation_record_count": len(operation_page.get("records", [])),
        "token_lookup_count": len(token_lookup_page.get("records", [])),
    }
    return {
        "source": settings["source"],
        "trade_records": trade_records,
        "operation_records": list(operation_page.get("records", [])),
        "status": status,
        "order_fills_page": order_fills_page,
        "operation_page": operation_page,
        "token_lookup_page": token_lookup_page,
    }


def build_screening_trade_bundle(
    *,
    settings: Mapping[str, Any],
    wallet: str,
    screening_mode: str,
    window_bounds: tuple[int, int] | None,
    page_size: int,
    now_epoch: int,
    fetch_order_fills: Callable[..., dict[str, Any]],
    fetch_token_condition_lookup: Callable[..., dict[str, Any]],
    graph_order_fill_asset_id: Callable[..., str],
    convert_order_fills_to_trade_records: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    start_ts: int | None = None
    end_ts: int | None = None
    max_pages_override: int | None = None
    if screening_mode == "screening_window" and window_bounds is not None:
        start_ts, end_ts = window_bounds
    elif screening_mode == "recent_activity":
        start_ts = 0
        end_ts = now_epoch
        max_pages_override = 1

    order_fills_page = fetch_order_fills(
        wallet=wallet,
        settings=settings,
        start_ts=start_ts,
        end_ts=end_ts,
        page_size_override=page_size,
        max_pages_override=max_pages_override,
    )
    token_lookup_page = fetch_token_condition_lookup(
        token_ids=sorted(
            {
                graph_order_fill_asset_id(record, settings=settings)
                for record in order_fills_page.get("records", [])
                if isinstance(record, Mapping)
                and graph_order_fill_asset_id(record, settings=settings)
            }
        ),
        settings=settings,
    )
    trade_records = convert_order_fills_to_trade_records(
        wallet=wallet,
        fills=order_fills_page.get("records", []),
        token_lookup_page=token_lookup_page,
        settings=settings,
    )
    trades_complete = bool(order_fills_page.get("complete", False)) and bool(
        token_lookup_page.get("complete", False)
    )
    history_scope = "recent_activity" if screening_mode == "recent_activity" else "screening_window"
    status = {
        "complete": trades_complete,
        "stop_reason": (
            "graphql_screening_trade_history_complete"
            if trades_complete
            else str(
                order_fills_page.get("stop_reason")
                or token_lookup_page.get("stop_reason")
                or ""
            )
        ),
        "collection_mode": "graphql_screening_history_provider",
        "source_section": "history_provider",
        "history_scope": history_scope,
        "source": settings["source"],
        "trades_complete": trades_complete,
        "operations_complete": False,
        "operations_attempted": False,
        "trades_attempted": True,
        "order_fill_count": len(order_fills_page.get("records", [])),
        "trade_record_count": len(trade_records),
        "token_lookup_count": len(token_lookup_page.get("records", [])),
    }
    if start_ts is not None:
        status["range_start"] = start_ts
    if end_ts is not None:
        status["range_end"] = end_ts
    return {
        "source": settings["source"],
        "trade_records": trade_records,
        "operation_records": [],
        "status": status,
        "order_fills_page": order_fills_page,
        "token_lookup_page": token_lookup_page,
    }


def build_trade_probe_records(
    *,
    settings: Mapping[str, Any],
    wallet: str,
    probe_limit: int,
    window_bounds: tuple[int, int] | None,
    fetch_order_fills: Callable[..., dict[str, Any]],
    fetch_token_condition_lookup: Callable[..., dict[str, Any]],
    graph_order_fill_asset_id: Callable[..., str],
    convert_order_fills_to_trade_records: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    start_ts = window_bounds[0] if window_bounds is not None else None
    end_ts = window_bounds[1] if window_bounds is not None else None
    order_fills_page = fetch_order_fills(
        wallet=wallet,
        settings=settings,
        start_ts=start_ts,
        end_ts=end_ts,
        page_size_override=max(1, probe_limit),
        max_pages_override=1,
    )
    token_lookup_page = fetch_token_condition_lookup(
        token_ids=sorted(
            {
                graph_order_fill_asset_id(record, settings=settings)
                for record in order_fills_page.get("records", [])
                if isinstance(record, Mapping)
                and graph_order_fill_asset_id(record, settings=settings)
            }
        ),
        settings=settings,
    )
    trade_records = convert_order_fills_to_trade_records(
        wallet=wallet,
        fills=order_fills_page.get("records", []),
        token_lookup_page=token_lookup_page,
        settings=settings,
    )
    return {
        "records": trade_records,
        "complete": bool(order_fills_page.get("complete", False))
        and bool(token_lookup_page.get("complete", False)),
        "order_fills_page": order_fills_page,
        "token_lookup_page": token_lookup_page,
    }


def merge_trades_page_with_history_provider(
    *,
    trades_page: Mapping[str, Any],
    history_provider: Mapping[str, Any],
    dedupe_records: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> dict[str, Any]:
    provider_records = [
        dict(record)
        for record in history_provider.get("trade_records", [])
        if isinstance(record, Mapping)
    ]
    if not provider_records:
        return dict(trades_page)
    merged_records = dedupe_records(
        [
            *(
                dict(record)
                for record in trades_page.get("records", [])
                if isinstance(record, Mapping)
            ),
            *provider_records,
        ]
    )
    status = history_provider.get("status", {}) if isinstance(history_provider, Mapping) else {}
    provider_complete = bool((status if isinstance(status, Mapping) else {}).get("trades_complete", False))
    provider_history_scope = str(
        (status if isinstance(status, Mapping) else {}).get("history_scope") or ""
    )
    return {
        **dict(trades_page),
        "records": merged_records,
        "complete": bool(trades_page.get("complete", True)) or provider_complete,
        "stop_reason": (
            "graphql_trade_history_complete"
            if provider_complete
            else str(trades_page.get("stop_reason") or "")
        ),
        "record_count": len(merged_records),
        "collection_mode": (
            "history_provider_merge"
            if list(trades_page.get("records", []))
            else "history_provider"
        ),
        "source_section": "trades",
        "history_scope": (
            provider_history_scope
            if provider_complete and provider_history_scope
            else (
                "full_history"
                if provider_complete
                else str(trades_page.get("history_scope") or "aggregate")
            )
        ),
        "provider_used": True,
        "provider_source": str((status if isinstance(status, Mapping) else {}).get("source") or ""),
        "provider_trade_count": len(provider_records),
        "range_start": (
            (status if isinstance(status, Mapping) else {}).get("range_start")
            if provider_complete and provider_history_scope == "screening_window"
            else trades_page.get("range_start")
        ),
        "range_end": (
            (status if isinstance(status, Mapping) else {}).get("range_end")
            if provider_complete and provider_history_scope == "screening_window"
            else trades_page.get("range_end")
        ),
    }


def skipped_provider_collection_page(section_name: str) -> dict[str, Any]:
    return {
        "records": [],
        "complete": True,
        "stop_reason": "not_requested",
        "page_count": 0,
        "record_count": 0,
        "last_offset": 0,
        "next_offset": 0,
        "collection_mode": "skipped",
        "source_section": section_name,
        "history_scope": "full_history",
    }
