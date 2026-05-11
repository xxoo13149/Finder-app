from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import os
import random
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError


DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
REQUEST_RETRYABLE_EXCEPTIONS = (HTTPError, URLError, TimeoutError, http.client.IncompleteRead)
LEADERBOARD_TIME_PERIOD_ALIASES = {
    "1D": "DAY",
}
COLLECTION_ENDPOINT_PATHS = {
    "/activity",
    "/trades",
    "/positions",
    "/closed-positions",
}


def normalize_leaderboard_time_period(value: str) -> str:
    return LEADERBOARD_TIME_PERIOD_ALIASES.get(value.strip().upper(), value)


@dataclass(frozen=True)
class RequestFailureSummary:
    error_type: str
    status_code: int | None
    reason: str
    retry_after_seconds: float | None = None


@dataclass
class RequestBucketState:
    condition: threading.Condition
    last_request_started: float = 0.0
    cooldown_until: float = 0.0
    retryable_failure_streak: int = 0
    cooldown_generation: int = 0


class PolymarketRequestError(RuntimeError):
    def __init__(
        self,
        *,
        url: str,
        path: str,
        params: dict[str, Any],
        attempts: int,
        status_code: int | None,
        reason: str,
        error_type: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
        failures: list[RequestFailureSummary] | None = None,
    ) -> None:
        self.url = url
        self.path = path
        self.params = dict(params)
        self.attempts = int(attempts)
        self.status_code = status_code
        self.reason = reason
        self.error_type = error_type
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.failures = list(failures or [])
        details: list[str] = [f"type={self.error_type or 'request_error'}", f"attempts={self.attempts}"]
        if self.status_code is not None:
            details.append(f"status={self.status_code}")
        if self.reason:
            details.append(f"reason={self.reason}")
        if self.retry_after_seconds is not None:
            details.append(f"retry_after={self.retry_after_seconds:.2f}s")
        super().__init__(f"Request failed for {self.url} ({', '.join(details)})")


