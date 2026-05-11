from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Any, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest


@dataclass(frozen=True)
class CloudflareD1Config:
    account_id: str
    database_id: str
    api_token: str
    email: str = ""
    global_api_key: str = ""
    timeout_seconds: float = 20.0

    @property
    def enabled(self) -> bool:
        return bool(self.account_id and self.database_id and (self.api_token or self.global_api_key))

    @property
    def cloudflare_account_id(self) -> str:
        return self.account_id

    @property
    def cloudflare_d1_database_id(self) -> str:
        return self.database_id

    @property
    def cloudflare_api_token(self) -> str:
        return self.api_token

    @property
    def cloudflare_email(self) -> str:
        return self.email

    @property
    def cloudflare_global_api_key(self) -> str:
        return self.global_api_key

    @property
    def query_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/d1/database/{self.database_id}/query"
        )

    @classmethod
    def from_settings(
        cls,
        settings: Mapping[str, Any],
        *,
        timeout_seconds_key: str = "timeout_seconds",
        timeout_seconds_default: float = 20.0,
    ) -> "CloudflareD1Config":
        return cls(
            account_id=str(settings.get("cloudflare_account_id") or "").strip(),
            database_id=str(settings.get("cloudflare_d1_database_id") or "").strip(),
            api_token=str(settings.get("cloudflare_api_token") or "").strip(),
            email=str(settings.get("cloudflare_email") or "").strip(),
            global_api_key=str(settings.get("cloudflare_global_api_key") or "").strip(),
            timeout_seconds=max(
                1.0,
                float(settings.get(timeout_seconds_key, timeout_seconds_default)),
            ),
        )


class CloudflareD1RequestError(RuntimeError):
    pass


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value)


def normalize_backend_choice(value: Any, *, default: str, allowed: set[str]) -> str:
    backend = str(value or default).strip().lower() or default
    return backend if backend in allowed else default


