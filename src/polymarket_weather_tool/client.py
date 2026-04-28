from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError


DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
LEADERBOARD_TIME_PERIOD_ALIASES = {
    "1D": "DAY",
}


def normalize_leaderboard_time_period(value: str) -> str:
    return LEADERBOARD_TIME_PERIOD_ALIASES.get(value.strip().upper(), value)


class PolymarketClient:
    def __init__(self, api_config: dict[str, Any]) -> None:
        self.timeout = float(api_config.get("timeout_seconds", 20))
        self.retry_count = int(api_config.get("retry_count", 3))
        self.retry_backoff = float(api_config.get("retry_backoff_seconds", 1.5))
        self.request_delay = float(api_config.get("request_delay_seconds", 0.15))
        self.user_agent = str(api_config.get("user_agent", "polymarket-weather-tool/0.1.0"))
        self.use_cache = bool(api_config.get("use_cache", True))
        self.cache_ttl = int(api_config.get("cache_ttl_seconds", 1800))
        self.cache_dir = Path(api_config.get("cache_dir", ".cache/polymarket-weather-tool"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._request_lock = threading.Lock()
        self._last_request_started = 0.0

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

    def fetch_activity_page(self, *, user: str, limit: int, offset: int) -> list[dict[str, Any]]:
        return self._get_json(
            DATA_API_BASE,
            "/activity",
            {"user": user, "limit": limit, "offset": offset},
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
        query = urllib.parse.urlencode(self._normalize_params(params or {}), doseq=True)
        url = f"{base_url}{path}"
        if query:
            url = f"{url}?{query}"

        cached = self._read_cache(url)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                payload = self._request_json(url)
                self._write_cache(url, payload)
                return payload
            except (HTTPError, URLError, TimeoutError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError) and exc.code < 500 and exc.code != 429:
                    break
                if attempt >= self.retry_count:
                    break
                time.sleep(self.retry_backoff * (2**attempt))
        raise RuntimeError(f"Request failed for {url}") from last_error

    def _request_json(self, url: str) -> Any:
        with self._request_lock:
            now = time.monotonic()
            wait_for = self.request_delay - (now - self._last_request_started)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request_started = time.monotonic()

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

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