class PolymarketClient:
    def __init__(self, api_config: dict[str, Any]) -> None:
        self.timeout = float(api_config.get("timeout_seconds", 20))
        self.retry_count = int(api_config.get("retry_count", 3))
        self.retry_backoff = float(api_config.get("retry_backoff_seconds", 1.5))
        self.request_delay = float(api_config.get("request_delay_seconds", 0.15))
        self.retry_jitter = max(0.0, float(api_config.get("retry_jitter_seconds", 0.35)))
        self.cooldown_after_retryable_failure = max(
            0.0,
            float(api_config.get("cooldown_after_retryable_failure_seconds", 3.0)),
        )
        self.cooldown_max_seconds = max(
            self.cooldown_after_retryable_failure,
            float(api_config.get("cooldown_max_seconds", 30.0)),
        )
        self.activity_page_zero_retry_count = max(
            self.retry_count,
            int(api_config.get("activity_page_zero_retry_count", 5)),
        )
        self.collection_page_zero_retry_count = max(
            self.retry_count,
            int(api_config.get("collection_page_zero_retry_count", 4)),
        )
        self.page_zero_backoff_multiplier = max(
            1.0,
            float(api_config.get("page_zero_backoff_multiplier", 1.5)),
        )
        self.user_agent = str(api_config.get("user_agent", "polymarket-weather-tool/0.2.0"))
        self.use_cache = bool(api_config.get("use_cache", True))
        self.cache_ttl = int(api_config.get("cache_ttl_seconds", 1800))
        self.cache_dir = Path(api_config.get("cache_dir", ".cache/polymarket-weather-tool"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._request_buckets_lock = threading.Lock()
        self._request_buckets: dict[str, RequestBucketState] = {}
        self._request_context = threading.local()
        self._inflight_requests_lock = threading.Lock()
        self._inflight_requests: dict[str, Future[bytes]] = {}

    def fetch_leaderboard_page(
        self,
        *,
        category: str,
        time_period: str,
        order_by: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        normalized_time_period = normalize_leaderboard_time_period(time_period)
        return self._get_json(
            DATA_API_BASE,
            "/v1/leaderboard",
            {
                "category": category,
                "timePeriod": normalized_time_period,
                "orderBy": order_by,
                "limit": limit,
                "offset": offset,
            },
        )

    def fetch_activity_page(
        self,
        *,
        user: str,
        limit: int,
        offset: int,
        activity_type: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
        if activity_type:
            params["type"] = activity_type
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        return self._get_json(
            DATA_API_BASE,
            "/activity",
            params,
        )

    def fetch_trades_page(self, *, user: str, limit: int, offset: int) -> list[dict[str, Any]]:
        return self._get_json(
            DATA_API_BASE,
            "/trades",
            {"user": user, "limit": limit, "offset": offset},
        )

    def fetch_positions_page(
        self,
        *,
        user: str,
        limit: int,
        offset: int,
        size_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
        if size_threshold is not None:
            params["sizeThreshold"] = size_threshold
        return self._get_json(DATA_API_BASE, "/positions", params)

    def fetch_closed_positions_page(
        self,
        *,
        user: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        return self._get_json(
            DATA_API_BASE,
            "/closed-positions",
            {"user": user, "limit": limit, "offset": offset},
        )

    def fetch_accounting_snapshot(self, *, user: str) -> dict[str, Any]:
        path = "/v1/accounting/snapshot"
        params = self._normalize_params({"user": user})
        url = self._build_url(DATA_API_BASE, path, params)
        cached = self._read_cache(url)
        if cached is not None:
            return cached

        raw_zip = self._get_bytes(DATA_API_BASE, path, params, accept="application/zip")
        payload = parse_accounting_snapshot_zip(raw_zip)
        self._write_cache(url, payload)
        return payload

    def fetch_graphql(
        self,
        *,
        endpoint_url: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = {
            "query": str(query),
            "variables": variables or {},
        }
        cache_key = self._build_graphql_cache_key(endpoint_url, body)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        payload = self._post_json(
            endpoint_url,
            body=body,
            path="/graphql",
            params={"endpoint_url": endpoint_url},
        )
        self._write_cache(cache_key, payload)
        return payload if isinstance(payload, dict) else {"data": payload}

    def fetch_events_page(
        self,
        *,
        limit: int,
        offset: int,
        tag_id: int | str | None = None,
        tag_slug: str | None = None,
        active: bool | None = None,
        closed: bool | None = None,
        archived: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tag_id is not None:
            params["tag_id"] = tag_id
        if tag_slug:
            params["tag_slug"] = tag_slug
        if active is not None:
            params["active"] = active
        if closed is not None:
            params["closed"] = closed
        if archived is not None:
            params["archived"] = archived
        return self._get_json(GAMMA_API_BASE, "/events", params)

    def fetch_events_keyset_page(
        self,
        *,
        limit: int,
        after_cursor: str | None = None,
        order: str | None = None,
        ascending: bool | None = None,
        tag_id: int | str | None = None,
        tag_slug: str | None = None,
        active: bool | None = None,
        closed: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if after_cursor:
            params["after_cursor"] = after_cursor
        if order:
            params["order"] = order
        if ascending is not None:
            params["ascending"] = ascending
        if tag_id is not None:
            params["tag_id"] = tag_id
        if tag_slug:
            params["tag_slug"] = tag_slug
        if active is not None:
            params["active"] = active
        if closed is not None:
            params["closed"] = closed
        if archived is not None:
            params["archived"] = archived
        return self._get_json(GAMMA_API_BASE, "/events/keyset", params)

    def fetch_event_by_slug(self, slug: str) -> dict[str, Any] | None:
        events = self._get_json(GAMMA_API_BASE, "/events", {"slug": slug})
        if not events:
            return None
        return events[0]

    def fetch_polygon_transactions(
        self,
        *,
        address: str,
        api_key: str,
        base_url: str = "https://api.etherscan.io",
        chain_id: int | str = 137,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 1000,
        sort: str = "asc",
    ) -> list[dict[str, Any]]:
        payload = self._get_json(
            base_url.rstrip("/"),
            "/v2/api",
            {
                "chainid": chain_id,
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": start_block,
                "endblock": end_block,
                "page": page,
                "offset": offset,
                "sort": sort,
                "apikey": api_key,
            },
        )
        if isinstance(payload, dict):
            result = payload.get("result", [])
            if isinstance(result, list):
                return result
        if isinstance(payload, list):
            return payload
        return []

    def fetch_polygon_logs(
        self,
        *,
        api_key: str,
        contract_address: str,
        topic0: str | None = None,
        topic1: str | None = None,
        base_url: str = "https://api.etherscan.io",
        chain_id: int | str = 137,
        from_block: int = 0,
        to_block: int = 99999999,
        page: int = 1,
        offset: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "chainid": chain_id,
            "module": "logs",
            "action": "getLogs",
            "address": contract_address,
            "fromBlock": from_block,
            "toBlock": to_block,
            "page": page,
            "offset": offset,
            "apikey": api_key,
        }
        if topic0:
            params["topic0"] = topic0
        if topic1:
            params["topic1"] = topic1
            if topic0:
                params["topic0_1_opr"] = "and"

        payload = self._get_json(base_url.rstrip("/"), "/v2/api", params)
        if isinstance(payload, dict):
            result = payload.get("result", [])
            if isinstance(result, list):
                return result
        if isinstance(payload, list):
            return payload
        return []

    def _get_json(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        normalized_params = self._normalize_params(params or {})
        url = self._build_url(base_url, path, normalized_params)

        cached = self._read_cache(url)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        failures: list[RequestFailureSummary] = []
        retry_limit = self._resolve_retry_limit(path=path, params=normalized_params)
        last_retryable = False
        last_summary = RequestFailureSummary(error_type="request_error", status_code=None, reason="")
        for attempt in range(retry_limit + 1):
            try:
                payload = self._request_json(url)
                self._clear_retryable_failure_state(url)
                self._write_cache(url, payload)
                return payload
            except REQUEST_RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                last_summary = summarize_request_failure(exc)
                failures.append(last_summary)
                last_retryable = self._is_retryable_exception(exc)
                if not last_retryable:
                    break
                if attempt >= retry_limit:
                    break
                retry_sleep_seconds = self._compute_retry_sleep_seconds(
                    path=path,
                    params=normalized_params,
                    attempt=attempt,
                    retry_after_seconds=last_summary.retry_after_seconds,
                )
                self._set_retryable_cooldown(
                    url=url,
                    path=path,
                    params=normalized_params,
                    delay_seconds=retry_sleep_seconds,
                )
                time.sleep(retry_sleep_seconds)
        raise PolymarketRequestError(
            url=url,
            path=path,
            params=normalized_params,
            attempts=len(failures),
            status_code=last_summary.status_code,
            reason=last_summary.reason,
            error_type=last_summary.error_type,
            retryable=last_retryable,
            retry_after_seconds=last_summary.retry_after_seconds,
            failures=failures,
        ) from last_error

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        path: str,
        params: dict[str, Any],
    ) -> Any:
        normalized_params = self._normalize_params(params)
        last_error: Exception | None = None
        failures: list[RequestFailureSummary] = []
        retry_limit = self.retry_count
        last_retryable = False
        last_summary = RequestFailureSummary(error_type="request_error", status_code=None, reason="")
        for attempt in range(retry_limit + 1):
            try:
                payload = self._request_json_via_post(url, body=body)
                self._clear_retryable_failure_state(url)
                return payload
            except REQUEST_RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                last_summary = summarize_request_failure(exc)
                failures.append(last_summary)
                last_retryable = self._is_retryable_exception(exc)
                if not last_retryable:
                    break
                if attempt >= retry_limit:
                    break
                retry_sleep_seconds = self._compute_retry_sleep_seconds(
                    path=path,
                    params=normalized_params,
                    attempt=attempt,
                    retry_after_seconds=last_summary.retry_after_seconds,
                )
                self._set_retryable_cooldown(
                    url=url,
                    path=path,
                    params=normalized_params,
                    delay_seconds=retry_sleep_seconds,
                )
                time.sleep(retry_sleep_seconds)
        raise PolymarketRequestError(
            url=url,
            path=path,
            params=normalized_params,
            attempts=len(failures),
            status_code=last_summary.status_code,
            reason=last_summary.reason,
            error_type=last_summary.error_type,
            retryable=last_retryable,
            retry_after_seconds=last_summary.retry_after_seconds,
            failures=failures,
        ) from last_error

    def _get_bytes(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any],
        *,
        accept: str,
    ) -> bytes:
        normalized_params = self._normalize_params(params)
        url = self._build_url(base_url, path, normalized_params)
        last_error: Exception | None = None
        failures: list[RequestFailureSummary] = []
        retry_limit = self._resolve_retry_limit(path=path, params=normalized_params)
        last_retryable = False
        last_summary = RequestFailureSummary(error_type="request_error", status_code=None, reason="")
        for attempt in range(retry_limit + 1):
            try:
                payload = self._request_bytes(url, accept=accept)
                self._clear_retryable_failure_state(url)
                return payload
            except REQUEST_RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                last_summary = summarize_request_failure(exc)
                failures.append(last_summary)
                last_retryable = self._is_retryable_exception(exc)
                if not last_retryable:
                    break
                if attempt >= retry_limit:
                    break
                retry_sleep_seconds = self._compute_retry_sleep_seconds(
                    path=path,
                    params=normalized_params,
                    attempt=attempt,
                    retry_after_seconds=last_summary.retry_after_seconds,
                )
                self._set_retryable_cooldown(
                    url=url,
                    path=path,
                    params=normalized_params,
                    delay_seconds=retry_sleep_seconds,
                )
                time.sleep(retry_sleep_seconds)
        raise PolymarketRequestError(
            url=url,
            path=path,
            params=normalized_params,
            attempts=len(failures),
            status_code=last_summary.status_code,
            reason=last_summary.reason,
            error_type=last_summary.error_type,
            retryable=last_retryable,
            retry_after_seconds=last_summary.retry_after_seconds,
            failures=failures,
        ) from last_error

    def _request_json(self, url: str) -> Any:
        return json.loads(self._request_bytes(url, accept="application/json").decode("utf-8"))

    def _request_json_via_post(self, url: str, *, body: dict[str, Any]) -> Any:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return json.loads(self._request_bytes(url, accept="application/json", data=payload).decode("utf-8"))

    def _request_bytes(
        self,
        url: str,
        *,
        accept: str,
        data: bytes | None = None,
    ) -> bytes:
        inflight_key = self._request_inflight_key(url=url, accept=accept, data=data)
        with self._inflight_requests_lock:
            inflight = self._inflight_requests.get(inflight_key)
            if inflight is None:
                inflight = Future()
                self._inflight_requests[inflight_key] = inflight
                is_leader = True
            else:
                is_leader = False
        if not is_leader:
            return inflight.result()

        try:
            payload = self._perform_request_bytes(url, accept=accept, data=data)
        except Exception as exc:
            inflight.set_exception(exc)
            raise
        else:
            inflight.set_result(payload)
            return payload
        finally:
            with self._inflight_requests_lock:
                if self._inflight_requests.get(inflight_key) is inflight:
                    del self._inflight_requests[inflight_key]

    def _perform_request_bytes(
        self,
        url: str,
        *,
        accept: str,
        data: bytes | None = None,
    ) -> bytes:
        bucket_key = self._request_bucket_key(url)
        bucket = self._request_bucket_for_key(bucket_key)
        with bucket.condition:
            while True:
                now = time.monotonic()
                earliest_start = max(
                    bucket.last_request_started + self.request_delay,
                    bucket.cooldown_until,
                )
                wait_for = max(0.0, earliest_start - now)
                if wait_for > 0:
                    bucket.condition.wait(timeout=wait_for)
                    continue
                bucket.last_request_started = now
                self._record_request_start_generation(bucket_key, bucket.cooldown_generation)
                break

        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": self.user_agent,
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
            data=data,
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    @staticmethod
    def _request_inflight_key(*, url: str, accept: str, data: bytes | None) -> str:
        data_digest = hashlib.sha256(data or b"").hexdigest()
        return f"{accept}\n{url}\n{data_digest}"

    def _resolve_retry_limit(self, *, path: str, params: dict[str, Any]) -> int:
        offset = self._param_int(params, "offset")
        if offset != 0:
            return self.retry_count
        if path == "/activity":
            return self.activity_page_zero_retry_count
        if path in COLLECTION_ENDPOINT_PATHS:
            return self.collection_page_zero_retry_count
        return self.retry_count

    def _compute_retry_sleep_seconds(
        self,
        *,
        path: str,
        params: dict[str, Any],
        attempt: int,
        retry_after_seconds: float | None,
    ) -> float:
        delay_seconds = self.retry_backoff * (2**attempt)
        if self._param_int(params, "offset") == 0 and path in COLLECTION_ENDPOINT_PATHS:
            delay_seconds *= self.page_zero_backoff_multiplier
        if retry_after_seconds is not None:
            delay_seconds = max(delay_seconds, retry_after_seconds)
        if self.retry_jitter > 0:
            delay_seconds += random.uniform(0.0, self.retry_jitter)
        return max(0.0, delay_seconds)

    def _set_retryable_cooldown(
        self,
        *,
        url: str,
        path: str,
        params: dict[str, Any],
        delay_seconds: float,
    ) -> None:
        if delay_seconds <= 0 and self.cooldown_after_retryable_failure <= 0:
            return
        bucket = self._request_bucket(url)
        with bucket.condition:
            bucket.retryable_failure_streak += 1
            cooldown_seconds = self.cooldown_after_retryable_failure * (
                2 ** max(0, bucket.retryable_failure_streak - 1)
            )
            if self._param_int(params, "offset") == 0 and path in COLLECTION_ENDPOINT_PATHS:
                cooldown_seconds *= self.page_zero_backoff_multiplier
            cooldown_seconds = min(
                self.cooldown_max_seconds,
                max(delay_seconds, cooldown_seconds),
            )
            bucket.cooldown_until = max(bucket.cooldown_until, time.monotonic() + cooldown_seconds)
            bucket.cooldown_generation += 1
            bucket.condition.notify_all()

    def _clear_retryable_failure_state(self, url: str) -> None:
        bucket_key = self._request_bucket_key(url)
        request_generation = self._pop_request_start_generation(bucket_key)
        bucket = self._request_bucket_for_key(bucket_key)
        with bucket.condition:
            if (
                request_generation is not None
                and bucket.cooldown_generation != request_generation
            ):
                return
            bucket.retryable_failure_streak = 0
            bucket.cooldown_until = 0.0
            bucket.condition.notify_all()

    def _request_bucket(self, url: str) -> RequestBucketState:
        bucket_key = self._request_bucket_key(url)
        return self._request_bucket_for_key(bucket_key)

    def _request_bucket_for_key(self, bucket_key: str) -> RequestBucketState:
        with self._request_buckets_lock:
            bucket = self._request_buckets.get(bucket_key)
            if bucket is None:
                bucket = RequestBucketState(condition=threading.Condition())
                self._request_buckets[bucket_key] = bucket
            return bucket

    def _record_request_start_generation(self, bucket_key: str, generation: int) -> None:
        generations = getattr(self._request_context, "bucket_generations", None)
        if not isinstance(generations, dict):
            generations = {}
            self._request_context.bucket_generations = generations
        generations[bucket_key] = int(generation)

    def _pop_request_start_generation(self, bucket_key: str) -> int | None:
        generations = getattr(self._request_context, "bucket_generations", None)
        if not isinstance(generations, dict):
            return None
        generation = generations.pop(bucket_key, None)
        return int(generation) if generation is not None else None

    @staticmethod
    def _request_bucket_key(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        scheme = (parsed.scheme or "https").lower()
        netloc = (parsed.netloc or "").lower()
        path = (parsed.path or "/").rstrip("/").lower() or "/"
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        module = str((query.get("module") or [""])[0]).strip().lower()
        action = str((query.get("action") or [""])[0]).strip().lower()
        if module or action:
            suffix_parts = [path]
            if module:
                suffix_parts.append(f"module={module}")
            if action:
                suffix_parts.append(f"action={action}")
            path = "#".join(suffix_parts)
        return f"{scheme}://{netloc}{path}"

    @staticmethod
    def _is_retryable_exception(exc: Exception) -> bool:
        if isinstance(exc, HTTPError):
            return exc.code == 429 or exc.code >= 500
        return isinstance(exc, (URLError, TimeoutError, http.client.IncompleteRead))

    @staticmethod
    def _param_int(params: dict[str, Any], key: str, default: int = 0) -> int:
        raw_value = params.get(key, default)
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return default

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    @staticmethod
    def _build_url(base_url: str, path: str, params: dict[str, Any]) -> str:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{base_url}{path}"
        if query:
            url = f"{url}?{query}"
        return url

    @staticmethod
    def _build_graphql_cache_key(endpoint_url: str, body: dict[str, Any]) -> str:
        digest = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"{endpoint_url}#graphql={digest}"

    def _read_cache(self, url: str) -> Any | None:
        if not self.use_cache:
            return None
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        fetched_at = float(payload.get("fetched_at", 0))
        if time.time() - fetched_at > self.cache_ttl:
            return None
        return payload.get("data")

    def _write_cache(self, url: str, data: Any) -> None:
        if not self.use_cache:
            return
        path = self._cache_path(url)
        payload = {"fetched_at": time.time(), "url": url, "data": data}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                normalized[key] = str(value).lower()
            else:
                normalized[key] = value
        return normalized


def resolve_api_key(config: dict[str, Any]) -> str:
    explicit = str(config.get("api_key", "")).strip()
    if explicit:
        return explicit
    for env_name in config.get("api_key_envs", [config.get("api_key_env", "")]):
        if not env_name:
            continue
        value = os.getenv(str(env_name).strip(), "").strip()
        if value:
            return value
    return ""


def parse_accounting_snapshot_zip(raw_zip: bytes) -> dict[str, Any]:
    rows_by_name: dict[str, list[dict[str, str]]] = {}
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with archive.open(name) as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                rows_by_name[Path(name).stem.lower()] = [
                    normalize_csv_row(row)
                    for row in csv.DictReader(text)
                    if isinstance(row, dict)
                ]

    positions = first_csv_rows(rows_by_name, ("positions", "position"))
    equity = first_csv_rows(rows_by_name, ("equity", "equities"))
    return {
        "positions": positions,
        "equity": equity,
        "files": sorted(rows_by_name),
        "record_counts": {
            "positions": len(positions),
            "equity": len(equity),
        },
    }


def first_csv_rows(
    rows_by_name: dict[str, list[dict[str, str]]],
    names: tuple[str, ...],
) -> list[dict[str, str]]:
    for name in names:
        if name in rows_by_name:
            return rows_by_name[name]
    return []


def normalize_csv_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[str(key).strip()] = "" if value is None else str(value).strip()
    return normalized


def summarize_request_failure(exc: Exception) -> RequestFailureSummary:
    if isinstance(exc, HTTPError):
        return RequestFailureSummary(
            error_type="http_error",
            status_code=exc.code,
            reason=normalize_request_reason(getattr(exc, "reason", "") or getattr(exc, "msg", "")),
            retry_after_seconds=parse_retry_after_seconds(
                exc.headers.get("Retry-After") if exc.headers else None
            ),
        )
    if isinstance(exc, TimeoutError):
        return RequestFailureSummary(
            error_type="timeout",
            status_code=None,
            reason=normalize_request_reason(str(exc) or "timed out"),
        )
    if isinstance(exc, http.client.IncompleteRead):
        return RequestFailureSummary(
            error_type="incomplete_read",
            status_code=None,
            reason=normalize_request_reason(str(exc) or "incomplete response body"),
        )
    if isinstance(exc, URLError):
        return RequestFailureSummary(
            error_type="transport_error",
            status_code=None,
            reason=normalize_request_reason(exc.reason),
        )
    return RequestFailureSummary(
        error_type=type(exc).__name__ or "request_error",
        status_code=None,
        reason=normalize_request_reason(str(exc)),
    )


def parse_retry_after_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def normalize_request_reason(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip().replace("\r", " ").replace("\n", " ")
    return " ".join(part for part in text.split(" ") if part)
