import {Check, ChevronLeft, ChevronRight, Download, Search, X} from 'lucide-react';
import type {ReactNode} from 'react';
import {useEffect, useMemo, useState} from 'react';
import {RunPicker} from '../components/RunPicker';
import {
  RunRecord,
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
        String(wallet.user_name || '').toLowerCase().includes(q);
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
        </div>
      </div>

      {error && <div className="m-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="flex-1 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="sticky top-0 z-10 bg-slate-50">
            <tr>
              <Header>排名</Header>
              <Header>钱包</Header>
              <Header>用户名</Header>
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
                <td colSpan={13} className="px-6 py-10 text-center text-sm text-slate-500">
                  正在加载钱包...
                </td>
              </tr>
            ) : (
              visible.map((wallet) => (
                <tr key={wallet.wallet} className="cursor-pointer transition-colors hover:bg-slate-50" onClick={() => onWalletSelected(wallet.wallet)}>
                  <Cell>{wallet.rank || '-'}</Cell>
                  <Cell mono>{shortAddress(wallet.wallet)}</Cell>
                  <Cell>{wallet.user_name || '-'}</Cell>
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
              ))
            )}
            {!loading && !visible.length && (
              <tr>
                <td colSpan={13} className="px-6 py-10 text-center text-sm text-slate-500">
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

function formatMultiple(value?: number): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `${value.toFixed(2)}x`;
}
