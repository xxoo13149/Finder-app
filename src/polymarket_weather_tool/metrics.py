from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any


UTC = timezone.utc
DEFAULT_COST_BINS = (0.0, 0.25, 0.5, 0.75, 1.0)
DEFAULT_TAG_FIELDS = (
    "tags",
    "tag",
    "tagSlug",
    "tag_slug",
    "category",
    "title",
    "slug",
    "eventSlug",
    "event_slug",
)
DEFAULT_REGION_FIELDS = (
    "region",
    "regionName",
    "region_name",
    "location",
    "locationName",
    "location_name",
    "city",
    "marketRegion",
    "market_region",
    "eventRegion",
    "event_region",
    "event.region",
    "event.location",
    "market.region",
    "market.location",
)
DEFAULT_NOTIONAL_FIELDS = (
    "usdcSize",
    "usdc_size",
    "amount",
    "notional",
    "value",
)
DEFAULT_CHIP_COST_FIELDS = (
    "chipCost",
    "chip_cost",
    "costBasis",
    "cost_basis",
    "avgPrice",
    "price",
)
DEFAULT_SWAP_TYPE_FIELDS = (
    "type",
    "activityType",
    "activity_type",
    "transactionType",
    "transaction_type",
    "eventType",
    "event_type",
    "action",
    "method",
    "methodName",
    "method_name",
)
DEFAULT_MARKET_DATE_FIELDS = (
    "_market_date",
    "marketDate",
    "market_date",
    "eventDate",
    "event_date",
    "weatherDate",
    "weather_date",
    "forecastDate",
    "forecast_date",
    "targetDate",
    "target_date",
    "temperatureDate",
    "temperature_date",
    "date",
    "event.date",
    "market.date",
    "endDate",
    "end_date",
)
DEFAULT_MARKET_TEXT_FIELDS = (
    "title",
    "question",
    "description",
    "slug",
    "marketSlug",
    "market_slug",
    "eventSlug",
    "event_slug",
    "category",
    "tags",
)
MONTH_LOOKUP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def profit_multiple(
    cost: Any,
    payout: Any | None = None,
    *,
    profit: Any | None = None,
) -> float:
    """Return total return multiple, e.g. 2.5 means payout is 2.5x cost."""
    cost_value = to_float(cost)
    if cost_value <= 0:
        return 0.0

    if payout is not None:
        payout_value = to_float(payout)
    elif profit is not None:
        payout_value = cost_value + to_float(profit)
    else:
        return 0.0

    if not math.isfinite(payout_value):
        return 0.0
    return payout_value / cost_value


def win_rate_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    pnl_field: str = "realizedPnl",
) -> dict[str, Any]:
    """Summarize win/loss/push rates for records with a numeric PnL field."""
    rows = list(records or [])
    wins = 0
    losses = 0
    pushes = 0
    missing = 0
    pnl_values: list[float] = []

    for record in rows:
        raw_value = get_field_value(record, pnl_field)
        if raw_value in (None, ""):
            missing += 1
            continue
        pnl = to_float(raw_value)
        pnl_values.append(pnl)
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        else:
            pushes += 1

    resolved = wins + losses + pushes
    return {
        "total_count": len(rows),
        "resolved_count": resolved,
        "missing_count": missing,
        "win_count": wins,
        "loss_count": losses,
        "push_count": pushes,
        "win_rate": ratio(wins, resolved),
        "loss_rate": ratio(losses, resolved),
        "push_rate": ratio(pushes, resolved),
        "total_pnl": sum(pnl_values),
        "average_pnl": ratio(sum(pnl_values), resolved),
        "median_pnl": median(pnl_values),
    }


def cost_basis_distribution(
    records: Iterable[Mapping[str, Any]],
    *,
    bins: Sequence[Any] = DEFAULT_COST_BINS,
    price_fields: Sequence[str] = ("avgPrice", "price", "costBasis", "cost_basis"),
    size_fields: Sequence[str] = ("size", "totalBought", "shares"),
    cost_fields: Sequence[str] = ("initialValue", "usdcSize", "cashSpent", "cost"),
) -> dict[str, Any]:
    """Bucket position/trade cost basis by share price and summarize exposure."""
    rows = list(records or [])
    edges = sorted({to_float(edge) for edge in bins if math.isfinite(to_float(edge))})
    if len(edges) < 2:
        edges = list(DEFAULT_COST_BINS)

    buckets = [
        {
            "min": edges[index],
            "max": edges[index + 1],
            "count": 0,
            "total_size": 0.0,
            "total_cost": 0.0,
            "weighted_average_cost": 0.0,
        }
        for index in range(len(edges) - 1)
    ]
    underflow = _empty_cost_bucket(max_value=edges[0])
    overflow = _empty_cost_bucket(min_value=edges[-1])

    observed_prices: list[float] = []
    total_size = 0.0
    total_cost = 0.0
    missing_price_count = 0

    for record in rows:
        price = first_number(record, price_fields)
        size = first_number(record, size_fields)
        explicit_cost = first_number(record, cost_fields)
        if price is None and explicit_cost is not None and size is not None and size > 0:
            price = explicit_cost / size
        if price is None:
            missing_price_count += 1
            continue

        if size is None and explicit_cost is not None and price > 0:
            size = explicit_cost / price
        if size is None:
            size = 1.0

        cost = explicit_cost if explicit_cost is not None else price * size
        observed_prices.append(price)
        total_size += size
        total_cost += cost

        bucket = _cost_bucket_for(price, buckets, underflow, overflow)
        bucket["count"] += 1
        bucket["total_size"] += size
        bucket["total_cost"] += cost

    for bucket in [underflow, *buckets, overflow]:
        bucket["weighted_average_cost"] = ratio(bucket["total_cost"], bucket["total_size"])

    return {
        "total_count": len(rows),
        "priced_count": len(observed_prices),
        "missing_price_count": missing_price_count,
        "total_size": total_size,
        "total_cost": total_cost,
        "weighted_average_cost": ratio(total_cost, total_size),
        "min_cost_basis": min(observed_prices) if observed_prices else 0.0,
        "median_cost_basis": median(observed_prices),
        "max_cost_basis": max(observed_prices) if observed_prices else 0.0,
        "underflow": underflow,
        "buckets": buckets,
        "overflow": overflow,
    }


def average_buy_price_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    price_fields: Sequence[str] = ("avgPrice", "price", "costBasis", "cost_basis"),
    size_fields: Sequence[str] = ("size", "shares", "totalBought"),
) -> dict[str, Any]:
    """Summarize weighted buy-entry prices with graceful price fallback."""
    rows = list(records or [])
    prices: list[float] = []
    weighted_price_total = 0.0
    total_size = 0.0
    total_notional = 0.0
    missing_price_count = 0

    for record in rows:
        price = first_number(record, price_fields)
        size = first_number(record, size_fields)
        notional = record_notional(record)

        if price is None and size is not None and size > 0 and notional > 0:
            price = notional / size
        if price is None:
            missing_price_count += 1
            continue

        if size is None or size <= 0:
            size = notional / price if price > 0 and notional > 0 else 1.0

        prices.append(price)
        weighted_price_total += price * size
        total_size += size
        total_notional += notional if notional > 0 else price * size

    return {
        "total_buy_count": len(rows),
        "priced_buy_count": len(prices),
        "missing_price_count": missing_price_count,
        "total_buy_size": total_size,
        "total_buy_notional": total_notional,
        "weighted_average_price": ratio(weighted_price_total, total_size),
        "average_price": ratio(weighted_price_total, total_size),
        "median_price": median(prices),
        "min_price": min(prices) if prices else 0.0,
        "max_price": max(prices) if prices else 0.0,
    }


