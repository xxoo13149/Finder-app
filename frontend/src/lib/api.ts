export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'partial' | 'artifact';

export type RunRecord = {
  run_id: string;
  status: RunStatus;
  output_dir: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: {
    report_path?: string;
    analysis_summary_path?: string;
    selected_wallet_count?: number;
    errors?: unknown[];
  } | null;
  error?: string | null;
  progress?: Array<{time: string; message: string}>;
  phase?: string;
  percent?: number;
  selected_wallet_count?: number;
  wallet_detail_count?: number;
  active_diagnostics?: {
    progress_counts?: Record<string, number>;
    wallets?: Record<string, number>;
    weather_events?: Record<string, number>;
    relay_source?: Record<string, unknown>;
  };
  resumable?: boolean;
  summary?: AnalysisSummary;
  files?: ArtifactFile[];
};

export type ArtifactFile = {
  name: string;
  path: string;
  type: string;
  size?: number | null;
  modified_at: string;
};

export type AnalysisSummary = {
  leaderboard_rows_fetched?: number;
  weather_events_indexed?: number;
  wallets_screened?: number;
  wallets_selected?: number;
  wallets_core_labeled?: number;
  finder_ai_summary?: FinderAiRunSummary;
  diagnostics?: RunDiagnostics;
  errors?: number;
  label_counts?: Record<string, number>;
  averages?: Record<string, number>;
  top_wallets_by_pnl?: WalletRankSummary[];
  top_wallets_by_frequency?: WalletRankSummary[];
};

export type FinderAiRunSummary = {
  selected_wallets?: number;
  finder_ai_present?: number;
  eligible?: number;
  generated?: number;
  cached?: number;
  fallback?: number;
  failed?: number;
  skipped?: number;
  needs_review?: number;
  has_conflict?: number;
  latest_generated_at?: string;
  status_counts?: Record<string, number>;
  reason_counts?: Record<string, number>;
};

export type RunDiagnostics = {
  weather_events?: {
    indexed?: number | null;
    max?: number | null;
    tag_id?: string | number | null;
    tag_slug?: string;
    fetch_mode?: string;
    reused_existing?: boolean;
    cap_hit?: boolean;
    stop_reason?: string;
    fetch_stop_reason?: string;
    page_count?: number | null;
    last_page_size?: number | null;
    terminal_next_cursor_present?: boolean | null;
    natural_end?: boolean;
    shortfall_hint?: string;
    coverage_note?: string;
    trading_fallback_enabled?: boolean;
  };
  core_labels?: {
    wallets?: number;
    by_key?: Record<string, number>;
  };
  hydration?: {
    completed?: number;
    skipped?: number;
    failed?: number;
    unknown?: number;
    status_counts?: Record<string, number>;
    reason_counts?: Record<string, number>;
    history_scopes?: Record<string, number>;
  };
  finder_ai?: FinderAiRunSummary;
};

export type WalletRankSummary = {
  wallet: string;
  rank?: string | number;
  user_name?: string;
  x_username?: string;
  pnl?: number;
  closed_profit_multiple?: number;
  closed_position_win_rate?: number;
  trades_per_active_day?: number;
  trade_count?: number;
};

export type WalletRow = {
  wallet: string;
  rank?: string | number;
  user_name?: string;
  x_username?: string;
  ai_strategy_focus?: string;
  ai_brief_short?: string;
  ai_generation_status?: string;
  ai_generation_reason?: string;
  ai_needs_review?: boolean;
  ai_has_conflict?: boolean;
  ai_evidence_level?: string;
  pnl?: number;
  volume?: number;
  trade_count?: number;
  weather_trade_count?: number;
  weather_trade_ratio?: number;
  weather_notional_ratio?: number;
  closed_position_win_rate?: number;
  closed_profit_multiple?: number;
  median_trade_notional?: number;
  trades_per_active_day?: number;
  dominant_region?: string;
  main_region?: string;
  dominant_region_trade_ratio?: number;
  max_region_daily_profit_multiple?: number;
  highest_burst?: number;
  highest_burst_region?: string;
  highest_burst_date?: string;
  recent_evidence_date?: string;
  best_region_win_rate_region?: string;
  best_region_positive_return_day_ratio?: number;
  low_chip_cost_trade_ratio?: number;
  labels?: string[];
  has_core_label?: boolean;
  core_label_keys?: string[];
  selected?: boolean;
  reasons?: string[];
  detail_available?: boolean;
  source?: string;
};

