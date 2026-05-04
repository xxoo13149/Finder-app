from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool import finder_ai_generation


WALLET = "0xabc1230000000000000000000000000000000000"


def build_payload(*, status: str = "ready", enabled: bool = True) -> dict[str, Any]:
    return {
        "sourceName": "finder",
        "runId": "run-001",
        "normalizedAddress": WALLET,
        "wallet": {
            "address": WALLET,
            "displayName": "weather-pro",
            "alias": "weather-pro",
        },
        "matched": True,
        "strategyFocus": "旧策略焦点",
        "aiBriefShort": "",
        "aiBriefNote": "",
        "aiDeepNote": "",
        "evidenceLevel": "structured_only",
        "hasConflict": False,
        "needsReview": False,
        "labels": [
            {
                "kind": "style",
                "value": "高频地区",
                "source": "finder",
                "evidence": "地区集中度高",
            }
        ],
        "primarySignals": [
            {
                "key": "high_frequency_region",
                "label": "高频地区",
                "matched": True,
                "reason": "同一区域重复下注",
            }
        ],
        "keyMetrics": [
            {
                "key": "weather_trade_ratio",
                "label": "天气交易占比",
                "value": 0.82,
            }
        ],
        "sourceExcerpt": "近期主要集中在天气市场的同一区域。",
        "weatherSignals": {
            "marketScope": "weather",
            "resolutionSource": "",
            "forecastBasis": "",
            "timingWindow": "day",
            "edgeStyle": "",
            "weatherDrivers": [],
            "evidenceQuality": "structured_only",
        },
        "providerMeta": {
            "provider": "deepseek",
            "model": "",
            "promptVersion": "finder-weather-brief-v1",
            "generatedAt": "",
            "inputHash": "sha256:test",
            "generationScope": "brief",
            "outputSchemaVersion": "finder-ai-v1",
            "cacheKey": f"{WALLET}|sha256:test|finder-weather-brief-v1|deepseek-v4-flash|finder-ai-v1",
        },
        "layeredInput": {
            "L0": {
                "normalizedAddress": WALLET,
                "sourceName": "finder",
                "runId": "run-001",
                "updatedAt": "2026-05-05T00:00:00+00:00",
                "version": "finder-ai-v1",
            },
            "L2": {
                "primarySignals": [
                    {
                        "key": "high_frequency_region",
                        "label": "高频地区",
                        "matched": True,
                        "reason": "同一区域重复下注",
                    }
                ],
                "labelHits": [],
                "labels": [],
                "keyMetrics": [],
                "sourceExcerpt": "近期主要集中在天气市场的同一区域。",
                "strategyFocusCandidate": "旧策略焦点",
            },
            "L3": {
                "headline": "近期主要集中在天气市场的同一区域。",
                "strategyNotes": ["旧策略焦点"],
                "activityLevel": "normal",
            },
        },
        "briefGeneration": {
            "enabled": enabled,
            "status": status,
            "reason": "ready_for_brief" if status == "ready" else status,
            "gateVersion": "finder-ai-brief-gate-v1",
            "decisionSource": "structured_only",
            "scope": "brief",
            "promptVersion": "finder-weather-brief-v1",
            "cacheKey": f"{WALLET}|sha256:test|finder-weather-brief-v1|deepseek-v4-flash|finder-ai-v1",
            "gate": {
                "eligible": True,
                "status": status,
                "reason": "ready_for_brief" if status == "ready" else status,
                "hasNormalizedAddress": True,
                "structuredEvidenceCount": 2,
                "strongEvidenceCount": 1,
                "hasSourceExcerpt": True,
                "auditComplete": True,
                "hasConflict": False,
                "needsReview": False,
                "generationScope": "brief",
            },
        },
    }