def profile_summary(
    trades: Iterable[Mapping[str, Any]],
    closed_positions: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    top_city_limit: int = 10,
) -> dict[str, Any]:
    """Build wallet profile metrics for city, entry price, and closed PnL views."""
    trade_rows = list(trades or [])
    closed_rows = list(closed_positions or [])
    buy_trades = [record for record in trade_rows if record_is_buy(record)]
    city_distribution = city_distribution_summary(
        trade_rows,
        closed_rows,
        region_fields=region_fields,
        top_city_limit=top_city_limit,
    )

    return {
        "average_buy_price": average_buy_price_summary(buy_trades),
        "city_distribution": city_distribution["city_distribution"],
        "top_cities": city_distribution["top_cities"],
        "buy_price_distribution": cost_basis_distribution(buy_trades),
        "closed_position_pnl": closed_position_pnl_summary(
            closed_rows,
            region_fields=region_fields,
        ),
    }


def city_distribution_summary(
    trades: Iterable[Mapping[str, Any]],
    closed_positions: Iterable[Mapping[str, Any]] = (),
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    top_city_limit: int = 10,
) -> dict[str, Any]:
    trade_rows = list(trades or [])
    closed_rows = list(closed_positions or [])
    stats: dict[str, dict[str, Any]] = {}
    unknown_trade_count = 0
    unknown_closed_position_count = 0

    for record in trade_rows:
        city, known = record_city(record, region_fields=region_fields)
        if not known:
            unknown_trade_count += 1
        stat = city_stat(stats, city)
        stat["trade_count"] += 1

        amount = record_notional(record)
        if record_is_buy(record):
            stat["buy_trade_count"] += 1
            stat["buy_amount"] += amount
        elif record_is_sell(record):
            stat["sell_trade_count"] += 1
            stat["sell_amount"] += amount

    for record in closed_rows:
        city, known = record_city(record, region_fields=region_fields)
        if not known:
            unknown_closed_position_count += 1
        stat = city_stat(stats, city)
        stat["closed_position_count"] += 1

        pnl_raw = get_field_value(record, "realizedPnl")
        if pnl_raw in (None, ""):
            stat["missing_closed_pnl_count"] += 1
        else:
            stat["realized_pnl"] += to_float(pnl_raw)
            stat["resolved_closed_pnl_count"] += 1

        bought = first_number(record, ("totalBought", "initialValue", "cashSpent", "cost"))
        if bought is not None:
            stat["closed_total_bought"] += bought

    cities = list(stats.values())
    day_win_rate_lookup = {
        str(item.get("region", "")): item
        for item in regional_day_win_rate_summary(
            trade_rows,
            region_fields=region_fields,
            min_trade_count=0,
        ).get("regions", [])
    }
    for stat in cities:
        stat["trade_ratio"] = ratio(stat["trade_count"], len(trade_rows))
        stat["net_trade_cashflow"] = stat["sell_amount"] - stat["buy_amount"]
        stat["closed_profit_multiple"] = profit_multiple(
            stat["closed_total_bought"],
            profit=stat["realized_pnl"],
        )
        day_stat = day_win_rate_lookup.get(str(stat.get("region") or stat.get("city") or ""), {})
        stat["positive_return_days"] = int(day_stat.get("positive_return_days") or 0)
        stat["total_trade_days"] = int(day_stat.get("total_trade_days") or 0)
        stat["positive_return_day_ratio"] = float(
            day_stat.get("positive_return_day_ratio") or 0.0
        )

    cities.sort(
        key=lambda item: (
            item["trade_count"],
            item["buy_amount"] + item["sell_amount"],
            item["realized_pnl"],
        ),
        reverse=True,
    )
    top_cities = {
        "by_buy_amount": top_city_records(cities, "buy_amount", top_city_limit),
        "by_sell_amount": top_city_records(cities, "sell_amount", top_city_limit),
        "by_realized_pnl": top_city_records(cities, "realized_pnl", top_city_limit),
        "by_net_trade_cashflow": top_city_records(
            cities,
            "net_trade_cashflow",
            top_city_limit,
        ),
    }

    return {
        "city_distribution": {
            "total_trade_count": len(trade_rows),
            "known_city_trade_count": len(trade_rows) - unknown_trade_count,
            "unknown_city_trade_count": unknown_trade_count,
            "closed_position_count": len(closed_rows),
            "known_city_closed_position_count": (
                len(closed_rows) - unknown_closed_position_count
            ),
            "unknown_city_closed_position_count": unknown_closed_position_count,
            "city_count": len(cities),
            "cities": cities,
        },
        "top_cities": top_cities,
    }


def closed_position_pnl_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    top_position_limit: int = 10,
) -> dict[str, Any]:
    rows = list(records or [])
    pnl_summary = win_rate_summary(rows)
    total_bought = sum(
        value
        for value in (
            first_number(record, ("totalBought", "initialValue", "cashSpent", "cost"))
            for record in rows
        )
        if value is not None
    )
    normalized_records = [
        closed_position_record(record, region_fields=region_fields)
        for record in rows
        if get_field_value(record, "realizedPnl") not in (None, "")
    ]
    top_winning = sorted(
        [record for record in normalized_records if record["realized_pnl"] > 0],
        key=lambda item: item["realized_pnl"],
        reverse=True,
    )[:top_position_limit]
    top_losing = sorted(
        [record for record in normalized_records if record["realized_pnl"] < 0],
        key=lambda item: item["realized_pnl"],
    )[:top_position_limit]

    return {
        "closed_position_count": pnl_summary["total_count"],
        "resolved_pnl_count": pnl_summary["resolved_count"],
        "missing_pnl_count": pnl_summary["missing_count"],
        "win_count": pnl_summary["win_count"],
        "loss_count": pnl_summary["loss_count"],
        "push_count": pnl_summary["push_count"],
        "win_rate": pnl_summary["win_rate"],
        "loss_rate": pnl_summary["loss_rate"],
        "push_rate": pnl_summary["push_rate"],
        "total_realized_pnl": pnl_summary["total_pnl"],
        "average_realized_pnl": pnl_summary["average_pnl"],
        "median_realized_pnl": pnl_summary["median_pnl"],
        "total_bought": total_bought,
        "profit_multiple": profit_multiple(total_bought, profit=pnl_summary["total_pnl"]),
        "top_winning_positions": top_winning,
        "top_losing_positions": top_losing,
    }