export type LabelSummary = {
  key: string;
  display_name: string;
  description?: string;
  evidence?: LabelEvidence;
};

export type LabelEvidenceRecord = {
  text?: string;
  city?: string;
  region?: string;
  date?: string;
  buy_date?: string;
  high_temperature_date?: string;
  multiple?: number | string;
  profit_multiple?: number | string;
  buy?: number;
  sell?: number;
  buy_amount?: number;
  sell_amount?: number;
  trade_count?: number;
  ratio?: number;
  trade_ratio?: number;
  chip_cost?: number;
  [key: string]: unknown;
};

export type LabelEvidence = {
  key?: string;
  display_name?: string;
  title?: string;
  description?: string;
  matched?: boolean;
  outcome?: string;
  reason?: string;
  decision?: string;
  facts?: Record<string, string | number | boolean | null | undefined>;
  details?: Record<string, unknown>;
  records?: Array<string | LabelEvidenceRecord>;
  evidence?: Array<string | LabelEvidenceRecord>;
};

export type LabelEvaluation = Omit<LabelEvidence, 'facts'> & {
  key: string;
  display_name?: string;
  matched: boolean;
  reason: string;
  facts?: Record<string, unknown>;
  details?: Record<string, unknown>;
  records?: Array<string | LabelEvidenceRecord>;
};

export type FinderAiLabel = {
  kind?: string;
  value?: string;
  source?: string;
  evidence?: string;
};

export type FinderAiPrimarySignal = {
  key?: string;
  label?: string;
  matched?: boolean;
  reason?: string;
};

export type FinderAiMetric = {
  key?: string;
  label?: string;
  value?: string | number | boolean | null;
};

export type FinderAiWeatherSignals = {
  marketScope?: string;
  resolutionSource?: string;
  forecastBasis?: string;
  timingWindow?: string;
  edgeStyle?: string;
  weatherDrivers?: string[];
  evidenceQuality?: string;
};

export type FinderAiProviderMeta = {
  provider?: string;
  model?: string;
  promptVersion?: string;
  generatedAt?: string;
  inputHash?: string;
  requestId?: string;
  generationScope?: 'brief' | 'deep' | string;
  outputSchemaVersion?: string;
};

export type FinderAiBriefGeneration = {
  enabled?: boolean;
  status?: string;
  reason?: string;
  lastError?: string;
};

export type FinderAiResult = {
  sourceName?: string;
  runId?: string;
  normalizedAddress?: string;
  wallet?: {
    address?: string;
    displayName?: string;
    alias?: string;
  };
  matched?: boolean;
  strategyFocus?: string;
  aiBriefShort?: string;
  aiBriefNote?: string;
  aiDeepNote?: string;
  evidenceLevel?: string;
  hasConflict?: boolean;
  needsReview?: boolean;
  labels?: FinderAiLabel[];
  primarySignals?: FinderAiPrimarySignal[];
  keyMetrics?: FinderAiMetric[];
  sourceExcerpt?: string;
  weatherSignals?: FinderAiWeatherSignals;
  providerMeta?: FinderAiProviderMeta;
  briefGeneration?: FinderAiBriefGeneration;
};

export type WalletDetail = {
  wallet: string;
  leaderboard_entry?: Record<string, unknown>;
  screening?: WalletRow;
  selection_record?: WalletRow;
  labels?: LabelSummary[];
  label_evaluations?: LabelEvaluation[];
  label_evidence?: LabelEvidence[] | Record<string, LabelEvidence>;
  label_match_details?: LabelEvidence[] | Record<string, LabelEvidence>;
  strategy_notes?: string[];
  metrics?: Record<string, unknown>;
  profile?: Record<string, unknown>;
  top_trades?: MarketRecord[];
  top_positions?: MarketRecord[];
  top_closed_positions?: MarketRecord[];
  raw_counts?: Record<string, number>;
  evidence_summary?: EvidenceSummary;
  operation_audit?: OperationAudit;
  finder_ai?: FinderAiResult;
};

export type EvidenceSummary = {
  headline?: string;
  matched_label_count?: number;
  label_count?: number;
  main_region?: string;
  highlight_multiple?: number;
  latest_evidence_date?: string;
  audit_complete?: boolean;
  trade_liquidity_profit?: number;
  final_settlement_profit?: number;
  unified_profit?: number;
};

