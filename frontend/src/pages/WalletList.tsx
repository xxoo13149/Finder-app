import {Check, ChevronLeft, ChevronRight, Download, Loader2, Search, Send, X} from 'lucide-react';
import type {ReactNode} from 'react';
import {useEffect, useMemo, useState} from 'react';
import {RunPicker} from '../components/RunPicker';
import {
  RunRecord,
  SmartProSyncResult,
  formatBytes,
  WalletRow,
  formatCurrency,
  formatNumber,
  formatPercent,
  getArtifact,
  getWallets,
  latestCompletedRun,
  listRuns,
  runDisplayName,
  shortAddress,
  syncSmartProImport,
} from '../lib/api';
import {cn} from '../lib/utils';

export function WalletList({
  activeRunId,
  pageSize,
  onRunSelected,
  onWalletSelected,
}: {
  activeRunId?: string;
  pageSize: number;
  onRunSelected: (runId: string) => void;
  onWalletSelected: (wallet: string) => void;
}) {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [wallets, setWallets] = useState<WalletRow[]>([]);
  const [query, setQuery] = useState('');
  const [tag, setTag] = useState('all');
  const [selectedOnly, setSelectedOnly] = useState('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [smartProSyncing, setSmartProSyncing] = useState(false);
  const [smartProMessage, setSmartProMessage] = useState<string>();
  const [smartProError, setSmartProError] = useState<string>();
  const [smartProStatus, setSmartProStatus] = useState<string>();

  const selectedRunId = activeRunId || latestCompletedRun(runs)?.run_id;
  const selectedRun = runs.find((item) => item.run_id === selectedRunId);

  useEffect(() => {
    let cancelled = false;
    listRuns()
      .then((items) => {
        if (cancelled) return;
        setRuns(items);
        const latest = activeRunId || latestCompletedRun(items)?.run_id;
        if (latest && !activeRunId) onRunSelected(latest);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRunId, onRunSelected]);

  useEffect(() => {
    if (!selectedRunId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getWallets(selectedRunId, {limit: 500})
      .then((payload) => {
        if (!cancelled) {
          setWallets(payload.items || []);
          setError(undefined);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const tags = useMemo(() => {
    const values = new Set<string>();
    wallets.forEach((wallet) => wallet.labels?.forEach((label) => values.add(label)));
    return Array.from(values).sort();
  }, [wallets]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return wallets.filter((wallet) => {
      const matchesQuery =
        !q ||
        wallet.wallet?.toLowerCase().includes(q) ||
        String(wallet.user_name || '').toLowerCase().includes(q) ||
        String(wallet.x_username || '').toLowerCase().includes(q);
      const matchesTag = tag === 'all' || wallet.labels?.includes(tag);
      const matchesSelected =
        selectedOnly === 'all' ||
        (selectedOnly === 'selected' && wallet.selected !== false) ||
        (selectedOnly === 'rejected' && wallet.selected === false);
      return matchesQuery && matchesTag && matchesSelected;
    });
  }, [query, selectedOnly, tag, wallets]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visible = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  const exportJson = async () => {
    if (!selectedRunId) return;
    const text = await getArtifact(selectedRunId, 'selected_wallets.json');
    const blob = new Blob([text], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${selectedRunId}-selected-wallets.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const syncToSmartPro = async () => {
    if (!selectedRunId || !filtered.length) return;
    const walletsToSync = filtered.map((wallet) => wallet.wallet).filter(Boolean);
    const walletChunks = chunkWallets(walletsToSync, 5);
    setSmartProSyncing(true);
    setSmartProMessage(undefined);
    setSmartProError(undefined);
    setSmartProStatus(`正在整理本次筛选结果，共 ${walletsToSync.length} 个钱包，准备分 ${walletChunks.length} 批同步...`);
    try {
      let mergedResult: SmartProSyncResult | undefined;
      for (let index = 0; index < walletChunks.length; index += 1) {
        const chunk = walletChunks[index];
        setSmartProStatus(`正在同步第 ${index + 1}/${walletChunks.length} 批，本批 ${chunk.length} 个钱包...`);
        const result = await syncSmartProImport({
          runId: selectedRunId,
          wallets: chunk,
          filters: {
            query,
            tag,
            selectedOnly,
          },
        });
        mergedResult = mergedResult ? mergeSmartProResults(mergedResult, result) : result;
        setSmartProStatus(`第 ${index + 1}/${walletChunks.length} 批已完成，累计同步 ${mergedResult.sent_count} 个钱包...`);
      }
      const result = mergedResult;
      if (!result) throw new Error('SmartPro sync returned no result');
      setSmartProStatus(undefined);
      setSmartProMessage(describeSmartProResult(result));
    } catch (err) {
      setSmartProError(err instanceof Error ? err.message : 'SmartPro sync failed');
    } finally {
      setSmartProSyncing(false);
      setSmartProStatus(undefined);
    }
  };

  if (!selectedRunId) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-semibold text-slate-900">还没有分析记录</h1>
        <p className="mt-2 text-sm text-slate-500">新建一次分析后，这里会展示钱包表格。</p>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-shrink-0 flex-col gap-4 border-b border-slate-100 px-6 py-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900">钱包列表</h1>
          <p className="mt-1 text-sm text-slate-500">{runDisplayName(selectedRun)}</p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <RunPicker runs={runs} selectedRunId={selectedRunId} onRunSelected={onRunSelected} />
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input
              type="text"
              placeholder="搜索地址或用户名"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              className="block w-64 rounded-md border border-slate-300 bg-white py-2 pl-10 pr-3 text-sm placeholder-slate-400 focus:border-[#2E5CFF] focus:outline-none focus:ring-1 focus:ring-[#2E5CFF]"
            />
          </div>
          <select
            value={tag}
            onChange={(event) => {
              setTag(event.target.value);
              setPage(1);
            }}
            className="block w-40 rounded-md border border-slate-300 bg-white py-2 pl-3 pr-10 text-sm"
          >
            <option value="all">全部标签</option>
            {tags.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select
            value={selectedOnly}
            onChange={(event) => {
              setSelectedOnly(event.target.value);
              setPage(1);
            }}
            className="block w-36 rounded-md border border-slate-300 bg-white py-2 pl-3 pr-10 text-sm"
          >
            <option value="all">全部状态</option>
            <option value="selected">已入选</option>
            <option value="rejected">已排除</option>
          </select>
          <button
            onClick={exportJson}
            className="inline-flex h-10 items-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
          >
            <Download className="mr-2 h-4 w-4 text-slate-500" />
            导出
          </button>
          <button
            onClick={syncToSmartPro}
            disabled={smartProSyncing || !filtered.length}
            className="inline-flex h-10 items-center rounded-md border border-[#2E5CFF] bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-[#244ad6] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="mr-2 h-4 w-4" />
            {smartProSyncing ? '同步中...' : `同步 SmartPro (${filtered.length})`}
          </button>
        </div>
      </div>

      {error && <div className="m-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {smartProSyncing && smartProStatus && (
        <div className="m-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700">
          <div className="flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>{smartProStatus}</span>
          </div>
        </div>
      )}
      {smartProError && <div className="m-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{smartProError}</div>}
      {smartProMessage && <div className="m-4 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{smartProMessage}</div>}

      <div className="flex-1 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr>
              <Header>排名</Header>
              <Header>钱包 / 用户名</Header>
              <Header align="right">盈亏</Header>
              <Header align="right">成交量</Header>
              <Header align="right">交易数</Header>
              <Header align="right">天气占比</Header>
              <Header align="right">胜率</Header>
              <Header>主地区</Header>
              <Header align="right">最高暴击</Header>
              <Header>最近证据日</Header>
              <Header>标签</Header>
              <Header align="center">入选</Header>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 bg-white">
            {loading ? (
              <tr>
                <td colSpan={12} className="px-6 py-10 text-center text-sm text-slate-500">
                  正在加载钱包...
                </td>
              </tr>
            ) : (
              visible.map((wallet) => {
                const walletName = preferredWalletListName(wallet);
                const aiBriefShort = preferredWalletListAiBrief(wallet);
                return (
                  <tr key={wallet.wallet} className="cursor-pointer transition-colors hover:bg-slate-50" onClick={() => onWalletSelected(wallet.wallet)}>
                    <Cell>{wallet.rank || '-'}</Cell>
                    <Cell>
                      <div className="min-w-0">
                        <div className={cn('truncate', walletName ? 'font-medium text-slate-900' : 'font-mono font-medium text-slate-900')}>
                          {walletName || shortAddress(wallet.wallet)}
                        </div>
                        {walletName && <div className="truncate font-mono text-xs text-slate-500">{shortAddress(wallet.wallet)}</div>}
                        {aiBriefShort && <div className="mt-1 max-w-[320px] truncate text-xs text-slate-500">{aiBriefShort}</div>}
                      </div>
                    </Cell>
                    <Cell align="right">{formatCurrency(wallet.pnl)}</Cell>
                    <Cell align="right">{formatCurrency(wallet.volume)}</Cell>
                    <Cell align="right">{formatNumber(wallet.trade_count)}</Cell>
                    <Cell align="right">{formatPercent(wallet.weather_notional_ratio)}</Cell>
                    <Cell align="right">{formatPercent(wallet.closed_position_win_rate)}</Cell>
                    <Cell>{wallet.main_region || wallet.dominant_region || '-'}</Cell>
                    <Cell align="right">{formatMultiple(wallet.highest_burst ?? wallet.max_region_daily_profit_multiple)}</Cell>
                    <Cell>{wallet.recent_evidence_date || wallet.highest_burst_date || '-'}</Cell>
                    <Cell>
                      <div className="flex max-w-md flex-wrap gap-1.5">
                        {(wallet.labels || []).slice(0, 3).map((label) => (
                          <span key={label} className="inline-flex items-center rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                            {label}
                          </span>
                        ))}
                        {(wallet.labels || []).length > 3 && <span className="text-xs text-slate-400">+{(wallet.labels || []).length - 3}</span>}
                      </div>
                    </Cell>
                    <Cell align="center">
                      {wallet.selected !== false ? (
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
                          <Check className="h-3.5 w-3.5" />
                        </span>
                      ) : (
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-300 text-slate-300">
                          <X className="h-3.5 w-3.5" />
                        </span>
                      )}
                    </Cell>
                  </tr>
                );
              })
            )}
            {!loading && !visible.length && (
              <tr>
                <td colSpan={12} className="px-6 py-10 text-center text-sm text-slate-500">
                  没有钱包符合当前筛选条件。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-shrink-0 items-center justify-between border-t border-slate-200 bg-white px-6 py-3">
        <div className="text-sm text-slate-500">
          {filtered.length ? (currentPage - 1) * pageSize + 1 : 0}-{Math.min(currentPage * pageSize, filtered.length)} / 共 {filtered.length}
        </div>
        <div className="inline-flex rounded-md shadow-sm">
          <button
            onClick={() => setPage((value) => Math.max(1, value - 1))}
            disabled={currentPage <= 1}
            className="rounded-l-md border border-slate-300 bg-white px-2 py-2 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="border-y border-slate-300 bg-blue-50 px-4 py-2 text-sm font-medium text-[#2E5CFF]">
            {currentPage} / {pageCount}
          </span>
          <button
            onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
            disabled={currentPage >= pageCount}
            className="rounded-r-md border border-slate-300 bg-white px-2 py-2 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

function Header({children, align = 'left'}: {children: ReactNode; align?: 'left' | 'right' | 'center'}) {
  return (
    <th
      className={cn(
        'px-6 py-3 text-xs font-medium uppercase tracking-wider text-slate-500',
        align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left',
      )}
    >
      {children}
    </th>
  );
}

function Cell({
  children,
  align = 'left',
  mono = false,
}: {
  children: ReactNode;
  align?: 'left' | 'right' | 'center';
  mono?: boolean;
}) {
  return (
    <td
      className={cn(
        'whitespace-nowrap px-6 py-3 text-sm text-slate-600',
        align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left',
        mono && 'font-mono font-medium text-slate-900',
      )}
    >
      {children}
    </td>
  );
}

function preferredWalletListName(wallet: WalletRow): string | undefined {
  for (const value of [wallet.user_name, wallet.x_username]) {
    const text = String(value || '').trim();
    if (!text) continue;
    const normalized = text.replace(/^@+/, '');
    if (!normalized) continue;
    const lowered = normalized.toLowerCase();
    if (lowered.startsWith('0x') && lowered.length >= 10) continue;
    return value === wallet.x_username ? `@${normalized}` : normalized;
  }
  return undefined;
}

function preferredWalletListAiBrief(wallet: WalletRow): string | undefined {
  for (const value of [wallet.ai_brief_short, wallet.ai_strategy_focus]) {
    const text = String(value || '').trim();
    if (text) return text;
  }
  return undefined;
}

function describeSmartProResult(result: SmartProSyncResult): string {
  const summary = result.summary || {};
  const commit = result.smart_pro?.data?.commit || {};
  const created = summary.createdCount ?? commit.createdCount ?? 0;
  const updated = summary.updatedCount ?? commit.updatedCount ?? 0;
  const failed = summary.failedCount ?? commit.failedRows?.length ?? 0;
  const valid = summary.validRows ?? result.smart_pro?.data?.validRows ?? result.sent_count;
  const fallback = summary.fallbackReason || result.smart_pro?.data?.fallbackReason;
  const payloadText = result.payload_bytes ? `，上传体积 ${formatBytes(result.payload_bytes)}` : '';
  const base = `SmartPro 同步完成：发送 ${result.sent_count} 条，AI 识别有效 ${valid} 条，新建 ${created} 条，更新 ${updated} 条，失败 ${failed} 条${payloadText}。`;
  return fallback ? `${base} ${humanizeFallbackReason(fallback)}` : base;
}

function chunkWallets(wallets: string[], size: number): string[][] {
  const chunks: string[][] = [];
  for (let index = 0; index < wallets.length; index += size) {
    chunks.push(wallets.slice(index, index + size));
  }
  return chunks;
}

function mergeSmartProResults(current: SmartProSyncResult, next: SmartProSyncResult): SmartProSyncResult {
  const currentSummary = current.summary || {};
  const nextSummary = next.summary || {};
  const fallbackParts = [currentSummary.fallbackReason, nextSummary.fallbackReason].filter(Boolean);
  const uniqueFallback = Array.from(new Set(fallbackParts));
  return {
    ...next,
    sent_count: current.sent_count + next.sent_count,
    requested_count: (current.requested_count || 0) + (next.requested_count || 0),
    payload_bytes: (current.payload_bytes || 0) + (next.payload_bytes || 0),
    summary: {
      totalRows: (currentSummary.totalRows || 0) + (nextSummary.totalRows || 0),
      validRows: (currentSummary.validRows || 0) + (nextSummary.validRows || 0),
      createdCount: (currentSummary.createdCount || 0) + (nextSummary.createdCount || 0),
      updatedCount: (currentSummary.updatedCount || 0) + (nextSummary.updatedCount || 0),
      failedCount: (currentSummary.failedCount || 0) + (nextSummary.failedCount || 0),
      fallbackReason: uniqueFallback.join(' | '),
    },
  };
}

function humanizeFallbackReason(reason: string): string {
  const normalized = reason.toLowerCase();
  if (normalized.includes('gemini request failed') || normalized.includes('groq request failed')) {
    return 'AI 预览通道当前不可用，系统已自动回退到 Finder 本地适配模式，不影响本次同步入库。';
  }
  return reason;
}

function formatMultiple(value?: number): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `${value.toFixed(2)}x`;
}
