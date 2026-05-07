from __future__ import annotations

import io
import sys
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.client import (
    PolymarketClient,
    PolymarketRequestError,
    parse_accounting_snapshot_zip,
)


class ControlledClient(PolymarketClient):
    def __init__(self, outcomes: list[object], api_config: dict[str, object] | None = None) -> None:
        config: dict[str, object] = {
            "use_cache": False,
            "request_delay_seconds": 0,
            "retry_backoff_seconds": 0,
            "retry_jitter_seconds": 0,
            "cooldown_after_retryable_failure_seconds": 0,
        }
        if api_config:
            config.update(api_config)
        super().__init__(config)
        self.outcomes = list(outcomes)
        self.requested_urls: list[str] = []

    def _request_json(self, url: str) -> object:
        self.requested_urls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ControlledBytesClient(PolymarketClient):
    def __init__(self, outcomes: list[object]) -> None:
        super().__init__(
            {
                "use_cache": False,
                "request_delay_seconds": 0,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 0,
            }
        )
        self.outcomes = list(outcomes)
        self.requested_urls: list[str] = []
        self.accept_headers: list[str] = []
        self.payloads: list[bytes | None] = []

    def _request_bytes(self, url: str, *, accept: str, data: bytes | None = None) -> bytes:
        self.requested_urls.append(url)
        self.accept_headers.append(accept)
        self.payloads.append(data)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class ControlledGraphQLClient(PolymarketClient):
    def __init__(self, outcomes: list[object], api_config: dict[str, object] | None = None) -> None:
        config: dict[str, object] = {
            "use_cache": False,
            "request_delay_seconds": 0,
            "retry_backoff_seconds": 0,
            "retry_jitter_seconds": 0,
            "cooldown_after_retryable_failure_seconds": 0,
        }
        if api_config:
            config.update(api_config)
        super().__init__(config)
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, object]] = []

    def _request_json_via_post(self, url: str, *, body: dict[str, object]) -> object:
        self.requests.append({"url": url, "body": body})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def accounting_snapshot_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "positions.csv",
            "conditionId,title,currentValue\ncond-1,Rain market,12.5\n",
        )
        archive.writestr(
            "equity.csv",
            "timestamp,equity\n1777000000,101.5\n",
        )
    return buffer.getvalue()