export type OperationAuditRecord = {
  operation?: string;
  audit_bucket?: string;
  verification?: string;
  source?: string;
  timestamp?: number;
  date?: string;
  transaction_hash?: string;
  title?: string;
  market?: string;
  region?: string;
  text?: string;
  [key: string]: unknown;
};

export type OperationBucket = {
  operation?: string;
  status?: string;
  reason?: string;
  count?: number;
  verified_count?: number;
  partial_count?: number;
  complete?: boolean;
  source?: string;
  evidence?: OperationAuditRecord[];
};

export type OperationAudit = {
  wallet?: string;
  complete?: boolean;
  collection_status?: Record<string, Record<string, unknown>>;
  profit_summary?: Record<string, unknown>;
  operations?: Record<string, OperationBucket>;
  record_count?: number;
  records?: OperationAuditRecord[];
};

export type MarketRecord = {
  title?: string;
  outcome?: string;
  side?: string;
  size?: number;
  price?: number;
  avgPrice?: number;
  curPrice?: number;
  currentValue?: number;
  cashPnl?: number;
  realizedPnl?: number;
  totalBought?: number;
  timestamp?: number;
  endDate?: string;
  slug?: string;
  eventSlug?: string;
  [key: string]: unknown;
};

export type AnalysisMode = 'standard' | 'weekly_high_profit' | 'smart_wallet_library_refresh' | 'relay_analysis';

export type ActivityFilterMode = 'all' | 'normal_active' | 'inactive';

export type DeepSeekRelayFilter = 'all' | 'completed' | 'incomplete';

export type RelayCoreLabelFilter = 'all' | 'core' | 'non_core';

export type RelayImportInput = {
  sourceRunId: string;
  coreLabelFilter?: RelayCoreLabelFilter;
  deepSeekFilter?: DeepSeekRelayFilter;
};

export type CreateRunInput = {
  analysis_mode?: AnalysisMode;
  name?: string;
  activity_filter_mode?: ActivityFilterMode;
  target_count?: number;
  min_pnl?: number;
  max_pnl?: number;
  min_volume?: number;
  max_volume?: number;
  min_traded_count?: number;
  max_traded_count?: number;
  min_weather_trade_ratio?: number;
  fetch_limit?: number;
  max_fetch_limit?: number;
  max_weather_events?: number;
  max_wallet_offset?: number;
  concurrent_wallets?: number;
  use_cache?: boolean;
  enable_chain_validation?: boolean;
  verbose?: boolean;
  chain_api_key_env?: string;
  wallet_import_payload?: unknown;
  wallet_import_file_name?: string;
  smart_wallet_import_payload?: unknown;
  smart_wallet_import_file_name?: string;
  relay_import?: RelayImportInput;
};

export type RelayImportBuildResult = {
  payload: unknown;
  file_name: string;
  summary: {
    wallet_count?: number;
    source_run_id?: string;
    source_pool?: string;
    source_total?: number;
    matched_count?: number;
    deepseek_completed_count?: number;
    deepseek_incomplete_count?: number;
    core_labeled_count?: number;
    non_core_count?: number;
    core_label_filter?: RelayCoreLabelFilter;
    deepseek_filter?: DeepSeekRelayFilter;
    [key: string]: unknown;
  };
  source_total: number;
  matched_count: number;
  completed_count: number;
  incomplete_count: number;
  core_labeled_count: number;
  non_core_count: number;
};

export type Paginated<T> = {
  items: T[];
  total: number;
  offset: number;
  limit: number;
};

export type CleanupItem = {
  id: string;
  label: string;
  path: string;
  item_type:
    | 'analysis_run'
    | 'diagnostic_run'
    | 'temp_output'
    | 'runtime_cache'
    | 'runtime_log'
    | 'python_cache'
    | 'wallet_roster'
    | 'wallet_registry'
    | 'wallet_registry_entry'
    | string;
  size_bytes: number;
  file_count: number;
  modified_at: string;
  note?: string;
  locked?: boolean;
  locked_reason?: string;
  run_id?: string;
  status?: string;
  detail_prunable_bytes?: number;
  wallet_address?: string;
  wallet?: string;
  address?: string;
  user_name?: string;
  username?: string;
  display_name?: string;
  wallet_name?: string;
  first_seen_at?: string;
  first_seen?: string;
  last_seen_at?: string;
  last_seen?: string;
  run_count?: number | string;
  runs_count?: number | string;
  related_run_count?: number | string;
};

