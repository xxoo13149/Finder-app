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
from polymarket_weather_tool.finder_ai_contract import FINDER_AI_PROMPT_VERSION


WALLET = "0xabc1230000000000000000000000000000000000"


def build_payload(*, status: str = "ready", enabled: bool = True) -> dict[str, Any]:
    cache_key = f"{WALLET}|sha256:test|{FINDER_AI_PROMPT_VERSION}|deepseek-v4-flash|finder-ai-v1"
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
        "strategyFocus": "Weather momentum",
        "aiBriefShort": "",
        "aiBriefNote": "",
        "aiDeepNote": "",
        "evidenceLevel": "structured_only",
        "hasConflict": False,
        "needsReview": False,
        "labels": [
            {
                "kind": "style",
                "value": "Regional concentration",
                "source": "finder",
                "evidence": "Trades cluster in the same city",
            }
        ],
        "primarySignals": [
            {
                "key": "high_frequency_region",
                "label": "Regional repetition",
                "matched": True,
                "reason": "Repeated weather trades in the same region",
            }
        ],
        "keyMetrics": [
            {
                "key": "weather_trade_ratio",
                "label": "Weather trade ratio",
                "value": 0.82,
            }
        ],
        "sourceExcerpt": "Repeats the same city when conviction is high.",
        "weatherSignals": {
            "marketScope": "weather",
            "resolutionSource": "",
            "forecastBasis": "",
            "timingWindow": "day",
            "edgeStyle": "event-driven",
            "weatherDrivers": ["temperature", "rainfall"],
            "evidenceQuality": "structured_only",
        },
        "providerMeta": {
            "provider": "deepseek",
            "model": "",
            "promptVersion": FINDER_AI_PROMPT_VERSION,
            "generatedAt": "",
            "inputHash": "sha256:test",
            "generationScope": "brief",
            "outputSchemaVersion": "finder-ai-v1",
            "cacheKey": cache_key,
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
                        "label": "Regional repetition",
                        "matched": True,
                        "reason": "Repeated weather trades in the same region",
                    }
                ],
                "labelHits": [],
                "labels": [],
                "keyMetrics": [
                    {
                        "key": "weather_trade_ratio",
                        "label": "Weather trade ratio",
                        "value": 0.82,
                    }
                ],
                "sourceExcerpt": "Repeats the same city when conviction is high.",
                "strategyFocusCandidate": "Weather momentum",
            },
            "L3": {
                "headline": "Same-city conviction trader",
                "strategyNotes": ["Focuses on repeated city setups"],
                "activityLevel": "normal",
                "behaviorSnapshot": {
                    "trade_count": 14,
                    "weather_trade_ratio": 0.82,
                    "dominant_region": "Dallas",
                    "closed_position_win_rate": 0.71,
                    "unified_profit_multiple": 1.46,
                    "snapshot_complete": True,
                    "ignored_noise": "should_not_be_forwarded",
                },
                "coverage": {
                    "auditComplete": True,
                    "snapshotComplete": True,
                    "structuredEvidenceCount": 3,
                    "strongEvidenceCount": 2,
                    "extraNoise": "skip_me",
                },
            },
            "L4": {
                "tradeSamples": [
                    {
                        "market_title": "Will Dallas reach 75F on May 6?",
                        "market_slug": "dallas-75f-may-6",
                        "event_slug": "dallas-may-6",
                        "city": "Dallas",
                        "side": "BUY",
                        "size_usd": 120.5,
                        "entry_price": 0.42,
                        "current_price": 0.61,
                        "entered_at": "2026-05-05T08:30:00+00:00",
                        "market_date": "2026-05-06",
                        "outcome": "Yes",
                        "ignore_me": "noise",
                    }
                ]
            },
        },
        "briefGeneration": {
            "enabled": enabled,
            "status": status,
            "reason": "ready_for_brief" if status == "ready" else status,
            "gateVersion": "finder-ai-brief-gate-v1",
            "decisionSource": "structured_only",
            "scope": "brief",
            "promptVersion": FINDER_AI_PROMPT_VERSION,
            "cacheKey": cache_key,
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


def build_wallet_result() -> dict[str, Any]:
    return {
        "structured_materials": {
            "summary": {
                "headline": "Structured headline from summary",
            }
        },
        "profile": {
            "average_buy_price": {
                "weighted_average_price": 0.38,
                "median_price": 0.41,
            },
            "city_distribution": {
                "city_count": 6,
                "known_city_trade_count": 12,
                "unknown_city_trade_count": 2,
            },
            "closed_position_pnl": {
                "win_rate": 0.71,
                "profit_multiple": 1.63,
                "total_realized_pnl": 212.4,
            },
            "top_cities": {
                "by_realized_pnl": [
                    {
                        "city": "Dallas",
                        "region": "Dallas",
                        "trade_count": 5,
                        "trade_ratio": 0.36,
                        "realized_pnl": 120.5,
                        "closed_profit_multiple": 1.88,
                    }
                ],
                "by_buy_amount": [
                    {
                        "city": "London",
                        "region": "London",
                        "trade_count": 4,
                        "trade_ratio": 0.28,
                        "buy_amount": 180.0,
                        "net_trade_cashflow": 22.0,
                    }
                ],
            },
        },
        "operation_audit": {
            "complete": True,
            "record_count": 24,
            "profit_summary": {
                "trade_liquidity_profit_multiple": 0.92,
                "final_settlement_profit_multiple": 1.48,
                "unified_profit_multiple": 1.31,
                "trade_liquidity_record_count": 14,
                "final_settlement_record_count": 10,
            },
            "operations": {
                "convert": {"status": "not_found", "count": 0, "verified_count": 0, "partial_count": 0},
                "split": {"status": "not_found", "count": 0, "verified_count": 0, "partial_count": 0},
                "redeem": {"status": "partial", "count": 6, "verified_count": 0, "partial_count": 6},
                "swap": {"status": "not_found", "count": 0, "verified_count": 0, "partial_count": 0},
            },
        },
        "top_trades": [
            {
                "title": "Will Dallas reach 75F on May 6?",
                "eventSlug": "dallas-may-6",
                "slug": "dallas-75f-may-6",
                "side": "BUY",
                "size": 120.5,
                "price": 0.42,
                "outcome": "Yes",
                "timestamp": 1772363400,
            }
        ],
    }


class FinderAiGenerationTests(unittest.TestCase):
    def test_derive_finder_strategy_focus_rewrites_generic_english_label(self) -> None:
        payload = build_payload()
        strategy_focus = finder_ai_generation.derive_finder_strategy_focus(
            "Weather momentum",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertEqual(strategy_focus, "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")

    def test_derive_finder_strategy_focus_rewrites_english_label_with_region_anchor(self) -> None:
        payload = build_payload()
        strategy_focus = finder_ai_generation.derive_finder_strategy_focus(
            "Dallas weather momentum",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertEqual(strategy_focus, "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")

    def test_derive_finder_ai_brief_note_rewrites_thin_note_into_persona_summary(self) -> None:
        payload = build_payload()
        note = finder_ai_generation.derive_finder_ai_brief_note(
            "Weather momentum",
            ai_deep_note="",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertIn("Dallas", note)
        self.assertIn("\u5929\u6c14\u4ea4\u6613\u8005", note)
        self.assertIn("\u7ed3\u679c\u5151\u73b0", note)
        self.assertIn("\u540e\u9762\u8fd8\u662f\u8981\u76ef", note)
        self.assertIn("\u7ed3\u7b97\u7aef", note)
        self.assertIn("BUY", note)
        self.assertIn("120", note)

    def test_derive_finder_ai_brief_note_rewrites_template_note_without_persona_anchor(self) -> None:
        payload = build_payload()
        note = finder_ai_generation.derive_finder_ai_brief_note(
            "\u8fd9\u4e2a\u5730\u5740\u8868\u73b0\u51fa\u8f83\u5f3a\u7684\u7b56\u7565\u4e00\u81f4\u6027\u548c\u533a\u57df\u504f\u597d\uff0c\u6574\u4f53\u98ce\u683c\u660e\u663e\uff0c\u4ecd\u9700\u6301\u7eed\u89c2\u5bdf\u3002",
            ai_deep_note="",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertIn("Dallas", note)
        self.assertIn("\u7ed3\u679c\u5151\u73b0", note)
        self.assertNotIn("\u7b56\u7565\u4e00\u81f4\u6027", note)

    def test_derive_finder_ai_brief_short_rewrites_template_label_into_persona_tag(self) -> None:
        payload = build_payload()
        short = finder_ai_generation.derive_finder_ai_brief_short_with_context(
            "\u5929\u6c14\u4ea4\u6613\u7b56\u7565\u578b\u9009\u624b",
            ai_brief_note="",
            ai_deep_note="",
            strategy_focus=payload["strategyFocus"],
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertEqual(short, "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertLessEqual(len(short), 28)

    def test_derive_finder_ai_deep_note_avoids_repeating_region_profile_sentence(self) -> None:
        payload = build_payload()
        deep_note = finder_ai_generation.derive_finder_ai_deep_note(
            "",
            ai_brief_note="",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertIn("\u4e3b\u6218\u573a", deep_note)
        self.assertIn("BUY", deep_note)
        self.assertNotIn("\u4ece\u76c8\u5229\u5206\u5e03\u770b", deep_note)
        self.assertIn("\u53cd\u590d\u51fa\u73b0", deep_note)
        self.assertNotIn("Repeated weather trades in the same region", deep_note)
        self.assertIn("\u771f\u653e\u5927\u4ed3\u4f4d", deep_note)
        self.assertIn("\u6562\u628a\u4ed3\u4f4d\u62ff\u5230\u7ed3\u7b97", deep_note)
        self.assertIn("\u786c\u8bc1\u636e", deep_note)
        self.assertIn("\u66f4\u591a\u4ea4\u6613\u65e5", deep_note)
        self.assertIn("\u53ea\u5728\u8fd9\u6279\u6837\u672c\u91cc\u6210\u7acb", deep_note)
        self.assertNotIn("\u8fd9\u5957\u753b\u50cf\u771f\u6b63\u7ad9\u5f97\u4f4f\u7684\u524d\u63d0", deep_note)

    def test_derive_finder_ai_deep_note_rewrites_english_generated_note(self) -> None:
        payload = build_payload()
        deep_note = finder_ai_generation.derive_finder_ai_deep_note(
            "This generated deep note explains the repeatable pattern and the caveat.",
            ai_brief_note="",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertIn("Dallas", deep_note)
        self.assertIn("BUY", deep_note)
        self.assertIn("\u4e3b\u6218\u573a", deep_note)
        self.assertNotIn("This generated deep note", deep_note)

    def test_derive_finder_ai_brief_note_rewrites_generic_region_anchor(self) -> None:
        payload = build_payload()
        note = finder_ai_generation.derive_finder_ai_brief_note(
            "Dallas \u7b56\u7565\u4e00\u81f4\u6027\u8f83\u5f3a\uff0c\u6574\u4f53\u98ce\u683c\u660e\u663e\uff0c\u540e\u7eed\u4ecd\u9700\u6301\u7eed\u89c2\u5bdf\u3002",
            ai_deep_note="",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertIn("Dallas", note)
        self.assertIn("BUY", note)
        self.assertNotIn("\u7b56\u7565\u4e00\u81f4\u6027", note)

    def test_derive_finder_ai_brief_short_keeps_trimmed_output_within_limit(self) -> None:
        payload = build_payload()
        short = finder_ai_generation.derive_finder_ai_brief_short_with_context(
            "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0\uff0c\u800c\u4e14\u540e\u7eed\u9700\u8981\u7ee7\u7eed\u8ddf\u8e2a\u590d\u73b0",
            ai_brief_note="",
            ai_deep_note="",
            strategy_focus="Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0",
            payload=payload,
            wallet_result=build_wallet_result(),
        )

        self.assertLessEqual(len(short), 28)
        self.assertTrue(short.endswith("..."))

    def test_build_finder_ai_prompt_messages_requests_ai_deep_note(self) -> None:
        payload = build_payload()
        wallet_result = build_wallet_result()

        messages = finder_ai_generation.build_finder_ai_prompt_messages(
            payload=payload,
            wallet_result=wallet_result,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("aiDeepNote", messages[0]["content"])
        self.assertIn("behaviorSnapshot", messages[0]["content"])
        self.assertIn("tradeSamples", messages[0]["content"])
        self.assertIn("profileSnapshot", messages[0]["content"])
        self.assertIn("operationAuditSnapshot", messages[0]["content"])
        self.assertIn("topTrades", messages[0]["content"])
        self.assertIn("Avoid empty phrases", messages[0]["content"])
        self.assertIn("Simplified Chinese strategy conclusion", messages[0]["content"])
        self.assertIn("not an English tag or abstract label", messages[0]["content"])
        self.assertIn("scan-friendly preview line", messages[0]["content"])
        self.assertIn("28 Chinese characters", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        prompt_context = json.loads(messages[1]["content"])
        self.assertEqual(prompt_context["normalizedAddress"], WALLET)
        self.assertEqual(prompt_context["wallet"]["displayName"], "weather-pro")
        self.assertEqual(prompt_context["headline"], payload["layeredInput"]["L3"]["headline"])
        self.assertEqual(prompt_context["strategyNotes"], ["Focuses on repeated city setups"])
        self.assertEqual(prompt_context["behaviorSnapshot"]["trade_count"], 14)
        self.assertEqual(prompt_context["behaviorSnapshot"]["dominant_region"], "Dallas")
        self.assertNotIn("ignored_noise", prompt_context["behaviorSnapshot"])
        self.assertEqual(prompt_context["coverage"]["auditComplete"], True)
        self.assertEqual(prompt_context["coverage"]["structuredEvidenceCount"], 3)
        self.assertEqual(prompt_context["coverage"]["eligible"], True)
        self.assertNotIn("extraNoise", prompt_context["coverage"])
        self.assertEqual(prompt_context["tradeSamples"][0]["market_title"], "Will Dallas reach 75F on May 6?")
        self.assertEqual(prompt_context["tradeSamples"][0]["side"], "BUY")
        self.assertNotIn("ignore_me", prompt_context["tradeSamples"][0])
        self.assertEqual(prompt_context["profileSnapshot"]["city_count"], 6)
        self.assertEqual(prompt_context["profileSnapshot"]["top_realized_pnl_cities"][0]["city"], "Dallas")
        self.assertEqual(prompt_context["operationAuditSnapshot"]["record_count"], 24)
        self.assertEqual(prompt_context["operationAuditSnapshot"]["operation_statuses"][2]["operation"], "redeem")
        self.assertEqual(prompt_context["topTrades"][0]["title"], "Will Dallas reach 75F on May 6?")
        self.assertEqual(prompt_context["weatherSignals"]["edgeStyle"], "event-driven")
        self.assertEqual(prompt_context["weatherSignals"]["weatherDrivers"], ["temperature", "rainfall"])
        self.assertEqual(prompt_context["keyMetrics"][0]["key"], "weather_trade_ratio")
        self.assertEqual(prompt_context["gate"]["strongEvidenceCount"], 1)
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
        self.assertEqual(result["strategyFocus"], "Weather momentum")
        self.assertEqual(result["aiBriefShort"], "")
        self.assertEqual(result["aiBriefNote"], "")
        self.assertEqual(result["aiDeepNote"], "")

    def test_generate_finder_ai_brief_backfills_deep_note_without_api_key_when_brief_exists(self) -> None:
        payload = build_payload()
        payload["aiBriefNote"] = "Looks like a repeatable city-focused weather trader. Conviction clusters around the same setup."
        wallet_result = build_wallet_result()
        expected_short = "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                finder_ai_generation,
                "request_deepseek_finder_ai_brief",
                side_effect=AssertionError("should not call provider"),
            ):
                result = finder_ai_generation.generate_finder_ai_brief(
                    payload=payload,
                    wallet_result=wallet_result,
                )

        self.assertEqual(result["briefGeneration"]["status"], "fallback")
        self.assertEqual(result["briefGeneration"]["reason"], "local_fallback")
        self.assertEqual(result["aiBriefShort"], expected_short)
        self.assertTrue(result["aiDeepNote"])
        self.assertIn("Dallas", result["aiDeepNote"])
        self.assertIn("BUY", result["aiDeepNote"])
        self.assertIn("\u4e3b\u6218\u573a", result["aiDeepNote"])
        self.assertIn("\u590d\u73b0", result["aiDeepNote"])
        self.assertIn("\u7ed3\u7b97\u7aef", result["aiDeepNote"])
        self.assertEqual(result["providerMeta"]["generatedAt"], "")

    def test_generate_finder_ai_brief_builds_local_fallback_without_api_key_when_notes_are_empty(self) -> None:
        payload = build_payload()

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                finder_ai_generation,
                "request_deepseek_finder_ai_brief",
                side_effect=AssertionError("should not call provider"),
            ):
                result = finder_ai_generation.generate_finder_ai_brief(
                    payload=payload,
                    wallet_result=build_wallet_result(),
                )

        self.assertEqual(result["briefGeneration"]["status"], "fallback")
        self.assertEqual(result["briefGeneration"]["reason"], "local_fallback")
        self.assertEqual(result["strategyFocus"], "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertEqual(result["aiBriefShort"], "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertIn("Dallas", result["aiBriefNote"])
        self.assertIn("BUY", result["aiBriefNote"])
        self.assertIn("120", result["aiBriefNote"])
        self.assertIn("Dallas", result["aiDeepNote"])
        self.assertIn("\u786c\u8bc1\u636e", result["aiDeepNote"])

    def test_generate_finder_ai_brief_backfills_short_when_existing_notes_are_present(self) -> None:
        payload = build_payload()
        payload["aiBriefNote"] = "\u8fd9\u4e2a\u5730\u5740\u56f4\u7ed5 Dallas \u96c6\u4e2d\u4e0b\u6ce8\uff0c\u66f4\u50cf\u7b49\u7ed3\u679c\u5151\u73b0\u7684\u5929\u6c14\u4ea4\u6613\u8005\u3002"
        payload["aiDeepNote"] = (
            "\u8fd9\u4e2a\u5730\u5740\u66f4\u50cf\u628a Dallas \u5f53\u4e3b\u6218\u573a\u7684\u5929\u6c14\u4ea4\u6613\u8005\uff0c"
            "\u4f1a\u56f4\u7ed5\u719f\u6089\u57ce\u5e02\u53cd\u590d\u51fa\u624b\u3002\u6837\u672c\u91cc\u53ef\u4ee5\u770b\u5230 BUY \u8ba2\u5355\u548c\u5355\u7b14\u653e\u5927\u4ed3\u4f4d\uff0c"
            "\u6536\u76ca\u66f4\u504f\u5411\u9760\u7ed3\u7b97\u7aef\u5151\u73b0\u3002\u540e\u7eed\u8981\u770b\u8fd9\u79cd\u6253\u6cd5\u80fd\u5426\u5728\u66f4\u591a\u4ea4\u6613\u65e5\u590d\u73b0\u3002"
        )

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            with patch.object(
                finder_ai_generation,
                "request_deepseek_finder_ai_brief",
                side_effect=AssertionError("should not call provider"),
            ):
                result = finder_ai_generation.generate_finder_ai_brief(
                    payload=payload,
                    wallet_result=build_wallet_result(),
                )

        self.assertEqual(result["briefGeneration"]["status"], "fallback")
        self.assertEqual(result["briefGeneration"]["reason"], "local_existing")
        self.assertEqual(result["aiBriefShort"], "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertLessEqual(len(result["aiBriefShort"]), 28)

    def test_generate_finder_ai_brief_uses_cached_result_and_backfills_missing_deep_note(self) -> None:
        payload = build_payload()
        expected_short = "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"
        cached = {
            "strategyFocus": "Cached weather momentum",
            "aiBriefNote": "Cached brief note. Repeats the same city when conviction is high. Watch concentration risk.",
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
                            wallet_result=build_wallet_result(),
                    )

        self.assertEqual(result["strategyFocus"], "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertEqual(result["aiBriefShort"], expected_short)
        self.assertTrue(result["aiDeepNote"])
        self.assertEqual(result["briefGeneration"]["status"], "cached")
        self.assertEqual(result["briefGeneration"]["reason"], "cache_hit")
        self.assertEqual(result["providerMeta"]["generatedAt"], "2026-05-05T08:00:00+00:00")
        self.assertEqual(result["providerMeta"]["requestId"], "cache-request-id")
        self.assertEqual(result["providerMeta"]["promptVersion"], FINDER_AI_PROMPT_VERSION)

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
        self.assertEqual(result["aiDeepNote"], "")
        self.assertEqual(result["providerMeta"]["model"], "deepseek-v4-flash")
        self.assertEqual(result["providerMeta"]["generatedAt"], "")
        self.assertNotIn("requestId", result["providerMeta"])

    def test_generate_finder_ai_brief_writes_generated_result_and_cache(self) -> None:
        payload = build_payload()
        expected_short = "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"
        generated = {
            "strategyFocus": "New weather momentum",
            "aiBriefShort": "New short summary",
            "aiBriefNote": "This is a generated analyst note for the detail view.",
            "aiDeepNote": "This generated deep note explains the repeatable pattern and the caveat.",
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
                            wallet_result=build_wallet_result(),
                        )

        request_mock.assert_called_once()
        write_mock.assert_called_once_with(
            payload["providerMeta"]["cacheKey"],
            generated,
        )
        self.assertEqual(result["strategyFocus"], "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertEqual(result["aiBriefShort"], expected_short)
        self.assertIn("Dallas", result["aiBriefNote"])
        self.assertIn("BUY", result["aiBriefNote"])
        self.assertNotIn("generated analyst note", result["aiBriefNote"])
        self.assertIn("Dallas", result["aiDeepNote"])
        self.assertIn("BUY", result["aiDeepNote"])
        self.assertNotIn("This generated deep note", result["aiDeepNote"])
        self.assertEqual(result["briefGeneration"]["status"], "generated")
        self.assertEqual(result["briefGeneration"]["reason"], "generated")
        self.assertEqual(result["providerMeta"]["generatedAt"], "2026-05-05T09:00:00+00:00")
        self.assertEqual(result["providerMeta"]["requestId"], "req-generated-001")
        self.assertEqual(result["providerMeta"]["promptVersion"], FINDER_AI_PROMPT_VERSION)
        self.assertEqual(result["providerMeta"]["inputHash"], "sha256:test")

    def test_generate_finder_ai_brief_recomputes_cache_key_after_model_override(self) -> None:
        payload = build_payload()
        expected_cache_key = f"{WALLET}|sha256:test|{FINDER_AI_PROMPT_VERSION}|deepseek-reasoner|finder-ai-v1"
        generated = {
            "strategyFocus": "Dallas \u7ed3\u7b97\u7aef\u6253\u6cd5",
            "aiBriefShort": "Dallas \u9ad8\u786e\u4fe1\u5929\u6c14\u5355",
            "aiBriefNote": "\u8fd9\u4e2a\u5730\u5740\u56f4\u7ed5 Dallas \u96c6\u4e2d\u4e0b\u6ce8\uff0c\u6837\u672c\u91cc\u80fd\u770b\u5230 BUY \u548c\u7ed3\u7b97\u7aef\u5151\u73b0\u3002",
            "aiDeepNote": "\u8fd9\u4e2a\u5730\u5740\u66f4\u50cf\u628a Dallas \u5f53\u6210\u4e3b\u6218\u573a\u7684\u5929\u6c14\u4ea4\u6613\u8005\u3002\u5b83\u4f1a\u56f4\u7ed5\u719f\u6089\u57ce\u5e02\u53cd\u590d\u51fa\u624b\uff0c\u4ee3\u8868\u6027 BUY \u8ba2\u5355\u4e5f\u80fd\u652f\u6491\u8fd9\u4e2a\u5224\u65ad\u3002\u6536\u76ca\u66f4\u504f\u5411\u7ed3\u7b97\u7aef\u5151\u73b0\uff0c\u540e\u7eed\u8981\u7ee7\u7eed\u770b\u590d\u73b0\u6027\u3002",
            "generatedAt": "2026-05-05T09:00:00+00:00",
            "provider": "deepseek",
            "model": "deepseek-reasoner",
        }

        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "deepseek-reasoner"},
            clear=True,
        ):
            with patch.object(finder_ai_generation, "read_cached_finder_ai_brief", return_value={}) as read_mock:
                with patch.object(finder_ai_generation, "request_deepseek_finder_ai_brief", return_value=generated):
                    with patch.object(finder_ai_generation, "write_cached_finder_ai_brief") as write_mock:
                        result = finder_ai_generation.generate_finder_ai_brief(
                            payload=payload,
                            wallet_result=build_wallet_result(),
                        )

        read_mock.assert_called_once_with(expected_cache_key)
        write_mock.assert_called_once_with(expected_cache_key, generated)
        self.assertEqual(result["providerMeta"]["cacheKey"], expected_cache_key)
        self.assertEqual(result["briefGeneration"]["cacheKey"], expected_cache_key)
        self.assertEqual(result["providerMeta"]["model"], "deepseek-reasoner")

    def test_request_deepseek_finder_ai_brief_accepts_json_wrapped_output_and_derives_deep_note(self) -> None:
        payload = build_payload()
        expected_short = "Dallas \u53cd\u590d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0"
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
                wallet_result=build_wallet_result(),
            )

        self.assertEqual(generated["strategyFocus"], "Dallas \u96c6\u4e2d\u4e0b\u6ce8\u3001\u504f\u7ed3\u7b97\u5151\u73b0")
        self.assertIn("Dallas", generated["aiBriefNote"])
        self.assertIn("BUY", generated["aiBriefNote"])
        self.assertNotIn("Weather momentum focus", generated["aiBriefNote"])
        self.assertEqual(generated["aiBriefShort"], expected_short)
        self.assertTrue(generated["aiDeepNote"])
        self.assertIn("Dallas", generated["aiDeepNote"])
        self.assertIn("BUY", generated["aiDeepNote"])
        self.assertIn("\u4e3b\u6218\u573a", generated["aiDeepNote"])
        self.assertIn("\u590d\u73b0", generated["aiDeepNote"])
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
