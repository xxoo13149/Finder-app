-- Cloudflare D1 schema for reusable Polymarket history persistence.
-- Stores deduplicated wallet registry, archived documents, trade/operation ledgers, and history gaps.

create table if not exists wallet_registry (
    wallet_address text primary key,
    user_name text not null default '',
    x_username text not null default '',
    first_seen_at text not null,
    last_seen_at text not null,
    last_run_id text not null default '',
    last_status text not null default '',
    run_count integer not null default 1,
    updated_at text not null default (datetime('now'))
);

create index if not exists idx_wallet_registry_last_seen_at
    on wallet_registry (last_seen_at desc);

create table if not exists archived_documents (
    document_key text primary key,
    document_type text not null,
    run_id text not null default '',
    wallet_address text not null default '',
    source_path text not null default '',
    content_sha256 text not null,
    payload text not null,
    metadata text not null default '{}',
    updated_at text not null default (datetime('now'))
);

create index if not exists idx_archived_documents_wallet_type_updated
    on archived_documents (wallet_address, document_type, updated_at desc);

create index if not exists idx_archived_documents_run_type
    on archived_documents (run_id, document_type);

create table if not exists wallet_trade_ledger (
    record_key text primary key,
    wallet_address text not null,
    run_id text not null default '',
    snapshot_scope text not null,
    history_scope text not null,
    event_timestamp integer,
    payload text not null,
    updated_at text not null default (datetime('now'))
);

create index if not exists idx_wallet_trade_ledger_wallet_ts
    on wallet_trade_ledger (wallet_address, event_timestamp desc);

create index if not exists idx_wallet_trade_ledger_run_id
    on wallet_trade_ledger (run_id);

create table if not exists wallet_operation_ledger (
    record_key text primary key,
    wallet_address text not null,
    run_id text not null default '',
    snapshot_scope text not null,
    history_scope text not null,
    operation_type text not null,
    event_timestamp integer,
    payload text not null,
    updated_at text not null default (datetime('now'))
);

create index if not exists idx_wallet_operation_ledger_wallet_ts
    on wallet_operation_ledger (wallet_address, event_timestamp desc);

create index if not exists idx_wallet_operation_ledger_wallet_op_ts
    on wallet_operation_ledger (wallet_address, operation_type, event_timestamp desc);

create index if not exists idx_wallet_operation_ledger_run_id
    on wallet_operation_ledger (run_id);

create table if not exists wallet_history_gaps (
    gap_key text primary key,
    wallet_address text not null,
    run_id text not null default '',
    snapshot_scope text not null,
    section_name text not null,
    history_scope text not null,
    collection_mode text not null default '',
    stop_reason text not null default '',
    complete integer not null default 0,
    range_start integer,
    range_end integer,
    payload text not null,
    updated_at text not null default (datetime('now'))
);

create index if not exists idx_wallet_history_gaps_open_wallet_section_scope
    on wallet_history_gaps (wallet_address, section_name, history_scope, range_start desc)
    where complete = 0;

create index if not exists idx_wallet_history_gaps_run_id
    on wallet_history_gaps (run_id);