class FinderAiGenerationTests(unittest.TestCase):
    def test_build_finder_ai_prompt_messages_includes_richer_context_fields(self) -> None:
        payload = build_payload()
        payload["weatherSignals"]["edgeStyle"] = "event-driven"
        payload["weatherSignals"]["weatherDrivers"] = ["temperature", "rainfall"]
        payload["layeredInput"]["L2"]["labelHits"] = [
            {
                "label_key": "high_frequency_region",
                "reason": "Repeated weather trades in the same region",
                "example_markets": ["NYC rain"],
            }
        ]
        payload["layeredInput"]["L2"]["keyMetrics"] = [
            {
                "key": "weather_trade_ratio",
                "label": "Weather trade ratio",
                "value": 0.82,
            }
        ]
        payload["briefGeneration"]["gate"]["strongEvidenceCount"] = 2
        wallet_result = {
            "structured_materials": {
                "summary": {
                    "headline": "Structured headline from summary",
                }
            }
        }

        messages = finder_ai_generation.build_finder_ai_prompt_messages(
            payload=payload,
            wallet_result=wallet_result,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        prompt_context = json.loads(messages[1]["content"])
        self.assertEqual(prompt_context["normalizedAddress"], WALLET)
        self.assertEqual(prompt_context["wallet"]["displayName"], "weather-pro")
        self.assertEqual(prompt_context["headline"], payload["layeredInput"]["L3"]["headline"])
        self.assertEqual(prompt_context["strategyFocusCandidate"], payload["layeredInput"]["L2"]["strategyFocusCandidate"])
        self.assertEqual(prompt_context["weatherSignals"]["edgeStyle"], "event-driven")
        self.assertEqual(prompt_context["weatherSignals"]["weatherDrivers"], ["temperature", "rainfall"])
        self.assertEqual(prompt_context["labelHits"][0]["label_key"], "high_frequency_region")
        self.assertEqual(prompt_context["keyMetrics"][0]["key"], "weather_trade_ratio")
        self.assertEqual(prompt_context["gate"]["strongEvidenceCount"], 2)
        self.assertEqual(prompt_context["updatedAt"], payload["layeredInput"]["L0"]["updatedAt"])

    def test_generate_finder_ai_brief_skips_when_not_ready(self) -> None:
        payload = build_payload(status="needs_review")

        with patch.object(
            finder_ai_generation,
            "request_deepseek_finder_ai_brief",
            side_effect=AssertionError("should not call provider"),
        ):
            result = finder_ai_generation.generate_finder_ai_brief(
                payload=payload,
                wallet_result={},
            )

        self.assertEqual(result["briefGeneration"]["status"], "needs_review")
        self.assertEqual(result["strategyFocus"], "旧策略焦点")
        self.assertEqual(result["aiBriefShort"], "")
        self.assertEqual(result["aiBriefNote"], "")

    def test_generate_finder_ai_brief_skips_without_api_key(self) -> None:
        payload = build_payload()

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                finder_ai_generation,
                "request_deepseek_finder_ai_brief",
                side_effect=AssertionError("should not call provider"),
            ):
                result = finder_ai_generation.generate_finder_ai_brief(
                    payload=payload,
                    wallet_result={},
                )

        self.assertEqual(result["briefGeneration"]["status"], "ready")
        self.assertEqual(result["briefGeneration"]["reason"], "ready_for_brief")
        self.assertEqual(result["aiBriefNote"], "")
        self.assertEqual(result["providerMeta"]["generatedAt"], "")

    def test_generate_finder_ai_brief_uses_cached_result(self) -> None:
        payload = build_payload()
        cached = {
            "strategyFocus": "缓存后的策略焦点",
            "aiBriefShort": "缓存短摘要",
            "aiBriefNote": "这是命中缓存后的 AI 简报。",
            "generatedAt": "2026-05-05T08:00:00+00:00",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "requestId": "cache-request-id",
        }

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with patch.object(finder_ai_generation, "read_cached_finder_ai_brief", return_value=cached):
                with patch.object(
                    finder_ai_generation,
                    "request_deepseek_finder_ai_brief",
                    side_effect=AssertionError("should not call provider when cache hits"),
                ):
                    result = finder_ai_generation.generate_finder_ai_brief(
                        payload=payload,
                        wallet_result={},
                    )

        self.assertEqual(result["strategyFocus"], "缓存后的策略焦点")
        self.assertEqual(result["aiBriefShort"], "缓存短摘要")
        self.assertEqual(result["aiBriefNote"], "这是命中缓存后的 AI 简报。")
        self.assertEqual(result["briefGeneration"]["status"], "cached")
        self.assertEqual(result["briefGeneration"]["reason"], "cache_hit")
        self.assertEqual(result["providerMeta"]["generatedAt"], "2026-05-05T08:00:00+00:00")
        self.assertEqual(result["providerMeta"]["requestId"], "cache-request-id")
        self.assertEqual(result["providerMeta"]["promptVersion"], "finder-weather-brief-v1")
        self.assertEqual(result["primarySignals"][0]["key"], "high_frequency_region")

    def test_generate_finder_ai_brief_marks_failed_when_provider_errors(self) -> None:
        payload = build_payload()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with patch.object(finder_ai_generation, "read_cached_finder_ai_brief", return_value={}):
                with patch.object(
                    finder_ai_generation,
                    "request_deepseek_finder_ai_brief",
                    side_effect=RuntimeError("provider exploded"),
                ):
                    result = finder_ai_generation.generate_finder_ai_brief(
                        payload=payload,
                        wallet_result={},
                    )

        self.assertEqual(result["briefGeneration"]["status"], "failed")
        self.assertEqual(result["briefGeneration"]["reason"], "provider_error")
        self.assertIn("provider exploded", result["briefGeneration"]["lastError"])
        self.assertEqual(result["aiBriefShort"], "")
        self.assertEqual(result["aiBriefNote"], "")
        self.assertEqual(result["providerMeta"]["model"], "deepseek-v4-flash")
        self.assertEqual(result["providerMeta"]["generatedAt"], "")
        self.assertNotIn("requestId", result["providerMeta"])

    def test_generate_finder_ai_brief_writes_generated_result_and_cache(self) -> None:
        payload = build_payload()
        generated = {
            "strategyFocus": "新的策略焦点",
            "aiBriefShort": "新的短摘要",
            "aiBriefNote": "这是新生成的 AI 简报，用于详情页展示。",
            "generatedAt": "2026-05-05T09:00:00+00:00",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "requestId": "req-generated-001",
        }

        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_MODEL": "deepseek-v4-flash",
            },
            clear=True,
        ):
            with patch.object(finder_ai_generation, "read_cached_finder_ai_brief", return_value={}):
                with patch.object(
                    finder_ai_generation,
                    "request_deepseek_finder_ai_brief",
                    return_value=generated,
                ) as request_mock:
                    with patch.object(finder_ai_generation, "write_cached_finder_ai_brief") as write_mock:
                        result = finder_ai_generation.generate_finder_ai_brief(
                            payload=payload,
                            wallet_result={"structured_materials": {"summary": {"headline": "headline"}}},
                        )

        request_mock.assert_called_once()
        write_mock.assert_called_once_with(
            payload["providerMeta"]["cacheKey"],
            generated,
        )
        self.assertEqual(result["strategyFocus"], "新的策略焦点")
        self.assertEqual(result["aiBriefShort"], "新的短摘要")
        self.assertEqual(result["aiBriefNote"], "这是新生成的 AI 简报，用于详情页展示。")
        self.assertEqual(result["briefGeneration"]["status"], "generated")
        self.assertEqual(result["briefGeneration"]["reason"], "generated")
        self.assertEqual(result["providerMeta"]["generatedAt"], "2026-05-05T09:00:00+00:00")
        self.assertEqual(result["providerMeta"]["requestId"], "req-generated-001")
        self.assertEqual(result["providerMeta"]["promptVersion"], "finder-weather-brief-v1")
        self.assertEqual(result["providerMeta"]["inputHash"], "sha256:test")

    def test_request_deepseek_finder_ai_brief_accepts_json_wrapped_output_and_derives_short_summary(self) -> None:
        payload = build_payload()
        response_payload = {
            "id": "req-compat-001",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {
                        "content": """```json
{"strategyFocus":"Weather momentum focus","aiBriefNote":"Weather momentum focus. Repeats the same region when conviction is high. Watch concentration risk."}
```"""
                    }
                }
            ],
        }

        with patch.object(
            finder_ai_generation,
            "post_deepseek_json",
            return_value=response_payload,
        ):
            generated = finder_ai_generation.request_deepseek_finder_ai_brief(
                api_key="test-key",
                model="deepseek-v4-flash",
                payload=payload,
                wallet_result={"structured_materials": {"summary": {"headline": "headline"}}},
            )

        self.assertEqual(generated["strategyFocus"], "Weather momentum focus")
        self.assertEqual(generated["aiBriefNote"], "Weather momentum focus. Repeats the same region when conviction is high. Watch concentration risk.")
        self.assertEqual(generated["aiBriefShort"], "Weather momentum focus")
        self.assertEqual(generated["provider"], "deepseek")
        self.assertEqual(generated["model"], "deepseek-v4-flash")
        self.assertEqual(generated["requestId"], "req-compat-001")

    def test_derive_finder_ai_brief_short_prefers_first_sentence_over_mechanical_truncation(self) -> None:
        short = finder_ai_generation.derive_finder_ai_brief_short(
            "",
            "",
            "First sentence is crisp; Second sentence keeps explaining the setup and would be redundant in preview.",
        )

        self.assertEqual(short, "First sentence is crisp")


if __name__ == "__main__":
    unittest.main()