export type CleanupSection = {
  key: string;
  label: string;
  description?: string;
  count: number;
  size_bytes: number;
  items: CleanupItem[];
};

export type CleanupAction = {
  key: string;
  label: string;
  description?: string;
  warning?: string;
  target_count: number;
  size_bytes: number;
};

export type CleanupActionCategory = 'history' | 'diagnostic' | 'runtime';

export type CleanupActionRisk = 'low' | 'medium' | 'high';

export type CleanupActionMeta = {
  key: string;
  category: CleanupActionCategory;
  risk: CleanupActionRisk;
  scopeLabel: string;
  preserveLabel?: string;
  confirmPhrase?: string;
};

export type CleanupInventory = {
  generated_at: string;
  sections: CleanupSection[];
  actions: CleanupAction[];
};

export type CleanupDeleteResult = {
  ok: boolean;
  deleted_count: number;
  deleted_bytes: number;
  deleted_item_ids: string[];
  deleted_run_ids: string[];
  inventory: CleanupInventory;
};

export type CleanupDeleteMode = 'delete' | 'prune';

export type CleanupDeleteRequest = {
  itemIds?: string[];
  actionKey?: string;
  mode?: CleanupDeleteMode;
};

export type HealthPayload = {
  ok: boolean;
  time: string;
};

export type SystemStatusPayload = {
  ok: boolean;
  root: string;
  runtime_state_path: string;
  launched_at?: string;
  frontend_url: string;
  processes: Record<string, {pid: number; running: boolean}>;
};

export type ShutdownPayload = {
  ok: boolean;
  message: string;
};

export type SmartProConfigPayload = {
  configured: boolean;
  base_url?: string | null;
  commit_path: string;
  timeout_seconds: number;
  token_configured: boolean;
  access_service_token_configured?: boolean;
  errors: string[];
};

export type SmartProSyncInput = {
  runId: string;
  wallets?: string[];
  filters?: Record<string, string>;
};

export type SmartProCommitResult = {
  createdCount?: number;
  updatedCount?: number;
  failedRows?: Array<{rowNumber?: number; displayName?: string; reason?: string}>;
};

export type SmartProSyncResult = {
  ok: boolean;
  run_id: string;
  requested_count?: number | null;
  sent_count: number;
  payload_bytes?: number;
  smart_pro_base_url?: string;
  endpoint?: string;
  smart_pro?: {
    ok?: boolean;
    data?: {
      totalRows?: number;
      validRows?: number;
      sourceName?: string;
      runId?: string;
      fallbackReason?: string;
      commit?: SmartProCommitResult;
    };
    error?: string;
    [key: string]: unknown;
  };
  summary?: {
    totalRows?: number;
    validRows?: number;
    createdCount?: number;
    updatedCount?: number;
    failedCount?: number;
    fallbackReason?: string;
  };
};

const API_BASE = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') || '';

export const apiBaseLabel = API_BASE || '同源 /api 代理';