def cloudflare_d1_select_rows(
    config: CloudflareD1Config,
    table: str,
    *,
    columns: list[str],
    filters: Mapping[str, str] | None = None,
    order: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    where_sql, params = _where_clause(filters or {})
    sql = f"select {_column_list(columns)} from {_identifier(table)}"
    if where_sql:
        sql = f"{sql} where {where_sql}"
    if order:
        sql = f"{sql} order by {_order_clause(order)}"
    if limit is not None:
        sql = f"{sql} limit ?"
        params.append(max(1, int(limit)))
    if offset is not None:
        sql = f"{sql} offset ?"
        params.append(max(0, int(offset)))
    return _decode_rows(_cloudflare_d1_query(config, sql, params=params))


def cloudflare_d1_upsert_rows(
    config: CloudflareD1Config,
    table: str,
    *,
    rows: list[Mapping[str, Any]],
    on_conflict: str,
) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    columns: list[str] = []
    for row in rows:
        normalized = {str(key): _encode_value(value) for key, value in dict(row).items()}
        if not normalized:
            continue
        for column in normalized:
            if column not in columns:
                columns.append(column)
        normalized_rows.append(normalized)
    if not normalized_rows:
        return []

    stored_rows: list[dict[str, Any]] = []
    page_size = 100
    for offset in range(0, len(normalized_rows), page_size):
        chunk = normalized_rows[offset : offset + page_size]
        params: list[Any] = []
        row_placeholders: list[str] = []
        for row in chunk:
            row_placeholders.append(f"({','.join('?' for _column in columns)})")
            params.extend(row.get(column) for column in columns)
        update_columns = [column for column in columns if column != on_conflict]
        update_sql = ", ".join(
            f"{_identifier(column)} = excluded.{_identifier(column)}"
            for column in update_columns
        )
        if not update_sql:
            update_sql = f"{_identifier(on_conflict)} = excluded.{_identifier(on_conflict)}"
        sql = (
            f"insert into {_identifier(table)} ({_column_list(columns)}) "
            f"values {','.join(row_placeholders)} "
            f"on conflict({_identifier(on_conflict)}) do update set {update_sql}"
        )
        _cloudflare_d1_query(config, sql, params=params)
        stored_rows.extend(_decode_row(row) for row in chunk)
    return stored_rows


def cloudflare_d1_count_rows(
    config: CloudflareD1Config,
    table: str,
    *,
    filters: Mapping[str, str],
) -> int:
    where_sql, params = _where_clause(filters)
    sql = f"select count(*) as row_count from {_identifier(table)}"
    if where_sql:
        sql = f"{sql} where {where_sql}"
    rows = _cloudflare_d1_query(config, sql, params=params)
    if not rows:
        return 0
    value = rows[0].get("row_count")
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def cloudflare_d1_delete_rows(
    config: CloudflareD1Config,
    table: str,
    *,
    filters: Mapping[str, str],
) -> list[dict[str, Any]]:
    if not filters:
        raise CloudflareD1RequestError("Refusing to delete rows without filters")
    where_sql, params = _where_clause(filters)
    deleted_count = cloudflare_d1_count_rows(config, table, filters=filters)
    if deleted_count <= 0:
        return []
    sql = f"delete from {_identifier(table)}"
    if where_sql:
        sql = f"{sql} where {where_sql}"
    _cloudflare_d1_query(config, sql, params=params)
    return [{"deleted_count": deleted_count}]


def _cloudflare_d1_query(
    config: CloudflareD1Config,
    sql: str,
    *,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    if not config.enabled:
        raise CloudflareD1RequestError("Cloudflare D1 is not configured")

    body = json.dumps({"sql": sql, "params": params or []}, ensure_ascii=False).encode("utf-8")
    request = urlrequest.Request(
        config.query_url,
        data=body,
        method="POST",
        headers=_cloudflare_headers(config),
    )
    try:
        with urlrequest.urlopen(request, timeout=max(1.0, float(config.timeout_seconds))) as response:
            raw_body = response.read()
    except urlerror.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise CloudflareD1RequestError(
            f"Cloudflare D1 query failed with HTTP {exc.code}: {response_body}"
        ) from exc
    except urlerror.URLError as exc:
        raise CloudflareD1RequestError(f"Cloudflare D1 query failed: {exc.reason}") from exc
    except (TimeoutError, IncompleteRead, OSError) as exc:
        raise CloudflareD1RequestError(f"Cloudflare D1 query failed: {exc}") from exc

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CloudflareD1RequestError("Cloudflare D1 returned invalid JSON") from exc
    if not isinstance(payload, Mapping) or not bool(payload.get("success", False)):
        raise CloudflareD1RequestError(f"Cloudflare D1 query failed: {payload}")
    result = payload.get("result", [])
    if not isinstance(result, list) or not result:
        return []
    first_result = result[0] if isinstance(result[0], Mapping) else {}
    rows = first_result.get("results", []) if isinstance(first_result, Mapping) else []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _cloudflare_headers(config: CloudflareD1Config) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.api_token:
        headers["Authorization"] = f"Bearer {config.api_token}"
    elif config.email and config.global_api_key:
        headers["X-Auth-Email"] = config.email
        headers["X-Auth-Key"] = config.global_api_key
    return headers


def _where_clause(filters: Mapping[str, str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for key, raw_value in filters.items():
        column = _identifier(str(key))
        value = str(raw_value)
        if value.startswith("eq."):
            clauses.append(f"{column} = ?")
            params.append(value[3:])
        elif value == "not.is.null":
            clauses.append(f"{column} is not null")
        else:
            clauses.append(f"{column} = ?")
            params.append(value)
    return " and ".join(clauses), params


def _identifier(value: str) -> str:
    normalized = str(value).strip()
    if not normalized.replace("_", "").isalnum():
        raise CloudflareD1RequestError(f"Unsafe SQL identifier: {value}")
    return f'"{normalized}"'


def _column_list(columns: list[str]) -> str:
    return ",".join(_identifier(column) for column in columns)


def _order_clause(order: str) -> str:
    parts = str(order or "").split(".", 1)
    column = _identifier(parts[0])
    direction = parts[1].lower() if len(parts) > 1 else "asc"
    direction_sql = "desc" if direction == "desc" else "asc"
    return f"{column} {direction_sql}"


def _encode_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _decode_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_decode_row(row) for row in rows]


def _decode_row(row: Mapping[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    for key in ("payload", "metadata"):
        value = decoded.get(key)
        if isinstance(value, str):
            try:
                decoded[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return decoded
