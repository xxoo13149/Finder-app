# Cloudflare D1 History Persistence

Version marker: `cloudflare-d1-history-20260507`

## What Changed

- Added a Cloudflare D1-backed reusable history layer for wallet registry, trade ledger, operation ledger, history gap metadata, and archived run documents.
- Kept the original local-first analysis path intact while adding optional Cloudflare replication and Cloudflare read fallback.
- Added ledger reuse for both trades and operations so normal analysis, weekly high-profit screening, and smart wallet library refresh can share accumulated history.
- Added GraphQL history provider fallback with time-partition recovery for deep order-fill and activity operation streams.
- Added cleanup-before-archive behavior so reusable run outputs can be archived before local detail files are pruned.
- Added D1 setup schema in `docs/spec/20260506193000000_cloudflare_d1_history_persistence.sql`.

## Setup

1. Create or reuse a Cloudflare D1 database, then execute `docs/spec/20260506193000000_cloudflare_d1_history_persistence.sql`.
2. Fill local `.env` with `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_D1_DATABASE_ID`, and either `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_EMAIL` plus `CLOUDFLARE_GLOBAL_API_KEY`.
3. Keep secrets in `.env`; `.env.example` intentionally contains placeholders only.
4. Use `history_ledger.backend=local` for local-first operation with cloud replication, or `history_ledger.backend=cloudflare` when the D1 database should be the primary reusable history store.
5. Use `/api/history/cloud/status` to inspect registry, ledger, archive, and run archive state.

## Validation

- `python -m py_compile src/polymarket_weather_tool/analysis.py src/polymarket_weather_tool/history_ledger.py src/polymarket_weather_tool/cloudflare_backend.py tests/test_upgrade_behaviors.py`
- `python -m unittest tests.test_upgrade_behaviors tests.test_server`
- `python -m unittest tests.test_pipeline_smoke`
- Live D1 smoke: batch upsert/select/delete against `wallet_registry` returned two inserted rows, two deleted rows, and zero remaining smoke rows.

## Notes

- Prefer scoped Cloudflare API Tokens for day-to-day use. Global API Key auth is supported as a fallback for local development.
- D1 free-tier quotas can be stressed by very large historical ledgers. The D1 client now batches upserts and avoids `DELETE ... RETURNING *` for bulk cleanup to reduce request and response pressure.