class ClientTests(unittest.TestCase):
    def test_fetch_activity_page_passes_partition_params(self) -> None:
        client = ControlledClient([[]])

        client.fetch_activity_page(
            user="0xabc",
            limit=25,
            offset=50,
            activity_type="TRADE",
            start=100,
            end=200,
        )

        parsed = urllib.parse.urlparse(client.requested_urls[0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/activity")
        self.assertEqual(query["user"], ["0xabc"])
        self.assertEqual(query["limit"], ["25"])
        self.assertEqual(query["offset"], ["50"])
        self.assertEqual(query["type"], ["TRADE"])
        self.assertEqual(query["start"], ["100"])
        self.assertEqual(query["end"], ["200"])

    def test_get_json_retries_429_using_retry_after(self) -> None:
        rate_limited = HTTPError(
            url="https://data-api.polymarket.com/activity?user=0xabc&limit=1&offset=0",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "3"},
            fp=None,
        )
        client = ControlledClient([rate_limited, [{"id": "ok"}]], api_config={"retry_count": 1})
        sleep_calls: list[float] = []

        with patch("polymarket_weather_tool.client.time.sleep", side_effect=lambda seconds: sleep_calls.append(seconds)):
            payload = client.fetch_activity_page(user="0xabc", limit=1, offset=0)

        self.assertEqual(payload, [{"id": "ok"}])
        self.assertEqual(len(client.requested_urls), 2)
        self.assertTrue(any(seconds >= 3 for seconds in sleep_calls))

    def test_get_json_raises_structured_error_with_status_code(self) -> None:
        service_error = HTTPError(
            url="https://data-api.polymarket.com/positions?user=0xabc&limit=5&offset=0",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )
        client = ControlledClient(
            [service_error],
            api_config={"retry_count": 0, "collection_page_zero_retry_count": 0},
        )

        with self.assertRaises(PolymarketRequestError) as ctx:
            client.fetch_positions_page(user="0xabc", limit=5, offset=0)

        error = ctx.exception
        self.assertEqual(error.path, "/positions")
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.params["offset"], 0)
        self.assertIn("status=503", str(error))
        self.assertIn("Service Unavailable", str(error))

    def test_get_json_marks_transport_errors_retryable(self) -> None:
        client = ControlledClient(
            [URLError("connection reset")],
            api_config={"retry_count": 0, "collection_page_zero_retry_count": 0},
        )

        with self.assertRaises(PolymarketRequestError) as ctx:
            client.fetch_trades_page(user="0xabc", limit=5, offset=0)

        error = ctx.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.error_type, "transport_error")
        self.assertIn("connection reset", error.reason)

    def test_parse_accounting_snapshot_zip_reads_positions_and_equity_csv(self) -> None:
        payload = parse_accounting_snapshot_zip(accounting_snapshot_zip())

        self.assertEqual(payload["positions"][0]["conditionId"], "cond-1")
        self.assertEqual(payload["equity"][0]["equity"], "101.5")
        self.assertEqual(payload["record_counts"], {"positions": 1, "equity": 1})

    def test_fetch_accounting_snapshot_uses_zip_endpoint(self) -> None:
        client = ControlledBytesClient([accounting_snapshot_zip()])

        payload = client.fetch_accounting_snapshot(user="0xabc")

        parsed = urllib.parse.urlparse(client.requested_urls[0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v1/accounting/snapshot")
        self.assertEqual(query["user"], ["0xabc"])
        self.assertEqual(client.accept_headers, ["application/zip"])
        self.assertEqual(payload["positions"][0]["title"], "Rain market")

    def test_fetch_graphql_posts_query_variables_and_caches_by_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = ControlledGraphQLClient(
                [
                    {"data": {"maker": [{"id": "first"}]}},
                    {"data": {"maker": [{"id": "second"}]}},
                ],
                api_config={
                    "use_cache": True,
                    "cache_dir": temp_dir,
                },
            )

            first = client.fetch_graphql(
                endpoint_url="https://example.com/subgraph",
                query="query Wallet($wallet: String!){ maker: orderFilledEvents(where:{ maker: $wallet }) { id } }",
                variables={"wallet": "0xabc"},
            )
            cached = client.fetch_graphql(
                endpoint_url="https://example.com/subgraph",
                query="query Wallet($wallet: String!){ maker: orderFilledEvents(where:{ maker: $wallet }) { id } }",
                variables={"wallet": "0xabc"},
            )
            second = client.fetch_graphql(
                endpoint_url="https://example.com/subgraph",
                query="query Wallet($wallet: String!){ maker: orderFilledEvents(where:{ maker: $wallet }) { id } }",
                variables={"wallet": "0xdef"},
            )

        self.assertEqual(first["data"]["maker"][0]["id"], "first")
        self.assertEqual(cached["data"]["maker"][0]["id"], "first")
        self.assertEqual(second["data"]["maker"][0]["id"], "second")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[0]["url"], "https://example.com/subgraph")
        self.assertEqual(
            client.requests[0]["body"],
            {
                "query": "query Wallet($wallet: String!){ maker: orderFilledEvents(where:{ maker: $wallet }) { id } }",
                "variables": {"wallet": "0xabc"},
            },
        )
        self.assertEqual(
            client.requests[1]["body"],
            {
                "query": "query Wallet($wallet: String!){ maker: orderFilledEvents(where:{ maker: $wallet }) { id } }",
                "variables": {"wallet": "0xdef"},
            },
        )

    def test_fetch_graphql_raises_structured_error_with_endpoint_context(self) -> None:
        client = ControlledGraphQLClient(
            [
                HTTPError(
                    url="https://example.com/subgraph",
                    code=503,
                    msg="Service Unavailable",
                    hdrs=None,
                    fp=None,
                )
            ],
            api_config={"retry_count": 0},
        )

        with self.assertRaises(PolymarketRequestError) as ctx:
            client.fetch_graphql(
                endpoint_url="https://example.com/subgraph",
                query="query Wallet { maker: orderFilledEvents { id } }",
            )

        error = ctx.exception
        self.assertEqual(error.path, "/graphql")
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.params["endpoint_url"], "https://example.com/subgraph")
        self.assertTrue(error.retryable)
        self.assertIn("status=503", str(error))
        self.assertIn("https://example.com/subgraph", error.params["endpoint_url"])


if __name__ == "__main__":
    unittest.main()
