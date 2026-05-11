from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest


FALCON_DEFAULT_BASE_URL = "https://narrative.agent.heisenberg.so/api/v2/semantic/retrieve/parameterized"
FALCON_LIFETIME_AGENT_ID = 586
FALCON_PROFIT_AND_LOSS_AGENT_ID = 569
FALCON_WALLET_360_AGENT_ID = 581
DEFAULT_FALCON_WIN_RATE_WINDOW_DAYS = 15
DEFAULT_FALCON_START_DATE = "2010-01-01"


@dataclass(frozen=True)
class FalconDisplayMetrics:
    wallet: str
    total_pnl: float | None = None
    total_roi: float | None = None
    win_rate: float | None = None
    win_rate_source: str = ""
    win_rate_window_label: str = ""
    total_trades: int | None = None
    total_invested: float | None = None
    wins: int | None = None
    losses: int | None = None
    pnl_updated_at: str = ""
    win_rate_updated_at: str = ""
    metric_source: str = "falcon"

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "total_pnl": self.total_pnl,
            "total_roi": self.total_roi,
            "win_rate": self.win_rate,
            "win_rate_source": self.win_rate_source,
            "win_rate_window_label": self.win_rate_window_label,
            "total_trades": self.total_trades,
            "total_invested": self.total_invested,
            "wins": self.wins,
            "losses": self.losses,
            "pnl_updated_at": self.pnl_updated_at,
            "win_rate_updated_at": self.win_rate_updated_at,
            "metric_source": self.metric_source,
        }


def falcon_enabled(config: Mapping[str, Any] | None = None) -> bool:
    settings = falcon_settings(config)
    enabled = settings.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    return bool(resolve_falcon_token(settings))


