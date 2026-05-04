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

from .finder_ai_contract import FINDER_AI_MODEL, safe_mapping, text_value


UTC = timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FINDER_AI_CACHE_DIR = PROJECT_ROOT / ".cache" / "finder-ai"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_TIMEOUT_SECONDS = 45.0
FINDER_AI_BRIEF_SHORT_MAX_LENGTH = 36


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

    if text_value(result.get("aiBriefNote")):
        return result

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return result

    provider_meta = safe_mapping(result.get("providerMeta"))
    model = (
        (os.getenv("DEEPSEEK_MODEL") or "").strip()
        or text_value(provider_meta.get("model"))
        or FINDER_AI_MODEL
    )
    provider_meta["model"] = model
    result["providerMeta"] = provider_meta

    cache_key = text_value(provider_meta.get("cacheKey") or brief_generation.get("cacheKey"))
    cached = read_cached_finder_ai_brief(cache_key)
    if cached:
        return apply_generated_finder_ai_brief(
            result=result,
            generated=cached,
            status="cached",
            reason="cache_hit",
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
    )


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
    strategy_focus = text_value(parsed.get("strategyFocus"))
    ai_brief_short = derive_finder_ai_brief_short(
        parsed.get("aiBriefShort"),
        strategy_focus,
        ai_brief_note,
    )
    return {
        "strategyFocus": strategy_focus,
        "aiBriefShort": ai_brief_short,
        "aiBriefNote": ai_brief_note,
        "aiDeepNote": text_value(parsed.get("aiDeepNote")),
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
        "activityLevel": text_value(l3.get("activityLevel")),
        "primarySignals": l2.get("primarySignals") if isinstance(l2.get("primarySignals"), list) else [],
        "labelHits": l2.get("labelHits") if isinstance(l2.get("labelHits"), list) else [],
        "labels": l2.get("labels") if isinstance(l2.get("labels"), list) else [],
        "keyMetrics": l2.get("keyMetrics") if isinstance(l2.get("keyMetrics"), list) else [],
        "weatherSignals": {
            "marketScope": text_value(weather_signals.get("marketScope")),
            "timingWindow": text_value(weather_signals.get("timingWindow")),
            "edgeStyle": text_value(weather_signals.get("edgeStyle")),
            "evidenceQuality": text_value(weather_signals.get("evidenceQuality")),
            "weatherDrivers": weather_signals.get("weatherDrivers") if isinstance(weather_signals.get("weatherDrivers"), list) else [],
        },
        "gate": safe_mapping(payload.get("briefGeneration")).get("gate"),
        "updatedAt": text_value(l0.get("updatedAt")),
    }

    return [
        {
            "role": "system",
            "content": (
                "你是 Finder 的 Polymarket 天气钱包分析助手。"
                "你只能依据给定结构化证据输出中文结论，不要编造未提供的事实。"
                "请输出严格 JSON，对象只包含 strategyFocus、aiBriefShort 和 aiBriefNote 三个字段。"
                "strategyFocus 保持一句短语；aiBriefShort 控制在 18 到 36 个中文字符内，适合列表和扩展程序小空间展示；"
                "aiBriefNote 用 2 到 4 句中文，说明该地址更像什么类型、为什么这样判断、当前还需要注意什么。"
            ),
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
) -> dict[str, Any]:
    updated = dict(result)
    strategy_focus = text_value(generated.get("strategyFocus"))
    if strategy_focus:
        updated["strategyFocus"] = strategy_focus
    ai_brief_note = text_value(generated.get("aiBriefNote"))
    updated["aiBriefNote"] = ai_brief_note
    updated["aiDeepNote"] = text_value(generated.get("aiDeepNote"))
    updated["aiBriefShort"] = derive_finder_ai_brief_short(
        generated.get("aiBriefShort"),
        strategy_focus,
        ai_brief_note,
        updated.get("aiDeepNote"),
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


def first_brief_sentence(text: str) -> str:
    parts = re.split(r"[。！？；;]\s*", text, maxsplit=1)
    return parts[0].strip() if parts else text.strip()


def trim_brief_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    shortened = text[:limit].rstrip("，。、!?！？:： ")
    return f"{shortened}..."