def audit_profit_summary(
    records: Iterable[Mapping[str, Any]] = (),
    *,
    liquidity_records: Iterable[Mapping[str, Any]] | None = None,
    settlement_records: Iterable[Mapping[str, Any]] | None = None,
    bucket_fields: Sequence[str] = (
        "auditBucket",
        "audit_bucket",
        "auditCaliber",
        "audit_caliber",
        "auditType",
        "audit_type",
        "recordType",
        "record_type",
        "kind",
    ),
    liquidity_bucket_values: Sequence[str] = (
        "trade_liquidity",
        "liquidity",
        "trade",
        "trading",
    ),
    settlement_bucket_values: Sequence[str] = (
        "final_settlement",
        "settlement",
        "redeem",
        "redemption",
        "close",
        "closed_position",
    ),
    side_field: str = "side",
    amount_fields: Sequence[str] = DEFAULT_NOTIONAL_FIELDS,
    settlement_cost_fields: Sequence[str] = (
        "totalBought",
        "total_bought",
        "initialValue",
        "initial_value",
        "cashSpent",
        "cash_spent",
        "cost",
        "costBasis",
        "cost_basis",
    ),
    settlement_payout_fields: Sequence[str] = (
        "payout",
        "payoutAmount",
        "payout_amount",
        "redeemedValue",
        "redeemed_value",
        "settlementValue",
        "settlement_value",
        "finalValue",
        "final_value",
        "returnAmount",
        "return_amount",
    ),
    settlement_pnl_fields: Sequence[str] = (
        "realizedPnl",
        "realized_pnl",
        "cashPnl",
        "cash_pnl",
        "pnl",
        "profit",
    ),
) -> dict[str, Any]:
    """Summarize audit profit in separate liquidity-trading and settlement calibers."""
    mixed_rows = list(records or [])
    liquidity_rows = list(liquidity_records or [])
    settlement_rows = list(settlement_records or [])
    unclassified_count = 0

    for record in mixed_rows:
        caliber = infer_audit_caliber(
            record,
            bucket_fields=bucket_fields,
            liquidity_bucket_values=liquidity_bucket_values,
            settlement_bucket_values=settlement_bucket_values,
            side_field=side_field,
            settlement_pnl_fields=settlement_pnl_fields,
            settlement_payout_fields=settlement_payout_fields,
            settlement_cost_fields=settlement_cost_fields,
        )
        if caliber == "trade_liquidity":
            liquidity_rows.append(record)
        elif caliber == "final_settlement":
            settlement_rows.append(record)
        else:
            unclassified_count += 1

    trade_liquidity = _trade_liquidity_audit_summary(
        liquidity_rows,
        side_field=side_field,
        amount_fields=amount_fields,
    )
    final_settlement = _final_settlement_audit_summary(
        settlement_rows,
        cost_fields=settlement_cost_fields,
        payout_fields=settlement_payout_fields,
        pnl_fields=settlement_pnl_fields,
    )
    combined_cost = trade_liquidity["cost_amount"] + final_settlement["cost_amount"]
    combined_payout = trade_liquidity["payout_amount"] + final_settlement["payout_amount"]
    combined_profit = trade_liquidity["profit_amount"] + final_settlement["profit_amount"]

    combined = {
        "caliber": "combined",
        "label": "unified_total",
        "record_count": trade_liquidity["record_count"] + final_settlement["record_count"],
        "resolved_count": final_settlement["resolved_count"],
        "missing_pnl_count": final_settlement["missing_pnl_count"],
        "cost_amount": combined_cost,
        "payout_amount": combined_payout,
        "profit_amount": combined_profit,
        "profit_multiple": profit_multiple(combined_cost, combined_payout),
    }

    return {
        "total_record_count": (
            len(mixed_rows)
            if mixed_rows
            else trade_liquidity["record_count"] + final_settlement["record_count"]
        ),
        "classified_record_count": trade_liquidity["record_count"] + final_settlement["record_count"],
        "unclassified_record_count": unclassified_count,
        "trade_liquidity_record_count": trade_liquidity["record_count"],
        "final_settlement_record_count": final_settlement["record_count"],
        "trade_liquidity_profit": trade_liquidity["profit_amount"],
        "trade_liquidity_profit_multiple": trade_liquidity["profit_multiple"],
        "trade_liquidity_buy_amount": trade_liquidity["buy_amount"],
        "trade_liquidity_sell_amount": trade_liquidity["sell_amount"],
        "final_settlement_profit": final_settlement["profit_amount"],
        "final_settlement_profit_multiple": final_settlement["profit_multiple"],
        "final_settlement_cost_amount": final_settlement["cost_amount"],
        "final_settlement_payout_amount": final_settlement["payout_amount"],
        "unified_profit": combined_profit,
        "unified_profit_multiple": combined["profit_multiple"],
        "unified_cost_amount": combined_cost,
        "unified_payout_amount": combined_payout,
        "trade_liquidity": trade_liquidity,
        "final_settlement": final_settlement,
        "combined": combined,
        "calibers": {
            "trade_liquidity": trade_liquidity,
            "final_settlement": final_settlement,
            "combined": combined,
        },
    }


def record_city(
    record: Mapping[str, Any],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
) -> tuple[str, bool]:
    city = first_text(record, region_fields)
    if city is None:
        return "Unknown", False
    return city, True


def city_stat(stats: dict[str, dict[str, Any]], city: str) -> dict[str, Any]:
    return stats.setdefault(
        city,
        {
            "city": city,
            "region": city,
            "trade_count": 0,
            "trade_ratio": 0.0,
            "buy_trade_count": 0,
            "sell_trade_count": 0,
            "buy_amount": 0.0,
            "sell_amount": 0.0,
            "net_trade_cashflow": 0.0,
            "closed_position_count": 0,
            "resolved_closed_pnl_count": 0,
            "missing_closed_pnl_count": 0,
            "realized_pnl": 0.0,
            "closed_total_bought": 0.0,
            "closed_profit_multiple": 0.0,
        },
    )


def top_city_records(
    cities: Sequence[Mapping[str, Any]],
    field: str,
    limit: int,
) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in sorted(
            cities,
            key=lambda item: (to_float(item.get(field)), to_float(item.get("trade_count"))),
            reverse=True,
        )
        if to_float(record.get(field)) != 0.0
    ][:limit]


def closed_position_record(
    record: Mapping[str, Any],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
) -> dict[str, Any]:
    city, _known = record_city(record, region_fields=region_fields)
    total_bought = first_number(record, ("totalBought", "initialValue", "cashSpent", "cost"))
    total_bought = total_bought if total_bought is not None else 0.0
    realized_pnl = to_float(get_field_value(record, "realizedPnl"))
    return {
        "city": city,
        "region": city,
        "title": first_text(record, ("title", "question", "slug")) or "",
        "outcome": first_text(record, ("outcome", "outcomeName", "outcome_name")) or "",
        "condition_id": str(get_field_value(record, "conditionId") or ""),
        "slug": first_text(record, ("slug", "marketSlug", "market_slug")) or "",
        "end_date": first_text(record, ("endDate", "end_date")) or "",
        "realized_pnl": realized_pnl,
        "total_bought": total_bought,
        "profit_multiple": profit_multiple(total_bought, profit=realized_pnl),
    }


def trade_frequency_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    timestamp_field: str = "timestamp",
) -> dict[str, Any]:
    """Summarize trading cadence from epoch seconds or ISO datetime strings."""
    rows = list(records or [])
    timestamps = [
        parsed
        for parsed in (parse_datetime_value(get_field_value(record, timestamp_field)) for record in rows)
        if parsed is not None
    ]
    timestamps.sort()

    by_day = Counter(timestamp.date().isoformat() for timestamp in timestamps)
    by_hour_utc = Counter(timestamp.hour for timestamp in timestamps)
    by_weekday = Counter(timestamp.strftime("%A") for timestamp in timestamps)

    first = timestamps[0] if timestamps else None
    last = timestamps[-1] if timestamps else None
    calendar_days = 0
    if first is not None and last is not None:
        calendar_days = (last.date() - first.date()).days + 1

    gaps_hours = [
        (later - earlier).total_seconds() / 3600.0
        for earlier, later in zip(timestamps, timestamps[1:])
    ]

    return {
        "total_count": len(rows),
        "timestamp_count": len(timestamps),
        "missing_timestamp_count": len(rows) - len(timestamps),
        "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
        "calendar_day_count": calendar_days,
        "active_day_count": len(by_day),
        "trades_per_active_day": ratio(len(timestamps), len(by_day)),
        "trades_per_calendar_day": ratio(len(timestamps), calendar_days),
        "max_trades_per_day": max(by_day.values()) if by_day else 0,
        "median_gap_hours": median(gaps_hours),
        "by_day": dict(sorted(by_day.items())),
        "by_hour_utc": {hour: by_hour_utc.get(hour, 0) for hour in range(24)},
        "by_weekday": dict(sorted(by_weekday.items())),
    }


def filter_records(
    records: Iterable[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]] | None = None,
    *,
    match: str = "all",
) -> list[Mapping[str, Any]]:
    """Return records matching declarative conditions."""
    rows = list(records or [])
    if not conditions:
        return rows

    mode = str(match or "all").lower()
    if mode not in {"all", "any"}:
        raise ValueError("match must be 'all' or 'any'")

    selected = []
    for record in rows:
        results = [condition_matches(record, condition) for condition in conditions]
        if (mode == "all" and all(results)) or (mode == "any" and any(results)):
            selected.append(record)
    return selected


