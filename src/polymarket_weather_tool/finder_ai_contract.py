from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


FINDER_AI_SOURCE_NAME = "finder"
FINDER_AI_PROVIDER = "deepseek"
FINDER_AI_MODEL = "deepseek-v4-flash"
FINDER_AI_GENERATION_SCOPE = "brief"
FINDER_AI_SCHEMA_VERSION = "finder-ai-v1"
FINDER_AI_PROMPT_VERSION = "finder-weather-brief-v6"


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    return ""


def safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def compact_text_list(values: Any, *, limit: int = 8, max_length: int = 180) -> list[str]:
    results: list[str] = []
    for item in safe_list(values):
        text = text_value(item)
        if not text:
            continue
        results.append(text[:max_length])
        if len(results) >= limit:
            break
    return results


def compact_trade_samples(values: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in safe_list(values):
        if not isinstance(item, Mapping):
            continue
        payload = compact_mapping(
            item,
            (
                "market_title",
                "market_slug",
                "condition_id",
                "event_slug",
                "city",
                "side",
                "size_usd",
                "entry_price",
                "current_price",
                "entered_at",
                "market_date",
                "outcome",
            ),
        )
        if not payload:
            continue
        results.append(payload)
        if len(results) >= limit:
            break
    return results


def compact_mapping(source: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    payload: dict[str, Any] = {}
    for key in keys:
        if key not in source:
            continue
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        payload[key] = value
    return payload


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def normalize_address(value: Any) -> str:
    text = text_value(value).lower()
    if not text:
        return ""
    return text if text.startswith("0x") else f"0x{text}"


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = text_value(value)
        if text:
            return text
    return ""


def build_finder_ai_primary_signals(label_evaluations: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in safe_list(label_evaluations):
        if not isinstance(item, Mapping) or not item.get("matched"):
            continue
        label = first_non_empty(
            item.get("display_name"),
            item.get("title"),
            item.get("name"),
            item.get("key"),
        )
        if not label:
            continue
        results.append(
            {
                "key": text_value(item.get("key")),
                "label": label,
                "matched": True,
                "reason": first_non_empty(item.get("reason"), safe_mapping(item.get("evidence")).get("reason")),
            }
        )
        if len(results) >= 6:
            break
    return results


def build_finder_ai_labels(labels: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in safe_list(labels):
        if not isinstance(item, Mapping):
            continue
        value = first_non_empty(
            item.get("display_name"),
            item.get("title"),
            item.get("name"),
            item.get("key"),
        )
        if not value:
            continue
        evidence = safe_mapping(item.get("evidence"))
        results.append(
            {
                "kind": "wallet_pattern",
                "value": value,
                "source": text_value(item.get("source")) or ("finder_system" if item.get("system_core") else "finder_rule"),
                "evidence": first_non_empty(evidence.get("reason"), item.get("reason"), item.get("description")),
            }
        )
        if len(results) >= 12:
            break
    return results


def build_finder_ai_key_metrics(
    selection_record: Mapping[str, Any],
    evidence_summary: Mapping[str, Any],
    metrics: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    metrics = safe_mapping(metrics)
    pnl_value = (
        selection_record.get("falcon_total_pnl")
        if selection_record.get("falcon_total_pnl") not in (None, "")
        else selection_record.get("display_pnl")
        if selection_record.get("display_pnl") not in (None, "")
        else metrics.get("falcon_total_pnl")
        if metrics.get("falcon_total_pnl") not in (None, "")
        else metrics.get("display_pnl")
    )
    win_rate_value = (
        selection_record.get("falcon_win_rate")
        if selection_record.get("falcon_win_rate") not in (None, "")
        else selection_record.get("display_win_rate")
        if selection_record.get("display_win_rate") not in (None, "")
        else metrics.get("falcon_win_rate")
        if metrics.get("falcon_win_rate") not in (None, "")
        else metrics.get("display_win_rate")
        if metrics.get("display_win_rate") not in (None, "")
        else None
    )
    win_rate_label = first_non_empty(
        selection_record.get("falcon_win_rate_window_label"),
        selection_record.get("display_win_rate_window_label"),
        metrics.get("falcon_win_rate_window_label"),
        metrics.get("display_win_rate_window_label"),
    )
    metric_specs = [
        ("pnl", "PnL", pnl_value),
        ("win_rate", win_rate_label or "Win rate", win_rate_value),
        ("volume", "Volume", selection_record.get("volume")),
        ("trade_count", "Trade count", selection_record.get("trade_count")),
        ("weather_notional_ratio", "Weather notional ratio", selection_record.get("weather_notional_ratio")),
        ("trades_per_active_day", "Trades per active day", selection_record.get("trades_per_active_day")),
        ("main_region", "Main region", first_non_empty(selection_record.get("main_region"), evidence_summary.get("main_region"))),
        ("dominant_region_trade_ratio", "Dominant region trade ratio", selection_record.get("dominant_region_trade_ratio")),
        ("max_region_daily_profit_multiple", "Highest daily burst", selection_record.get("max_region_daily_profit_multiple")),
        ("wallet_age_days", "Wallet age days", selection_record.get("wallet_age_days")),
        (
            "recent_evidence_date",
            "Recent evidence date",
            first_non_empty(selection_record.get("recent_evidence_date"), evidence_summary.get("latest_evidence_date")),
        ),
    ]
    metrics: list[dict[str, Any]] = []
    for key, label, value in metric_specs:
        if value in (None, ""):
            continue
        metrics.append({"key": key, "label": label, "value": value})
    return metrics[:8]


def build_finder_ai_contract(*, run_id: str, wallet_result: Mapping[str, Any]) -> dict[str, Any]:
    selection_record = safe_mapping(wallet_result.get("selection_record"))
    leaderboard_entry = safe_mapping(wallet_result.get("leaderboard_entry"))
    profile = safe_mapping(wallet_result.get("profile"))
    metrics = safe_mapping(wallet_result.get("metrics"))
    evidence_summary = safe_mapping(wallet_result.get("evidence_summary"))
    strategy_notes = compact_text_list(wallet_result.get("strategy_notes"), limit=4, max_length=180)
    primary_signals = build_finder_ai_primary_signals(wallet_result.get("label_evaluations"))
    labels = build_finder_ai_labels(wallet_result.get("labels"))
    normalized_address = normalize_address(
        selection_record.get("wallet") or wallet_result.get("wallet") or leaderboard_entry.get("proxyWallet")
    )
    display_name = first_non_empty(
        selection_record.get("user_name"),
        leaderboard_entry.get("userName"),
        profile.get("displayName"),
        profile.get("display_name"),
    )
    source_excerpt = first_non_empty(
        evidence_summary.get("headline"),
        primary_signals[0].get("reason") if primary_signals else "",
        strategy_notes[0] if strategy_notes else "",
    )

    return {
        "sourceName": FINDER_AI_SOURCE_NAME,
        "runId": text_value(run_id),
        "normalizedAddress": normalized_address,
        "wallet": {
            "address": normalized_address,
            "displayName": display_name,
            "alias": first_non_empty(profile.get("alias"), display_name),
        },
        "matched": bool(primary_signals or labels),
        "strategyFocus": strategy_notes[0] if strategy_notes else "",
        "aiBriefShort": "",
        "aiBriefNote": "",
        "aiDeepNote": "",
        "evidenceLevel": "structured_only" if primary_signals or labels else "insufficient",
        "hasConflict": False,
        "needsReview": False,
        "labels": labels,
        "primarySignals": primary_signals,
        "keyMetrics": build_finder_ai_key_metrics(selection_record, evidence_summary, metrics),
        "sourceExcerpt": source_excerpt,
        "weatherSignals": {
            "marketScope": "weather",
            "resolutionSource": "",
            "forecastBasis": "",
            "timingWindow": "",
            "edgeStyle": "",
            "weatherDrivers": [],
            "evidenceQuality": "structured_only" if primary_signals else "insufficient",
        },
        "providerMeta": {
            "provider": FINDER_AI_PROVIDER,
            "model": FINDER_AI_MODEL,
            "promptVersion": "",
            "generatedAt": "",
            "inputHash": "",
            "generationScope": FINDER_AI_GENERATION_SCOPE,
            "outputSchemaVersion": FINDER_AI_SCHEMA_VERSION,
        },
    }


def build_finder_ai_generation_layers(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    structured_materials = safe_mapping(wallet_result.get("structured_materials"))
    identity = safe_mapping(structured_materials.get("identity"))
    summary = safe_mapping(structured_materials.get("summary"))
    signals = safe_mapping(structured_materials.get("signals"))
    records = safe_mapping(structured_materials.get("records"))
    metrics = safe_mapping(wallet_result.get("metrics"))
    selection_record = safe_mapping(wallet_result.get("selection_record"))
    primary_signals = safe_list(payload.get("primarySignals"))[:5]
    labels = safe_list(payload.get("labels"))[:5]
    key_metrics = safe_list(payload.get("keyMetrics"))[:8]
    label_hits = safe_list(signals.get("label_hits"))[:6]
    strategy_notes = compact_text_list(summary.get("strategy_notes"), limit=3, max_length=160)
    trade_samples = compact_trade_samples(records.get("trade_samples"), limit=5)
    behavior_snapshot = compact_mapping(
        selection_record,
        (
            "trade_count",
            "weather_trade_count",
            "weather_trade_ratio",
            "weather_notional_ratio",
            "closed_profit_multiple",
            "trades_per_active_day",
            "dominant_region",
            "dominant_region_trade_ratio",
            "max_region_daily_profit_multiple",
            "highest_burst_region",
            "highest_burst_date",
            "recent_evidence_date",
            "activity_level",
            "latest_trade_date",
            "days_since_latest_trade",
            "wallet_age_days",
            "wallet_registration_date",
            "wallet_registration_source",
            "high_temp_off_day_buy_ratio",
            "split_avg_chip_cost",
            "split_evidence_count",
            "split_player_validation_passed",
            "liquidity_swap_ratio",
            "liquidity_sell_dominant_region_day_ratio",
            "low_chip_cost_trade_ratio",
            "trade_liquidity_profit",
            "final_settlement_profit",
            "unified_profit",
        ),
    )
    behavior_snapshot.update(
        compact_mapping(
            metrics,
            (
                "trade_liquidity_profit_multiple",
                "final_settlement_profit_multiple",
                "unified_profit_multiple",
                "reward_total_usdc",
                "reward_activity_count",
                "holding_duration_coverage",
                "median_holding_hours",
                "time_to_end_coverage",
                "median_time_to_end_hours",
                "largest_event_notional_ratio",
                "current_open_value",
                "closed_realized_pnl",
                "snapshot_complete",
            ),
        )
    )

    return {
        "L0": {
            "normalizedAddress": text_value(payload.get("normalizedAddress")),
            "sourceName": text_value(payload.get("sourceName")),
            "runId": text_value(payload.get("runId")),
            "updatedAt": first_non_empty(identity.get("captured_at")),
            "version": FINDER_AI_SCHEMA_VERSION,
        },
        "L1": {
            "manualLabels": [],
            "manualNotes": [],
            "watchlisted": False,
            "reviewStatus": "",
        },
        "L2": {
            "primarySignals": primary_signals,
            "labelHits": label_hits,
            "labels": labels,
            "keyMetrics": key_metrics,
            "sourceExcerpt": first_non_empty(payload.get("sourceExcerpt"), summary.get("source_excerpt")),
            "strategyFocusCandidate": first_non_empty(payload.get("strategyFocus")),
        },
        "L3": {
            "headline": first_non_empty(summary.get("headline")),
            "strategyNotes": strategy_notes,
            "activityLevel": first_non_empty(selection_record.get("activity_level")),
            "behaviorSnapshot": behavior_snapshot,
            "coverage": {
                "auditComplete": bool(summary.get("audit_complete")),
                "snapshotComplete": bool(selection_record.get("audit_complete")),
                "structuredEvidenceCount": len(label_hits),
                "strongEvidenceCount": len(primary_signals),
            },
        },
        "L4": {
            "tradeSamples": trade_samples,
        },
    }


def build_finder_ai_generation_gate(
    *,
    layers: Mapping[str, Any],
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    l0 = safe_mapping(layers.get("L0"))
    l2 = safe_mapping(layers.get("L2"))
    l3 = safe_mapping(layers.get("L3"))
    evidence_summary = safe_mapping(wallet_result.get("evidence_summary"))
    primary_signals = [item for item in safe_list(l2.get("primarySignals")) if isinstance(item, Mapping)]
    label_hits = [item for item in safe_list(l2.get("labelHits")) if isinstance(item, Mapping)]
    structured_evidence_count = len(label_hits) if label_hits else len(primary_signals)
    strong_evidence_count = 0
    for item in label_hits:
        if item.get("reason") or safe_list(item.get("example_markets")) or safe_mapping(item.get("numeric_evidence")):
            strong_evidence_count += 1
    if not strong_evidence_count:
        strong_evidence_count = len(primary_signals)
    has_normalized_address = bool(text_value(l0.get("normalizedAddress")))
    has_source_excerpt = bool(first_non_empty(l2.get("sourceExcerpt"), l3.get("headline")))
    audit_complete = bool(evidence_summary.get("audit_complete"))
    eligible = has_normalized_address and (
        structured_evidence_count >= 2 or (strong_evidence_count >= 1 and has_source_excerpt)
    )
    has_conflict = False
    needs_review = eligible and (not audit_complete or has_conflict)

    if not has_normalized_address:
        status = "insufficient"
        reason = "missing_normalized_address"
    elif not eligible:
        status = "insufficient"
        reason = "evidence_below_gate"
    elif needs_review:
        status = "needs_review"
        reason = "analysis_audit_incomplete" if not audit_complete else "signal_conflict"
    else:
        status = "ready"
        reason = "ready_for_brief"

    return {
        "eligible": eligible,
        "status": status,
        "reason": reason,
        "hasNormalizedAddress": has_normalized_address,
        "structuredEvidenceCount": structured_evidence_count,
        "strongEvidenceCount": strong_evidence_count,
        "hasSourceExcerpt": has_source_excerpt,
        "auditComplete": audit_complete,
        "hasConflict": has_conflict,
        "needsReview": needs_review,
        "generationScope": FINDER_AI_GENERATION_SCOPE,
    }


def build_finder_ai_input_hash(layers: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(json_bytes(layers)).hexdigest()}"


def build_finder_ai_cache_key(
    *,
    normalized_address: str,
    input_hash: str,
    model: str,
    prompt_version: str,
    output_schema_version: str,
) -> str:
    return "|".join(
        (
            normalize_address(normalized_address),
            text_value(input_hash),
            text_value(prompt_version),
            text_value(model),
            text_value(output_schema_version),
        )
    )


def enrich_finder_ai_generation_context(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(payload)
    layers = build_finder_ai_generation_layers(payload=result, wallet_result=wallet_result)
    gate = build_finder_ai_generation_gate(layers=layers, payload=result, wallet_result=wallet_result)
    provider_meta = safe_mapping(result.get("providerMeta"))
    cache_key = build_finder_ai_cache_key(
        normalized_address=text_value(result.get("normalizedAddress")),
        input_hash=build_finder_ai_input_hash(layers),
        model=text_value(provider_meta.get("model")) or FINDER_AI_MODEL,
        prompt_version=FINDER_AI_PROMPT_VERSION,
        output_schema_version=text_value(provider_meta.get("outputSchemaVersion")) or FINDER_AI_SCHEMA_VERSION,
    )
    provider_meta["promptVersion"] = FINDER_AI_PROMPT_VERSION
    provider_meta["inputHash"] = build_finder_ai_input_hash(layers)
    provider_meta["cacheKey"] = cache_key
    provider_meta["generationScope"] = FINDER_AI_GENERATION_SCOPE
    provider_meta["outputSchemaVersion"] = text_value(provider_meta.get("outputSchemaVersion")) or FINDER_AI_SCHEMA_VERSION
    result["providerMeta"] = provider_meta
    result["hasConflict"] = bool(gate.get("hasConflict"))
    result["needsReview"] = bool(gate.get("needsReview"))
    if not gate.get("eligible"):
        result["evidenceLevel"] = "insufficient"
    elif result.get("evidenceLevel") in (None, "", "insufficient"):
        result["evidenceLevel"] = "structured_only"
    result["layeredInput"] = layers
    result["briefGeneration"] = {
        "enabled": bool(gate.get("eligible")) and not bool(gate.get("needsReview")),
        "status": text_value(gate.get("status")),
        "reason": text_value(gate.get("reason")),
        "gateVersion": "finder-ai-brief-gate-v1",
        "decisionSource": "structured_only",
        "scope": FINDER_AI_GENERATION_SCOPE,
        "promptVersion": FINDER_AI_PROMPT_VERSION,
        "cacheKey": cache_key,
        "gate": gate,
    }
    return result


def compact_finder_ai_result(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}

    result = compact_mapping(
        payload,
        (
            "sourceName",
            "runId",
            "normalizedAddress",
            "matched",
            "strategyFocus",
            "aiBriefShort",
            "aiBriefNote",
            "aiDeepNote",
            "sourceExcerpt",
            "evidenceLevel",
            "hasConflict",
            "needsReview",
        ),
    )

    wallet = compact_mapping(
        payload.get("wallet") if isinstance(payload.get("wallet"), Mapping) else None,
        ("address", "displayName", "alias"),
    )
    if wallet:
        result["wallet"] = wallet

    labels: list[dict[str, Any]] = []
    for item in safe_list(payload.get("labels")):
        if not isinstance(item, Mapping):
            continue
        compact_item = compact_mapping(item, ("kind", "value", "source", "evidence"))
        if compact_item:
            labels.append(compact_item)
    if labels:
        result["labels"] = labels[:12]

    primary_signals: list[dict[str, Any]] = []
    for item in safe_list(payload.get("primarySignals")):
        if not isinstance(item, Mapping):
            continue
        compact_item = compact_mapping(item, ("key", "label", "matched", "reason"))
        if compact_item:
            primary_signals.append(compact_item)
    if primary_signals:
        result["primarySignals"] = primary_signals[:6]

    key_metrics: list[dict[str, Any]] = []
    for item in safe_list(payload.get("keyMetrics")):
        if not isinstance(item, Mapping):
            continue
        compact_item = compact_mapping(item, ("key", "label", "value"))
        if compact_item:
            key_metrics.append(compact_item)
    if key_metrics:
        result["keyMetrics"] = key_metrics[:8]

    weather_signals = compact_mapping(
        payload.get("weatherSignals") if isinstance(payload.get("weatherSignals"), Mapping) else None,
        (
            "marketScope",
            "resolutionSource",
            "forecastBasis",
            "timingWindow",
            "edgeStyle",
            "evidenceQuality",
        ),
    )
    weather_drivers = compact_text_list(
        safe_mapping(payload.get("weatherSignals")).get("weatherDrivers"),
        limit=6,
        max_length=80,
    )
    if weather_drivers:
        weather_signals["weatherDrivers"] = weather_drivers
    if weather_signals:
        result["weatherSignals"] = weather_signals

    provider_meta = compact_mapping(
        payload.get("providerMeta") if isinstance(payload.get("providerMeta"), Mapping) else None,
        (
            "provider",
            "model",
            "promptVersion",
            "generatedAt",
            "inputHash",
            "generationScope",
            "outputSchemaVersion",
        ),
    )
    if provider_meta:
        result["providerMeta"] = provider_meta

    return result
