from __future__ import annotations

import http.client
import io
import sys
import tempfile
import threading
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
    RequestBucketState,
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


class RecordingCondition:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.wait_calls: list[float | None] = []
        self.notify_count = 0
        self.last_request_started = 0.0
        self.cooldown_until = 0.0
        self.retryable_failure_streak = 0

    def __enter__(self) -> "RecordingCondition":
        self.lock.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.lock.release()
        return False

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        return True

    def notify_all(self) -> None:
        self.notify_count += 1


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

    def test_get_json_retries_incomplete_read(self) -> None:
        client = ControlledClient(
            [http.client.IncompleteRead(b"", 10), [{"id": "ok"}]],
            api_config={"retry_count": 1},
        )

        with patch("polymarket_weather_tool.client.time.sleep", return_value=None):
            payload = client.fetch_leaderboard_page(
                category="WEATHER",
                time_period="ALL",
                order_by="PNL",
                limit=1,
                offset=0,
            )

        self.assertEqual(payload, [{"id": "ok"}])
        self.assertEqual(len(client.requested_urls), 2)

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

    def test_request_bytes_waits_outside_lock(self) -> None:
        client = PolymarketClient(
            {
                "use_cache": False,
                "request_delay_seconds": 0.2,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 0,
            }
        )
        entered = threading.Event()
        release = threading.Event()

        class DummyResponse:
            def __enter__(self) -> "DummyResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

            def read(self) -> bytes:
                return b"[]"

        def fake_urlopen(_request: object, timeout: float) -> DummyResponse:
            entered.set()
            release.wait(timeout=1)
            return DummyResponse()

        with patch("polymarket_weather_tool.client.urllib.request.urlopen", side_effect=fake_urlopen):
            first = threading.Thread(target=lambda: client.fetch_activity_page(user="0xabc", limit=1, offset=0))
            first.start()
            self.assertTrue(entered.wait(timeout=1))

            second_done = threading.Event()
            second = threading.Thread(
                target=lambda: (client.fetch_activity_page(user="0xdef", limit=1, offset=0), second_done.set())
            )
            second.start()
            second.join(timeout=0.05)

            self.assertTrue(second.is_alive())

            release.set()
            first.join(timeout=1)
            second.join(timeout=1)
            self.assertTrue(second_done.is_set())

    def test_request_bucket_key_isolated_by_endpoint_family(self) -> None:
        client = PolymarketClient({"use_cache": False})

        data_bucket = client._request_bucket("https://data-api.polymarket.com/activity?user=0xabc")
        trades_bucket = client._request_bucket("https://data-api.polymarket.com/trades?user=0xdef")
        positions_bucket = client._request_bucket("https://data-api.polymarket.com/positions?user=0xghi")
        gamma_bucket = client._request_bucket("https://gamma-api.polymarket.com/events?limit=10")
        graph_bucket = client._request_bucket("https://example.com/subgraph")

        self.assertIs(client._request_bucket("https://data-api.polymarket.com/activity?user=0xzzz"), data_bucket)
        self.assertIsNot(data_bucket, trades_bucket)
        self.assertIsNot(data_bucket, positions_bucket)
        self.assertIsNot(trades_bucket, positions_bucket)
        self.assertIsNot(data_bucket, gamma_bucket)
        self.assertIsNot(data_bucket, graph_bucket)
        self.assertIsNot(gamma_bucket, graph_bucket)

    def test_set_retryable_cooldown_and_clear_apply_per_bucket(self) -> None:
        client = PolymarketClient(
            {
                "use_cache": False,
                "request_delay_seconds": 0,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 2,
            }
        )

        data_url = "https://data-api.polymarket.com/activity?user=0xabc&limit=1&offset=0"
        gamma_url = "https://gamma-api.polymarket.com/events?limit=10"

        client._set_retryable_cooldown(
            url=data_url,
            path="/activity",
            params={"offset": 0},
            delay_seconds=1.5,
        )
        data_bucket = client._request_bucket(data_url)
        gamma_bucket = client._request_bucket(gamma_url)

        self.assertGreater(data_bucket.cooldown_until, 0.0)
        self.assertEqual(data_bucket.retryable_failure_streak, 1)
        self.assertEqual(gamma_bucket.cooldown_until, 0.0)
        self.assertEqual(gamma_bucket.retryable_failure_streak, 0)

        client._clear_retryable_failure_state(data_url)

        self.assertEqual(data_bucket.cooldown_until, 0.0)
        self.assertEqual(data_bucket.retryable_failure_streak, 0)
        self.assertEqual(gamma_bucket.cooldown_until, 0.0)
        self.assertEqual(gamma_bucket.retryable_failure_streak, 0)

    def test_set_retryable_cooldown_does_not_leak_between_data_api_endpoint_families(self) -> None:
        client = PolymarketClient(
            {
                "use_cache": False,
                "request_delay_seconds": 0,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 2,
            }
        )

        activity_url = "https://data-api.polymarket.com/activity?user=0xabc&limit=1&offset=0"
        trades_url = "https://data-api.polymarket.com/trades?user=0xabc&limit=1&offset=0"

        client._set_retryable_cooldown(
            url=activity_url,
            path="/activity",
            params={"offset": 0},
            delay_seconds=1.5,
        )
        activity_bucket = client._request_bucket(activity_url)
        trades_bucket = client._request_bucket(trades_url)

        self.assertGreater(activity_bucket.cooldown_until, 0.0)
        self.assertEqual(activity_bucket.retryable_failure_streak, 1)
        self.assertEqual(trades_bucket.cooldown_until, 0.0)
        self.assertEqual(trades_bucket.retryable_failure_streak, 0)

    def test_success_from_older_inflight_request_does_not_clear_newer_cooldown(self) -> None:
        client = PolymarketClient(
            {
                "use_cache": False,
                "request_delay_seconds": 0,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 2,
            }
        )
        url = "https://data-api.polymarket.com/activity?user=0xabc&limit=1&offset=0"
        bucket_key = client._request_bucket_key(url)
        client._record_request_start_generation(bucket_key, 0)

        client._set_retryable_cooldown(
            url=url,
            path="/activity",
            params={"offset": 0},
            delay_seconds=1.5,
        )
        bucket = client._request_bucket(url)
        cooldown_until = bucket.cooldown_until

        client._clear_retryable_failure_state(url)

        self.assertEqual(bucket.retryable_failure_streak, 1)
        self.assertEqual(bucket.cooldown_until, cooldown_until)
        self.assertEqual(bucket.cooldown_generation, 1)

    def test_request_bucket_key_isolated_by_chain_module_and_action(self) -> None:
        client = PolymarketClient({"use_cache": False})

        tx_bucket = client._request_bucket(
            "https://api.etherscan.io/v2/api?module=account&action=txlist&address=0xabc"
        )
        logs_bucket = client._request_bucket(
            "https://api.etherscan.io/v2/api?module=logs&action=getLogs&address=0xabc"
        )
        other_tx_bucket = client._request_bucket(
            "https://api.etherscan.io/v2/api?module=account&action=txlist&address=0xdef"
        )

        self.assertIs(tx_bucket, other_tx_bucket)
        self.assertIsNot(tx_bucket, logs_bucket)

    def test_request_bucket_key_isolated_by_graphql_endpoint_path(self) -> None:
        client = PolymarketClient({"use_cache": False})

        subgraph_bucket = client._request_bucket("https://example.com/subgraph")
        alt_subgraph_bucket = client._request_bucket("https://example.com/subgraph-v2")

        self.assertIsNot(subgraph_bucket, alt_subgraph_bucket)

    def test_request_bytes_waits_for_bucket_cooldown_before_dispatch(self) -> None:
        client = PolymarketClient(
            {
                "use_cache": False,
                "request_delay_seconds": 0,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 0,
            }
        )
        url = "https://data-api.polymarket.com/activity?user=0xabc&limit=1&offset=0"
        bucket = client._request_bucket(url)
        condition = RecordingCondition()
        bucket_state = RequestBucketState(condition=condition, cooldown_until=15.0)
        client._request_buckets[client._request_bucket_key(url)] = bucket_state

        monotonic_values = iter([10.0, 15.0])

        class DummyResponse:
            def __enter__(self) -> "DummyResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

            def read(self) -> bytes:
                return b"[]"

        with patch("polymarket_weather_tool.client.time.monotonic", side_effect=lambda: next(monotonic_values)):
            with patch(
                "polymarket_weather_tool.client.urllib.request.urlopen",
                return_value=DummyResponse(),
            ):
                payload = client._request_bytes(url, accept="application/json")

        self.assertEqual(payload, b"[]")
        self.assertTrue(condition.wait_calls)
        self.assertAlmostEqual(condition.wait_calls[0] or 0.0, 5.0)
        self.assertEqual(bucket_state.last_request_started, 15.0)

    def test_request_bytes_coalesces_identical_inflight_requests(self) -> None:
        client = PolymarketClient(
            {
                "use_cache": False,
                "request_delay_seconds": 0,
                "retry_backoff_seconds": 0,
                "retry_jitter_seconds": 0,
                "cooldown_after_retryable_failure_seconds": 0,
            }
        )
        url = "https://data-api.polymarket.com/activity?user=0xabc&limit=1&offset=0"
        entered = threading.Event()
        release = threading.Event()
        call_count = 0
        call_count_lock = threading.Lock()

        class DummyResponse:
            def __enter__(self) -> "DummyResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

            def read(self) -> bytes:
                return b"[1]"

        def fake_urlopen(_request: object, timeout: float) -> DummyResponse:
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            entered.set()
            release.wait(timeout=1)
            return DummyResponse()

        results: list[bytes] = []
        errors: list[BaseException] = []

        def fetch() -> None:
            try:
                results.append(client._request_bytes(url, accept="application/json"))
            except BaseException as exc:
                errors.append(exc)

        with patch("polymarket_weather_tool.client.urllib.request.urlopen", side_effect=fake_urlopen):
            first = threading.Thread(target=fetch)
            second = threading.Thread(target=fetch)
            first.start()
            self.assertTrue(entered.wait(timeout=1))
            second.start()
            second.join(timeout=0.05)
            self.assertTrue(second.is_alive())
            release.set()
            first.join(timeout=1)
            second.join(timeout=1)

        self.assertEqual(errors, [])
        self.assertEqual(results, [b"[1]", b"[1]"])
        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