def condition_matches(record: Mapping[str, Any], condition: Mapping[str, Any]) -> bool:
    """Evaluate one filter condition against a record."""
    op = str(condition.get("op", "==")).lower()

    if op in {"tag_matches", "tags_match"}:
        return record_matches_tags(
            record,
            condition.get("value", []),
            fields=condition.get("fields", DEFAULT_TAG_FIELDS),
            match=str(condition.get("match", "any")),
        )

    field = str(condition.get("field", ""))
    actual = get_field_value(record, field)
    target = condition.get("value")

    if op in {"exists", "present"}:
        return actual not in (None, "")
    if op in {"missing", "not_exists"}:
        return actual in (None, "")

    if op in {">", ">=", "<", "<=", "between"}:
        return _numeric_condition(actual, target, op)

    if op == "contains":
        return _contains(actual, target)
    if op in {"not_contains", "excludes"}:
        return not _contains(actual, target)
    if op == "in":
        return _in(actual, target)
    if op == "not_in":
        return not _in(actual, target)
    if op == "startswith":
        return str(actual or "").lower().startswith(str(target or "").lower())
    if op == "endswith":
        return str(actual or "").lower().endswith(str(target or "").lower())
    if op in {"!=", "ne"}:
        return normalize_scalar(actual) != normalize_scalar(target)
    return normalize_scalar(actual) == normalize_scalar(target)


def record_matches_tags(
    record: Mapping[str, Any],
    tags: Any,
    *,
    fields: Sequence[str] = DEFAULT_TAG_FIELDS,
    match: str = "any",
) -> bool:
    """Match requested tags against structured tag fields and searchable text."""
    wanted = normalize_tags(tags)
    if not wanted:
        return True

    candidates: set[str] = set()
    for field in fields:
        candidates.update(normalize_tags(get_field_value(record, field)))

    mode = str(match or "any").lower()
    if mode == "all":
        return all(_tag_is_present(tag, candidates) for tag in wanted)
    if mode != "any":
        raise ValueError("match must be 'any' or 'all'")
    return any(_tag_is_present(tag, candidates) for tag in wanted)


def tag_match_summary(
    records: Iterable[Mapping[str, Any]],
    tags: Any,
    *,
    fields: Sequence[str] = DEFAULT_TAG_FIELDS,
) -> dict[str, Any]:
    """Count how often each target tag matches the supplied records."""
    rows = list(records or [])
    wanted = sorted(normalize_tags(tags))
    per_tag = {
        tag: sum(1 for record in rows if record_matches_tags(record, [tag], fields=fields))
        for tag in wanted
    }
    matched_records = [
        record for record in rows if record_matches_tags(record, wanted, fields=fields)
    ]
    return {
        "total_count": len(rows),
        "matched_count": len(matched_records),
        "matched_ratio": ratio(len(matched_records), len(rows)),
        "per_tag": per_tag,
    }


def regional_trade_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    timestamp_field: str = "timestamp",
    dominance_threshold: float = 0.6,
    collapse_by_day: bool = False,
) -> dict[str, Any]:
    """Summarize regional dominance from raw trades or unique region-day samples."""
    rows = list(records or [])
    if collapse_by_day:
        region_days = _regional_day_amount_groups(
            rows,
            region_fields=region_fields,
            timestamp_field=timestamp_field,
            side_field="side",
            amount_fields=DEFAULT_NOTIONAL_FIELDS,
        )
        region_stats: dict[str, dict[str, Any]] = {}
        unknown_region_count = 0
        missing_timestamp_count = 0

        for record in rows:
            region = first_text(record, region_fields)
            if region is None:
                unknown_region_count += 1
                continue
            if parse_datetime_value(get_field_value(record, timestamp_field)) is None:
                missing_timestamp_count += 1

        for day in region_days:
            region = str(day["region"])
            stat = region_stats.setdefault(
                region,
                {
                    "region": region,
                    "trade_count": 0,
                    "trade_ratio": 0.0,
                    "raw_trade_count": 0,
                    "buy_trade_count": 0,
                    "sell_trade_count": 0,
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "first_date": "",
                    "last_date": "",
                },
            )
            stat["trade_count"] += 1
            stat["raw_trade_count"] += int(day.get("trade_count") or 0)
            stat["buy_trade_count"] += int(day.get("buy_trade_count") or 0)
            stat["sell_trade_count"] += int(day.get("sell_trade_count") or 0)
            stat["buy_amount"] += to_float(day.get("buy_amount"))
            stat["sell_amount"] += to_float(day.get("sell_amount"))
            date = str(day.get("date") or "")
            if date and (not stat["first_date"] or date < stat["first_date"]):
                stat["first_date"] = date
            if date and (not stat["last_date"] or date > stat["last_date"]):
                stat["last_date"] = date

        regions = list(region_stats.values())
        for stat in regions:
            stat["trade_ratio"] = ratio(stat["trade_count"], len(region_days))

        regions.sort(
            key=lambda item: (
                item["trade_ratio"],
                item["trade_count"],
                item["raw_trade_count"],
            ),
            reverse=True,
        )
        ratios = [item["trade_ratio"] for item in regions]
        ratio_spread = (max(ratios) - min(ratios)) if ratios else 0.0
        dominant = regions[0] if regions else {}
        dominant_ratio = float(dominant.get("trade_ratio", 0.0))

        return {
            "total_count": len(region_days),
            "raw_total_count": len(rows),
            "known_region_count": len(rows) - unknown_region_count,
            "unknown_region_count": unknown_region_count,
            "missing_timestamp_count": missing_timestamp_count,
            "region_count": len(regions),
            "region_day_count": len(region_days),
            "count_mode": "region_day",
            "regions": regions,
            "dominant_region": dominant.get("region", ""),
            "dominant_region_day_count": dominant.get("trade_count", 0),
            "dominant_region_trade_count": dominant.get("trade_count", 0),
            "dominant_region_trade_ratio": dominant_ratio,
            "dominant_region_raw_trade_count": dominant.get("raw_trade_count", 0),
            "region_trade_ratio_spread": ratio_spread,
            "is_balanced_without_dominant_region": bool(
                regions and dominant_ratio < dominance_threshold and ratio_spread <= 0.1000000001
            ),
            "matched_high_frequency_region": dominant_ratio >= dominance_threshold,
        }

    counts: Counter[str] = Counter()
    unknown_region_count = 0

    for record in rows:
        region = first_text(record, region_fields)
        if region is None:
            unknown_region_count += 1
            continue
        counts[region] += 1

    regions = [
        {
            "region": region,
            "trade_count": count,
            "trade_ratio": ratio(count, len(rows)),
        }
        for region, count in counts.most_common()
    ]
    ratios = [item["trade_ratio"] for item in regions]
    ratio_spread = (max(ratios) - min(ratios)) if ratios else 0.0
    dominant = regions[0] if regions else {}
    dominant_ratio = float(dominant.get("trade_ratio", 0.0))

    return {
        "total_count": len(rows),
        "known_region_count": len(rows) - unknown_region_count,
        "unknown_region_count": unknown_region_count,
        "region_count": len(regions),
        "count_mode": "trade",
        "regions": regions,
        "dominant_region": dominant.get("region", ""),
        "dominant_region_trade_count": dominant.get("trade_count", 0),
        "dominant_region_trade_ratio": dominant_ratio,
        "region_trade_ratio_spread": ratio_spread,
        "is_balanced_without_dominant_region": bool(
            regions and dominant_ratio < dominance_threshold and ratio_spread <= 0.1000000001
        ),
        "matched_high_frequency_region": dominant_ratio >= dominance_threshold,
    }