function endpoint(path: string): string {
  return `${API_BASE}${path}`;
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(endpoint(path), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch {
      // Keep the HTTP message.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

async function apiText(path: string): Promise<string> {
  const response = await fetch(endpoint(path));
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.text();
}

export async function listRuns(): Promise<RunRecord[]> {
  const payload = await apiJson<{items: RunRecord[]}>('/api/runs');
  return payload.items || [];
}

export async function getHealth(): Promise<HealthPayload> {
  return apiJson<HealthPayload>('/api/health');
}

export async function getSystemStatus(): Promise<SystemStatusPayload> {
  return apiJson<SystemStatusPayload>('/api/system/status');
}

export async function shutdownApplication(): Promise<ShutdownPayload> {
  try {
    return await apiJson<ShutdownPayload>('/api/system/shutdown', {
      method: 'POST',
      body: JSON.stringify({source: 'frontend'}),
      keepalive: true,
    });
  } catch (err) {
    if (err instanceof TypeError) {
      return {ok: true, message: 'application shutdown requested'};
    }
    throw err;
  }
}

export async function getSmartProConfig(): Promise<SmartProConfigPayload> {
  return apiJson<SmartProConfigPayload>('/api/smart-pro/config');
}

export async function syncSmartProImport(input: SmartProSyncInput): Promise<SmartProSyncResult> {
  return apiJson<SmartProSyncResult>('/api/smart-pro/import/commit', {
    method: 'POST',
    body: JSON.stringify({
      run_id: input.runId,
      wallets: input.wallets,
      filters: input.filters,
    }),
  });
}

export async function getRun(runId: string): Promise<RunRecord> {
  return apiJson<RunRecord>(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function getSummary(runId: string): Promise<AnalysisSummary> {
  return apiJson<AnalysisSummary>(`/api/runs/${encodeURIComponent(runId)}/summary`);
}

export async function getWallets(
  runId: string,
  options: {offset?: number; limit?: number} = {},
): Promise<Paginated<WalletRow>> {
  const params = new URLSearchParams();
  if (options.offset != null) params.set('offset', String(options.offset));
  if (options.limit != null) params.set('limit', String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return apiJson<Paginated<WalletRow>>(`/api/runs/${encodeURIComponent(runId)}/wallets${suffix}`);
}

export async function getAllWallets(runId: string, pageSize = 500): Promise<WalletRow[]> {
  const rows: WalletRow[] = [];
  let offset = 0;
  for (;;) {
    const page = await getWallets(runId, {offset, limit: pageSize});
    rows.push(...(page.items || []));
    offset += page.limit || pageSize;
    if (rows.length >= page.total || !page.items?.length) {
      break;
    }
  }
  return rows;
}

export async function buildRelayImportPayload(input: RelayImportInput): Promise<RelayImportBuildResult> {
  return apiJson<RelayImportBuildResult>(`/api/runs/${encodeURIComponent(input.sourceRunId)}/relay-import`, {
    method: 'POST',
    body: JSON.stringify({
      core_label_filter: input.coreLabelFilter || 'all',
      deepseek_filter: input.deepSeekFilter || 'all',
    }),
  });
}

export async function getWalletDetail(runId: string, wallet: string): Promise<WalletDetail> {
  return apiJson<WalletDetail>(
    `/api/runs/${encodeURIComponent(runId)}/wallets/${encodeURIComponent(wallet.toLowerCase())}`,
  );
}

export async function getReport(runId: string): Promise<string> {
  return apiText(`/api/runs/${encodeURIComponent(runId)}/report`);
}

export async function getFiles(runId: string): Promise<ArtifactFile[]> {
  const payload = await apiJson<{items: ArtifactFile[]}>(`/api/runs/${encodeURIComponent(runId)}/files`);
  return payload.items || [];
}

export async function getArtifact(runId: string, path: string): Promise<string> {
  return apiText(`/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(path)}`);
}

export async function getDefaultConfig(): Promise<Record<string, any>> {
  return apiJson<Record<string, any>>('/api/config/default');
}

export async function saveDefaultConfig(config: Record<string, any>): Promise<{ok: boolean; path: string}> {
  return apiJson<{ok: boolean; path: string}>('/api/config/default', {
    method: 'PUT',
    body: JSON.stringify({config}),
  });
}

export async function startRun(input: CreateRunInput): Promise<RunRecord> {
  const runName = input.name?.trim();
  const body = {
    analysis_mode: input.analysis_mode,
    run_id: runName ? slugifyRunId(runName) : undefined,
    overrides: {
      target_count: input.target_count,
      min_pnl: input.min_pnl,
      max_pnl: input.max_pnl,
      min_volume: input.min_volume,
      max_volume: input.max_volume,
      min_traded_count: input.min_traded_count,
      max_traded_count: input.max_traded_count,
      min_weather_trade_ratio: input.min_weather_trade_ratio,
      activity_filter_mode: input.activity_filter_mode,
      fetch_limit: input.fetch_limit,
      max_fetch_limit: input.max_fetch_limit,
      max_weather_events: input.max_weather_events,
      max_wallet_offset: input.max_wallet_offset,
      concurrent_wallets: input.concurrent_wallets,
      use_cache: input.use_cache,
      enable_chain_validation: input.enable_chain_validation,
      verbose: input.verbose,
      chain_api_key_env: input.chain_api_key_env,
    },
    wallet_import:
      input.wallet_import_payload != null
        ? {
            file_name: input.wallet_import_file_name,
            payload: input.wallet_import_payload,
          }
        : undefined,
    smart_wallet_import:
      input.smart_wallet_import_payload != null
        ? {
            file_name: input.smart_wallet_import_file_name,
            payload: input.smart_wallet_import_payload,
          }
        : undefined,
    relay_import: input.relay_import
      ? {
          source_run_id: input.relay_import.sourceRunId,
          core_label_filter: input.relay_import.coreLabelFilter || 'all',
          deepseek_filter: input.relay_import.deepSeekFilter || 'all',
        }
      : undefined,
  };
  return apiJson<RunRecord>('/api/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function resumeRun(runId: string): Promise<RunRecord> {
  return apiJson<RunRecord>(`/api/runs/${encodeURIComponent(runId)}/resume`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function getCleanupInventory(): Promise<CleanupInventory> {
  return apiJson<CleanupInventory>('/api/history/cleanup');
}

export async function performCleanupDelete(request: CleanupDeleteRequest): Promise<CleanupDeleteResult> {
  return apiJson<CleanupDeleteResult>('/api/history/cleanup/delete', {
    method: 'POST',
    body: JSON.stringify({
      item_ids: request.itemIds,
      action_key: request.actionKey,
      operation: request.mode || 'delete',
    }),
  });
}

export async function deleteCleanupItems(itemIds: string[]): Promise<CleanupDeleteResult> {
  return performCleanupDelete({itemIds});
}

export async function pruneCleanupItems(itemIds: string[]): Promise<CleanupDeleteResult> {
  return performCleanupDelete({itemIds, mode: 'prune'});
}

export async function runCleanupAction(actionKey: string): Promise<CleanupDeleteResult> {
  return performCleanupDelete({actionKey});
}

const cleanupActionMetaMap: Record<string, CleanupActionMeta> = {
  delete_diagnostic_records: {
    key: 'delete_diagnostic_records',
    category: 'diagnostic',
    risk: 'medium',
    scopeLabel: '删除冒烟测试记录、验收截图、测试产物与临时输出。',
    preserveLabel: '不会删除正式分析历史，也不会影响当前配置。',
  },
  clear_runtime_storage: {
    key: 'clear_runtime_storage',
    category: 'runtime',
    risk: 'low',
    scopeLabel: '一键删除接口缓存、运行日志和 Python 编译缓存。',
    preserveLabel: '不会删除正式分析结果；缓存会在后续运行中自动重建。',
  },
  clear_api_cache: {
    key: 'clear_api_cache',
    category: 'runtime',
    risk: 'low',
    scopeLabel: '删除接口响应缓存，下次分析会重新抓取远端数据。',
    preserveLabel: '不会删除历史分析结果，只会牺牲下一次请求的命中率。',
  },
  clear_runtime_logs: {
    key: 'clear_runtime_logs',
    category: 'runtime',
    risk: 'low',
    scopeLabel: '删除启动器、API 与前端运行日志。',
    preserveLabel: '不会影响报告、摘要和分析记录。',
  },
  clear_python_caches: {
    key: 'clear_python_caches',
    category: 'runtime',
    risk: 'low',
    scopeLabel: '删除 Python 编译缓存与运行期缓存目录。',
    preserveLabel: '缓存会在下次运行时自动重建。',
  },
  prune_run_details: {
    key: 'prune_run_details',
    category: 'history',
    risk: 'high',
    scopeLabel: '保留 report、summary、selected_wallets，移除钱包明细、原始快照和完整交易附件。',
    preserveLabel: '清理后仍能查看摘要和报告，但钱包详情页与深度证据会失效。',
    confirmPhrase: '确认删除',
  },
  clear_wallet_registry: {
    key: 'clear_wallet_registry',
    category: 'history',
    risk: 'medium',
    scopeLabel: '删除历史已抓取钱包名册，让这些钱包在后续筛选中重新出现。',
    preserveLabel: '不会删除已有分析报告，但下一次搜索将不再默认排除这些历史钱包。',
    confirmPhrase: '确认清空',
  },
};

export function getCleanupActionMeta(actionKey: string): CleanupActionMeta {
  return (
    cleanupActionMetaMap[actionKey] || {
      key: actionKey,
      category: 'runtime',
      risk: 'medium',
      scopeLabel: '此操作会删除一组历史或运行数据。',
    }
  );
}

const cleanupWalletRosterItemTypes = new Set([
  'wallet_roster',
  'wallet_registry',
  'wallet_registry_entry',
  'historical_wallet',
  'wallet_record',
  'tracked_wallet',
]);

export function cleanupItemTypeLabel(itemType?: string): string {
  const labels: Record<string, string> = {
    analysis_run: '正式分析',
    diagnostic_run: '测试/诊断',
    temp_output: '临时输出',
    runtime_cache: '接口缓存',
    runtime_log: '运行日志',
    python_cache: '代码缓存',
    wallet_roster: '历史名册',
    wallet_registry: '历史名册',
  };
  if (cleanupWalletRosterItemTypes.has(itemType || '')) {
    return '历史名册';
  }
  return labels[itemType || ''] || '其他数据';
}

export function cleanupItemTypeTone(itemType?: string): string {
  if (itemType === 'analysis_run') return 'border-violet-200 bg-violet-50 text-violet-700';
  if (itemType === 'diagnostic_run') return 'border-amber-200 bg-amber-50 text-amber-700';
  if (itemType === 'temp_output') return 'border-slate-200 bg-slate-50 text-slate-700';
  if (itemType === 'runtime_cache') return 'border-cyan-200 bg-cyan-50 text-cyan-700';
  if (itemType === 'runtime_log') return 'border-slate-200 bg-slate-50 text-slate-700';
  if (itemType === 'python_cache') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (cleanupWalletRosterItemTypes.has(itemType || '')) return 'border-blue-200 bg-blue-50 text-blue-700';
  return 'border-slate-200 bg-slate-50 text-slate-700';
}

export function isCleanupWalletRosterItemType(itemType?: string): boolean {
  return cleanupWalletRosterItemTypes.has(itemType || '');
}

function slugifyRunId(value: string): string {
  const cleaned = value
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+$/, 'Z');
  return cleaned ? `${cleaned}-${stamp}` : `polymarket-weather-${stamp}`;
}

export function statusTone(status?: string): string {
  if (status === 'succeeded') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (status === 'failed') return 'bg-red-50 text-red-700 border-red-200';
  if (status === 'running') return 'bg-blue-50 text-blue-700 border-blue-200';
  if (status === 'queued') return 'bg-amber-50 text-amber-700 border-amber-200';
  return 'bg-slate-50 text-slate-700 border-slate-200';
}

export function statusLabel(status?: string): string {
  const labels: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    succeeded: '已完成',
    failed: '失败',
    partial: '部分完成',
    artifact: '历史结果',
  };
  return labels[status || 'artifact'] || status || '历史结果';
}

export function latestCompletedRun(runs: RunRecord[]): RunRecord | undefined {
  const readableRuns = sortRunsForDisplay(runs).filter((run) => !isDiagnosticRun(run.run_id));
  return readableRuns.find(hasReadableRunResult) || runs.find(hasReadableRunResult) || readableRuns[0] || runs[0];
}

export function resolveSelectedRunId(runs: RunRecord[], activeRunId?: string): string | undefined {
  if (activeRunId) {
    const activeRun = runs.find((run) => run.run_id === activeRunId);
    if (activeRun && hasReadableRunResult(activeRun)) return activeRunId;
  }
  return latestCompletedRun(runs)?.run_id;
}

export function sortRunsForDisplay(runs: RunRecord[]): RunRecord[] {
  return [...runs].sort((left, right) => {
    const rightStamp = runSortTimestamp(right);
    const leftStamp = runSortTimestamp(left);
    if (rightStamp !== leftStamp) return rightStamp - leftStamp;
    const rightRank = runSortRank(right);
    const leftRank = runSortRank(left);
    if (rightRank !== leftRank) return rightRank - leftRank;
    return right.run_id.localeCompare(left.run_id);
  });
}

export function hasReadableRunResult(run?: RunRecord): boolean {
  if (!run) return false;
  const status = run.status || 'artifact';
  if (status === 'queued' || status === 'running') return true;
  if (status === 'failed') return false;
  if (run.resumable || status === 'succeeded' || status === 'partial') return true;
  return Number(run.selected_wallet_count || 0) > 0 || Number(run.wallet_detail_count || 0) > 0;
}

export function isDiagnosticRun(runId?: string): boolean {
  const value = (runId || '').toLowerCase();
  return ['smoke', 'codex', 'browser-', 'ui-api', 'test-fast'].some((token) => value.includes(token));
}

export function runDisplayName(run?: Pick<RunRecord, 'run_id' | 'created_at' | 'finished_at' | 'status'>): string {
  if (!run) return '未选择分析';
  const time = formatShortDateTime(run.finished_at || run.created_at);
  if (isDiagnosticRun(run.run_id)) return `测试记录 · ${time}`;

  const cleaned = run.run_id
    .replace(/-\d{8}T\d{6}Z?$/i, '')
    .replace(/^polymarket-weather-\d{8}-\d{6}Z-[a-z0-9]+$/i, '')
    .replace(/[-_]+/g, ' ')
    .trim();

  return cleaned ? `${cleaned} · ${time}` : `分析结果 · ${time}`;
}

export function runDetailLabel(run?: Pick<RunRecord, 'run_id' | 'created_at' | 'finished_at' | 'status'>): string {
  if (!run) return '';
  const status = statusLabel(run.status);
  const time = formatShortDateTime(run.finished_at || run.created_at);
  return `${status} · ${time}`;
}

export function shortAddress(value?: string): string {
  if (!value) return '-';
  if (value.length <= 12) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

export function shortRunId(value?: string): string {
  const text = String(value || '').trim();
  if (!text) return '-';
  return text.length <= 10 ? text : text.slice(-8);
}

export function runPickerLabel(
  run: Pick<RunRecord, 'run_id' | 'created_at' | 'finished_at' | 'status'>,
  duplicate = false,
): string {
  const base = runDisplayName(run);
  return duplicate ? `${base} · ${shortRunId(run.run_id)}` : base;
}

export function formatCurrency(value?: number): string {
  if (value == null || Number.isNaN(value)) return '-';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: value >= 1000 ? 0 : 2,
  }).format(value);
}

export function formatNumber(value?: number, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '-';
  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatPercent(value?: number): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `${(value * 100).toFixed(1)}%`;
}

export function formatBytes(value?: number): string {
  if (value == null || Number.isNaN(value) || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 100 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN');
}

export function formatShortDateTime(value?: string | null): string {
  if (!value) return '未记录时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function runSortTimestamp(run: Pick<RunRecord, 'created_at' | 'finished_at'>): number {
  return toSortTimestamp(run.finished_at) || toSortTimestamp(run.created_at) || 0;
}

function runSortRank(run: Pick<RunRecord, 'status'>): number {
  const status = String(run.status || 'artifact');
  const ranks: Record<string, number> = {
    running: 5,
    queued: 4,
    partial: 3,
    succeeded: 2,
    artifact: 1,
    failed: 0,
  };
  return ranks[status] ?? 1;
}

function toSortTimestamp(value?: string | null): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function finderAiGenerationReasonLabel(
  reason?: string,
  options: {
    status?: string;
    needsReview?: boolean;
    hasConflict?: boolean;
  } = {},
): string | undefined {
  const key = String(reason || '').trim().toLowerCase();
  const status = String(options.status || '').trim().toLowerCase();

  const labels: Record<string, string> = {
    analysis_audit_incomplete: '抓取不完整，已建议人工复核',
    signal_conflict: '信号存在冲突，已建议人工复核',
    evidence_below_gate: '证据不足，暂不生成 AI 文案',
    missing_normalized_address: '地址信息不完整，暂不生成 AI 文案',
    ready_for_brief: '结构化证据已就绪，等待生成 AI 文案',
    generated: '已完成本次 AI 生成',
    cache_hit: '当前展示的是缓存摘要',
    local_fallback: '未调用外部模型，已切换到本地兜底摘要',
    local_existing: '沿用现有摘要并补齐短摘',
    provider_error: '模型调用失败，已保留结构化证据',
  };

  if (labels[key]) return labels[key];
  if (options.hasConflict) return '信号存在冲突，已建议人工复核';
  if (options.needsReview || status === 'needs_review') return '当前样本已建议人工复核';
  if (status === 'insufficient') return '证据不足，暂不生成 AI 文案';
  if (status === 'ready') return '结构化证据已就绪，等待生成 AI 文案';
  if (status === 'fallback') return '未调用外部模型，已切换到本地兜底摘要';
  if (status === 'failed') return '模型调用失败，已保留结构化证据';
  return undefined;
}