def falcon_settings(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(config, Mapping):
        value = config.get("falcon")
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def resolve_falcon_token(settings: Mapping[str, Any] | None = None) -> str:
    configured = ""
    if isinstance(settings, Mapping):
        token_env = str(settings.get("token_env") or "").strip()
        if token_env:
            configured = str(os.environ.get(token_env) or "").strip()
    if configured:
        return configured
    for env_name in (
        "FALCON_API_TOKEN",
        "HEISENBERG_API_TOKEN",
        "FALCON_TOKEN",
    ):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value
    return ""


def falcon_cache_dir(settings: Mapping[str, Any] | None = None) -> Path:
    configured = ""
    if isinstance(settings, Mapping):
        configured = str(settings.get("cache_dir") or "").strip()
    if configured:
        return Path(configured)
    return Path(".cache") / "falcon"


def falcon_cache_ttl_seconds(settings: Mapping[str, Any] | None = None) -> int:
    raw = None
    if isinstance(settings, Mapping):
        raw = settings.get("cache_ttl_seconds")
    try:
        return max(0, int(raw if raw not in (None, "") else 3600))
    except (TypeError, ValueError):
        return 3600


def falcon_base_url(settings: Mapping[str, Any] | None = None) -> str:
    if isinstance(settings, Mapping):
        text = str(settings.get("base_url") or "").strip()
        if text:
            return text
    return FALCON_DEFAULT_BASE_URL


def falcon_timeout_seconds(settings: Mapping[str, Any] | None = None) -> float:
    raw = None
    if isinstance(settings, Mapping):
        raw = settings.get("timeout_seconds")
    try:
        return max(1.0, float(raw if raw not in (None, "") else 20.0))
    except (TypeError, ValueError):
        return 20.0


def falcon_win_rate_window_days(settings: Mapping[str, Any] | None = None) -> int:
    raw = None
    if isinstance(settings, Mapping):
        raw = settings.get("win_rate_window_days")
    try:
        value = int(raw if raw not in (None, "") else DEFAULT_FALCON_WIN_RATE_WINDOW_DAYS)
    except (TypeError, ValueError):
        value = DEFAULT_FALCON_WIN_RATE_WINDOW_DAYS
    return value if value in {1, 3, 7, 15} else DEFAULT_FALCON_WIN_RATE_WINDOW_DAYS


def falcon_win_rate_window_label(settings: Mapping[str, Any] | None = None) -> str:
    return f"Falcon {falcon_win_rate_window_days(settings)}d"


def falcon_profit_and_loss_start_date(settings: Mapping[str, Any] | None = None) -> str:
    if isinstance(settings, Mapping):
        value = str(settings.get("profit_and_loss_start_date") or "").strip()
        if value:
            return value
    return DEFAULT_FALCON_START_DATE


def falcon_display_metrics_for_wallet(
    wallet: str,
    *,
    config: Mapping[str, Any] | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_wallet(wallet)
    if not normalized:
        return {}
    settings = falcon_settings(config)
    if not falcon_enabled(config):
        return {}

    cache_key = falcon_metrics_cache_key(
        normalized,
        settings=settings,
        now_date=now_date,
    )
    cached = load_cached_falcon_metrics(cache_key, settings=settings)
    if cached:
        return cached

    token = resolve_falcon_token(settings)
    if not token:
        return {}

    sources, errors = fetch_falcon_display_metric_sources(
        normalized,
        settings=settings,
        token=token,
        now_date=now_date,
    )

    metrics = build_falcon_display_metrics(
        normalized,
        lifetime=sources.get("lifetime", {}),
        wallet_360=sources.get("wallet360", {}),
        profit_and_loss=sources.get("profit_and_loss", {}),
        settings=settings,
    )
    payload = metrics.to_dict()
    if errors:
        payload["errors"] = errors
    if any(value not in (None, "", [], {}) for key, value in payload.items() if key not in {"wallet", "metric_source", "errors"}):
        write_cached_falcon_metrics(cache_key, payload, settings=settings)
    return payload


def fetch_falcon_display_metric_sources(
    wallet: str,
    *,
    settings: Mapping[str, Any],
    token: str,
    now_date: str | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    tasks: dict[str, dict[str, Any]] = {
        "lifetime": {
            "agent_id": FALCON_LIFETIME_AGENT_ID,
            "params": {"wallet_address": wallet},
            "pagination": {"limit": 50, "offset": 0},
        },
        "wallet360": {
            "agent_id": FALCON_WALLET_360_AGENT_ID,
            "params": {
                "proxy_wallet": wallet,
                "window_days": str(falcon_win_rate_window_days(settings)),
            },
            "pagination": {"limit": 5, "offset": 0},
        },
    }
    if now_date:
        tasks["profit_and_loss"] = {
            "agent_id": FALCON_PROFIT_AND_LOSS_AGENT_ID,
            "params": {
                "wallet": wallet,
                "granularity": "all",
                "start_time": falcon_profit_and_loss_start_date(settings),
                "end_time": now_date,
                "condition_id": "ALL",
            },
        }

    sources: dict[str, dict[str, Any]] = {name: {} for name in tasks}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
        futures = {
            executor.submit(
                falcon_post,
                agent_id=int(task["agent_id"]),
                params=task["params"],
                settings=settings,
                token=token,
                pagination=task.get("pagination"),
            ): name
            for name, task in tasks.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                sources[name] = future.result()
            except FalconRequestError as exc:
                failures[name] = exc.reason

    errors = [
        f"{name}:{failures[name]}"
        for name in ("lifetime", "wallet360", "profit_and_loss")
        if name in failures
    ]
    return sources, errors


class FalconRequestError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_falcon_display_metrics(
    wallet: str,
    *,
    lifetime: Mapping[str, Any],
    wallet_360: Mapping[str, Any],
    profit_and_loss: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> FalconDisplayMetrics:
    lifetime_result = first_result(lifetime)
    wallet_360_result = first_result(wallet_360)
    pnl_result = first_result(profit_and_loss)

    total_pnl = number_value(lifetime_result.get("total_pnl"))
    total_roi = number_value(lifetime_result.get("roi_pct"))
    total_trades = integer_value(lifetime_result.get("total_trades"))
    total_invested = number_value(lifetime_result.get("total_invested"))
    pnl_updated_at = text_value(lifetime_result.get("last_updated"))

    win_rate = number_value(wallet_360_result.get("win_rate"))
    win_rate_source = "falcon_wallet_360"
    win_rate_window_label = falcon_win_rate_window_label(settings)
    win_rate_updated_at = text_value(wallet_360_result.get("date_range_end") or wallet_360_result.get("date"))
    wins = integer_value(wallet_360_result.get("winning_trades"))
    losses = integer_value(wallet_360_result.get("losing_trades"))

    if total_trades is None:
        total_trades = integer_value(wallet_360_result.get("total_trades"))
    if total_invested is None:
        total_invested = number_value(wallet_360_result.get("total_invested"))
    if wins is None:
        wins = integer_value(pnl_result.get("wins"))
    if losses is None:
        losses = integer_value(pnl_result.get("losses"))

    return FalconDisplayMetrics(
        wallet=wallet,
        total_pnl=total_pnl,
        total_roi=total_roi,
        win_rate=win_rate,
        win_rate_source=win_rate_source if win_rate is not None else "",
        win_rate_window_label=win_rate_window_label if win_rate is not None else "",
        total_trades=total_trades,
        total_invested=total_invested,
        wins=wins,
        losses=losses,
        pnl_updated_at=pnl_updated_at,
        win_rate_updated_at=win_rate_updated_at,
    )


def falcon_post(
    *,
    agent_id: int,
    params: Mapping[str, Any],
    settings: Mapping[str, Any],
    token: str,
    pagination: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "params": dict(params),
        "formatter_config": {"format_type": "raw"},
    }
    if pagination:
        body["pagination"] = dict(pagination)
    data = json.dumps(body).encode("utf-8")
    request = urlrequest.Request(
        falcon_base_url(settings),
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=falcon_timeout_seconds(settings)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        reason = f"http_{exc.code}"
        try:
            body_text = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body_text = ""
        if body_text:
            reason = f"{reason}:{body_text[:240]}"
        raise FalconRequestError(reason) from exc
    except urlerror.URLError as exc:
        raise FalconRequestError(str(exc.reason).strip() or "transport_error") from exc
    except TimeoutError as exc:
        raise FalconRequestError(str(exc).strip() or "timeout") from exc
    except json.JSONDecodeError as exc:
        raise FalconRequestError("invalid_json") from exc
    return dict(payload) if isinstance(payload, Mapping) else {}


def falcon_metrics_cache_key(
    wallet: str,
    *,
    settings: Mapping[str, Any],
    now_date: str | None,
) -> str:
    parts = {
        "wallet": wallet,
        "win_rate_window_days": falcon_win_rate_window_days(settings),
        "profit_and_loss_start_date": falcon_profit_and_loss_start_date(settings),
        "end_date": now_date or "",
    }
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()
    return digest


def load_cached_falcon_metrics(cache_key: str, *, settings: Mapping[str, Any]) -> dict[str, Any]:
    path = falcon_cache_dir(settings) / f"{cache_key}.json"
    if not path.exists():
        return {}
    ttl = falcon_cache_ttl_seconds(settings)
    if ttl > 0:
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def write_cached_falcon_metrics(
    cache_key: str,
    payload: Mapping[str, Any],
    *,
    settings: Mapping[str, Any],
) -> None:
    path = falcon_cache_dir(settings) / f"{cache_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def first_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return {}
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return {}
    first = results[0]
    return dict(first) if isinstance(first, Mapping) else {}


def normalize_wallet(value: Any) -> str:
    text = text_value(value).lower()
    return text if text.startswith("0x") else ""


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def number_value(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if result == result else None
    if isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
        return result if result == result else None
    return None


def integer_value(value: Any) -> int | None:
    number = number_value(value)
    if number is None:
        return None
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None