def regional_daily_profit_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    timestamp_field: str = "timestamp",
    side_field: str = "side",
    amount_fields: Sequence[str] = DEFAULT_NOTIONAL_FIELDS,
    profit_multiple_threshold: float = 2.0,
) -> dict[str, Any]:
    """Aggregate same-day buy/sell amounts by region and flag >2x days."""
    rows = list(records or [])
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    missing_region_count = 0
    missing_timestamp_count = 0

    for record in rows:
        region = first_text(record, region_fields)
        if region is None:
            missing_region_count += 1
            continue

        timestamp = parse_datetime_value(get_field_value(record, timestamp_field))
        if timestamp is None:
            missing_timestamp_count += 1
            continue

        date = timestamp.date().isoformat()
        key = (region, date)
        group = groups.setdefault(
            key,
            {
                "region": region,
                "date": date,
                "trade_count": 0,
                "buy_trade_count": 0,
                "sell_trade_count": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "profit_multiple": 0.0,
            },
        )
        group["trade_count"] += 1

        side = str(get_field_value(record, side_field) or "").upper()
        amount = record_notional(record, amount_fields=amount_fields)
        if side == "BUY":
            group["buy_trade_count"] += 1
            group["buy_amount"] += amount
        elif side == "SELL":
            group["sell_trade_count"] += 1
            group["sell_amount"] += amount

    region_days = list(groups.values())
    for group in region_days:
        group["profit_multiple"] = ratio(group["sell_amount"], group["buy_amount"])

    region_days.sort(
        key=lambda item: (item["profit_multiple"], item["sell_amount"], item["trade_count"]),
        reverse=True,
    )
    qualified = [
        item
        for item in region_days
        if item["profit_multiple"] > profit_multiple_threshold
    ]
    max_day = region_days[0] if region_days else {}

    return {
        "total_count": len(rows),
        "region_day_count": len(region_days),
        "missing_region_count": missing_region_count,
        "missing_timestamp_count": missing_timestamp_count,
        "profit_multiple_threshold": profit_multiple_threshold,
        "qualified_count": len(qualified),
        "max_region": max_day.get("region", ""),
        "max_date": max_day.get("date", ""),
        "max_profit_multiple": max_day.get("profit_multiple", 0.0),
        "max_buy_amount": max_day.get("buy_amount", 0.0),
        "max_sell_amount": max_day.get("sell_amount", 0.0),
        "matched_high_daily_region_profit": bool(qualified),
        "qualified_region_days": qualified,
        "region_days": region_days,
    }


def regional_day_win_rate_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    timestamp_field: str = "timestamp",
    side_field: str = "side",
    amount_fields: Sequence[str] = DEFAULT_NOTIONAL_FIELDS,
    win_rate_threshold: float = 0.6,
    min_trade_count: int = 3,
) -> dict[str, Any]:
    """Count positive-return trading days by region."""
    region_days = _regional_day_amount_groups(
        records,
        region_fields=region_fields,
        timestamp_field=timestamp_field,
        side_field=side_field,
        amount_fields=amount_fields,
    )
    region_stats: dict[str, dict[str, Any]] = {}

    for day in region_days:
        region = str(day["region"])
        stat = region_stats.setdefault(
            region,
            {
                "region": region,
                "total_trade_days": 0,
                "positive_return_days": 0,
                "positive_return_day_ratio": 0.0,
                "trade_count": 0,
                "buy_trade_count": 0,
                "sell_trade_count": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
            },
        )
        stat["total_trade_days"] += 1
        stat["trade_count"] += int(day.get("trade_count") or 0)
        stat["buy_trade_count"] += int(day.get("buy_trade_count") or 0)
        stat["sell_trade_count"] += int(day.get("sell_trade_count") or 0)
        stat["buy_amount"] += to_float(day.get("buy_amount"))
        stat["sell_amount"] += to_float(day.get("sell_amount"))
        if day["sell_amount"] > day["buy_amount"]:
            stat["positive_return_days"] += 1

    regions = list(region_stats.values())
    for stat in regions:
        stat["positive_return_day_ratio"] = ratio(
            stat["positive_return_days"],
            stat["total_trade_days"],
        )

    regions.sort(
        key=lambda item: (
            item["positive_return_day_ratio"],
            item["positive_return_days"],
            item["trade_count"],
            item["total_trade_days"],
        ),
        reverse=True,
    )
    qualified = [
        item
        for item in regions
        if item["positive_return_day_ratio"] >= win_rate_threshold
        and item["trade_count"] >= min_trade_count
    ]
    best = qualified[0] if qualified else (regions[0] if regions else {})

    return {
        "region_count": len(regions),
        "region_day_count": len(region_days),
        "win_rate_threshold": win_rate_threshold,
        "min_trade_count": min_trade_count,
        "qualified_count": len(qualified),
        "best_region": best.get("region", ""),
        "best_positive_return_days": best.get("positive_return_days", 0),
        "best_total_trade_days": best.get("total_trade_days", 0),
        "best_positive_return_day_ratio": best.get("positive_return_day_ratio", 0.0),
        "best_trade_count": best.get("trade_count", 0),
        "matched_regional_high_win_rate": bool(qualified),
        "qualified_regions": qualified,
        "regions": regions,
    }


def low_chip_cost_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    cost_fields: Sequence[str] = DEFAULT_CHIP_COST_FIELDS,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    timestamp_field: str = "timestamp",
    side_field: str = "side",
    threshold: float = 30.0,
) -> dict[str, Any]:
    """Summarize trades whose chip cost is below the configured threshold."""
    rows = list(records or [])
    low_count = 0
    cost_count = 0
    missing_cost_count = 0
    normalized_costs: list[float] = []
    region_counts: dict[str, dict[str, Any]] = {}
    low_chip_records: list[dict[str, Any]] = []

    for record in rows:
        cost = first_number(record, cost_fields)
        if cost is None:
            missing_cost_count += 1
            continue
        cost_count += 1
        normalized = normalize_chip_cost(cost)
        normalized_costs.append(normalized)
        if normalized < threshold:
            low_count += 1
            region = first_text(record, region_fields) or ""
            if region:
                region_stat = region_counts.setdefault(
                    region,
                    {
                        "region": region,
                        "low_chip_cost_count": 0,
                        "low_chip_cost_ratio": 0.0,
                    },
                )
                region_stat["low_chip_cost_count"] += 1
            timestamp = parse_datetime_value(get_field_value(record, timestamp_field))
            low_chip_records.append(
                {
                    "region": region,
                    "city": region,
                    "date": timestamp.date().isoformat() if timestamp else "",
                    "side": str(get_field_value(record, side_field) or "").upper(),
                    "chip_cost": normalized,
                    "notional": record_notional(record),
                    "title": first_text(record, ("title", "question", "slug")) or "",
                    "slug": first_text(record, ("slug", "marketSlug", "market_slug")) or "",
                    "condition_id": str(get_field_value(record, "conditionId") or ""),
                }
            )

    low_ratio = ratio(low_count, len(rows))
    low_chip_regions = list(region_counts.values())
    for region_stat in low_chip_regions:
        region_stat["low_chip_cost_ratio"] = ratio(
            region_stat["low_chip_cost_count"],
            low_count,
        )
    low_chip_regions.sort(
        key=lambda item: (item["low_chip_cost_count"], item["low_chip_cost_ratio"]),
        reverse=True,
    )
    low_chip_records.sort(
        key=lambda item: (
            float(item["chip_cost"]),
            -float(item["notional"]),
        )
    )
    top_region = low_chip_regions[0] if low_chip_regions else {}
    return {
        "total_count": len(rows),
        "cost_count": cost_count,
        "missing_cost_count": missing_cost_count,
        "threshold": threshold,
        "low_chip_cost_count": low_count,
        "low_chip_cost_ratio": low_ratio,
        "matched_lottery_player": low_ratio > 0.5,
        "min_chip_cost": min(normalized_costs) if normalized_costs else 0.0,
        "median_chip_cost": median(normalized_costs),
        "max_chip_cost": max(normalized_costs) if normalized_costs else 0.0,
        "top_low_chip_region": top_region.get("region", ""),
        "top_low_chip_region_count": top_region.get("low_chip_cost_count", 0),
        "top_low_chip_region_ratio": top_region.get("low_chip_cost_ratio", 0.0),
        "low_chip_regions": low_chip_regions,
        "low_chip_records": low_chip_records[:10],
    }


