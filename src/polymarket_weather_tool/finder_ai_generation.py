from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

from .finder_ai_contract import (
    FINDER_AI_MODEL,
    FINDER_AI_PROMPT_VERSION,
    FINDER_AI_SCHEMA_VERSION,
    build_finder_ai_cache_key,
    compact_mapping,
    first_non_empty,
    safe_list,
    safe_mapping,
    text_value,
)


UTC = timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FINDER_AI_CACHE_DIR = PROJECT_ROOT / ".cache" / "finder-ai"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_TIMEOUT_SECONDS = 45.0
FINDER_AI_BRIEF_SHORT_MAX_LENGTH = 28


def generate_finder_ai_brief(
    *,
    payload: Mapping[str, Any] | None,
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(payload) if isinstance(payload, Mapping) else {}
    if not result:
        return result

    brief_generation = safe_mapping(result.get("briefGeneration"))
    if not brief_generation.get("enabled") or text_value(brief_generation.get("status")) != "ready":
        return result

    provider_meta = safe_mapping(result.get("providerMeta"))
    model = (
        (os.getenv("DEEPSEEK_MODEL") or "").strip()
        or text_value(provider_meta.get("model"))
        or FINDER_AI_MODEL
    )
    result = refresh_finder_ai_cache_context(result, model=model)
    brief_generation = safe_mapping(result.get("briefGeneration"))

    if text_value(result.get("aiBriefNote")) and text_value(result.get("aiDeepNote")):
        return apply_local_finder_ai_fallback(
            result=result,
            reason="local_existing",
            wallet_result=wallet_result,
        )

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return apply_local_finder_ai_fallback(
            result=result,
            reason="local_fallback",
            wallet_result=wallet_result,
        )

    provider_meta = safe_mapping(result.get("providerMeta"))

    cache_key = text_value(provider_meta.get("cacheKey") or brief_generation.get("cacheKey"))
    cached = read_cached_finder_ai_brief(cache_key)
    if cached:
        return apply_generated_finder_ai_brief(
            result=result,
            generated=cached,
            status="cached",
            reason="cache_hit",
            wallet_result=wallet_result,
        )

    try:
        generated = request_deepseek_finder_ai_brief(
            api_key=api_key,
            model=model,
            payload=result,
            wallet_result=wallet_result,
        )
    except Exception as exc:
        failed = dict(result)
        brief_generation = safe_mapping(failed.get("briefGeneration"))
        brief_generation["status"] = "failed"
        brief_generation["reason"] = "provider_error"
        brief_generation["lastError"] = (text_value(exc) or str(exc).strip())[:240]
        failed["briefGeneration"] = brief_generation
        return failed

    write_cached_finder_ai_brief(cache_key, generated)
    return apply_generated_finder_ai_brief(
        result=result,
        generated=generated,
        status="generated",
        reason="generated",
        wallet_result=wallet_result,
    )


def refresh_finder_ai_cache_context(result: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    updated = dict(result)
    provider_meta = safe_mapping(updated.get("providerMeta"))
    brief_generation = safe_mapping(updated.get("briefGeneration"))
    prompt_version = FINDER_AI_PROMPT_VERSION
    output_schema_version = text_value(provider_meta.get("outputSchemaVersion")) or FINDER_AI_SCHEMA_VERSION
    input_hash = text_value(provider_meta.get("inputHash"))
    normalized_address = text_value(updated.get("normalizedAddress"))

    provider_meta["model"] = text_value(model) or FINDER_AI_MODEL
    provider_meta["promptVersion"] = prompt_version
    provider_meta["outputSchemaVersion"] = output_schema_version
    brief_generation["promptVersion"] = prompt_version

    if normalized_address and input_hash:
        cache_key = build_finder_ai_cache_key(
            normalized_address=normalized_address,
            input_hash=input_hash,
            model=provider_meta["model"],
            prompt_version=prompt_version,
            output_schema_version=output_schema_version,
        )
        provider_meta["cacheKey"] = cache_key
        brief_generation["cacheKey"] = cache_key

    updated["providerMeta"] = provider_meta
    updated["briefGeneration"] = brief_generation
    return updated


def apply_local_finder_ai_fallback(
    *,
    result: Mapping[str, Any],
    reason: str,
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    updated = postprocess_finder_ai_brief_fields(
        result=result,
        generated=None,
        wallet_result=wallet_result,
    )
    brief_generation = safe_mapping(updated.get("briefGeneration"))
    brief_generation["status"] = "fallback"
    brief_generation["reason"] = text_value(reason) or "local_fallback"
    updated["briefGeneration"] = brief_generation
    return updated


def request_deepseek_finder_ai_brief(
    *,
    api_key: str,
    model: str,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    request_payload = {
        "model": model,
        "messages": build_finder_ai_prompt_messages(payload=payload, wallet_result=wallet_result),
        "temperature": 0.2,
    }
    response_payload = post_deepseek_json(
        path="/chat/completions",
        api_key=api_key,
        payload=request_payload,
    )
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("DeepSeek response did not contain choices")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else {}
    content = text_value(safe_mapping(message).get("content"))
    parsed = parse_generated_brief_content(content)
    ai_brief_note = text_value(parsed.get("aiBriefNote"))
    if not ai_brief_note:
        raise ValueError("DeepSeek response did not contain aiBriefNote")
    strategy_focus = derive_finder_strategy_focus(
        parsed.get("strategyFocus"),
        payload=payload,
        wallet_result=wallet_result,
    )
    ai_deep_note = derive_finder_ai_deep_note(
        parsed.get("aiDeepNote"),
        ai_brief_note=ai_brief_note,
        payload=payload,
        wallet_result=wallet_result,
    )
    ai_brief_note = derive_finder_ai_brief_note(
        parsed.get("aiBriefNote"),
        ai_deep_note=ai_deep_note,
        payload=payload,
        wallet_result=wallet_result,
    )
    ai_brief_short = derive_finder_ai_brief_short_with_context(
        parsed.get("aiBriefShort"),
        ai_brief_note=ai_brief_note,
        ai_deep_note=ai_deep_note,
        strategy_focus=strategy_focus,
        payload=payload,
        wallet_result=wallet_result,
    )
    return {
        "strategyFocus": strategy_focus,
        "aiBriefShort": ai_brief_short,
        "aiBriefNote": ai_brief_note,
        "aiDeepNote": ai_deep_note,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": "deepseek",
        "model": text_value(response_payload.get("model")) or model,
        "requestId": text_value(response_payload.get("id")),
    }


def build_finder_ai_prompt_messages(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> list[dict[str, str]]:
    layered_input = safe_mapping(payload.get("layeredInput"))
    l0 = safe_mapping(layered_input.get("L0"))
    l2 = safe_mapping(layered_input.get("L2"))
    l3 = safe_mapping(layered_input.get("L3"))
    l4 = safe_mapping(layered_input.get("L4"))
    wallet = safe_mapping(payload.get("wallet"))
    summary = safe_mapping(safe_mapping(wallet_result.get("structured_materials")).get("summary"))
    weather_signals = safe_mapping(payload.get("weatherSignals"))

    prompt_context = {
        "normalizedAddress": text_value(payload.get("normalizedAddress")),
        "wallet": {
            "displayName": text_value(wallet.get("displayName")),
            "alias": text_value(wallet.get("alias")),
        },
        "runId": text_value(payload.get("runId")),
        "sourceName": text_value(payload.get("sourceName")),
        "strategyFocusCandidate": text_value(l2.get("strategyFocusCandidate") or payload.get("strategyFocus")),
        "sourceExcerpt": text_value(l2.get("sourceExcerpt") or payload.get("sourceExcerpt")),
        "headline": text_value(l3.get("headline") or summary.get("headline")),
        "strategyNotes": safe_list(l3.get("strategyNotes"))[:4],
        "activityLevel": text_value(l3.get("activityLevel")),
        "behaviorSnapshot": build_prompt_behavior_snapshot(l3.get("behaviorSnapshot")),
        "coverage": build_prompt_coverage_snapshot(
            l3.get("coverage"),
            safe_mapping(payload.get("briefGeneration")).get("gate"),
        ),
        "tradeSamples": build_prompt_trade_samples(l4.get("tradeSamples")),
        "profileSnapshot": build_prompt_profile_snapshot(wallet_result),
        "operationAuditSnapshot": build_prompt_operation_snapshot(wallet_result),
        "topTrades": build_prompt_top_trades(wallet_result.get("top_trades")),
        "primarySignals": safe_list(l2.get("primarySignals")),
        "labelHits": safe_list(l2.get("labelHits")),
        "labels": safe_list(l2.get("labels")),
        "keyMetrics": safe_list(l2.get("keyMetrics")),
        "weatherSignals": {
            "marketScope": text_value(weather_signals.get("marketScope")),
            "timingWindow": text_value(weather_signals.get("timingWindow")),
            "edgeStyle": text_value(weather_signals.get("edgeStyle")),
            "evidenceQuality": text_value(weather_signals.get("evidenceQuality")),
            "weatherDrivers": safe_list(weather_signals.get("weatherDrivers")),
        },
        "gate": safe_mapping(payload.get("briefGeneration")).get("gate"),
        "updatedAt": text_value(l0.get("updatedAt")),
    }

    system_prompt = (
        "You are Finder's Polymarket weather wallet analyst. "
        "Use only the structured evidence from the user message. "
        "Do not invent facts, dates, numbers, motives, regions, or behaviors that were not provided. "
        "Write natural Simplified Chinese that sounds like a human analyst, not a label dump or metric list. "
        "Use behaviorSnapshot, coverage, and tradeSamples as your main behavioral evidence, and use primarySignals or labelHits as supporting evidence. "
        "Use profileSnapshot, operationAuditSnapshot, and topTrades to explain the wallet's preferred battlefield, execution style, and how profits are actually realized. "
        "When possible, point to repeatable patterns shown in the trade samples instead of only repeating label names. "
        "Avoid empty phrases such as 'still worth watching' unless you say exactly what should be verified next. "
        "When evidence exists, mention at least one concrete city, market, or execution pattern. "
        "Return strict JSON with exactly four string fields: strategyFocus, aiBriefShort, aiBriefNote, aiDeepNote. "
        "strategyFocus should be one short Simplified Chinese strategy conclusion, not an English tag or abstract label. "
        "When evidence exists, prefer concrete wording such as region plus execution style or region plus settlement preference. "
        "aiBriefShort should be one crisp, scan-friendly preview line and should stay within 28 Chinese characters when possible. "
        "It should read like a human-facing short takeaway, not a stacked label. "
        "aiBriefNote should use 2 to 4 sentences to explain what kind of trader this looks like, what evidence supports that view, and the main caveat. "
        "aiDeepNote should use 4 to 6 sentences to explain the trader archetype, repeatable behavior pattern, likely edge or motivation, what looks fragile, and what still needs verification. "
        "If the evidence is incomplete or mixed, say that explicitly instead of guessing. "
        "Do not output markdown or any text outside the JSON object."
    )

    return [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": json.dumps(prompt_context, ensure_ascii=False),
        },
    ]


def post_deepseek_json(
    *,
    path: str,
    api_key: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL).strip().rstrip("/")
    timeout_seconds = resolve_deepseek_timeout_seconds()
    request = urlrequest.Request(
        f"{base_url}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body[:240]}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"DeepSeek request failed: {exc.reason}") from exc


def parse_generated_brief_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        return {}
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
        return dict(payload) if isinstance(payload, Mapping) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
                return dict(payload) if isinstance(payload, Mapping) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def apply_generated_finder_ai_brief(
    *,
    result: Mapping[str, Any],
    generated: Mapping[str, Any],
    status: str,
    reason: str,
    wallet_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    updated = postprocess_finder_ai_brief_fields(
        result=result,
        generated=generated,
        wallet_result=wallet_result or {},
    )
    provider_meta = safe_mapping(updated.get("providerMeta"))
    provider_meta["provider"] = text_value(generated.get("provider")) or provider_meta.get("provider") or "deepseek"
    provider_meta["model"] = text_value(generated.get("model")) or provider_meta.get("model") or FINDER_AI_MODEL
    provider_meta["generatedAt"] = text_value(generated.get("generatedAt"))
    request_id = text_value(generated.get("requestId"))
    if request_id:
        provider_meta["requestId"] = request_id
    updated["providerMeta"] = provider_meta

    brief_generation = safe_mapping(updated.get("briefGeneration"))
    brief_generation["status"] = status
    brief_generation["reason"] = reason
    updated["briefGeneration"] = brief_generation
    return updated


def postprocess_finder_ai_brief_fields(
    *,
    result: Mapping[str, Any],
    generated: Mapping[str, Any] | None,
    wallet_result: Mapping[str, Any],
) -> dict[str, Any]:
    updated = dict(result)
    generated_payload = safe_mapping(generated)
    strategy_focus = derive_finder_strategy_focus(
        generated_payload.get("strategyFocus") or updated.get("strategyFocus"),
        payload=updated,
        wallet_result=wallet_result,
    )
    if strategy_focus:
        updated["strategyFocus"] = strategy_focus

    deep_note_seed = text_value(generated_payload.get("aiDeepNote")) or text_value(updated.get("aiDeepNote"))
    ai_brief_note = derive_finder_ai_brief_note(
        generated_payload.get("aiBriefNote") or updated.get("aiBriefNote"),
        ai_deep_note=deep_note_seed,
        payload=updated,
        wallet_result=wallet_result,
    )
    updated["aiBriefNote"] = ai_brief_note
    ai_deep_note = derive_finder_ai_deep_note(
        generated_payload.get("aiDeepNote") or updated.get("aiDeepNote"),
        ai_brief_note=ai_brief_note,
        payload=updated,
        wallet_result=wallet_result,
    )
    updated["aiDeepNote"] = ai_deep_note
    updated["aiBriefShort"] = derive_finder_ai_brief_short_with_context(
        generated_payload.get("aiBriefShort") or updated.get("aiBriefShort"),
        ai_brief_note=ai_brief_note,
        ai_deep_note=ai_deep_note,
        strategy_focus=strategy_focus,
        payload=updated,
        wallet_result=wallet_result,
    )
    return updated


def resolve_deepseek_timeout_seconds() -> float:
    raw_value = (os.getenv("DEEPSEEK_TIMEOUT_SECONDS") or "").strip()
    if not raw_value:
        return DEEPSEEK_DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return DEEPSEEK_DEFAULT_TIMEOUT_SECONDS


def read_cached_finder_ai_brief(cache_key: str) -> dict[str, Any]:
    path = cache_path_for_key(cache_key)
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def write_cached_finder_ai_brief(cache_key: str, payload: Mapping[str, Any]) -> None:
    path = cache_path_for_key(cache_key)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_path_for_key(cache_key: str) -> Path | None:
    normalized = text_value(cache_key)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return FINDER_AI_CACHE_DIR / f"{digest}.json"


def build_prompt_profile_snapshot(wallet_result: Mapping[str, Any]) -> dict[str, Any]:
    profile = safe_mapping(wallet_result.get("profile"))
    average_buy_price = safe_mapping(profile.get("average_buy_price"))
    city_distribution = safe_mapping(profile.get("city_distribution"))
    closed_position_pnl = safe_mapping(profile.get("closed_position_pnl"))
    top_cities = safe_mapping(profile.get("top_cities"))

    snapshot = compact_mapping(
        {
            "weighted_average_buy_price": average_buy_price.get("weighted_average_price"),
            "median_buy_price": average_buy_price.get("median_price"),
            "city_count": city_distribution.get("city_count"),
            "known_city_trade_count": city_distribution.get("known_city_trade_count"),
            "unknown_city_trade_count": city_distribution.get("unknown_city_trade_count"),
            "closed_win_rate": closed_position_pnl.get("win_rate"),
            "closed_profit_multiple": closed_position_pnl.get("profit_multiple"),
            "total_realized_pnl": closed_position_pnl.get("total_realized_pnl"),
        },
        (
            "weighted_average_buy_price",
            "median_buy_price",
            "city_count",
            "known_city_trade_count",
            "unknown_city_trade_count",
            "closed_win_rate",
            "closed_profit_multiple",
            "total_realized_pnl",
        ),
    )
    top_realized = build_prompt_city_leaders(
        top_cities.get("by_realized_pnl"),
        value_keys=("realized_pnl", "closed_profit_multiple"),
    )
    top_buy_amount = build_prompt_city_leaders(
        top_cities.get("by_buy_amount"),
        value_keys=("buy_amount", "net_trade_cashflow"),
    )
    if top_realized:
        snapshot["top_realized_pnl_cities"] = top_realized
    if top_buy_amount:
        snapshot["top_buy_amount_cities"] = top_buy_amount
    return snapshot


def build_prompt_city_leaders(value: Any, *, value_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in safe_list(value):
        if not isinstance(item, Mapping):
            continue
        payload = compact_mapping(
            item,
            (
                "city",
                "region",
                "trade_count",
                "trade_ratio",
                *value_keys,
            ),
        )
        if not payload:
            continue
        results.append(payload)
        if len(results) >= 3:
            break
    return results


def build_prompt_operation_snapshot(wallet_result: Mapping[str, Any]) -> dict[str, Any]:
    operation_audit = safe_mapping(wallet_result.get("operation_audit"))
    profit_summary = safe_mapping(operation_audit.get("profit_summary"))
    operations = safe_mapping(operation_audit.get("operations"))

    snapshot = compact_mapping(
        {
            "complete": operation_audit.get("complete"),
            "record_count": operation_audit.get("record_count"),
            "trade_liquidity_profit_multiple": profit_summary.get("trade_liquidity_profit_multiple"),
            "final_settlement_profit_multiple": profit_summary.get("final_settlement_profit_multiple"),
            "unified_profit_multiple": profit_summary.get("unified_profit_multiple"),
            "trade_liquidity_record_count": profit_summary.get("trade_liquidity_record_count"),
            "final_settlement_record_count": profit_summary.get("final_settlement_record_count"),
        },
        (
            "complete",
            "record_count",
            "trade_liquidity_profit_multiple",
            "final_settlement_profit_multiple",
            "unified_profit_multiple",
            "trade_liquidity_record_count",
            "final_settlement_record_count",
        ),
    )
    operation_statuses = build_prompt_operation_statuses(operations)
    if operation_statuses:
        snapshot["operation_statuses"] = operation_statuses
    return snapshot


def build_prompt_operation_statuses(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for operation_name in ("convert", "split", "redeem", "swap"):
        operation = safe_mapping(value.get(operation_name))
        payload = compact_mapping(
            {
                "operation": operation_name,
                "status": operation.get("status"),
                "reason": operation.get("reason"),
                "count": operation.get("count"),
                "verified_count": operation.get("verified_count"),
                "partial_count": operation.get("partial_count"),
            },
            (
                "operation",
                "status",
                "reason",
                "count",
                "verified_count",
                "partial_count",
            ),
        )
        if payload:
            results.append(payload)
    return results


def build_prompt_top_trades(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in safe_list(value):
        if not isinstance(item, Mapping):
            continue
        payload = compact_mapping(
            item,
            (
                "title",
                "eventSlug",
                "slug",
                "side",
                "size",
                "price",
                "outcome",
                "timestamp",
            ),
        )
        if not payload:
            continue
        results.append(payload)
        if len(results) >= 5:
            break
    return results


def build_profile_insight(profile_snapshot: Mapping[str, Any]) -> str:
    top_realized = safe_list(profile_snapshot.get("top_realized_pnl_cities"))
    if top_realized:
        first_city = safe_mapping(top_realized[0])
        city_name = first_non_empty(first_city.get("city"), first_city.get("region"))
        if city_name:
            return (
                f"\u4ece\u76c8\u5229\u5206\u5e03\u770b\uff0c{city_name} \u8fd9\u7c7b\u91cd\u70b9\u57ce\u5e02\u4e0d\u50cf\u662f\u5076\u53d1\u547d\u4e2d\uff0c"
                "\u66f4\u50cf\u662f\u5b83\u4f1a\u53cd\u590d\u56de\u5230\u7684\u4e3b\u6218\u573a\uff0c\u8bf4\u660e\u5b83\u5bf9\u5c11\u6570\u719f\u6089\u5730\u533a\u6709\u660e\u663e\u504f\u597d\u3002"
            )

    city_count = profile_snapshot.get("city_count")
    unknown_city_trade_count = profile_snapshot.get("unknown_city_trade_count")
    if city_count not in (None, "") and unknown_city_trade_count not in (None, ""):
        return (
            f"\u4ece\u753b\u50cf\u8986\u76d6\u9762\u770b\uff0c\u5b83\u4e0d\u662f\u53ea\u76ef\u4e00\u5ea7\u57ce\u5e02\uff0c"
            f"\u4f46\u4e5f\u4e0d\u662f\u5e73\u5747\u6492\u7f51\uff0c\u5f53\u524d\u53ef\u89c1\u57ce\u5e02\u6570\u7ea6\u4e3a {city_count}\uff0c"
            "\u66f4\u50cf\u662f\u5148\u8fc7\u4e00\u904d\u6c60\u5b50\uff0c\u518d\u628a\u7b79\u7801\u96c6\u4e2d\u5230\u5c11\u6570\u91cd\u70b9\u6218\u573a\u3002"
        )

    return ""


def build_operation_insight(operation_snapshot: Mapping[str, Any]) -> str:
    trade_multiple = operation_snapshot.get("trade_liquidity_profit_multiple")
    final_multiple = operation_snapshot.get("final_settlement_profit_multiple")
    statuses = {
        text_value(item.get("operation")): safe_mapping(item)
        for item in safe_list(operation_snapshot.get("operation_statuses"))
    }

    if isinstance(trade_multiple, (int, float)) and isinstance(final_multiple, (int, float)):
        if trade_multiple < 1 and final_multiple > 1:
            return (
                "\u8fd9\u7c7b\u5730\u5740\u66f4\u50cf\u62bc\u5bf9\u4e4b\u540e\u6562\u628a\u4ed3\u4f4d\u62ff\u5230\u7ed3\u7b97\uff0c"
                "\u800c\u4e0d\u662f\u9760\u76d8\u4e2d\u53cd\u590d\u5012\u624b\u5403\u4ef7\u5dee\u3002"
            )
        if trade_multiple > 1 and final_multiple > 1:
            return (
                "\u5b83\u4e0d\u662f\u53ea\u6709\u4e00\u79cd\u5151\u73b0\u624b\u6cd5\uff0c"
                "\u65e2\u4f1a\u5728\u4e2d\u9014\u9501\u4f4f\u90e8\u5206\u4ef7\u683c\u4f18\u52bf\uff0c\u4e5f\u6562\u628a\u5229\u6da6\u7559\u5230\u7ed3\u7b97\u7aef\u3002"
            )

    split_status = text_value(statuses.get("split", {}).get("status"))
    swap_status = text_value(statuses.get("swap", {}).get("status"))
    if split_status == "not_found" and swap_status == "not_found":
        return (
            "\u94fe\u8def\u5ba1\u8ba1\u91cc\u51e0\u4e4e\u770b\u4e0d\u5230 split \u6216 swap \u8fd9\u7c7b\u590d\u6742\u52a8\u4f5c\uff0c"
            "\u5f53\u524d\u66f4\u50cf\u662f\u76f4\u63a5\u4ea4\u6613\u518d\u7b49\u7ed3\u679c\u5151\u73b0\u7684\u8def\u5f84\u3002"
        )

    return ""


def build_top_trade_insight(top_trades: list[dict[str, Any]]) -> str:
    if not top_trades:
        return ""
    first_trade = safe_mapping(top_trades[0])
    title = text_value(first_trade.get("title"))
    side = text_value(first_trade.get("side"))
    size = first_trade.get("size")
    if title and side and size not in (None, ""):
        return (
            f"\u50cf\u201c{title}\u201d\u8fd9\u79cd\u5355\u7b14 {side}\u3001\u91d1\u989d\u7ea6 {render_metric_value(size)} \u7684\u6210\u4ea4\u90fd\u80fd\u6392\u8fdb\u4ee3\u8868\u6027\u5927\u989d\u4ea4\u6613\uff0c"
            "\u57fa\u672c\u8bf4\u660e\u5b83\u770b\u51c6\u4e86\u4f1a\u771f\u653e\u5927\u4ed3\u4f4d\uff0c\u4e0d\u662f\u7528\u5c0f\u5355\u56db\u5904\u8bd5\u9519\u3002"
        )
    if title:
        return f"\u4ece\u4ee3\u8868\u6027\u5927\u989d\u6210\u4ea4\u770b\uff0c\u5b83\u4f1a\u628a\u7b79\u7801\u96c6\u4e2d\u5230\u50cf\u201c{title}\u201d\u8fd9\u6837\u7684\u91cd\u70b9\u5408\u7ea6\u4e0a\u3002"

    return ""


def derive_finder_ai_brief_note(
    candidate: Any,
    *,
    ai_deep_note: str,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    explicit = normalize_brief_text(candidate)
    strategy_focus = text_value(payload.get("strategyFocus"))
    if explicit and not looks_like_thin_brief_note(explicit, strategy_focus=strategy_focus):
        if not looks_like_template_brief_note(
            explicit,
            payload=payload,
            wallet_result=wallet_result,
        ):
            return trim_note_text(explicit, limit=220)

    synthesized = build_context_brief_note(payload=payload, wallet_result=wallet_result)
    if synthesized:
        return trim_note_text(synthesized, limit=220)

    if explicit:
        return trim_note_text(explicit, limit=220)
    if ai_deep_note:
        return trim_note_text(extract_brief_note_from_deep_note(ai_deep_note), limit=220)
    return ""


def derive_finder_ai_brief_short_with_context(
    candidate: Any,
    *,
    ai_brief_note: str,
    ai_deep_note: str,
    strategy_focus: str,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    explicit = normalize_brief_text(candidate)
    if explicit and not looks_like_thin_brief_short(explicit, strategy_focus=strategy_focus):
        if not looks_like_template_brief_short(
            explicit,
            payload=payload,
            wallet_result=wallet_result,
        ):
            return derive_finder_ai_brief_short(explicit)

    synthesized = build_context_brief_short(payload=payload, wallet_result=wallet_result)
    if synthesized:
        return trim_brief_text(synthesized, limit=FINDER_AI_BRIEF_SHORT_MAX_LENGTH)

    return derive_finder_ai_brief_short(explicit, ai_brief_note, ai_deep_note, strategy_focus)


def build_context_brief_note(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    region = infer_focus_region(payload=payload, wallet_result=wallet_result)
    operation_snapshot = build_prompt_operation_snapshot(wallet_result)
    sentences: list[str] = []
    if region:
        lead = f"\u5b83\u66f4\u50cf\u56f4\u7ed5 {region} \u8fd9\u7c7b\u719f\u6089\u57ce\u5e02\u53cd\u590d\u4e0b\u6ce8\u7684\u5929\u6c14\u4ea4\u6613\u8005"
    else:
        lead = "\u5b83\u66f4\u50cf\u56f4\u7ed5\u5c11\u6570\u719f\u6089\u9898\u6750\u53cd\u590d\u4e0b\u6ce8\u7684\u5929\u6c14\u4ea4\u6613\u8005"

    evidence_fragment = build_brief_evidence_fragment(payload=payload, wallet_result=wallet_result)
    if evidence_fragment:
        lead = f"{lead}\uff0c{evidence_fragment}"
    sentences.append(ensure_sentence(lead))

    execution_sentence = build_brief_execution_sentence(operation_snapshot)
    if execution_sentence:
        sentences.append(execution_sentence)

    caveat_sentence = build_brief_caveat_sentence(payload=payload, wallet_result=wallet_result)
    if caveat_sentence:
        sentences.append(caveat_sentence)

    return " ".join(dedupe_sentences(sentences))


def build_brief_evidence_fragment(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    layered_input = safe_mapping(payload.get("layeredInput"))
    l3 = safe_mapping(layered_input.get("L3"))
    l4 = safe_mapping(layered_input.get("L4"))
    behavior_snapshot = build_prompt_behavior_snapshot(l3.get("behaviorSnapshot"))
    trade_samples = build_prompt_trade_samples(l4.get("tradeSamples"))
    top_trades = build_prompt_top_trades(wallet_result.get("top_trades"))
    fragments: list[str] = []

    weather_ratio = behavior_snapshot.get("weather_trade_ratio")
    if isinstance(weather_ratio, (int, float)):
        fragments.append(f"\u6837\u672c\u91cc\u5929\u6c14\u4ea4\u6613\u5360\u6bd4 {render_metric_value(weather_ratio)}")

    trade = safe_mapping(trade_samples[0]) if trade_samples else {}
    title = text_value(trade.get("market_title"))
    side = text_value(trade.get("side"))
    size = trade.get("size_usd")
    if not trade and top_trades:
        top_trade = safe_mapping(top_trades[0])
        title = text_value(top_trade.get("title"))
        side = text_value(top_trade.get("side"))
        size = top_trade.get("size")

    if title and side:
        fragments.append(f"\u4ee3\u8868\u6027\u8ba2\u5355\u662f\u201c{title}\u201d\u7684 {side}")
    elif title:
        fragments.append(f"\u4ee3\u8868\u6027\u8ba2\u5355\u843d\u5728\u201c{title}\u201d")
    if size not in (None, ""):
        fragments.append(f"\u5355\u7b14\u7ea6 {render_metric_value(size)}")

    return "\uff0c".join(fragments[:3])


def build_context_brief_short(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    region = infer_focus_region(payload=payload, wallet_result=wallet_result)
    operation_snapshot = build_prompt_operation_snapshot(wallet_result)
    trade_multiple = operation_snapshot.get("trade_liquidity_profit_multiple")
    final_multiple = operation_snapshot.get("final_settlement_profit_multiple")

    if region and isinstance(trade_multiple, (int, float)) and isinstance(final_multiple, (int, float)):
        if trade_multiple < 1 and final_multiple > 1:
            return f"{region} \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"
    if region:
        return f"{region} \u53cd\u590d\u4e0b\u6ce8"

    strategy_focus = text_value(payload.get("strategyFocus"))
    if strategy_focus:
        return strategy_focus
    return ""


def derive_finder_strategy_focus(
    candidate: Any,
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    explicit = normalize_brief_text(candidate)
    if explicit and not looks_like_template_strategy_focus(
        explicit,
        payload=payload,
        wallet_result=wallet_result,
    ):
        trimmed_explicit = trim_strategy_focus(explicit)
        if contains_cjk_text(trimmed_explicit):
            return trimmed_explicit

    synthesized = build_context_strategy_focus(payload=payload, wallet_result=wallet_result)
    if synthesized:
        return trim_strategy_focus(synthesized)

    layered_input = safe_mapping(payload.get("layeredInput"))
    l2 = safe_mapping(layered_input.get("L2"))
    fallback = first_non_empty(l2.get("strategyFocusCandidate"), payload.get("strategyFocus"), explicit)
    trimmed_fallback = trim_strategy_focus(fallback)
    if contains_cjk_text(trimmed_fallback):
        return trimmed_fallback
    return ""


def build_context_strategy_focus(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    region = infer_focus_region(payload=payload, wallet_result=wallet_result)
    operation_snapshot = build_prompt_operation_snapshot(wallet_result)
    trade_multiple = operation_snapshot.get("trade_liquidity_profit_multiple")
    final_multiple = operation_snapshot.get("final_settlement_profit_multiple")

    if region and isinstance(trade_multiple, (int, float)) and isinstance(final_multiple, (int, float)):
        if trade_multiple < 1 and final_multiple > 1:
            return f"{region} \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"
        if trade_multiple > 1 and final_multiple > 1:
            return f"{region} \u96c6\u4e2d\u4e0b\u6ce8\u3001\u5206\u5c42\u5151\u73b0"
    if region:
        return f"{region} \u53cd\u590d\u4e0b\u6ce8"

    if isinstance(trade_multiple, (int, float)) and isinstance(final_multiple, (int, float)):
        if trade_multiple < 1 and final_multiple > 1:
            return "\u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"
        if trade_multiple > 1 and final_multiple > 1:
            return "\u96c6\u4e2d\u4e0b\u6ce8\u3001\u5206\u5c42\u5151\u73b0"
    return ""


def infer_focus_region(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    layered_input = safe_mapping(payload.get("layeredInput"))
    l3 = safe_mapping(layered_input.get("L3"))
    behavior_snapshot = build_prompt_behavior_snapshot(l3.get("behaviorSnapshot"))
    dominant_region = text_value(behavior_snapshot.get("dominant_region"))
    if dominant_region:
        return dominant_region

    profile_snapshot = build_prompt_profile_snapshot(wallet_result)
    top_realized = safe_list(profile_snapshot.get("top_realized_pnl_cities"))
    if top_realized:
        top_city = safe_mapping(top_realized[0])
        return first_non_empty(top_city.get("city"), top_city.get("region"))
    return ""


def build_brief_execution_sentence(operation_snapshot: Mapping[str, Any]) -> str:
    trade_multiple = operation_snapshot.get("trade_liquidity_profit_multiple")
    final_multiple = operation_snapshot.get("final_settlement_profit_multiple")

    if isinstance(trade_multiple, (int, float)) and isinstance(final_multiple, (int, float)):
        if trade_multiple < 1 and final_multiple > 1:
            return "\u4e0b\u624b\u65f6\u4f1a\u660e\u663e\u653e\u5927\u9ad8\u786e\u4fe1\u5ea6\u5355\u7b14\uff0c\u6536\u76ca\u66f4\u504f\u5411\u9760\u7ed3\u679c\u5151\u73b0\u653e\u5927\uff0c\u800c\u4e0d\u662f\u4e2d\u9014\u9ad8\u9891\u5012\u624b\u3002"
        if trade_multiple > 1 and final_multiple > 1:
            return "\u5b83\u65e2\u4f1a\u5728\u6301\u4ed3\u8fc7\u7a0b\u4e2d\u5151\u73b0\u90e8\u5206\u4ef7\u683c\u4f18\u52bf\uff0c\u4e5f\u80fd\u628a\u5229\u6da6\u7559\u5230\u7ed3\u7b97\u7aef\uff0c\u4e0d\u662f\u5355\u4e00\u7684\u4e00\u79cd\u505a\u6cd5\u3002"
    return "\u4e0b\u624b\u65f6\u66f4\u50cf\u662f\u6311\u5c11\u6570\u9ad8\u786e\u4fe1\u5ea6\u673a\u4f1a\u96c6\u4e2d\u51fa\u624b\uff0c\u800c\u4e0d\u662f\u5e73\u5747\u6492\u7f51\u3002"


def build_brief_caveat_sentence(
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    layered_input = safe_mapping(payload.get("layeredInput"))
    l3 = safe_mapping(layered_input.get("L3"))
    coverage = build_prompt_coverage_snapshot(
        l3.get("coverage"),
        safe_mapping(payload.get("briefGeneration")).get("gate"),
    )
    region = infer_focus_region(payload=payload, wallet_result=wallet_result)
    operation_snapshot = build_prompt_operation_snapshot(wallet_result)
    verification_target = build_deep_note_verification_target(
        region=region,
        operation_snapshot=operation_snapshot,
    )
    if coverage.get("needsReview") or not coverage.get("auditComplete"):
        if verification_target:
            return (
                f"\u4f46\u5f53\u524d\u5ba1\u8ba1\u8986\u76d6\u8fd8\u6ca1\u8865\u9f50\uff0c"
                f"\u540e\u9762\u8fd8\u662f\u8981\u76ef\u8fd9\u7c7b{verification_target}\u7684\u6253\u6cd5\u4f1a\u4e0d\u4f1a\u7ee7\u7eed\u590d\u73b0\u3002"
            )
        return "\u4f46\u5f53\u524d\u5ba1\u8ba1\u8986\u76d6\u8fd8\u6ca1\u8865\u9f50\uff0c\u8fd9\u4e2a\u5224\u65ad\u8fd8\u5f97\u7ee7\u7eed\u8ddf\u3002"
    if verification_target:
        return f"\u4f46\u540e\u9762\u8fd8\u662f\u8981\u76ef\u8fd9\u7c7b{verification_target}\u7684\u6253\u6cd5\u4f1a\u4e0d\u4f1a\u7ee7\u7eed\u590d\u73b0\u3002"
    return "\u4f46\u540e\u9762\u8fd8\u662f\u8981\u76ef\u8fd9\u4e2a\u5224\u65ad\u4f1a\u4e0d\u4f1a\u5728\u66f4\u591a\u4ea4\u6613\u65e5\u91cc\u7ee7\u7eed\u6210\u7acb\u3002"


def looks_like_thin_brief_note(text: str, *, strategy_focus: str) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return True
    stripped = strip_sentence_punctuation(normalized)
    focus = strip_sentence_punctuation(strategy_focus)
    if focus and stripped == focus:
        return True
    return len(normalized) < 24


def looks_like_thin_brief_short(text: str, *, strategy_focus: str) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return True
    stripped = strip_sentence_punctuation(normalized)
    focus = strip_sentence_punctuation(strategy_focus)
    if focus and stripped == focus:
        return True
    return len(normalized) < 8


def looks_like_template_strategy_focus(
    text: str,
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return True
    if has_persona_anchor(text, payload=payload, wallet_result=wallet_result):
        return False
    if is_likely_english_fragment(normalized):
        return True

    return any(
        marker in normalized
        for marker in (
            "\u5929\u6c14\u4ea4\u6613",
            "\u5929\u6c14\u52a8\u91cf",
            "\u4e8b\u4ef6\u9a71\u52a8",
            "\u7b56\u7565\u578b",
            "\u52a8\u91cf",
            "\u504f\u597d",
        )
    )


def looks_like_template_brief_note(
    text: str,
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return True

    generic_hits = sum(
        marker in normalized
        for marker in (
            "策略一致性",
            "交易风格",
            "行为模式",
            "整体风格",
            "区域偏好",
            "较强",
            "明显",
            "值得关注",
            "持续观察",
            "后续观察",
            "仍需观察",
        )
    )
    if generic_hits >= 2:
        return True
    return not has_persona_anchor(text, payload=payload, wallet_result=wallet_result)


def looks_like_template_brief_short(
    text: str,
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return True
    if is_likely_english_fragment(normalized):
        return True
    if has_persona_anchor(text, payload=payload, wallet_result=wallet_result):
        return False

    return any(
        marker in normalized
        for marker in (
            "策略型选手",
            "策略型交易者",
            "天气交易者",
            "天气交易选手",
            "event-driven",
            "Weather momentum",
        )
    )


def has_persona_anchor(
    text: str,
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return False

    behavior_markers = (
        "结果兑现",
        "结算端",
        "高频倒手",
        "价差",
        "高确信度",
        "主战场",
        "集中下注",
        "下注",
        "反复下注",
        "反复出手",
        "放大仓位",
        "单笔",
        "合约",
        "BUY",
        "SELL",
        "buy",
        "sell",
        "天气占比",
        "天气交易占比",
        "同一座城",
        "熟悉城市",
    )
    has_behavior_anchor = any(marker in normalized for marker in behavior_markers)
    region = infer_focus_region(payload=payload, wallet_result=wallet_result)
    if region and region in normalized:
        return has_behavior_anchor

    return has_behavior_anchor


def extract_brief_note_from_deep_note(text: str) -> str:
    parts = [part.strip() for part in re.split(r"[。！？.!?；;]\s*", normalize_brief_text(text)) if part.strip()]
    return " ".join(f"{strip_sentence_punctuation(part)}。" for part in parts[:2])


def build_prompt_behavior_snapshot(value: Any) -> dict[str, Any]:
    snapshot = safe_mapping(value)
    return compact_mapping(
        snapshot,
        (
            "trade_count",
            "weather_trade_count",
            "weather_trade_ratio",
            "weather_notional_ratio",
            "closed_position_win_rate",
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
            "high_temp_off_day_buy_ratio",
            "split_avg_chip_cost",
            "split_player_validation_passed",
            "liquidity_swap_ratio",
            "liquidity_sell_dominant_region_day_ratio",
            "low_chip_cost_trade_ratio",
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


def build_prompt_coverage_snapshot(coverage_value: Any, gate_value: Any) -> dict[str, Any]:
    coverage = compact_mapping(
        safe_mapping(coverage_value),
        (
            "auditComplete",
            "snapshotComplete",
            "structuredEvidenceCount",
            "strongEvidenceCount",
        ),
    )
    gate = compact_mapping(
        safe_mapping(gate_value),
        (
            "eligible",
            "status",
            "reason",
            "hasSourceExcerpt",
            "needsReview",
            "hasConflict",
        ),
    )
    merged = dict(coverage)
    merged.update({key: value for key, value in gate.items() if key not in merged})
    return merged


def build_prompt_trade_samples(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in safe_list(value):
        if not isinstance(item, Mapping):
            continue
        payload = compact_mapping(
            item,
            (
                "market_title",
                "market_slug",
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
        if len(results) >= 5:
            break
    return results


def derive_finder_ai_deep_note(
    candidate: Any,
    *,
    ai_brief_note: str,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> str:
    explicit = normalize_brief_text(candidate)
    if explicit and not looks_like_weak_deep_note(
        explicit,
        payload=payload,
        wallet_result=wallet_result,
    ):
        return trim_note_text(explicit, limit=360)

    layered_input = safe_mapping(payload.get("layeredInput"))
    l2 = safe_mapping(layered_input.get("L2"))
    l3 = safe_mapping(layered_input.get("L3"))
    summary = safe_mapping(safe_mapping(wallet_result.get("structured_materials")).get("summary"))
    coverage = build_prompt_coverage_snapshot(
        l3.get("coverage"),
        safe_mapping(payload.get("briefGeneration")).get("gate"),
    )
    profile_snapshot = build_prompt_profile_snapshot(wallet_result)
    operation_snapshot = build_prompt_operation_snapshot(wallet_result)
    top_trades = build_prompt_top_trades(wallet_result.get("top_trades"))
    region = infer_focus_region(payload=payload, wallet_result=wallet_result)

    base = first_non_empty(
        payload.get("strategyFocus"),
        l2.get("strategyFocusCandidate"),
        first_brief_sentence(ai_brief_note),
        l3.get("headline"),
        summary.get("headline"),
    )
    source_excerpt = first_non_empty(
        l2.get("sourceExcerpt"),
        payload.get("sourceExcerpt"),
        summary.get("source_excerpt"),
    )
    strategy_note = first_non_empty(*safe_list(l3.get("strategyNotes"))[:2])
    signal_reason = first_prompt_signal_reason(l2)
    metric_fragment = first_prompt_metric_fragment(l2)
    profile_insight = build_profile_insight(profile_snapshot)
    operation_insight = build_operation_insight(operation_snapshot)
    top_trade_insight = build_top_trade_insight(top_trades)

    sentences: list[str] = []
    if region:
        sentences.append(
            f"\u8fd9\u4e2a\u5730\u5740\u66f4\u50cf\u628a {region} \u8fd9\u7c7b\u719f\u6089\u57ce\u5e02\u5f53\u4e3b\u6218\u573a\u3001\u53cd\u590d\u4e0b\u6ce8\u7684\u5929\u6c14\u4ea4\u6613\u8005\u3002"
        )
    elif base:
        sentences.append(ensure_sentence(base))

    candidate_sentences: list[str] = []
    if profile_insight and not region:
        candidate_sentences.append(profile_insight)
    if top_trade_insight:
        candidate_sentences.append(top_trade_insight)
    elif profile_insight:
        candidate_sentences.append(profile_insight)
    if operation_insight:
        candidate_sentences.append(operation_insight)
    evidence_sentence = build_deep_note_evidence_sentence(
        signal_reason=signal_reason,
        source_excerpt=source_excerpt,
        region=region,
    )
    if evidence_sentence:
        candidate_sentences.append(evidence_sentence)
    if strategy_note:
        candidate_sentences.append(
            f"\u4ece\u7b56\u7565\u8282\u594f\u770b\uff0c\u5b83\u4e0d\u50cf\u968f\u673a\u626b\u5355\uff0c\u66f4\u50cf\u5728\u91cd\u590d\u4e00\u5957\u5df2\u7ecf\u9a8c\u8bc1\u8fc7\u7684\u505a\u6cd5\uff1a{strip_sentence_punctuation(strategy_note)}\u3002"
        )
    elif metric_fragment:
        candidate_sentences.append(
            f"\u5173\u952e\u6307\u6807\u91cc\u80fd\u770b\u5230 {metric_fragment}\uff0c\u8fd9\u8bf4\u660e\u5b83\u7684\u4f18\u52bf\u66f4\u50cf\u53ef\u91cd\u590d\u7684\u884c\u4e3a\u6a21\u5f0f\uff0c\u800c\u4e0d\u662f\u5355\u6b21\u8d70\u8fd0\u3002"
        )

    for sentence in dedupe_sentences(candidate_sentences)[:3]:
        sentences.append(sentence)

    caveat_sentence = build_deep_note_caveat_sentence(
        coverage=coverage,
        region=region,
        operation_snapshot=operation_snapshot,
    )
    if caveat_sentence:
        sentences.append(caveat_sentence)

    deep_note = " ".join(dedupe_sentences(sentences)[:5])
    if deep_note:
        return trim_note_text(deep_note, limit=360)

    return trim_note_text(ai_brief_note, limit=360)


def looks_like_weak_deep_note(
    text: str,
    *,
    payload: Mapping[str, Any],
    wallet_result: Mapping[str, Any],
) -> bool:
    normalized = normalize_brief_text(text)
    if not normalized:
        return True
    if is_likely_english_fragment(normalized) or not contains_cjk_text(normalized):
        return True
    if len(normalized) < 56:
        return True

    generic_hits = sum(
        marker in normalized
        for marker in (
            "策略一致性",
            "交易风格",
            "行为模式",
            "整体风格",
            "区域偏好",
            "较强",
            "明显",
            "值得关注",
            "持续观察",
            "后续观察",
            "仍需观察",
            "深度分析",
        )
    )
    if generic_hits >= 2:
        return True
    return not has_persona_anchor(normalized, payload=payload, wallet_result=wallet_result)


def derive_finder_ai_brief_short(*candidates: Any) -> str:
    for candidate in candidates:
        text = normalize_brief_text(candidate)
        if not text:
            continue
        sentence = first_brief_sentence(text)
        if sentence:
            text = sentence
        return trim_brief_text(text, limit=FINDER_AI_BRIEF_SHORT_MAX_LENGTH)
    return ""


def normalize_brief_text(value: Any) -> str:
    text = text_value(value)
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def trim_strategy_focus(text: Any, *, limit: int = 24) -> str:
    normalized = strip_sentence_punctuation(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip("，。!?！？:：;；、 ")


def first_brief_sentence(text: str) -> str:
    parts = re.split(r"[。！？.!?；;]\s*", text, maxsplit=1)
    return parts[0].strip() if parts else text.strip()


def trim_brief_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return text[:limit]
    shortened = text[: limit - len(suffix)].rstrip("，。!?！？:：;；、 ")
    if not shortened:
        return text[:limit]
    return f"{shortened}{suffix}"


def trim_note_text(text: str, *, limit: int) -> str:
    normalized = normalize_brief_text(text)
    if len(normalized) <= limit:
        return normalized
    suffix = "..."
    if limit <= len(suffix):
        return normalized[:limit]
    shortened = normalized[: limit - len(suffix)].rstrip("，。!?！？:：;；、 ")
    if not shortened:
        return normalized[:limit]
    return f"{shortened}{suffix}"


def ensure_sentence(text: str) -> str:
    normalized = strip_sentence_punctuation(text)
    if not normalized:
        return ""
    return f"{normalized}。"


def strip_sentence_punctuation(text: Any) -> str:
    normalized = normalize_brief_text(text)
    return normalized.rstrip("，。!?！？:：;；")


def first_prompt_signal_reason(l2: Mapping[str, Any]) -> str:
    for item in safe_list(l2.get("primarySignals")) + safe_list(l2.get("labelHits")):
        if not isinstance(item, Mapping):
            continue
        reason = first_non_empty(item.get("reason"), safe_mapping(item.get("evidence")).get("reason"))
        if reason:
            return reason
    return ""


def build_deep_note_evidence_sentence(
    *,
    signal_reason: str,
    source_excerpt: str,
    region: str,
) -> str:
    rendered_signal = render_deep_note_evidence_fragment(signal_reason, region=region)
    if rendered_signal:
        return f"\u786c\u8bc1\u636e\u4e5f\u6446\u5728\u8fd9\uff1a{rendered_signal}\u3002"

    rendered_excerpt = render_deep_note_evidence_fragment(source_excerpt, region=region)
    if rendered_excerpt:
        return f"\u771f\u8981\u843d\u5230\u884c\u4e3a\u4e0a\u770b\uff0c{rendered_excerpt}\u3002"
    return ""


def build_deep_note_caveat_sentence(
    *,
    coverage: Mapping[str, Any],
    region: str,
    operation_snapshot: Mapping[str, Any],
) -> str:
    verification_target = build_deep_note_verification_target(
        region=region,
        operation_snapshot=operation_snapshot,
    )
    if coverage.get("needsReview") or not coverage.get("auditComplete"):
        if verification_target:
            return (
                f"\u4e0d\u8fc7\u5f53\u524d\u5ba1\u8ba1\u8986\u76d6\u8fd8\u6ca1\u6709\u8865\u9f50\uff0c"
                f"\u540e\u9762\u771f\u6b63\u8981\u7ee7\u7eed\u76ef\u7684\uff0c\u662f\u8fd9\u79cd{verification_target}\u7684\u6253\u6cd5\u80fd\u4e0d\u80fd\u5728\u66f4\u591a\u4ea4\u6613\u65e5\u91cc\u7ee7\u7eed\u590d\u73b0\u3002"
            )
        return (
            "\u4e0d\u8fc7\u5f53\u524d\u5ba1\u8ba1\u8986\u76d6\u8fd8\u6ca1\u6709\u8865\u9f50\uff0c"
            "\u8fd9\u4e2a\u5224\u65ad\u8fd8\u5f97\u7ee7\u7eed\u770b\u540e\u9762\u7684\u4ea4\u6613\u65e5\u80fd\u4e0d\u80fd\u590d\u73b0\u3002"
        )

    if verification_target:
        return (
            f"\u540e\u9762\u771f\u6b63\u8981\u7ee7\u7eed\u76ef\u7684\uff0c\u662f\u8fd9\u79cd{verification_target}\u7684\u6253\u6cd5\u80fd\u4e0d\u80fd\u5728\u66f4\u591a\u4ea4\u6613\u65e5\u91cc\u7ee7\u7eed\u590d\u73b0\uff0c"
            "\u800c\u4e0d\u662f\u53ea\u5728\u8fd9\u6279\u6837\u672c\u91cc\u6210\u7acb\u3002"
        )
    return (
        "\u540e\u9762\u771f\u6b63\u8981\u7ee7\u7eed\u76ef\u7684\uff0c"
        "\u662f\u8fd9\u4e2a\u5224\u65ad\u80fd\u4e0d\u80fd\u5728\u66f4\u591a\u4ea4\u6613\u65e5\u91cc\u7ee7\u7eed\u590d\u73b0\uff0c\u800c\u4e0d\u662f\u53ea\u5728\u8fd9\u6279\u6837\u672c\u91cc\u6210\u7acb\u3002"
    )


def build_deep_note_verification_target(
    *,
    region: str,
    operation_snapshot: Mapping[str, Any],
) -> str:
    parts: list[str] = []
    if region:
        parts.append(f"\u56f4\u7ed5 {region} \u96c6\u4e2d\u4e0b\u6ce8")

    trade_multiple = operation_snapshot.get("trade_liquidity_profit_multiple")
    final_multiple = operation_snapshot.get("final_settlement_profit_multiple")
    if isinstance(trade_multiple, (int, float)) and isinstance(final_multiple, (int, float)):
        if trade_multiple < 1 and final_multiple > 1:
            parts.append("\u628a\u76c8\u5229\u62ff\u5230\u7ed3\u7b97\u7aef")
        elif trade_multiple > 1 and final_multiple > 1:
            parts.append("\u5206\u5c42\u5151\u73b0\u5229\u6da6")

    return "\u3001".join(parts)


def render_deep_note_evidence_fragment(text: str, *, region: str) -> str:
    normalized = strip_sentence_punctuation(text)
    if not normalized:
        return ""
    if not is_likely_english_fragment(normalized):
        return normalized

    lowered = normalized.lower()
    focus_region = region or "\u540c\u4e00\u4e2a\u533a\u57df"
    if "repeat" in lowered and "same region" in lowered:
        return f"{focus_region} \u7684\u5929\u6c14\u4ea4\u6613\u5728\u53cd\u590d\u51fa\u73b0"
    if "repeat" in lowered and "same city" in lowered and "conviction" in lowered:
        return "\u53ea\u8981\u786e\u4fe1\u5ea6\u4e0a\u6765\uff0c\u5b83\u5c31\u4f1a\u53cd\u590d\u56de\u5230\u540c\u4e00\u5ea7\u57ce\u5e02\u51fa\u624b"
    if "repeat" in lowered and "same city" in lowered:
        return "\u5b83\u4f1a\u53cd\u590d\u56de\u5230\u540c\u4e00\u5ea7\u57ce\u5e02\u4e0b\u6ce8"
    if "cluster" in lowered and "same city" in lowered:
        return "\u4ea4\u6613\u7b79\u7801\u4f1a\u660e\u663e\u5411\u540c\u4e00\u5ea7\u57ce\u5e02\u96c6\u4e2d"
    if "weather trades" in lowered and "region" in lowered:
        return f"{focus_region} \u8fd9\u7c7b\u533a\u57df\u7684\u5929\u6c14\u4ea4\u6613\u5728\u53cd\u590d\u51fa\u73b0"
    return normalized


def is_likely_english_fragment(text: str) -> bool:
    letters = sum(char.isascii() and char.isalpha() for char in text)
    if letters < 6:
        return False
    visible = sum(not char.isspace() for char in text)
    if visible == 0:
        return False
    ascii_chars = sum(char.isascii() for char in text if not char.isspace())
    return ascii_chars / visible >= 0.6


def contains_cjk_text(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def first_prompt_metric_fragment(l2: Mapping[str, Any]) -> str:
    for item in safe_list(l2.get("keyMetrics")):
        if not isinstance(item, Mapping):
            continue
        label = first_non_empty(item.get("label"), item.get("key"))
        value = item.get("value")
        if not label or value in (None, ""):
            continue
        rendered = render_metric_value(value)
        return f"{label}={rendered}" if rendered else label
    return ""


def render_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if 0 <= value <= 1:
            return f"{value:.1%}"
        if abs(value) >= 100:
            return f"{value:.0f}"
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return text_value(value)


def dedupe_sentences(values: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        normalized = normalize_brief_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        results.append(normalized)
    return results
