from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping
from urllib.error import HTTPError

from .client import PolymarketClient, resolve_api_key
from .labels import CORE_LABEL_KEYS, build_strategy_notes, evaluate_label_evaluations, evaluate_labels
from .metrics import (
    DEFAULT_REGION_FIELDS,
    audit_profit_summary as summarize_audit_profit,
    cost_basis_distribution as summarize_cost_basis_distribution,
    first_number,
    get_field_value,
    high_temperature_early_entry_summary as summarize_high_temperature_early_entry,
    low_chip_cost_summary as summarize_low_chip_cost,
    liquidity_player_summary as summarize_liquidity_player,
    normalize_chip_cost,
    parse_datetime_value as parse_metric_datetime,
    profit_multiple as summarize_profit_multiple,
    profile_summary as summarize_profile,
    recent_activity_summary as summarize_recent_activity,
    regional_day_win_rate_summary as summarize_regional_day_win_rate,
    regional_daily_profit_summary as summarize_regional_daily_profit,
    record_market_date as metric_record_market_date,
    regional_trade_summary as summarize_regional_trades,
    trade_frequency_summary as summarize_trade_frequency,
    wallet_age_summary as summarize_wallet_age,
    win_rate_summary as summarize_win_rate,
)
from .report import build_report


UTC = timezone.utc
DEFAULT_NEG_RISK_ADAPTER_ADDRESS = "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"
POSITIONS_CONVERTED_TOPIC0 = "0xb03d19dddbc72a87e735ff0ea3b57bef133ebe44e1894284916a84044deb367e"
OPERATION_KEYS = ("convert", "split", "redeem", "swap")
HISTORY_REGISTRY_DIRNAME = "_wallet_registry"
HISTORY_ALREADY_FETCHED_REASON = "历史已抓取过，已默认排除"
HISTORY_REGISTRY_LOCK = Lock()


@dataclass
class WeatherIndex:
    event_ids: set[str]
    event_slugs: set[str]
    condition_ids: set[str]
    market_slugs: set[str]
    regions_by_key: dict[str, str]
    market_dates_by_key: dict[str, str] = field(default_factory=dict)