def liquidity_player_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    activity_records: Iterable[Mapping[str, Any]] | None = None,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    timestamp_field: str = "timestamp",
    side_field: str = "side",
    swap_type_fields: Sequence[str] = DEFAULT_SWAP_TYPE_FIELDS,
    swap_ratio_threshold: float = 0.1,
    sell_dominant_threshold: float = 0.5,
) -> dict[str, Any]:
    """Flag low-swap traders whose region-days are mostly sell-led."""
    rows = list(records or [])
    activity_rows = list(activity_records or [])
    swap_source_rows = activity_rows if activity_rows else rows
    swap_count = sum(
        1 for record in swap_source_rows if record_is_swap(record, fields=swap_type_fields)
    )
    swap_ratio = ratio(swap_count, len(rows))
    low_swap_activity = swap_count == 0 or swap_ratio < swap_ratio_threshold

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    trade_dates: set[str] = set()
    missing_region_count = 0
    missing_timestamp_count = 0

    for record in rows:
        timestamp = parse_datetime_value(get_field_value(record, timestamp_field))
        if timestamp is None:
            missing_timestamp_count += 1
            continue
        date = timestamp.date().isoformat()
        trade_dates.add(date)

        region = first_text(record, region_fields)
        if region is None:
            missing_region_count += 1
            continue

        key = (region, date)
        group = groups.setdefault(
            key,
            {
                "region": region,
                "date": date,
                "trade_count": 0,
                "sell_trade_count": 0,
                "sell_trade_ratio": 0.0,
                "sell_dominant": False,
            },
        )
        group["trade_count"] += 1
        if record_is_sell(record, side_field=side_field):
            group["sell_trade_count"] += 1

    region_days = list(groups.values())
    for group in region_days:
        group["sell_trade_ratio"] = ratio(group["sell_trade_count"], group["trade_count"])
        group["sell_dominant"] = group["sell_trade_ratio"] > sell_dominant_threshold

    region_days.sort(
        key=lambda item: (item["sell_trade_ratio"], item["sell_trade_count"], item["trade_count"]),
        reverse=True,
    )
    sell_dominant_region_days = [item for item in region_days if item["sell_dominant"]]
    sell_dominant_dates = {str(item.get("date", "")) for item in sell_dominant_region_days}
    sell_dominant_dates.discard("")
    sell_dominant_day_ratio = ratio(len(sell_dominant_dates), len(trade_dates))
    top_day = sell_dominant_region_days[0] if sell_dominant_region_days else {}

    return {
        "total_count": len(rows),
        "activity_count": len(activity_rows),
        "swap_count": swap_count,
        "swap_ratio": swap_ratio,
        "swap_ratio_threshold": swap_ratio_threshold,
        "low_swap_activity": low_swap_activity,
        "unique_trade_day_count": len(trade_dates),
        "regional_trade_day_count": len(region_days),
        "missing_region_count": missing_region_count,
        "missing_timestamp_count": missing_timestamp_count,
        "sell_dominant_threshold": sell_dominant_threshold,
        "sell_dominant_region_day_count": len(sell_dominant_region_days),
        "sell_dominant_day_count": len(sell_dominant_dates),
        "sell_dominant_region_day_ratio": sell_dominant_day_ratio,
        "top_sell_dominant_region": top_day.get("region", ""),
        "top_sell_dominant_date": top_day.get("date", ""),
        "matched_liquidity_player": bool(
            rows and low_swap_activity and sell_dominant_day_ratio > 0.5
        ),
        "sell_dominant_region_days": sell_dominant_region_days,
        "region_days": region_days,
    }


def recent_activity_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    timestamp_field: str = "timestamp",
    active_days: int = 3,
    normal_active_days: int = 1,
) -> dict[str, Any]:
    """Summarize latest trade recency by date."""
    rows = list(records or [])
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    timestamps = [
        parsed
        for parsed in (parse_datetime_value(get_field_value(record, timestamp_field)) for record in rows)
        if parsed is not None
    ]
    if not timestamps:
        return {
            "total_count": len(rows),
            "timestamp_count": 0,
            "missing_timestamp_count": len(rows),
            "current_date": current.date().isoformat(),
            "latest_trade_datetime": None,
            "latest_trade_date": None,
            "days_since_latest_trade": 999999,
            "active_days": active_days,
            "normal_active_days": normal_active_days,
            "activity_level": "inactive",
            "matched_recent_active": False,
        }

    latest = max(timestamps)
    days_since_latest_trade = max(0, (current.date() - latest.date()).days)
    if days_since_latest_trade <= normal_active_days:
        activity_level = "normal_active"
    elif days_since_latest_trade <= active_days:
        activity_level = "low_active"
    else:
        activity_level = "inactive"

    return {
        "total_count": len(rows),
        "timestamp_count": len(timestamps),
        "missing_timestamp_count": len(rows) - len(timestamps),
        "current_date": current.date().isoformat(),
        "latest_trade_datetime": latest.isoformat(),
        "latest_trade_date": latest.date().isoformat(),
        "days_since_latest_trade": days_since_latest_trade,
        "active_days": active_days,
        "normal_active_days": normal_active_days,
        "activity_level": activity_level,
        "matched_recent_active": activity_level != "inactive",
    }


def wallet_age_summary(
    registration_datetime: Any,
    *,
    now: datetime | None = None,
    source: str = "",
    new_wallet_days: int = 60,
    hidden_new_wallet_days: int = 10,
) -> dict[str, Any]:
    """Summarize wallet age from a supported registration/first-seen datetime."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)

    registered = parse_datetime_value(registration_datetime)
    if registered is None:
        return {
            "source": source or "missing",
            "current_date": current.date().isoformat(),
            "registration_datetime": None,
            "registration_date": None,
            "wallet_age_days": 999999,
            "new_wallet_days": new_wallet_days,
            "hidden_new_wallet_days": hidden_new_wallet_days,
            "status": "missing_registration_date",
            "matched_new_wallet": False,
            "matched_hidden_new_wallet": False,
        }

    age_days = (current.date() - registered.date()).days
    if age_days < 0:
        return {
            "source": source or "unknown",
            "current_date": current.date().isoformat(),
            "registration_datetime": registered.isoformat(),
            "registration_date": registered.date().isoformat(),
            "wallet_age_days": age_days,
            "new_wallet_days": new_wallet_days,
            "hidden_new_wallet_days": hidden_new_wallet_days,
            "status": "future_registration_date",
            "matched_new_wallet": False,
            "matched_hidden_new_wallet": False,
        }

    matched_hidden = age_days < hidden_new_wallet_days
    matched_new = hidden_new_wallet_days <= age_days < new_wallet_days
    return {
        "source": source or "unknown",
        "current_date": current.date().isoformat(),
        "registration_datetime": registered.isoformat(),
        "registration_date": registered.date().isoformat(),
        "wallet_age_days": age_days,
        "new_wallet_days": new_wallet_days,
        "hidden_new_wallet_days": hidden_new_wallet_days,
        "status": "resolved",
        "matched_new_wallet": matched_new,
        "matched_hidden_new_wallet": matched_hidden,
    }


def high_temperature_early_entry_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str] = DEFAULT_REGION_FIELDS,
    timestamp_field: str = "timestamp",
    side_field: str = "side",
    date_fields: Sequence[str] = DEFAULT_MARKET_DATE_FIELDS,
    text_fields: Sequence[str] = DEFAULT_MARKET_TEXT_FIELDS,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Measure high-temperature BUY records entered outside the target market day."""
    rows = list(records or [])
    high_temp_buy_count = 0
    analyzed_records: list[dict[str, Any]] = []
    off_day_records: list[dict[str, Any]] = []
    missing_timestamp_count = 0
    missing_market_date_count = 0
    buy_before_market_day_count = 0
    buy_after_market_day_count = 0

    for record in rows:
        if not record_is_buy(record, side_field=side_field):
            continue
        if not record_is_high_temperature_market(record, fields=text_fields):
            continue

        high_temp_buy_count += 1
        buy_datetime = parse_datetime_value(get_field_value(record, timestamp_field))
        if buy_datetime is None:
            missing_timestamp_count += 1
            continue

        market_date = record_market_date(record, fields=date_fields, text_fields=text_fields)
        if market_date is None:
            missing_market_date_count += 1
            continue

        day_difference = (buy_datetime.date() - market_date).days
        if day_difference < 0:
            buy_before_market_day_count += 1
        elif day_difference > 0:
            buy_after_market_day_count += 1

        evidence = {
            "region": first_text(record, region_fields) or "",
            "title": first_text(record, ("title", "question", "slug")) or "",
            "slug": first_text(record, ("slug", "marketSlug", "market_slug")) or "",
            "condition_id": str(get_field_value(record, "conditionId") or ""),
            "buy_datetime": buy_datetime.isoformat(),
            "buy_date": buy_datetime.date().isoformat(),
            "high_temperature_date": market_date.isoformat(),
            "day_difference": day_difference,
            "off_day": day_difference != 0,
            "notional": record_notional(record),
        }
        analyzed_records.append(evidence)
        if evidence["off_day"]:
            off_day_records.append(evidence)

    off_day_ratio = ratio(len(off_day_records), len(analyzed_records))
    off_day_records.sort(
        key=lambda item: (
            abs(int(item.get("day_difference") or 0)),
            float(item.get("notional") or 0),
        ),
        reverse=True,
    )
    return {
        "total_count": len(rows),
        "threshold": threshold,
        "high_temperature_buy_count": high_temp_buy_count,
        "analyzed_buy_count": len(analyzed_records),
        "missing_timestamp_count": missing_timestamp_count,
        "missing_market_date_count": missing_market_date_count,
        "off_day_buy_count": len(off_day_records),
        "same_day_buy_count": len(analyzed_records) - len(off_day_records),
        "off_day_buy_ratio": off_day_ratio,
        "buy_before_market_day_count": buy_before_market_day_count,
        "buy_after_market_day_count": buy_after_market_day_count,
        "matched_early_positioning": bool(analyzed_records and off_day_ratio > threshold),
        "top_off_day_buy_records": off_day_records[:10],
        "off_day_buy_records": off_day_records,
        "analyzed_records": analyzed_records,
    }


def get_field_value(record: Mapping[str, Any], field: str, default: Any = None) -> Any:
    """Read a field from a mapping, supporting dotted paths and list indexes."""
    if not field:
        return default
    current: Any = record
    for part in field.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


def normalize_tags(value: Any) -> set[str]:
    tags: set[str] = set()
    if value in (None, ""):
        return tags
    if isinstance(value, Mapping):
        for key in ("slug", "label", "name", "title", "id", "key", "value"):
            if key in value:
                tags.update(normalize_tags(value[key]))
        return tags
    if isinstance(value, str):
        for token in value.replace("_", "-").split(","):
            normalized = _normalize_text(token)
            if normalized:
                tags.add(normalized)
        return tags
    if isinstance(value, Iterable):
        for item in value:
            tags.update(normalize_tags(item))
        return tags
    normalized = _normalize_text(value)
    if normalized:
        tags.add(normalized)
    return tags


def first_number(record: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    for field in fields:
        value = get_field_value(record, field)
        if value in (None, ""):
            continue
        number = _coerce_float(value)
        if number is not None:
            return number
    return None


def first_text(record: Mapping[str, Any], fields: Sequence[str]) -> str | None:
    for field in fields:
        value = get_field_value(record, field)
        if value in (None, "") or isinstance(value, Mapping):
            continue
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            continue
        text = " ".join(str(value).strip().split())
        if text:
            return text
    return None


def record_notional(
    record: Mapping[str, Any],
    *,
    amount_fields: Sequence[str] = DEFAULT_NOTIONAL_FIELDS,
) -> float:
    explicit = first_number(record, amount_fields)
    if explicit is not None and explicit > 0:
        return explicit

    size = first_number(record, ("size", "totalBought", "shares"))
    price = first_number(record, ("price", "avgPrice"))
    if size is not None and price is not None and size > 0 and price > 0:
        return size * price

    fallback = first_number(record, ("currentValue", "initialValue", "totalBought"))
    if fallback is not None and fallback > 0:
        return fallback
    return 0.0


def record_is_swap(
    record: Mapping[str, Any],
    *,
    fields: Sequence[str] = DEFAULT_SWAP_TYPE_FIELDS,
) -> bool:
    for field in fields:
        value = get_field_value(record, field)
        if value in (None, ""):
            continue
        text = str(value).strip().lower().replace("_", " ").replace("-", " ")
        if "swap" in text:
            return True
    return False


def record_is_sell(record: Mapping[str, Any], *, side_field: str = "side") -> bool:
    side = str(get_field_value(record, side_field) or "").strip().lower()
    return side in {"sell", "sold", "\u5df2\u5356\u51fa"} or side.startswith("sell")


def record_is_buy(record: Mapping[str, Any], *, side_field: str = "side") -> bool:
    side = str(get_field_value(record, side_field) or "").strip().lower()
    return side in {"buy", "bought", "\u4e70\u5165", "\u5df2\u4e70\u5165"} or side.startswith("buy")


def record_is_high_temperature_market(
    record: Mapping[str, Any],
    *,
    fields: Sequence[str] = DEFAULT_MARKET_TEXT_FIELDS,
) -> bool:
    text = " ".join(_flatten_search_text(get_field_value(record, field)) for field in fields)
    normalized = " ".join(text.lower().replace("_", " ").replace("-", " ").split())
    return any(
        phrase in normalized
        for phrase in (
            "highest temperature",
            "highest temp",
            "high temperature",
            "high temp",
            "maximum temperature",
            "maximum temp",
            "max temperature",
            "max temp",
            "daily high",
        )
    )


def record_market_date(
    record: Mapping[str, Any],
    *,
    fields: Sequence[str] = DEFAULT_MARKET_DATE_FIELDS,
    text_fields: Sequence[str] = DEFAULT_MARKET_TEXT_FIELDS,
) -> date | None:
    for field in fields:
        value = get_field_value(record, field)
        parsed = parse_date_value(value)
        if parsed is not None:
            return parsed

    for field in text_fields:
        parsed = parse_date_from_text(get_field_value(record, field))
        if parsed is not None:
            return parsed
    return None


def parse_date_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    parsed_datetime = parse_datetime_value(value)
    if parsed_datetime is not None:
        return parsed_datetime.date()
    return parse_date_from_text(value)


def parse_date_from_text(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = " ".join(str(value).replace("_", " ").replace("-", " ").split())

    match = re.search(r"\b(20\d{2})[ /\-.](\d{1,2})[ /\-.](\d{1,2})\b", text)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = re.search(r"\b(\d{1,2})[ /\-.](\d{1,2})[ /\-.](20\d{2})\b", text)
    if match:
        return _safe_date(int(match.group(3)), int(match.group(1)), int(match.group(2)))

    month_names = "|".join(sorted(MONTH_LOOKUP, key=len, reverse=True))
    match = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return _safe_date(
            int(match.group(3)),
            MONTH_LOOKUP[match.group(1).lower()],
            int(match.group(2)),
        )

    match = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})[,]?\s+(20\d{{2}})\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return _safe_date(
            int(match.group(3)),
            MONTH_LOOKUP[match.group(2).lower()],
            int(match.group(1)),
        )
    return None


def normalize_chip_cost(value: Any) -> float:
    cost = to_float(value)
    if 0 < cost <= 1:
        return cost * 100
    return cost


def to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return number


def ratio(numerator: Any, denominator: Any) -> float:
    denominator_value = to_float(denominator)
    if denominator_value == 0:
        return 0.0
    return to_float(numerator) / denominator_value


def median(values: Iterable[Any]) -> float:
    cleaned = [to_float(value) for value in values if value not in (None, "")]
    if not cleaned:
        return 0.0
    return float(statistics.median(cleaned))