def run_pipeline(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    wallets_dir = output_dir / "wallets"
    wallets_dir.mkdir(parents=True, exist_ok=True)
    history_registry_dir = wallet_history_registry_dir(output_dir)
    history_run_id = resolve_history_run_id(config, output_dir)

    write_json(output_dir / "resolved_config.json", config)

    client = PolymarketClient(config["api"])
    errors: list[dict[str, Any]] = []
    leaderboard_settings = config["leaderboard"]
    target_count = int(config["wallet_filter"]["target_count"])
    concurrent_wallets = max(1, int(config["analysis"].get("concurrent_wallets", 1)))
    leaderboard_page_size = max(1, int(leaderboard_settings["page_size"]))
    auto_extend_leaderboard = bool(leaderboard_settings.get("auto_extend_to_target", True))
    raw_max_leaderboard_rows = leaderboard_settings.get("max_fetch_limit")
    max_leaderboard_rows = (
        None if raw_max_leaderboard_rows in (None, "") else max(0, int(raw_max_leaderboard_rows))
    )

    progress(config, "Fetching leaderboard")
    leaderboard = fetch_leaderboard(client, config)
    progress(config, f"Fetched {len(leaderboard)} leaderboard rows")

    screening_records: list[dict[str, Any]] = []
    selected_wallets: list[dict[str, Any]] = []
    wallet_results: list[dict[str, Any]] = []
    leaderboard_entries = [
        entry for entry in leaderboard if str(entry.get("proxyWallet", "")).strip()
    ]
    candidate_entries, prefiltered_records = split_leaderboard_prefilter_candidates(
        leaderboard_entries,
        config,
        history_registry_dir=history_registry_dir,
    )
    screening_records.extend(prefiltered_records)
    progress(
        config,
        f"Leaderboard prefilter kept {len(candidate_entries)} of {len(leaderboard_entries)} candidates",
    )

    weather_events: list[dict[str, Any]] = []
    weather_index = WeatherIndex(set(), set(), set(), set(), {})
    weather_index_ready = False
    write_json(output_dir / "weather_events.json", weather_events)

    processed_entries = 0
    next_leaderboard_offset = len(leaderboard)

    while True:
        if len(selected_wallets) >= target_count:
            break
        if processed_entries >= len(candidate_entries):
            if not auto_extend_leaderboard:
                break
            if max_leaderboard_rows is not None and next_leaderboard_offset >= max_leaderboard_rows:
                progress(
                    config,
                    f"Stopped extending leaderboard at configured cap {max_leaderboard_rows}",
                )
                break

            additional_limit = leaderboard_page_size
            if max_leaderboard_rows is not None:
                additional_limit = min(additional_limit, max_leaderboard_rows - next_leaderboard_offset)
            if additional_limit <= 0:
                break

            extra_rows = fetch_leaderboard(
                client,
                config,
                offset=next_leaderboard_offset,
                fetch_limit=additional_limit,
            )
            if not extra_rows:
                break

            leaderboard.extend(extra_rows)
            extra_entries = [
                entry for entry in extra_rows if str(entry.get("proxyWallet", "")).strip()
            ]
            extra_candidates, extra_prefiltered = split_leaderboard_prefilter_candidates(
                extra_entries,
                config,
                history_registry_dir=history_registry_dir,
            )
            candidate_entries.extend(extra_candidates)
            screening_records.extend(extra_prefiltered)
            next_leaderboard_offset += len(extra_rows)
            progress(
                config,
                f"Extended leaderboard to {len(leaderboard)} rows; {len(candidate_entries)} candidates remain under consideration",
            )
            continue

        if candidate_entries and not weather_index_ready:
            progress(config, "Fetching weather events")
            weather_events = fetch_weather_events(client, config)
            write_json(output_dir / "weather_events.json", weather_events)
            weather_index = build_weather_index(weather_events)
            weather_index_ready = True
            progress(config, f"Indexed {len(weather_events)} weather events")

        batch = candidate_entries[processed_entries : processed_entries + concurrent_wallets]
        progress(
            config,
            f"Analyzing wallets {processed_entries + 1}-{processed_entries + len(batch)} of {len(candidate_entries)}",
        )
        for result in analyze_wallet_batch(
            client=client,
            leaderboard_entries=batch,
            weather_index=weather_index,
            config=config,
            max_workers=concurrent_wallets,
            history_registry_dir=history_registry_dir,
            history_run_id=history_run_id,
        ):
            wallet = result["wallet"]
            if result.get("error"):
                errors.append({"wallet": wallet, "error": result["error"]})
                continue
            if result.get("screening"):
                screening_records.append(result["screening"])
                continue

            wallet_result = result["wallet_result"]
            screening_records.append(wallet_result["screening"])
            if wallet_result["screening"]["selected"]:
                wallet_results.append(wallet_result)
                selected_wallets.append(wallet_result["selection_record"])
                write_json(wallets_dir / f"{wallet}.json", wallet_result)
                if len(selected_wallets) >= target_count:
                    break
        processed_entries += len(batch)

    write_json(output_dir / "leaderboard.json", leaderboard)
    write_json(output_dir / "screening_records.json", screening_records)
    write_json(output_dir / "selected_wallets.json", selected_wallets)
    write_json(output_dir / "errors.json", errors)
    analysis_summary = build_analysis_summary(
        leaderboard=leaderboard,
        weather_events=weather_events,
        screening_records=screening_records,
        wallet_results=wallet_results,
        errors=errors,
    )
    analysis_summary_path = output_dir / "analysis_summary.json"
    write_json(analysis_summary_path, analysis_summary)

    report_path = output_dir / "report.txt"
    report_path.write_text(
        build_report(
            config=config,
            leaderboard=leaderboard,
            weather_events=weather_events,
            wallet_results=wallet_results,
            errors=errors,
        ),
        encoding="utf-8",
    )
    return {
        "report_path": str(report_path),
        "analysis_summary_path": str(analysis_summary_path),
        "selected_wallet_count": len(selected_wallets),
        "errors": errors,
    }


def fetch_leaderboard(
    client: PolymarketClient,
    config: dict[str, Any],
    *,
    offset: int = 0,
    fetch_limit: int | None = None,
) -> list[dict[str, Any]]:
    settings = config["leaderboard"]
    requested_limit = int(fetch_limit if fetch_limit is not None else settings["fetch_limit"])
    raw_max_fetch_limit = settings.get("max_fetch_limit")
    if raw_max_fetch_limit not in (None, ""):
        remaining_limit = max(0, int(raw_max_fetch_limit) - offset)
        requested_limit = min(requested_limit, remaining_limit)
    fetch_limit = requested_limit
    page_size = int(settings["page_size"])
    if fetch_limit <= 0:
        return []

    records: list[dict[str, Any]] = []
    current_offset = offset
    while len(records) < fetch_limit:
        limit = min(page_size, fetch_limit - len(records))
        page = client.fetch_leaderboard_page(
            category=str(settings["category"]),
            time_period=str(settings["time_period"]),
            order_by=str(settings["order_by"]),
            limit=limit,
            offset=current_offset,
        )
        if not page:
            break
        records.extend(page)
        if len(page) < limit:
            break
        current_offset += limit
    return records


def fetch_weather_events(client: PolymarketClient, config: dict[str, Any]) -> list[dict[str, Any]]:
    weather = config["weather"]
    pagination = config["pagination"]
    active = True if weather.get("active_only") else None
    closed = True if weather.get("closed_only") else None
    archived = None if weather.get("include_archived") else False
    tag_id = weather.get("tag_id")
    tag_slug = weather.get("tag_slug")

    if weather.get("use_keyset", True):
        return fetch_weather_events_keyset(
            client=client,
            page_size=int(weather["page_size"]),
            max_events=int(weather.get("max_events", weather["page_size"])),
            order=str(weather.get("order", "createdAt")),
            ascending=bool(weather.get("ascending", False)),
            tag_id=tag_id,
            tag_slug=tag_slug,
            active=active,
            closed=closed,
            archived=archived,
        )

    return paginate(
        page_size=int(weather["page_size"]),
        max_offset=int(pagination["max_offset"]),
        fetch_page=lambda limit, offset: client.fetch_events_page(
            limit=limit,
            offset=offset,
            tag_id=tag_id,
            tag_slug=tag_slug,
            active=active,
            closed=closed,
            archived=archived,
        ),
    )


def fetch_weather_events_keyset(
    *,
    client: PolymarketClient,
    page_size: int,
    max_events: int,
    order: str,
    ascending: bool,
    tag_id: int | str | None,
    tag_slug: str | None,
    active: bool | None,
    closed: bool | None,
    archived: bool | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor: str | None = None

    while len(events) < max_events:
        limit = min(page_size, max_events - len(events))
        payload = client.fetch_events_keyset_page(
            limit=limit,
            after_cursor=cursor,
            order=order,
            ascending=ascending,
            tag_id=tag_id,
            tag_slug=tag_slug,
            active=active,
            closed=closed,
            archived=archived,
        )
        page = payload.get("events", [])
        if not page:
            break
        events.extend(page[: max_events - len(events)])
        cursor = payload.get("next_cursor")
        if not cursor or len(page) < limit:
            break
    return events


def fetch_wallet_snapshot(
    client: PolymarketClient,
    wallet: str,
    config: dict[str, Any],
    *,
    prefetched_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pagination = config["pagination"]
    page_size = int(pagination["page_size"])
    max_offset = int(pagination["max_offset"])
    size_threshold = float(config["analysis"].get("position_size_threshold", 0.1))

    activity_page = paginate_with_status(
        page_size=page_size,
        max_offset=max_offset,
        fetch_page=lambda limit, offset: client.fetch_activity_page(
            user=wallet,
            limit=limit,
            offset=offset,
        ),
    )
    positions_page = paginate_with_status(
        page_size=page_size,
        max_offset=max_offset,
        fetch_page=lambda limit, offset: client.fetch_positions_page(
            user=wallet,
            limit=limit,
            offset=offset,
            size_threshold=size_threshold,
        ),
    )
    closed_positions_page = paginate_with_status(
        page_size=page_size,
        max_offset=max_offset,
        fetch_page=lambda limit, offset: client.fetch_closed_positions_page(
            user=wallet,
            limit=limit,
            offset=offset,
        ),
    )

    if prefetched_trades is not None:
        trades_page = {
            "records": list(prefetched_trades),
            "complete": True,
            "stop_reason": "prefetched_complete",
            "page_count": 1 if prefetched_trades else 0,
            "record_count": len(prefetched_trades),
            "last_offset": 0,
            "next_offset": len(prefetched_trades),
        }
    else:
        trades_page = paginate_with_status(
            page_size=page_size,
            max_offset=max_offset,
            fetch_page=lambda limit, offset: client.fetch_trades_page(
                user=wallet,
                limit=limit,
                offset=offset,
            ),
        )

    activity = activity_page["records"]
    positions = positions_page["records"]
    closed_positions = closed_positions_page["records"]
    trades = trades_page["records"]
    rewards = [
        record
        for record in activity
        if str(record.get("type", "")).upper() in {"REWARD", "YIELD"}
    ]
    chain_validation = fetch_optional_chain_validation(client, wallet, config)
    collection_status = {
        "activity": activity_page,
        "trades": trades_page,
        "positions": {
            **positions_page,
            "size_threshold": size_threshold,
        },
        "closed_positions": closed_positions_page,
    }
    operation_audit = build_operation_audit(
        wallet=wallet,
        trades=trades,
        activity=activity,
        closed_positions=closed_positions,
        chain_validation=chain_validation,
        collection_status=collection_status,
    )

    return {
        "wallet": wallet,
        "activity": activity,
        "trades": trades,
        "rewards": rewards,
        "positions": positions,
        "closed_positions": closed_positions,
        "chain_validation": chain_validation,
        "collection_status": collection_status,
        "operation_audit": operation_audit,
    }


def fetch_optional_chain_validation(
    client: PolymarketClient,
    wallet: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    settings = config.get("chain_validation", {})
    if not settings.get("enabled", False):
        return empty_chain_validation(status="disabled", reason="chain validation disabled")

    api_key = resolve_api_key(settings)
    if not api_key:
        return empty_chain_validation(status="missing_api_key", reason="missing Polygonscan API key")

    contract_address = normalize_address(
        settings.get("neg_risk_adapter", DEFAULT_NEG_RISK_ADAPTER_ADDRESS)
    )
    configured_topic0 = str(
        settings.get("positions_converted_topic0", POSITIONS_CONVERTED_TOPIC0)
    ).lower()
    try:
        logs_page = fetch_polygon_logs_paginated(
            client=client,
            api_key=api_key,
            contract_address=contract_address,
            topic0=configured_topic0,
            topic1=address_to_topic(wallet),
            base_url=str(settings.get("provider_base_url", "https://api.etherscan.io")),
            chain_id=settings.get("chain_id", 137),
            from_block=int(settings.get("from_block", 0)),
            to_block=int(settings.get("to_block", 99999999)),
            offset=int(settings.get("offset", 1000)),
            start_page=int(settings.get("page", 1)),
            max_pages=int(settings.get("max_pages", 10)),
        )
        transaction_page = fetch_polygon_transactions_paginated(
            client=client,
            address=wallet,
            api_key=api_key,
            base_url=str(settings.get("provider_base_url", "https://api.etherscan.io")),
            chain_id=settings.get("chain_id", 137),
            start_block=0,
            end_block=int(settings.get("to_block", 99999999)),
            offset=int(settings.get("transaction_offset", settings.get("offset", 1000))),
            start_page=1,
            sort="asc",
            max_pages=int(settings.get("transaction_max_pages", 1)),
        )
    except Exception as exc:
        return empty_chain_validation(status="request_failed", reason=str(exc))

    logs = logs_page["records"]
    transactions = transaction_page["records"]
    evidence = normalize_positions_converted_logs(
        logs,
        wallet,
        contract_address,
        expected_topic0=configured_topic0,
    )
    convert_operation = build_chain_operation_bucket(
        "convert",
        evidence,
        logs_complete=bool(logs_page.get("complete", True)),
        source="polygon_logs",
    )
    operations = {
        "convert": convert_operation,
        "split": build_chain_operation_bucket(
            "split",
            [],
            logs_complete=bool(logs_page.get("complete", True)),
            source="polygon_logs",
        ),
        "redeem": build_chain_operation_bucket(
            "redeem",
            [],
            logs_complete=bool(logs_page.get("complete", True)),
            source="polygon_logs",
        ),
        "swap": build_chain_operation_bucket(
            "swap",
            [],
            logs_complete=bool(logs_page.get("complete", True)),
            source="polygon_logs",
        ),
    }
    status = convert_operation["status"] if evidence else "no_split_evidence"
    reason = (
        "positions converted logs found"
        if evidence and logs_page.get("complete", True)
        else "positions converted logs found in a truncated log window"
        if evidence
        else "no matching PositionsConverted logs"
    )
    first_tx = transactions[0] if transactions else {}
    first_timestamp = first_tx.get("timeStamp")
    first_datetime = epoch_to_datetime(first_timestamp)
    return {
        "status": status,
        "reason": reason,
        "wallet": wallet,
        "first_transaction_timestamp": to_float(first_timestamp),
        "first_transaction_datetime": first_datetime.isoformat() if first_datetime else None,
        "first_transaction_hash": first_tx.get("hash", ""),
        "neg_risk_adapter": contract_address,
        "positions_converted_topic0": configured_topic0,
        "split_evidence_count": len(evidence),
        "evidence": evidence,
        "logs_complete": bool(logs_page.get("complete", True)),
        "logs_stop_reason": logs_page.get("stop_reason", ""),
        "logs_page_count": int(logs_page.get("page_count", 0) or 0),
        "transaction_history_complete": bool(transaction_page.get("complete", True)),
        "transaction_history_stop_reason": transaction_page.get("stop_reason", ""),
        "transaction_count": len(transactions),
        "operations": operations,
        "summary": {
            "verified_operation_count": sum(
                1 for item in operations.values() if item.get("status") == "verified"
            ),
            "matched_operation_count": sum(
                1 for item in operations.values() if int(item.get("count", 0) or 0) > 0
            ),
            "log_count": len(logs),
            "transaction_count": len(transactions),
            "sources": ["polygon_logs", "polygon_transactions"],
        },
    }


def fetch_polygon_logs_paginated(
    *,
    client: PolymarketClient,
    api_key: str,
    contract_address: str,
    topic0: str | None = None,
    topic1: str | None = None,
    base_url: str,
    chain_id: int | str,
    from_block: int,
    to_block: int,
    offset: int,
    start_page: int,
    max_pages: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    page = max(1, start_page)
    page_count = 0
    complete = True
    stop_reason = "empty_page"
    while page_count < max(1, max_pages):
        chunk = client.fetch_polygon_logs(
            api_key=api_key,
            contract_address=contract_address,
            topic0=topic0,
            topic1=topic1,
            base_url=base_url,
            chain_id=chain_id,
            from_block=from_block,
            to_block=to_block,
            page=page,
            offset=offset,
        )
        page_count += 1
        if not chunk:
            stop_reason = "empty_page"
            break
        records.extend(chunk)
        if len(chunk) < offset:
            stop_reason = "last_page_partial"
            break
        page += 1
    else:
        complete = False
        stop_reason = "max_pages_reached"

    return {
        "records": records,
        "complete": complete,
        "stop_reason": stop_reason,
        "page_count": page_count,
    }


def fetch_polygon_transactions_paginated(
    *,
    client: PolymarketClient,
    address: str,
    api_key: str,
    base_url: str,
    chain_id: int | str,
    start_block: int,
    end_block: int,
    offset: int,
    start_page: int,
    sort: str,
    max_pages: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    page = max(1, start_page)
    page_count = 0
    complete = True
    stop_reason = "empty_page"
    while page_count < max(1, max_pages):
        chunk = client.fetch_polygon_transactions(
            address=address,
            api_key=api_key,
            base_url=base_url,
            chain_id=chain_id,
            start_block=start_block,
            end_block=end_block,
            page=page,
            offset=offset,
            sort=sort,
        )
        page_count += 1
        if not chunk:
            stop_reason = "empty_page"
            break
        records.extend(chunk)
        if len(chunk) < offset:
            stop_reason = "last_page_partial"
            break
        page += 1
    else:
        complete = False
        stop_reason = "max_pages_reached"

    return {
        "records": records,
        "complete": complete,
        "stop_reason": stop_reason,
        "page_count": page_count,
    }


def build_chain_operation_bucket(
    key: str,
    evidence: list[dict[str, Any]],
    *,
    logs_complete: bool,
    source: str,
) -> dict[str, Any]:
    count = len(evidence)
    if count and logs_complete:
        status = "verified"
        reason = f"{key} evidence verified from {source}"
    elif count:
        status = "partial"
        reason = f"{key} evidence found from {source}, but log pagination was truncated"
    else:
        status = "not_found"
        reason = f"no {key} evidence found from {source}"
    return {
        "operation": key,
        "status": status,
        "reason": reason,
        "count": count,
        "verified_count": count if status == "verified" else 0,
        "partial_count": count if status == "partial" else 0,
        "complete": logs_complete,
        "source": source,
        "evidence": evidence,
    }


def fetch_first_polygon_transaction(
    client: PolymarketClient,
    wallet: str,
    api_key: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    if settings.get("fetch_first_transaction", True) is False:
        return {}
    records = client.fetch_polygon_transactions(
        address=wallet,
        api_key=api_key,
        base_url=str(settings.get("provider_base_url", "https://api.etherscan.io")),
        chain_id=settings.get("chain_id", 137),
        start_block=0,
        end_block=int(settings.get("to_block", 99999999)),
        page=1,
        offset=1,
        sort="asc",
    )
    return records[0] if records else {}


def empty_chain_validation(*, status: str, reason: str) -> dict[str, Any]:
    operations = {
        key: {
            "operation": key,
            "status": "not_found",
            "reason": "chain validation unavailable",
            "count": 0,
            "verified_count": 0,
            "partial_count": 0,
            "complete": True,
            "source": "polygon_logs",
            "evidence": [],
        }
        for key in OPERATION_KEYS
    }
    return {
        "status": status,
        "reason": reason,
        "wallet": "",
        "first_transaction_timestamp": 0.0,
        "first_transaction_datetime": None,
        "first_transaction_hash": "",
        "neg_risk_adapter": "",
        "positions_converted_topic0": POSITIONS_CONVERTED_TOPIC0,
        "split_evidence_count": 0,
        "evidence": [],
        "logs_complete": True,
        "logs_stop_reason": "",
        "logs_page_count": 0,
        "transaction_history_complete": True,
        "transaction_history_stop_reason": "",
        "transaction_count": 0,
        "operations": operations,
        "summary": {
            "verified_operation_count": 0,
            "matched_operation_count": 0,
            "log_count": 0,
            "transaction_count": 0,
            "sources": [],
        },
    }


def normalize_positions_converted_logs(
    logs: list[dict[str, Any]],
    wallet: str,
    contract_address: str,
    *,
    expected_topic0: str = POSITIONS_CONVERTED_TOPIC0,
) -> list[dict[str, Any]]:
    expected_topic1 = address_to_topic(wallet)
    expected_contract = normalize_address(contract_address)
    expected_topic0 = str(expected_topic0 or POSITIONS_CONVERTED_TOPIC0).lower()
    evidence: list[dict[str, Any]] = []

    for log in logs:
        address = normalize_address(log.get("address", ""))
        if address and address != expected_contract:
            continue
        topics = log.get("topics", [])
        if not isinstance(topics, list) or len(topics) < 4:
            continue
        topic0 = str(topics[0]).lower()
        if topic0 != expected_topic0:
            continue
        if str(topics[1]).lower() != expected_topic1:
            continue
        evidence.append(
            {
                "operation": "convert",
                "audit_bucket": "final_settlement",
                "verification": "chain",
                "source": "chain_validation.convert",
                "transaction_hash": log.get("transactionHash", ""),
                "block_number": decode_int(log.get("blockNumber")),
                "timestamp": decode_int(log.get("timeStamp")),
                "date": (
                    epoch_to_datetime(log.get("timeStamp")).date().isoformat()
                    if epoch_to_datetime(log.get("timeStamp")) is not None
                    else ""
                ),
                "stakeholder": topic_to_address(str(topics[1])),
                "market_id": str(topics[2]),
                "index_set": decode_int(topics[3]),
                "amount": decode_int(log.get("data")),
                "log_index": decode_int(log.get("logIndex")),
                "text": f"链上 convert 证据 {log.get('transactionHash', '') or '-'}",
            }
        )
    return evidence


def address_to_topic(address: str) -> str:
    normalized = normalize_address(address).removeprefix("0x")
    if len(normalized) != 40:
        return ""
    return "0x" + normalized.rjust(64, "0")


def topic_to_address(topic: str) -> str:
    text = str(topic).lower().removeprefix("0x")
    if len(text) < 40:
        return ""
    return "0x" + text[-40:]


def normalize_address(value: Any) -> str:
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


def wallet_history_registry_dir(output_dir: Path) -> Path:
    return output_dir.parent / HISTORY_REGISTRY_DIRNAME


def resolve_history_run_id(config: dict[str, Any], output_dir: Path) -> str:
    runtime = config.get("runtime", {})
    run_id = str(runtime.get("run_id") or output_dir.name).strip()
    return run_id or output_dir.name


def wallet_history_record_path(history_registry_dir: Path | None, wallet: str) -> Path | None:
    normalized_wallet = normalize_address(wallet)
    if history_registry_dir is None or not normalized_wallet:
        return None
    return history_registry_dir / f"{normalized_wallet}.json"


def wallet_is_in_history_registry(history_registry_dir: Path | None, wallet: str) -> bool:
    record_path = wallet_history_record_path(history_registry_dir, wallet)
    return bool(record_path and record_path.exists())


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_wallet_history_record(
    *,
    history_registry_dir: Path | None,
    wallet: str,
    leaderboard_entry: dict[str, Any],
    run_id: str,
    status: str,
) -> dict[str, Any] | None:
    record_path = wallet_history_record_path(history_registry_dir, wallet)
    normalized_wallet = normalize_address(wallet)
    if record_path is None or not normalized_wallet:
        return None

    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    with HISTORY_REGISTRY_LOCK:
        existing = read_json_file(record_path) if record_path.exists() else {}
        last_run_id = str(existing.get("last_run_id") or "").strip()
        run_count = decode_int(existing.get("run_count"))
        if last_run_id != run_id:
            run_count += 1

        record = {
            "wallet_address": normalized_wallet,
            "user_name": str(
                leaderboard_entry.get("userName") or existing.get("user_name") or ""
            ),
            "x_username": str(
                leaderboard_entry.get("xUsername") or existing.get("x_username") or ""
            ),
            "first_seen_at": str(existing.get("first_seen_at") or timestamp),
            "last_seen_at": timestamp,
            "run_count": run_count,
            "last_run_id": run_id,
            "last_status": status,
        }
        write_json(record_path, record)
    return record


def split_leaderboard_prefilter_candidates(
    leaderboard_entries: list[dict[str, Any]],
    config: dict[str, Any],
    history_registry_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    screening_records: list[dict[str, Any]] = []
    for entry in leaderboard_entries:
        wallet = normalize_address(entry.get("proxyWallet", ""))
        screening = build_leaderboard_prefilter_record(
            wallet,
            entry,
            config,
            history_registry_dir=history_registry_dir,
        )
        if screening is None:
            candidates.append(entry)
        else:
            screening_records.append(screening)
    return candidates, screening_records


def build_leaderboard_prefilter_record(
    wallet: str,
    leaderboard_entry: dict[str, Any],
    config: dict[str, Any],
    *,
    history_registry_dir: Path | None = None,
) -> dict[str, Any] | None:
    filter_config = config["wallet_filter"]
    include_wallets = {
        normalize_address(item) for item in filter_config.get("include_wallets", [])
    }
    exclude_wallets = {
        normalize_address(item) for item in filter_config.get("exclude_wallets", [])
    }

    if wallet in exclude_wallets:
        return prefilter_screening_record(
            wallet,
            leaderboard_entry,
            reasons=["wallet in exclude list"],
            stage="leaderboard",
        )
    if wallet in include_wallets:
        return None
    if wallet_is_in_history_registry(history_registry_dir, wallet):
        return prefilter_screening_record(
            wallet,
            leaderboard_entry,
            reasons=[HISTORY_ALREADY_FETCHED_REASON],
            stage="leaderboard",
        )

    checks = [
        (
            to_float(leaderboard_entry.get("pnl")) >= to_float(filter_config.get("min_pnl")),
            f"pnl>={filter_config.get('min_pnl')}",
        ),
        (
            to_float(leaderboard_entry.get("vol")) >= to_float(filter_config.get("min_volume")),
            f"volume>={filter_config.get('min_volume')}",
        ),
    ]
    if filter_config.get("max_pnl") is not None:
        checks.append(
            (
                to_float(leaderboard_entry.get("pnl")) <= to_float(filter_config.get("max_pnl")),
                f"pnl<={filter_config.get('max_pnl')}",
            )
        )
    if filter_config.get("max_volume") is not None:
        checks.append(
            (
                to_float(leaderboard_entry.get("vol")) <= to_float(filter_config.get("max_volume")),
                f"volume<={filter_config.get('max_volume')}",
            )
        )

    failed = [label for ok, label in checks if not ok]
    if not failed:
        return None
    return prefilter_screening_record(
        wallet,
        leaderboard_entry,
        reasons=[f"failed:{label}" for label in failed],
        stage="leaderboard",
    )


def probe_wallet_trade_window(
    client: PolymarketClient,
    wallet: str,
    leaderboard_entry: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    filter_config = config["wallet_filter"]
    include_wallets = {
        normalize_address(item) for item in filter_config.get("include_wallets", [])
    }
    if wallet in include_wallets:
        return {"prefetched_trades": None, "trade_probe_fetched": False}

    min_traded_count = int(filter_config.get("min_traded_count", 0) or 0)
    raw_max_traded_count = filter_config.get("max_traded_count")
    if raw_max_traded_count in (None, "") and min_traded_count <= 0:
        return {"prefetched_trades": None, "trade_probe_fetched": False}

    page_size = max(1, int(config["pagination"]["page_size"]))
    max_traded_count = None if raw_max_traded_count in (None, "") else int(raw_max_traded_count)
    probe_limit = page_size if max_traded_count is None else min(page_size, max_traded_count + 1)
    trades = client.fetch_trades_page(user=wallet, limit=probe_limit, offset=0)
    trade_count = len(trades)

    if max_traded_count is not None and trade_count > max_traded_count:
        return {
            "screening": prefilter_screening_record(
                wallet,
                leaderboard_entry,
                reasons=[f"failed:trade_count<={max_traded_count}"],
                stage="trade_probe",
                trade_count=trade_count,
            ),
            "trade_probe_fetched": True,
        }

    is_complete = probe_limit < page_size or trade_count < probe_limit
    if is_complete and trade_count < min_traded_count:
        return {
            "screening": prefilter_screening_record(
                wallet,
                leaderboard_entry,
                reasons=[f"failed:trade_count>={min_traded_count}"],
                stage="trade_probe",
                trade_count=trade_count,
            ),
            "trade_probe_fetched": True,
        }

    return {
        "prefetched_trades": trades if is_complete else None,
        "trade_probe_fetched": True,
    }


def prefilter_screening_record(
    wallet: str,
    leaderboard_entry: dict[str, Any],
    *,
    reasons: list[str],
    stage: str,
    trade_count: int | None = None,
) -> dict[str, Any]:
    return {
        "wallet": wallet,
        "rank": leaderboard_entry.get("rank"),
        "user_name": leaderboard_entry.get("userName"),
        "x_username": leaderboard_entry.get("xUsername"),
        "pnl": to_float(leaderboard_entry.get("pnl")),
        "volume": to_float(leaderboard_entry.get("vol")),
        "trade_count": trade_count,
        "weather_trade_count": None,
        "weather_trade_ratio": None,
        "weather_notional_ratio": None,
        "selected": False,
        "reasons": reasons,
        "prefilter_stage": stage,
        "labels": [],
    }


def analyze_wallet_batch(
    *,
    client: PolymarketClient,
    leaderboard_entries: list[dict[str, Any]],
    weather_index: WeatherIndex,
    config: dict[str, Any],
    max_workers: int,
    history_registry_dir: Path | None = None,
    history_run_id: str = "",
) -> list[dict[str, Any]]:
    if max_workers <= 1 or len(leaderboard_entries) <= 1:
        return [
            analyze_leaderboard_entry(
                client=client,
                leaderboard_entry=entry,
                weather_index=weather_index,
                config=config,
                history_registry_dir=history_registry_dir,
                history_run_id=history_run_id,
            )
            for entry in leaderboard_entries
        ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(
            executor.map(
                lambda entry: analyze_leaderboard_entry(
                    client=client,
                    leaderboard_entry=entry,
                    weather_index=weather_index,
                    config=config,
                    history_registry_dir=history_registry_dir,
                    history_run_id=history_run_id,
                ),
                leaderboard_entries,
            )
        )


def analyze_leaderboard_entry(
    *,
    client: PolymarketClient,
    leaderboard_entry: dict[str, Any],
    weather_index: WeatherIndex,
    config: dict[str, Any],
    history_registry_dir: Path | None = None,
    history_run_id: str = "",
) -> dict[str, Any]:
    wallet = normalize_address(leaderboard_entry.get("proxyWallet", ""))
    trade_probe_fetched = False
    snapshot_fetched = False
    try:
        trade_probe = probe_wallet_trade_window(client, wallet, leaderboard_entry, config)
        trade_probe_fetched = bool(trade_probe.get("trade_probe_fetched"))
        if trade_probe.get("screening"):
            if trade_probe_fetched:
                write_wallet_history_record(
                    history_registry_dir=history_registry_dir,
                    wallet=wallet,
                    leaderboard_entry=leaderboard_entry,
                    run_id=history_run_id,
                    status="trade_probe_screened_out",
                )
            return {"wallet": wallet, "screening": trade_probe["screening"]}

        snapshot = fetch_wallet_snapshot(
            client,
            wallet,
            config,
            prefetched_trades=trade_probe.get("prefetched_trades"),
        )
        snapshot_fetched = True
        wallet_result = analyze_wallet(
            wallet=wallet,
            leaderboard_entry=leaderboard_entry,
            snapshot=snapshot,
            weather_index=weather_index,
            config=config,
        )
        if trade_probe_fetched or snapshot_fetched:
            write_wallet_history_record(
                history_registry_dir=history_registry_dir,
                wallet=wallet,
                leaderboard_entry=leaderboard_entry,
                run_id=history_run_id,
                status="selected" if wallet_result["screening"]["selected"] else "screened_out",
            )
        return {"wallet": wallet, "wallet_result": wallet_result}
    except Exception as exc:
        if trade_probe_fetched or snapshot_fetched:
            write_wallet_history_record(
                history_registry_dir=history_registry_dir,
                wallet=wallet,
                leaderboard_entry=leaderboard_entry,
                run_id=history_run_id,
                status="analysis_error",
            )
        return {"wallet": wallet, "error": str(exc)}


def analyze_wallet(
    *,
    wallet: str,
    leaderboard_entry: dict[str, Any],
    snapshot: dict[str, Any],
    weather_index: WeatherIndex,
    config: dict[str, Any],
) -> dict[str, Any]:
    metrics = compute_metrics(
        snapshot=snapshot,
        leaderboard_entry=leaderboard_entry,
        weather_index=weather_index,
        config=config,
    )
    configured_label_rules = list(config.get("labels", []))
    label_evaluations = evaluate_label_evaluations(metrics, configured_label_rules)
    labels = merge_system_and_configured_labels(
        build_system_core_labels(label_evaluations),
        evaluate_labels(metrics, configured_label_rules),
    )
    recent_evidence_date = latest_label_evidence_date(label_evaluations, metrics)
    strategy_notes = build_strategy_notes(metrics, labels)
    label_evidence = build_label_evidence_records(labels)
    screening = build_screening_record(wallet, leaderboard_entry, metrics, config)
    profile = metrics["profile"]
    operation_audit = metrics.get("operation_audit", {})
    evidence_summary = build_evidence_summary(
        label_evaluations=label_evaluations,
        metrics=metrics,
        recent_evidence_date=recent_evidence_date,
    )
    return {
        "wallet": wallet,
        "leaderboard_entry": leaderboard_entry,
        "screening": screening,
        "selection_record": {
            "wallet": wallet,
            "rank": leaderboard_entry.get("rank"),
            "user_name": leaderboard_entry.get("userName"),
            "pnl": metrics["leaderboard_pnl"],
            "volume": metrics["leaderboard_volume"],
            "trade_count": metrics["trade_count"],
            "weather_trade_count": metrics["weather_trade_count"],
            "weather_trade_ratio": metrics["weather_trade_ratio"],
            "weather_notional_ratio": metrics["weather_notional_ratio"],
            "closed_position_win_rate": metrics["closed_position_win_rate"],
            "closed_profit_multiple": metrics["closed_profit_multiple"],
            "median_trade_notional": metrics["median_trade_notional"],
            "trades_per_active_day": metrics["trades_per_active_day"],
            "dominant_region": metrics["dominant_region"],
            "main_region": metrics["dominant_region"],
            "dominant_region_trade_ratio": metrics["dominant_region_trade_ratio"],
            "max_region_daily_profit_multiple": metrics["max_region_daily_profit_multiple"],
            "highest_burst": metrics["max_region_daily_profit_multiple"],
            "highest_burst_region": metrics["max_region_daily_profit_region"],
            "highest_burst_date": metrics["max_region_daily_profit_date"],
            "recent_evidence_date": recent_evidence_date,
            "best_region_win_rate_region": metrics["best_region_win_rate_region"],
            "best_region_positive_return_day_ratio": metrics[
                "best_region_positive_return_day_ratio"
            ],
            "best_region_trade_count": metrics["best_region_trade_count"],
            "low_chip_cost_trade_ratio": metrics["low_chip_cost_trade_ratio"],
            "liquidity_swap_ratio": metrics["liquidity_swap_ratio"],
            "liquidity_sell_dominant_region_day_ratio": metrics[
                "liquidity_sell_dominant_region_day_ratio"
            ],
            "activity_level": metrics["activity_level"],
            "latest_trade_date": metrics["latest_trade_date"],
            "days_since_latest_trade": metrics["days_since_latest_trade"],
            "wallet_registration_date": metrics["wallet_registration_date"],
            "wallet_age_days": metrics["wallet_age_days"],
            "wallet_registration_source": metrics["wallet_registration_source"],
            "high_temp_off_day_buy_ratio": metrics["high_temp_off_day_buy_ratio"],
            "split_avg_chip_cost": metrics["split_avg_chip_cost"],
            "split_evidence_count": metrics["split_evidence_count"],
            "split_player_validation_passed": metrics["split_player_validation_passed"],
            "trade_liquidity_profit": metrics["trade_liquidity_profit"],
            "final_settlement_profit": metrics["final_settlement_profit"],
            "unified_profit": metrics["unified_profit"],
            "audit_complete": metrics["snapshot_complete"],
            "labels": [label["display_name"] for label in labels],
            "selected": screening["selected"],
            "reasons": screening["reasons"],
        },
        "labels": labels,
        "label_evaluations": label_evaluations,
        "label_evidence": label_evidence,
        "label_match_details": label_evidence,
        "evidence_summary": evidence_summary,
        "profile": profile,
        "strategy_notes": strategy_notes,
        "metrics": metrics,
        "operation_audit": operation_audit,
        "top_trades": top_records(
            snapshot["trades"],
            limit=int(config["analysis"]["top_trades_in_report"]),
            sort_key=lambda item: record_notional(item),
        ),
        "top_positions": top_records(
            snapshot["positions"],
            limit=int(config["analysis"]["top_positions_in_report"]),
            sort_key=lambda item: to_float(item.get("currentValue")),
        ),
        "top_closed_positions": top_records(
            snapshot["closed_positions"],
            limit=int(config["analysis"]["top_closed_positions_in_report"]),
            sort_key=lambda item: to_float(item.get("realizedPnl")),
        ),
        "raw_counts": {
            "activity_count": len(snapshot["activity"]),
            "trade_count": len(snapshot["trades"]),
            "reward_count": len(snapshot["rewards"]),
            "position_count": len(snapshot["positions"]),
            "closed_position_count": len(snapshot["closed_positions"]),
            "operation_record_count": len(operation_audit.get("records", [])),
        },
    }


def build_label_evidence_records(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for label in labels:
        evidence = label.get("evidence")
        if not isinstance(evidence, dict):
            continue
        records.append(
            {
                "key": label.get("key"),
                "display_name": label.get("display_name"),
                "description": label.get("description"),
                **evidence,
            }
        )
    return records


def build_system_core_labels(
    label_evaluations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for evaluation in label_evaluations:
        if not evaluation.get("matched"):
            continue
        labels.append(
            {
                "key": evaluation.get("key"),
                "display_name": evaluation.get("display_name"),
                "description": evaluation.get("description"),
                "system_core": True,
                "evidence": {
                    "matched": True,
                    "reason": evaluation.get("reason") or "",
                    "details": evaluation.get("facts") or evaluation.get("details") or {},
                    "facts": evaluation.get("facts") or {},
                    "records": evaluation.get("records") or [],
                },
            }
        )
    return labels


def merge_system_and_configured_labels(
    system_labels: list[dict[str, Any]],
    configured_labels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for label in [*system_labels, *configured_labels]:
        key = str(label.get("key", "")).strip()
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        labels.append(label)
    return labels


def latest_label_evidence_date(
    label_evaluations: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> str:
    candidates: list[str] = []
    for evaluation in label_evaluations:
        facts = evaluation.get("facts")
        if isinstance(facts, Mapping):
            append_date_candidate(candidates, facts.get("date"))
        records = evaluation.get("records")
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                for field in ("date", "buy_date", "high_temperature_date"):
                    append_date_candidate(candidates, record.get(field))

    append_date_candidate(candidates, metrics.get("latest_trade_date"))
    dated = [
        (parsed, value)
        for value in candidates
        if (parsed := parse_datetime(value)) is not None
    ]
    if not dated:
        return ""
    return max(dated, key=lambda item: item[0])[0].date().isoformat()


def append_date_candidate(candidates: list[str], value: Any) -> None:
    if value in (None, ""):
        return
    text = str(value).strip()
    if text:
        candidates.append(text)


def build_evidence_summary(
    *,
    label_evaluations: list[dict[str, Any]],
    metrics: dict[str, Any],
    recent_evidence_date: str,
) -> dict[str, Any]:
    matched = [item for item in label_evaluations if item.get("matched")]
    lead = matched[0] if matched else (label_evaluations[0] if label_evaluations else {})
    return {
        "headline": str(lead.get("reason") or "后端尚未生成标签证据摘要。"),
        "matched_label_count": len(matched),
        "label_count": len(label_evaluations),
        "main_region": str(metrics.get("dominant_region") or ""),
        "highlight_multiple": metrics.get("max_region_daily_profit_multiple") or 0.0,
        "latest_evidence_date": recent_evidence_date,
        "audit_complete": bool(metrics.get("snapshot_complete")),
        "trade_liquidity_profit": metrics.get("trade_liquidity_profit") or 0.0,
        "final_settlement_profit": metrics.get("final_settlement_profit") or 0.0,
        "unified_profit": metrics.get("unified_profit") or 0.0,
    }


def compute_metrics(
    *,
    snapshot: dict[str, Any],
    leaderboard_entry: dict[str, Any],
    weather_index: WeatherIndex,
    config: dict[str, Any],
) -> dict[str, Any]:
    activity = snapshot["activity"]
    trades = snapshot["trades"]
    rewards = snapshot["rewards"]
    positions = snapshot["positions"]
    closed_positions = snapshot["closed_positions"]
    collection_status = snapshot.get("collection_status") or {}
    default_chain_status = "missing_snapshot"
    default_chain_reason = "chain validation snapshot missing"
    if not config.get("chain_validation", {}).get("enabled", False):
        default_chain_status = "disabled"
        default_chain_reason = "chain validation disabled"
    chain_validation = snapshot.get("chain_validation") or empty_chain_validation(
        status=default_chain_status,
        reason=default_chain_reason,
    )
    operation_audit = snapshot.get("operation_audit") or build_operation_audit(
        wallet=str(snapshot.get("wallet") or ""),
        trades=trades,
        activity=activity,
        closed_positions=closed_positions,
        chain_validation=chain_validation,
        collection_status=collection_status,
    )
    audit_profit_summary = operation_audit.get("profit_summary") or summarize_audit_profit(
        liquidity_records=trades,
        settlement_records=closed_positions,
    )
    snapshot_complete = bool(operation_audit.get("complete", True))

    trade_notionals = [record_notional(record) for record in trades]
    total_trade_notional = sum(trade_notionals)

    weather_trades = [record for record in trades if is_weather_record(record, weather_index)]
    weather_trade_notional = sum(record_notional(record) for record in weather_trades)

    distinct_events = {
        record_event_key(record)
        for record in trades
        if record_event_key(record)
    }

    event_notionals: defaultdict[str, float] = defaultdict(float)
    for trade in trades:
        event_key = record_event_key(trade)
        if not event_key:
            continue
        event_notionals[event_key] += record_notional(trade)

    active_days = {
        epoch_to_datetime(record.get("timestamp")).date().isoformat()
        for record in trades
        if epoch_to_datetime(record.get("timestamp")) is not None
    }

    holding_stats = estimate_holding_stats(trades)
    end_lookup = build_end_lookup(snapshot)
    time_to_end_hours = collect_time_to_end_hours(trades, end_lookup)

    now = resolve_analysis_now(config)
    long_dated_cutoff = now + timedelta(
        days=int(config["analysis"].get("long_dated_threshold_days", 90))
    )
    long_dated_positions = [
        position
        for position in positions
        if (end_dt := parse_datetime(position.get("endDate"))) is not None and end_dt >= long_dated_cutoff
    ]

    wins = sum(1 for position in closed_positions if to_float(position.get("realizedPnl")) > 0)
    losses = sum(1 for position in closed_positions if to_float(position.get("realizedPnl")) < 0)
    reward_total_usdc = sum(record_notional(record) for record in rewards)
    closed_realized_pnl = sum(
        to_float(position.get("realizedPnl")) for position in closed_positions
    )
    closed_total_bought = sum(
        to_float(position.get("totalBought")) for position in closed_positions
    )
    closed_profit_multiple = summarize_profit_multiple(
        closed_total_bought,
        profit=closed_realized_pnl,
    )
    buy_trades = [trade for trade in trades if str(trade.get("side", "")).upper() == "BUY"]
    sell_trades = [trade for trade in trades if str(trade.get("side", "")).upper() == "SELL"]
    cost_distribution = summarize_cost_basis_distribution(buy_trades)
    frequency_summary = summarize_trade_frequency(trades)
    win_rate_summary = summarize_win_rate(closed_positions)
    raw_region_fields = config.get("analysis", {}).get("region_fields", DEFAULT_REGION_FIELDS)
    if isinstance(raw_region_fields, str):
        configured_region_fields = (raw_region_fields,)
    else:
        configured_region_fields = tuple(str(field) for field in raw_region_fields)
    regional_trades = enrich_trades_with_regions(
        trades,
        weather_index=weather_index,
        region_fields=configured_region_fields,
    )
    weather_regional_trades = enrich_trades_with_regions(
        weather_trades,
        weather_index=weather_index,
        region_fields=configured_region_fields,
    )
    regional_closed_positions = enrich_trades_with_regions(
        closed_positions,
        weather_index=weather_index,
        region_fields=configured_region_fields,
    )
    metric_region_fields = ("_region", *configured_region_fields)
    profile = summarize_profile(
        regional_trades,
        regional_closed_positions,
        region_fields=metric_region_fields,
    )
    regional_trade_summary = summarize_regional_trades(
        weather_regional_trades,
        region_fields=metric_region_fields,
        collapse_by_day=True,
        dominance_threshold=float(
            config["analysis"].get("regional_frequency_min_day_ratio", 0.4)
        ),
    )
    regional_daily_profit_summary = summarize_regional_daily_profit(
        regional_trades,
        region_fields=metric_region_fields,
    )
    regional_day_win_rate_summary = summarize_regional_day_win_rate(
        regional_trades,
        region_fields=metric_region_fields,
        min_trade_count=int(config["analysis"].get("regional_win_rate_min_trade_count", 3)),
    )
    low_chip_cost_summary = summarize_low_chip_cost(
        regional_trades,
        region_fields=metric_region_fields,
    )
    liquidity_player_summary = summarize_liquidity_player(
        regional_trades,
        activity_records=activity,
        region_fields=metric_region_fields,
    )
    recent_activity_summary = summarize_recent_activity(
        trades,
        now=now,
        active_days=int(config["analysis"].get("recent_active_days", 3)),
        normal_active_days=int(config["analysis"].get("normal_active_days", 1)),
    )
    registration_datetime, registration_source = resolve_wallet_registration_datetime(
        snapshot=snapshot,
        leaderboard_entry=leaderboard_entry,
        chain_validation=chain_validation,
    )
    wallet_age_summary = summarize_wallet_age(
        registration_datetime,
        now=now,
        source=registration_source,
        new_wallet_days=int(config["analysis"].get("new_wallet_days", 60)),
        hidden_new_wallet_days=int(config["analysis"].get("hidden_new_wallet_days", 10)),
    )
    high_temperature_early_entry_summary = summarize_high_temperature_early_entry(
        regional_trades,
        region_fields=metric_region_fields,
    )
    chain_settings = config.get("chain_validation", {})
    split_cost_summary = split_position_average_cost_summary(
        positions,
        target=float(chain_settings.get("split_target_avg_chip_cost", 5.0)),
        tolerance=float(chain_settings.get("split_avg_chip_cost_tolerance", 0.5)),
    )
    split_evidence_count = int(chain_validation.get("split_evidence_count", 0))
    required_split_evidence_count = int(chain_settings.get("min_split_evidence_count", 2))
    split_chain_verified = (
        chain_validation.get("status") == "verified"
        and split_evidence_count >= required_split_evidence_count
    )
    split_player_validation_passed = (
        split_cost_summary["matched_split_avg_chip_cost"] and split_chain_verified
    )

    return {
        "leaderboard_pnl": to_float(leaderboard_entry.get("pnl")),
        "leaderboard_volume": to_float(leaderboard_entry.get("vol")),
        "trade_count": len(trades),
        "buy_trade_count": len(buy_trades),
        "sell_trade_count": len(sell_trades),
        "weather_trade_count": len(weather_trades),
        "weather_trade_ratio": ratio(len(weather_trades), len(trades)),
        "weather_notional": weather_trade_notional,
        "weather_notional_ratio": ratio(weather_trade_notional, total_trade_notional),
        "distinct_event_count": len(distinct_events),
        "largest_event_notional_ratio": (
            max(event_notionals.values()) / total_trade_notional if event_notionals and total_trade_notional else 0.0
        ),
        "active_day_count": len(active_days),
        "trades_per_active_day": ratio(len(trades), len(active_days)),
        "median_trade_notional": median(trade_notionals),
        "reward_activity_count": len(rewards),
        "reward_total_usdc": reward_total_usdc,
        "open_position_count": len(positions),
        "open_position_long_dated_ratio": ratio(len(long_dated_positions), len(positions)),
        "closed_position_count": len(closed_positions),
        "closed_position_win_rate": ratio(wins, len(closed_positions)),
        "closed_position_loss_rate": ratio(losses, len(closed_positions)),
        "winning_closed_position_count": wins,
        "losing_closed_position_count": losses,
        "profile": profile,
        "closed_total_bought": closed_total_bought,
        "profit_multiple": closed_profit_multiple,
        "closed_profit_multiple": closed_profit_multiple,
        "win_rate_summary": win_rate_summary,
        "cost_basis_distribution": cost_distribution,
        "trade_frequency": frequency_summary,
        "regional_trade_summary": regional_trade_summary,
        "dominant_region": regional_trade_summary["dominant_region"],
        "dominant_region_trade_count": regional_trade_summary["dominant_region_trade_count"],
        "dominant_region_trade_ratio": regional_trade_summary["dominant_region_trade_ratio"],
        "region_trade_ratio_spread": regional_trade_summary["region_trade_ratio_spread"],
        "is_balanced_without_dominant_region": regional_trade_summary[
            "is_balanced_without_dominant_region"
        ],
        "regional_daily_profit_summary": regional_daily_profit_summary,
        "max_region_daily_profit_region": regional_daily_profit_summary["max_region"],
        "max_region_daily_profit_date": regional_daily_profit_summary["max_date"],
        "max_region_daily_profit_multiple": regional_daily_profit_summary[
            "max_profit_multiple"
        ],
        "max_region_daily_profit_buy_amount": regional_daily_profit_summary["max_buy_amount"],
        "max_region_daily_profit_sell_amount": regional_daily_profit_summary["max_sell_amount"],
        "regional_day_win_rate_summary": regional_day_win_rate_summary,
        "best_region_win_rate_region": regional_day_win_rate_summary["best_region"],
        "best_region_positive_return_days": regional_day_win_rate_summary[
            "best_positive_return_days"
        ],
        "best_region_total_trade_days": regional_day_win_rate_summary["best_total_trade_days"],
        "best_region_positive_return_day_ratio": regional_day_win_rate_summary[
            "best_positive_return_day_ratio"
        ],
        "best_region_trade_count": regional_day_win_rate_summary["best_trade_count"],
        "low_chip_cost_summary": low_chip_cost_summary,
        "low_chip_cost_trade_count": low_chip_cost_summary["low_chip_cost_count"],
        "low_chip_cost_trade_ratio": low_chip_cost_summary["low_chip_cost_ratio"],
        "low_chip_cost_threshold": low_chip_cost_summary["threshold"],
        "top_low_chip_region": low_chip_cost_summary["top_low_chip_region"],
        "top_low_chip_region_count": low_chip_cost_summary["top_low_chip_region_count"],
        "top_low_chip_region_ratio": low_chip_cost_summary["top_low_chip_region_ratio"],
        "liquidity_player_summary": liquidity_player_summary,
        "liquidity_swap_count": liquidity_player_summary["swap_count"],
        "liquidity_swap_ratio": liquidity_player_summary["swap_ratio"],
        "liquidity_low_swap_activity": liquidity_player_summary["low_swap_activity"],
        "liquidity_regional_trade_day_count": liquidity_player_summary[
            "unique_trade_day_count"
        ],
        "liquidity_sell_dominant_region_day_count": liquidity_player_summary[
            "sell_dominant_region_day_count"
        ],
        "liquidity_sell_dominant_region_day_ratio": liquidity_player_summary[
            "sell_dominant_region_day_ratio"
        ],
        "liquidity_top_sell_dominant_region": liquidity_player_summary[
            "top_sell_dominant_region"
        ],
        "liquidity_top_sell_dominant_date": liquidity_player_summary[
            "top_sell_dominant_date"
        ],
        "liquidity_player_matched": liquidity_player_summary["matched_liquidity_player"],
        "recent_activity_summary": recent_activity_summary,
        "current_date": recent_activity_summary["current_date"],
        "latest_trade_datetime": recent_activity_summary["latest_trade_datetime"],
        "latest_trade_date": recent_activity_summary["latest_trade_date"],
        "days_since_latest_trade": recent_activity_summary["days_since_latest_trade"],
        "activity_level": recent_activity_summary["activity_level"],
        "matched_recent_active": recent_activity_summary["matched_recent_active"],
        "wallet_age_summary": wallet_age_summary,
        "wallet_registration_source": wallet_age_summary["source"],
        "wallet_registration_datetime": wallet_age_summary["registration_datetime"],
        "wallet_registration_date": wallet_age_summary["registration_date"],
        "wallet_age_days": wallet_age_summary["wallet_age_days"],
        "wallet_age_status": wallet_age_summary["status"],
        "new_wallet_days": wallet_age_summary["new_wallet_days"],
        "hidden_new_wallet_days": wallet_age_summary["hidden_new_wallet_days"],
        "new_wallet_matched": wallet_age_summary["matched_new_wallet"],
        "hidden_new_wallet_matched": wallet_age_summary["matched_hidden_new_wallet"],
        "snapshot_collection_status": collection_status,
        "snapshot_complete": snapshot_complete,
        "operation_audit": operation_audit,
        "audit_profit_summary": audit_profit_summary,
        "trade_liquidity_profit": audit_profit_summary["trade_liquidity_profit"],
        "trade_liquidity_profit_multiple": audit_profit_summary[
            "trade_liquidity_profit_multiple"
        ],
        "final_settlement_profit": audit_profit_summary["final_settlement_profit"],
        "final_settlement_profit_multiple": audit_profit_summary[
            "final_settlement_profit_multiple"
        ],
        "unified_profit": audit_profit_summary["unified_profit"],
        "unified_profit_multiple": audit_profit_summary["unified_profit_multiple"],
        "high_temperature_early_entry_summary": high_temperature_early_entry_summary,
        "high_temp_buy_count": high_temperature_early_entry_summary[
            "high_temperature_buy_count"
        ],
        "high_temp_analyzed_buy_count": high_temperature_early_entry_summary[
            "analyzed_buy_count"
        ],
        "high_temp_off_day_buy_count": high_temperature_early_entry_summary[
            "off_day_buy_count"
        ],
        "high_temp_off_day_buy_ratio": high_temperature_early_entry_summary[
            "off_day_buy_ratio"
        ],
        "high_temp_same_day_buy_count": high_temperature_early_entry_summary[
            "same_day_buy_count"
        ],
        "high_temp_missing_market_date_count": high_temperature_early_entry_summary[
            "missing_market_date_count"
        ],
        "high_temp_early_positioning_matched": high_temperature_early_entry_summary[
            "matched_early_positioning"
        ],
        "split_position_average_cost_summary": split_cost_summary,
        "split_avg_chip_cost": split_cost_summary["average_chip_cost"],
        "split_avg_chip_cost_target": split_cost_summary["target"],
        "split_avg_chip_cost_tolerance": split_cost_summary["tolerance"],
        "split_avg_chip_cost_matched": split_cost_summary["matched_split_avg_chip_cost"],
        "chain_validation": chain_validation,
        "chain_validation_enabled": bool(chain_settings.get("enabled", False)),
        "chain_validation_status": chain_validation.get("status", ""),
        "chain_validation_reason": chain_validation.get("reason", ""),
        "chain_first_transaction_datetime": chain_validation.get("first_transaction_datetime"),
        "split_evidence_count": split_evidence_count,
        "required_split_evidence_count": required_split_evidence_count,
        "split_chain_verified": split_chain_verified,
        "split_player_validation_passed": split_player_validation_passed,
        "holding_duration_coverage": ratio(
            holding_stats["matched_sell_count"], holding_stats["sell_count"]
        ),
        "median_holding_hours": median(holding_stats["holding_hours"]),
        "time_to_end_coverage": ratio(len(time_to_end_hours), len(trades)),
        "median_time_to_end_hours": median(time_to_end_hours),
        "total_trade_notional": total_trade_notional,
        "current_open_value": sum(to_float(position.get("currentValue")) for position in positions),
        "closed_realized_pnl": closed_realized_pnl,
    }


def build_analysis_summary(
    *,
    leaderboard: list[dict[str, Any]],
    weather_events: list[dict[str, Any]],
    screening_records: list[dict[str, Any]],
    wallet_results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics_list = [wallet["metrics"] for wallet in wallet_results]
    label_counts: Counter[str] = Counter()
    core_label_keys = set(CORE_LABEL_KEYS)
    wallets_core_labeled = 0
    for wallet in wallet_results:
        label_counts.update(str(label.get("display_name")) for label in wallet["labels"])
        evaluations = wallet.get("label_evaluations") or []
        if any(
            isinstance(item, Mapping)
            and str(item.get("key") or "") in core_label_keys
            and bool(item.get("matched"))
            for item in evaluations
        ):
            wallets_core_labeled += 1

    return {
        "leaderboard_rows_fetched": len(leaderboard),
        "weather_events_indexed": len(weather_events),
        "wallets_screened": len(screening_records),
        "wallets_selected": len(wallet_results),
        "wallets_core_labeled": wallets_core_labeled,
        "errors": len(errors),
        "label_counts": dict(label_counts.most_common()),
        "averages": {
            "weather_notional_ratio": mean(
                [metrics["weather_notional_ratio"] for metrics in metrics_list]
            ),
            "closed_position_win_rate": mean(
                [metrics["closed_position_win_rate"] for metrics in metrics_list]
            ),
            "closed_profit_multiple": mean(
                [metrics["closed_profit_multiple"] for metrics in metrics_list]
            ),
            "trades_per_active_day": mean(
                [metrics["trades_per_active_day"] for metrics in metrics_list]
            ),
        },
        "top_wallets_by_pnl": [
            {
                "wallet": wallet["wallet"],
                "rank": wallet["leaderboard_entry"].get("rank"),
                "pnl": wallet["metrics"]["leaderboard_pnl"],
                "closed_profit_multiple": wallet["metrics"]["closed_profit_multiple"],
                "closed_position_win_rate": wallet["metrics"]["closed_position_win_rate"],
            }
            for wallet in sorted(
                wallet_results,
                key=lambda item: item["metrics"]["leaderboard_pnl"],
                reverse=True,
            )[:10]
        ],
        "top_wallets_by_frequency": [
            {
                "wallet": wallet["wallet"],
                "rank": wallet["leaderboard_entry"].get("rank"),
                "trades_per_active_day": wallet["metrics"]["trades_per_active_day"],
                "trade_count": wallet["metrics"]["trade_count"],
            }
            for wallet in sorted(
                wallet_results,
                key=lambda item: item["metrics"]["trades_per_active_day"],
                reverse=True,
            )[:10]
        ],
    }


def build_operation_audit(
    *,
    wallet: str,
    trades: list[dict[str, Any]],
    activity: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
    chain_validation: dict[str, Any],
    collection_status: Mapping[str, Any],
) -> dict[str, Any]:
    trade_records = normalize_trade_audit_records(trades)
    settlement_records = normalize_closed_position_audit_records(closed_positions)
    activity_records = normalize_activity_operation_records(activity)
    chain_records = normalize_chain_operation_records(chain_validation)
    profit_summary = summarize_audit_profit(
        liquidity_records=trades,
        settlement_records=closed_positions,
    )
    chain_operations = chain_validation.get("operations", {})
    operations = {
        key: merge_operation_bucket(
            key=key,
            chain_bucket=chain_operations.get(key, {}) if isinstance(chain_operations, Mapping) else {},
            records=[
                record
                for record in [*activity_records, *settlement_records]
                if str(record.get("operation", "")).lower() == key
            ],
        )
        for key in OPERATION_KEYS
    }
    records = [*trade_records, *settlement_records, *activity_records, *chain_records]
    records.sort(key=audit_record_sort_key, reverse=True)
    complete = all(
        bool((status if isinstance(status, Mapping) else {}).get("complete", True))
        for status in collection_status.values()
    )
    complete = complete and bool(chain_validation.get("logs_complete", True))
    complete = complete and bool(chain_validation.get("transaction_history_complete", True))
    return {
        "wallet": wallet,
        "complete": complete,
        "collection_status": dict(collection_status),
        "profit_summary": profit_summary,
        "operations": operations,
        "record_count": len(records),
        "records": records,
    }


def normalize_trade_audit_records(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        timestamp = to_float(trade.get("timestamp"))
        trade_dt = epoch_to_datetime(timestamp)
        amount = record_notional(trade)
        side = str(trade.get("side") or "").upper()
        rows.append(
            {
                "operation": "trade",
                "audit_bucket": "trade_liquidity",
                "verification": "app",
                "source": "trades",
                "timestamp": timestamp,
                "date": trade_dt.date().isoformat() if trade_dt else "",
                "transaction_hash": first_non_empty_value(
                    trade,
                    ("transactionHash", "txHash", "hash", "id"),
                ),
                "side": side,
                "title": str(trade.get("title") or trade.get("slug") or ""),
                "market": record_event_key(trade),
                "region": str(trade.get("_region") or trade.get("region") or ""),
                "notional": amount,
                "buy_amount": amount if side == "BUY" else 0.0,
                "sell_amount": amount if side == "SELL" else 0.0,
                "text": f"{side or '-'} {str(trade.get('title') or trade.get('slug') or '-')} {amount:.2f} USDC",
            }
        )
    return rows


def normalize_closed_position_audit_records(
    closed_positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in closed_positions:
        end_dt = parse_datetime(position.get("endDate"))
        cost = to_float(position.get("totalBought"))
        pnl = to_float(position.get("realizedPnl"))
        payout = cost + pnl
        rows.append(
            {
                "operation": "redeem",
                "audit_bucket": "final_settlement",
                "verification": "app",
                "source": "closed_positions",
                "timestamp": end_dt.timestamp() if end_dt else 0.0,
                "date": end_dt.date().isoformat() if end_dt else "",
                "transaction_hash": first_non_empty_value(
                    position,
                    ("transactionHash", "txHash", "hash", "id"),
                ),
                "title": str(position.get("title") or position.get("slug") or ""),
                "market": record_event_key(position),
                "region": str(position.get("_region") or position.get("region") or ""),
                "cost_amount": cost,
                "payout_amount": payout,
                "profit_amount": pnl,
                "text": (
                    f"最终兑换/已平仓 {str(position.get('title') or position.get('slug') or '-')}"
                    f" 盈亏 {pnl:.2f} USDC"
                ),
            }
        )
    return rows


def normalize_activity_operation_records(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in activity:
        operation = infer_activity_operation(record)
        if not operation:
            continue
        raw_timestamp = first_non_empty_value(
            record,
            ("timestamp", "createdAt", "created_at", "timeStamp", "time"),
        )
        parsed = parse_metric_datetime(raw_timestamp)
        amount = record_notional(record)
        row = {
            "operation": operation,
            "audit_bucket": "trade_liquidity" if operation == "swap" else "final_settlement",
            "verification": "app",
            "source": "activity",
            "timestamp": parsed.timestamp() if parsed else 0.0,
            "date": parsed.date().isoformat() if parsed else "",
            "transaction_hash": first_non_empty_value(
                record,
                ("transactionHash", "txHash", "hash", "id"),
            ),
            "title": str(
                first_non_empty_value(
                    record,
                    ("title", "question", "description", "slug", "type"),
                )
                or ""
            ),
            "market": str(
                first_non_empty_value(
                    record,
                    ("eventSlug", "conditionId", "slug"),
                )
                or ""
            ),
            "notional": amount,
            "text": str(
                first_non_empty_value(
                    record,
                    ("description", "title", "type"),
                )
                or f"activity {operation}"
            ),
        }
        rows.append(row)
    return rows


def infer_activity_operation(record: Mapping[str, Any]) -> str:
    text = " ".join(
        str(
            first_non_empty_value(
                record,
                ("type", "activityType", "activity_type", "description", "title", "verb"),
            )
            or ""
        ).lower().replace("_", " ").replace("-", " ").split()
    )
    if not text:
        return ""
    if "swap" in text:
        return "swap"
    if any(token in text for token in ("redeem", "settle", "settlement", "payout", "claim")):
        return "redeem"
    if any(token in text for token in ("convert", "converted")):
        return "convert"
    if any(token in text for token in ("split", "merge")):
        return "split"
    return ""


def normalize_chain_operation_records(chain_validation: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    operations = chain_validation.get("operations", {})
    if not isinstance(operations, Mapping):
        return rows
    for key in OPERATION_KEYS:
        bucket = operations.get(key, {})
        if not isinstance(bucket, Mapping):
            continue
        for record in bucket.get("evidence", []) or []:
            if not isinstance(record, Mapping):
                continue
            normalized = dict(record)
            normalized.setdefault("operation", key)
            normalized.setdefault("audit_bucket", "final_settlement")
            normalized.setdefault("verification", "chain")
            normalized.setdefault("source", f"chain_validation.{key}")
            normalized.setdefault("text", f"链上 {key} 证据")
            rows.append(normalized)
    return rows


def merge_operation_bucket(
    *,
    key: str,
    chain_bucket: Mapping[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    chain_evidence = [
        dict(item)
        for item in (chain_bucket.get("evidence", []) if isinstance(chain_bucket, Mapping) else [])
        if isinstance(item, Mapping)
    ]
    count = len(chain_evidence) + len(records)
    if chain_evidence:
        status = str(chain_bucket.get("status") or "verified")
        reason = str(chain_bucket.get("reason") or "")
    elif records:
        status = "partial"
        reason = f"{key} 只有应用层记录，尚无链上强校验。"
    else:
        status = "not_found"
        reason = f"未发现 {key} 记录。"
    return {
        "operation": key,
        "status": status,
        "reason": reason,
        "count": count,
        "verified_count": len(chain_evidence),
        "partial_count": len(records),
        "complete": bool(chain_bucket.get("complete", True)) if isinstance(chain_bucket, Mapping) else True,
        "source": str(chain_bucket.get("source") or "mixed") if isinstance(chain_bucket, Mapping) else "mixed",
        "evidence": [*chain_evidence, *records],
    }


def audit_record_sort_key(record: Mapping[str, Any]) -> tuple[float, str]:
    return (to_float(record.get("timestamp")), str(record.get("transaction_hash") or ""))


def first_non_empty_value(record: Mapping[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        value = get_field_value(record, field)
        if value not in (None, ""):
            return value
    return None


def resolve_analysis_now(config: dict[str, Any]) -> datetime:
    settings = config.get("analysis", {})
    configured = settings.get("current_datetime") or settings.get("current_date")
    parsed = parse_datetime(configured)
    if parsed is not None:
        return parsed.astimezone(UTC)
    return datetime.now(UTC)


REGISTRATION_DATE_FIELDS = (
    "registrationDate",
    "registration_date",
    "registeredAt",
    "registered_at",
    "createdAt",
    "created_at",
    "created",
    "walletCreatedAt",
    "wallet_created_at",
    "walletRegisteredAt",
    "wallet_registered_at",
    "profile.createdAt",
    "profile.created_at",
    "user.createdAt",
    "user.created_at",
)


def resolve_wallet_registration_datetime(
    *,
    snapshot: dict[str, Any],
    leaderboard_entry: dict[str, Any],
    chain_validation: dict[str, Any],
) -> tuple[datetime | None, str]:
    for source_name, record in (
        ("leaderboard_entry", leaderboard_entry),
        ("snapshot", snapshot),
    ):
        resolved = first_registration_datetime(record)
        if resolved is not None:
            return resolved, source_name

    first_chain_datetime = parse_metric_datetime(
        chain_validation.get("first_transaction_datetime")
    )
    if first_chain_datetime is not None:
        return first_chain_datetime, "chain_validation.first_transaction_datetime"

    first_chain_timestamp = parse_metric_datetime(
        chain_validation.get("first_transaction_timestamp")
    )
    if first_chain_timestamp is not None:
        return first_chain_timestamp, "chain_validation.first_transaction_timestamp"

    return None, ""


def first_registration_datetime(record: Mapping[str, Any]) -> datetime | None:
    for field_name in REGISTRATION_DATE_FIELDS:
        parsed = parse_metric_datetime(get_field_value(record, field_name))
        if parsed is not None:
            return parsed
    return None


def split_position_average_cost_summary(
    positions: list[dict[str, Any]],
    *,
    target: float,
    tolerance: float,
) -> dict[str, Any]:
    weighted_total = 0.0
    total_weight = 0.0
    values: list[float] = []
    missing_cost_count = 0

    for position in positions:
        raw_cost = first_number(position, ("avgPrice", "costBasis", "cost_basis", "price"))
        if raw_cost is None:
            missing_cost_count += 1
            continue
        cost = normalize_chip_cost(raw_cost)
        weight = first_number(position, ("size", "totalBought", "shares"))
        if weight is None or weight <= 0:
            weight = 1.0
        values.append(cost)
        weighted_total += cost * weight
        total_weight += weight

    average = ratio(weighted_total, total_weight)
    diff = abs(average - target) if values else 0.0
    return {
        "position_count": len(positions),
        "priced_position_count": len(values),
        "missing_cost_count": missing_cost_count,
        "target": target,
        "tolerance": tolerance,
        "average_chip_cost": average,
        "median_chip_cost": median(values),
        "difference_from_target": diff,
        "matched_split_avg_chip_cost": bool(values and diff <= tolerance),
    }


def build_screening_record(
    wallet: str,
    leaderboard_entry: dict[str, Any],
    metrics: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    filter_config = config["wallet_filter"]
    normalized_wallet = normalize_address(wallet)
    include_wallets = {
        normalize_address(item) for item in filter_config.get("include_wallets", [])
    }
    exclude_wallets = {
        normalize_address(item) for item in filter_config.get("exclude_wallets", [])
    }

    reasons: list[str] = []
    selected = True

    if normalized_wallet in exclude_wallets:
        selected = False
        reasons.append("wallet in exclude list")
    elif normalized_wallet in include_wallets:
        reasons.append("wallet in include list")
    else:
        checks = [
            (
                metrics["leaderboard_pnl"] >= to_float(filter_config.get("min_pnl")),
                f"pnl>={filter_config.get('min_pnl')}",
            ),
            (
                metrics["leaderboard_volume"] >= to_float(filter_config.get("min_volume")),
                f"volume>={filter_config.get('min_volume')}",
            ),
            (
                metrics["trade_count"] >= int(filter_config.get("min_traded_count", 0)),
                f"trade_count>={filter_config.get('min_traded_count')}",
            ),
        ]
        min_weather_trade_ratio = filter_config.get("min_weather_trade_ratio")
        if min_weather_trade_ratio not in (None, ""):
            checks.append(
                (
                    metrics["weather_trade_ratio"] >= to_float(min_weather_trade_ratio),
                    f"weather_trade_ratio>={min_weather_trade_ratio}",
                )
            )
        if filter_config.get("max_pnl") is not None:
            checks.append(
                (
                    metrics["leaderboard_pnl"] <= to_float(filter_config.get("max_pnl")),
                    f"pnl<={filter_config.get('max_pnl')}",
                )
            )
        if filter_config.get("max_volume") is not None:
            checks.append(
                (
                    metrics["leaderboard_volume"] <= to_float(filter_config.get("max_volume")),
                    f"volume<={filter_config.get('max_volume')}",
                )
            )
        if filter_config.get("max_traded_count") is not None:
            checks.append(
                (
                    metrics["trade_count"] <= int(filter_config.get("max_traded_count")),
                    f"trade_count<={filter_config.get('max_traded_count')}",
                )
            )
        failed = [label for ok, label in checks if not ok]
        if failed:
            selected = False
            reasons.extend(f"failed:{label}" for label in failed)
        else:
            reasons.append("passed all numeric filters")

    return {
        "wallet": wallet,
        "rank": leaderboard_entry.get("rank"),
        "user_name": leaderboard_entry.get("userName"),
        "x_username": leaderboard_entry.get("xUsername"),
        "pnl": metrics["leaderboard_pnl"],
        "volume": metrics["leaderboard_volume"],
        "trade_count": metrics["trade_count"],
        "weather_trade_count": metrics["weather_trade_count"],
        "weather_trade_ratio": metrics["weather_trade_ratio"],
        "weather_notional_ratio": metrics["weather_notional_ratio"],
        "selected": selected,
        "reasons": reasons,
    }


def build_weather_index(events: list[dict[str, Any]]) -> WeatherIndex:
    event_ids: set[str] = set()
    event_slugs: set[str] = set()
    condition_ids: set[str] = set()
    market_slugs: set[str] = set()
    regions_by_key: dict[str, str] = {}
    market_dates_by_key: dict[str, str] = {}

    for event in events:
        region = extract_event_region(event)
        event_market_date = extract_record_market_date(event)
        event_id = str(event.get("id", "")).strip()
        event_slug = str(event.get("slug", "")).strip()
        if event_id:
            event_ids.add(event_id)
            if region:
                regions_by_key[event_id] = region
            if event_market_date:
                market_dates_by_key[event_id] = event_market_date
        if event_slug:
            event_slugs.add(event_slug)
            if region:
                regions_by_key[event_slug] = region
            if event_market_date:
                market_dates_by_key[event_slug] = event_market_date

        for market in event.get("markets", []):
            market_date = extract_record_market_date(market) or event_market_date
            condition_id = str(market.get("conditionId", "")).strip()
            market_slug = str(market.get("slug", "")).strip()
            if condition_id:
                condition_ids.add(condition_id)
                if region:
                    regions_by_key[condition_id] = region
                if market_date:
                    market_dates_by_key[condition_id] = market_date
            if market_slug:
                market_slugs.add(market_slug)
                if region:
                    regions_by_key[market_slug] = region
                if market_date:
                    market_dates_by_key[market_slug] = market_date
            market_id = str(market.get("id", "")).strip()
            if market_id and region:
                regions_by_key[market_id] = region
            if market_id and market_date:
                market_dates_by_key[market_id] = market_date

    return WeatherIndex(
        event_ids=event_ids,
        event_slugs=event_slugs,
        condition_ids=condition_ids,
        market_slugs=market_slugs,
        regions_by_key=regions_by_key,
        market_dates_by_key=market_dates_by_key,
    )


def is_weather_record(record: dict[str, Any], weather_index: WeatherIndex) -> bool:
    event_id = str(record.get("eventId", "")).strip()
    event_slug = str(record.get("eventSlug", "")).strip()
    condition_id = str(record.get("conditionId", "")).strip()
    market_slug = str(record.get("slug", "")).strip()
    return any(
        (
            event_id and event_id in weather_index.event_ids,
            event_slug and event_slug in weather_index.event_slugs,
            condition_id and condition_id in weather_index.condition_ids,
            market_slug and market_slug in weather_index.market_slugs,
        )
    )


GENERIC_WEATHER_TAGS = {
    "weather",
    "recurring",
    "hide from new",
    "daily temperature",
    "daily weather",
    "highest temperature",
    "lowest temperature",
    "temperature",
    "rain",
    "snow",
    "wind",
    "air quality",
}


def enrich_trades_with_regions(
    trades: list[dict[str, Any]],
    *,
    weather_index: WeatherIndex,
    region_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for trade in trades:
        region = record_region(trade, weather_index, region_fields=region_fields)
        market_date = record_market_date(trade, weather_index)
        if not region and not market_date:
            enriched.append(trade)
            continue
        copy = dict(trade)
        if region:
            copy["_region"] = region
        if market_date:
            copy["_market_date"] = market_date
        enriched.append(copy)
    return enriched


def record_region(
    record: Mapping[str, Any],
    weather_index: WeatherIndex,
    *,
    region_fields: tuple[str, ...],
) -> str:
    direct = first_record_text(record, region_fields)
    if direct:
        return direct

    for key in (
        "conditionId",
        "slug",
        "eventSlug",
        "eventId",
        "marketSlug",
        "marketId",
    ):
        value = str(record.get(key, "")).strip()
        if value and value in weather_index.regions_by_key:
            return weather_index.regions_by_key[value]
    return ""


def record_market_date(record: Mapping[str, Any], weather_index: WeatherIndex) -> str:
    direct = extract_record_market_date(record)
    if direct:
        return direct

    for key in (
        "conditionId",
        "slug",
        "eventSlug",
        "eventId",
        "marketSlug",
        "marketId",
    ):
        value = str(record.get(key, "")).strip()
        if value and value in weather_index.market_dates_by_key:
            return weather_index.market_dates_by_key[value]
    return ""


def extract_record_market_date(record: Mapping[str, Any]) -> str:
    parsed = metric_record_market_date(record)
    return parsed.isoformat() if parsed else ""


def extract_event_region(event: Mapping[str, Any]) -> str:
    direct = first_record_text(event, DEFAULT_REGION_FIELDS)
    if direct:
        return direct

    for series in event.get("series", []) or []:
        if not isinstance(series, Mapping):
            continue
        for field in ("title", "slug", "ticker"):
            candidate = clean_region_candidate(series.get(field))
            if candidate:
                for suffix in (" daily weather", "-daily-weather", " weather"):
                    if candidate.lower().endswith(suffix):
                        trimmed = candidate[: -len(suffix)].strip(" -")
                        if is_region_candidate(trimmed):
                            return trimmed

    for tag in event.get("tags", []) or []:
        if not isinstance(tag, Mapping):
            continue
        candidate = clean_region_candidate(tag.get("label") or tag.get("slug"))
        if is_region_candidate(candidate):
            return candidate

    for field in ("title", "slug", "ticker"):
        candidate = infer_region_from_weather_text(event.get(field))
        if candidate:
            return candidate
    return ""


def infer_region_from_weather_text(value: Any) -> str:
    text = clean_region_candidate(value)
    if not text:
        return ""

    match = re_search_region(r"\bin\s+(.+?)\s+(?:on|by|from|for)\b", text)
    if match:
        return match

    slug = text.lower().replace("_", "-")
    match = re_search_region(r"(?:temperature|rain|snow|wind|air-quality)-in-(.+?)-(?:on|by|from|for)-", slug)
    if match:
        return match.replace("-", " ").title()
    return ""


def re_search_region(pattern: str, text: str) -> str:
    import re

    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    candidate = clean_region_candidate(match.group(1))
    return candidate if is_region_candidate(candidate) else ""


def first_record_text(record: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = get_field_value(record, field)
        candidate = clean_region_candidate(value)
        if candidate:
            return candidate
    return ""


def clean_region_candidate(value: Any) -> str:
    if value in (None, "") or isinstance(value, Mapping):
        return ""
    if isinstance(value, (list, tuple, set)):
        return ""
    return " ".join(str(value).strip().replace("_", " ").split())


def is_region_candidate(value: str) -> bool:
    normalized = " ".join(str(value).lower().replace("-", " ").split())
    if not normalized or normalized in GENERIC_WEATHER_TAGS:
        return False
    if normalized.startswith("rewards automation"):
        return False
    return True


def estimate_holding_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        asset = str(trade.get("asset", "")).strip() or record_event_key(trade)
        if asset:
            grouped[asset].append(trade)

    holding_hours: list[float] = []
    sell_count = 0
    matched_sell_count = 0

    for group in grouped.values():
        queue: list[list[float]] = []
        for trade in sorted(group, key=lambda item: to_float(item.get("timestamp"))):
            timestamp = to_float(trade.get("timestamp"))
            size = to_float(trade.get("size"))
            side = str(trade.get("side", "")).upper()
            if side == "BUY":
                queue.append([timestamp, size])
                continue
            if side != "SELL":
                continue

            sell_count += 1
            matched_any = False
            remaining = size
            while remaining > 1e-9 and queue:
                buy_timestamp, buy_size = queue[0]
                matched = min(remaining, buy_size)
                if timestamp >= buy_timestamp:
                    holding_hours.append((timestamp - buy_timestamp) / 3600.0)
                    matched_any = True
                remaining -= matched
                buy_size -= matched
                if buy_size <= 1e-9:
                    queue.pop(0)
                else:
                    queue[0][1] = buy_size
            if matched_any:
                matched_sell_count += 1

    return {
        "holding_hours": holding_hours,
        "sell_count": sell_count,
        "matched_sell_count": matched_sell_count,
    }


def build_end_lookup(snapshot: dict[str, Any]) -> dict[str, datetime]:
    lookup: dict[str, datetime] = {}
    for record in [*snapshot["positions"], *snapshot["closed_positions"]]:
        end_dt = parse_datetime(record.get("endDate"))
        if end_dt is None:
            continue
        for key in (
            str(record.get("conditionId", "")).strip(),
            str(record.get("slug", "")).strip(),
            str(record.get("eventSlug", "")).strip(),
            str(record.get("eventId", "")).strip(),
        ):
            if key and key not in lookup:
                lookup[key] = end_dt
    return lookup


def collect_time_to_end_hours(
    trades: list[dict[str, Any]],
    end_lookup: dict[str, datetime],
) -> list[float]:
    values: list[float] = []
    for trade in trades:
        trade_dt = epoch_to_datetime(trade.get("timestamp"))
        if trade_dt is None:
            continue
        end_dt = None
        for key in (
            str(trade.get("conditionId", "")).strip(),
            str(trade.get("slug", "")).strip(),
            str(trade.get("eventSlug", "")).strip(),
        ):
            if key and key in end_lookup:
                end_dt = end_lookup[key]
                break
        if end_dt is None:
            continue
        values.append((end_dt - trade_dt).total_seconds() / 3600.0)
    return [value for value in values if value >= 0]


def paginate(
    *,
    page_size: int,
    max_offset: int,
    fetch_page: Callable[[int, int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return paginate_with_status(
        page_size=page_size,
        max_offset=max_offset,
        fetch_page=fetch_page,
    )["records"]


def paginate_with_status(
    *,
    page_size: int,
    max_offset: int,
    fetch_page: Callable[[int, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    offset = 0
    page_count = 0
    complete = True
    stop_reason = "empty_page"
    while offset <= max_offset:
        try:
            page = fetch_page(page_size, offset)
        except RuntimeError as exc:
            complete = False
            if is_terminal_pagination_error(exc, offset):
                stop_reason = "terminal_http_400"
                break
            raise
        page_count += 1
        if not page:
            stop_reason = "empty_page"
            break
        results.extend(page)
        if len(page) < page_size:
            stop_reason = "last_page_partial"
            break
        next_offset = offset + page_size
        if next_offset > max_offset:
            complete = False
            stop_reason = "max_offset_reached"
            offset = next_offset
            break
        offset = next_offset
    return {
        "records": results,
        "complete": complete,
        "stop_reason": stop_reason,
        "page_count": page_count,
        "record_count": len(results),
        "last_offset": offset if results else 0,
        "next_offset": offset + page_size if results else 0,
    }


def is_terminal_pagination_error(exc: RuntimeError, offset: int) -> bool:
    if offset <= 0:
        return False
    cause = exc.__cause__
    return isinstance(cause, HTTPError) and cause.code == 400


def top_records(
    records: list[dict[str, Any]],
    *,
    limit: int,
    sort_key: Callable[[dict[str, Any]], float],
) -> list[dict[str, Any]]:
    return sorted(records, key=sort_key, reverse=True)[:limit]


def record_event_key(record: dict[str, Any]) -> str:
    for key in ("eventSlug", "eventId", "conditionId", "slug"):
        value = str(record.get(key, "")).strip()
        if value:
            return value
    return ""


def record_notional(record: dict[str, Any]) -> float:
    explicit = to_float(record.get("usdcSize"))
    if explicit > 0:
        return explicit
    size = to_float(record.get("size"))
    price = to_float(record.get("price"))
    if size > 0 and price > 0:
        return size * price
    for field in ("currentValue", "initialValue", "totalBought"):
        value = to_float(record.get(field))
        if value > 0:
            return value
    return 0.0


def epoch_to_datetime(value: Any) -> datetime | None:
    timestamp = to_float(value)
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def ratio(numerator: float | int, denominator: float | int) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def median(values: list[float]) -> float:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return 0.0
    return float(statistics.median(cleaned))


def mean(values: list[float]) -> float:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return 0.0
    return float(statistics.mean(cleaned))


def to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def progress(config: dict[str, Any], message: str) -> None:
    runtime = config.get("runtime", {})
    analysis = config.get("analysis", {})
    progress_log_path = runtime.get("progress_log_path")
    if progress_log_path:
        path = Path(str(progress_log_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp}\t{message}\n")
    if runtime.get("verbose") or analysis.get("verbose"):
        print(f"[polymarket-weather] {message}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