def parse_datetime_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        numeric = to_float(text)
        if numeric > 0 and text.replace(".", "", 1).isdigit():
            parsed = datetime.fromtimestamp(numeric, tz=UTC)
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return ""
        number = _coerce_float(text)
        if number is not None:
            return number
        return text.lower()
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _empty_cost_bucket(
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> dict[str, Any]:
    return {
        "min": min_value,
        "max": max_value,
        "count": 0,
        "total_size": 0.0,
        "total_cost": 0.0,
        "weighted_average_cost": 0.0,
    }


def _cost_bucket_for(
    price: float,
    buckets: Sequence[dict[str, Any]],
    underflow: dict[str, Any],
    overflow: dict[str, Any],
) -> dict[str, Any]:
    for index, bucket in enumerate(buckets):
        lower = bucket["min"]
        upper = bucket["max"]
        is_last = index == len(buckets) - 1
        if lower <= price < upper or (is_last and lower <= price <= upper):
            return bucket
    if price < buckets[0]["min"]:
        return underflow
    return overflow


def _numeric_condition(actual: Any, target: Any, op: str) -> bool:
    actual_value = _coerce_float(actual)
    if actual_value is None:
        return False
    if op == "between":
        if not isinstance(target, Sequence) or isinstance(target, (str, bytes, bytearray)):
            return False
        if len(target) != 2:
            return False
        low = _coerce_float(target[0])
        high = _coerce_float(target[1])
        if low is None or high is None:
            return False
        return low <= actual_value <= high

    target_value = _coerce_float(target)
    if target_value is None:
        return False
    if op == ">":
        return actual_value > target_value
    if op == ">=":
        return actual_value >= target_value
    if op == "<":
        return actual_value < target_value
    return actual_value <= target_value


def _contains(actual: Any, target: Any) -> bool:
    if isinstance(actual, str):
        return str(target or "").lower() in actual.lower()
    if isinstance(actual, Iterable):
        normalized_target = normalize_scalar(target)
        return any(normalize_scalar(item) == normalized_target for item in actual)
    return normalize_scalar(actual) == normalize_scalar(target)


def _in(actual: Any, target: Any) -> bool:
    if isinstance(target, str) or not isinstance(target, Iterable):
        target_values = [target]
    else:
        target_values = list(target)
    normalized_actual = normalize_scalar(actual)
    return any(normalize_scalar(item) == normalized_actual for item in target_values)


def _tag_is_present(tag: str, candidates: set[str]) -> bool:
    return any(tag == candidate or tag in candidate for candidate in candidates)


def _normalize_text(value: Any) -> str:
    return str(value).strip().lower().replace("_", "-")


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _flatten_search_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, Mapping):
        return " ".join(_flatten_search_text(item) for item in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_flatten_search_text(item) for item in value)
    return str(value)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _regional_day_amount_groups(
    records: Iterable[Mapping[str, Any]],
    *,
    region_fields: Sequence[str],
    timestamp_field: str,
    side_field: str,
    amount_fields: Sequence[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}

    for record in records or []:
        region = first_text(record, region_fields)
        if region is None:
            continue

        timestamp = parse_datetime_value(get_field_value(record, timestamp_field))
        if timestamp is None:
            continue

        date = timestamp.date().isoformat()
        key = (region, date)
        group = groups.setdefault(
            key,
            {
                "region": region,
                "date": date,
                "trade_count": 0,
                "buy_trade_count": 0,
                "sell_trade_count": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
            },
        )
        group["trade_count"] += 1

        side = str(get_field_value(record, side_field) or "").upper()
        amount = record_notional(record, amount_fields=amount_fields)
        if side == "BUY":
            group["buy_trade_count"] += 1
            group["buy_amount"] += amount
        elif side == "SELL":
            group["sell_trade_count"] += 1
            group["sell_amount"] += amount

    return list(groups.values())


def infer_audit_caliber(
    record: Mapping[str, Any],
    *,
    bucket_fields: Sequence[str],
    liquidity_bucket_values: Sequence[str],
    settlement_bucket_values: Sequence[str],
    side_field: str,
    settlement_pnl_fields: Sequence[str],
    settlement_payout_fields: Sequence[str],
    settlement_cost_fields: Sequence[str],
) -> str:
    for field in bucket_fields:
        raw_value = get_field_value(record, field)
        if raw_value in (None, ""):
            continue
        normalized = _normalize_text(raw_value)
        if normalized in {_normalize_text(value) for value in liquidity_bucket_values}:
            return "trade_liquidity"
        if normalized in {_normalize_text(value) for value in settlement_bucket_values}:
            return "final_settlement"

    if record_is_buy(record, side_field=side_field) or record_is_sell(record, side_field=side_field):
        return "trade_liquidity"

    if any(
        get_field_value(record, field) not in (None, "")
        for field in (*settlement_pnl_fields, *settlement_payout_fields)
    ):
        return "final_settlement"

    if any(get_field_value(record, field) not in (None, "") for field in settlement_cost_fields):
        return "final_settlement"

    return ""


def _trade_liquidity_audit_summary(
    records: Sequence[Mapping[str, Any]],
    *,
    side_field: str,
    amount_fields: Sequence[str],
) -> dict[str, Any]:
    buy_trade_count = 0
    sell_trade_count = 0
    unknown_side_count = 0
    buy_amount = 0.0
    sell_amount = 0.0

    for record in records:
        amount = record_notional(record, amount_fields=amount_fields)
        if record_is_buy(record, side_field=side_field):
            buy_trade_count += 1
            buy_amount += amount
        elif record_is_sell(record, side_field=side_field):
            sell_trade_count += 1
            sell_amount += amount
        else:
            unknown_side_count += 1

    profit_amount = sell_amount - buy_amount
    return {
        "caliber": "trade_liquidity",
        "label": "trade_liquidity",
        "record_count": len(records),
        "buy_trade_count": buy_trade_count,
        "sell_trade_count": sell_trade_count,
        "unknown_side_count": unknown_side_count,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "cost_amount": buy_amount,
        "payout_amount": sell_amount,
        "profit_amount": profit_amount,
        "net_cashflow": profit_amount,
        "profit_multiple": profit_multiple(buy_amount, sell_amount),
        "resolved_count": 0,
        "missing_pnl_count": 0,
    }


def _final_settlement_audit_summary(
    records: Sequence[Mapping[str, Any]],
    *,
    cost_fields: Sequence[str],
    payout_fields: Sequence[str],
    pnl_fields: Sequence[str],
) -> dict[str, Any]:
    cost_amount = 0.0
    payout_amount = 0.0
    pnl_values: list[float] = []
    win_count = 0
    loss_count = 0
    push_count = 0
    missing_pnl_count = 0

    for record in records:
        cost = first_number(record, cost_fields)
        payout = first_number(record, payout_fields)
        pnl = first_number(record, pnl_fields)
        cost_value = cost if cost is not None else 0.0

        if payout is None and pnl is not None:
            payout = cost_value + pnl
        if pnl is None and payout is not None:
            pnl = payout - cost_value

        cost_amount += cost_value
        payout_amount += payout if payout is not None else 0.0

        if pnl is None:
            missing_pnl_count += 1
            continue

        pnl_values.append(pnl)
        if pnl > 0:
            win_count += 1
        elif pnl < 0:
            loss_count += 1
        else:
            push_count += 1

    resolved_count = len(pnl_values)
    profit_amount = sum(pnl_values)
    return {
        "caliber": "final_settlement",
        "label": "final_settlement",
        "record_count": len(records),
        "resolved_count": resolved_count,
        "missing_pnl_count": missing_pnl_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "push_count": push_count,
        "win_rate": ratio(win_count, resolved_count),
        "loss_rate": ratio(loss_count, resolved_count),
        "push_rate": ratio(push_count, resolved_count),
        "cost_amount": cost_amount,
        "payout_amount": payout_amount,
        "profit_amount": profit_amount,
        "total_bought": cost_amount,
        "total_realized_pnl": profit_amount,
        "average_pnl": ratio(profit_amount, resolved_count),
        "median_pnl": median(pnl_values),
        "profit_multiple": profit_multiple(cost_amount, payout_amount),
    }
