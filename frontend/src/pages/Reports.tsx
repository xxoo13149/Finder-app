import {Bar, BarChart, CartesianGrid, Cell as BarCell, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {Download, FileText, RefreshCcw, Table2, Wallet} from 'lucide-react';
import type {ReactNode} from 'react';
import {useEffect, useMemo, useState} from 'react';
import {FinderAiRunSummaryStrip, hasFinderAiPreview, toFinderAiPreviewItem} from '../components/FinderAiRunSummaryStrip';
import {RunPicker} from '../components/RunPicker';
import {
  AnalysisSummary,
  RunRecord,
  WalletRow,
  formatCurrency,
  formatNumber,
  formatPercent,
  getReport,
  getSummary,
  getWallets,
  hasReadableRunResult,
  listRuns,
  resolveSelectedRunId,
  runDisplayName,
  shortAddress,
} from '../lib/api';

const chartColors = ['#2E5CFF', '#10b981', '#f59e0b', '#64748b', '#8b5cf6', '#ef4444'];

export function Reports({
  activeRunId,
  onRunSelected,
  onNavigate,
  onWalletSelected,
}: {
  activeRunId?: string;
  onRunSelected: (runId: string) => void;
  onNavigate?: (page: string) => void;
  onWalletSelected?: (wallet: string) => void;
}) {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [summary, setSummary] = useState<AnalysisSummary>();
  const [wallets, setWallets] = useState<WalletRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState<'report' | 'wallets'>();
  const [error, setError] = useState<string>();

  const selectedRunId = resolveSelectedRunId(runs, activeRunId);
  const selectedRun = runs.find((item) => item.run_id === selectedRunId);
  const averages = summary?.averages || {};

  const labelData = useMemo(
    () =>
      Object.entries(summary?.label_counts || {})
        .sort((a, b) => Number(b[1]) - Number(a[1]))
        .slice(0, 8)
        .map(([name, value]) => ({name, value})),
    [summary],
  );

  const funnelData = useMemo(
    () => [
      {name: '排行榜记录', value: Number(summary?.leaderboard_rows_fetched || 0)},
      {name: '已筛选钱包', value: Number(summary?.wallets_screened || 0)},
      {name: '入选钱包', value: Number(summary?.wallets_selected || wallets.length || 0)},
      {name: '已打标签钱包', value: Number(summary?.wallets_core_labeled || 0)},
    ],
    [summary, wallets.length],
  );

  const topWallets = useMemo(
    () =>
      [...wallets]
        .sort((left, right) => Number(right.pnl || 0) - Number(left.pnl || 0))
        .slice(0, 8),
    [wallets],
  );

  const finderAiPreviewItems = useMemo(
    () =>
      [...wallets]
        .filter(hasFinderAiPreview)
        .sort((left, right) => Number(right.pnl || 0) - Number(left.pnl || 0))
        .slice(0, 10)
        .map(toFinderAiPreviewItem),
    [wallets],
  );

  const stats = [
    {label: '入选钱包', value: formatNumber(summary?.wallets_selected ?? wallets.length), caption: `从 ${formatNumber(summary?.wallets_screened)} 个候选中筛出`},
    {label: '平均胜率', value: formatPercent(averages.closed_position_win_rate), caption: '已平仓头寸口径'},
    {label: '平均盈利倍数', value: `${formatNumber(averages.closed_profit_multiple, 2)}x`, caption: '入选钱包平均值'},
    {label: '日均交易', value: formatNumber(averages.trades_per_active_day, 1), caption: '活跃交易频率'},
  ];

  const loadResult = async (runId: string) => {
    setLoading(true);
    try {
      const [nextSummary, walletPayload] = await Promise.all([getSummary(runId), getWallets(runId, {limit: 80})]);
      setSummary(nextSummary);
      setWallets(walletPayload.items || []);
      setError(undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    listRuns()
      .then((items) => {
        if (cancelled) return;
        setRuns(items);
        const activeRun = activeRunId ? items.find((item) => item.run_id === activeRunId) : undefined;
        const nextRunId = resolveSelectedRunId(items, activeRunId);
        if (nextRunId && (!activeRunId || !activeRun || !hasReadableRunResult(activeRun))) {
          onRunSelected(nextRunId);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRunId, onRunSelected]);

  useEffect(() => {
    if (selectedRunId) loadResult(selectedRunId);
  }, [selectedRunId]);

  const exportReport = async () => {
    if (!selectedRunId) return;
    setExporting('report');
    setError(undefined);
    try {
      const text = await getReport(selectedRunId);
      downloadBlob(`${safeFileName(selectedRunId)}-report.txt`, text, 'text/plain;charset=utf-8');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(undefined);
    }
  };

  const exportTaggedWallets = () => {
    if (!selectedRunId || !wallets.length) return;
    setExporting('wallets');
    try {
      downloadBlob(`${safeFileName(selectedRunId)}-tagged-wallets.csv`, walletsToCsv(wallets), 'text/csv;charset=utf-8');
    } finally {
      setExporting(undefined);
    }
  };

  if (!selectedRunId) {
    return (
      <div className="mx-auto mt-12 max-w-xl rounded-md border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-semibold text-slate-900">还没有分析结果</h1>
        <p className="mt-2 text-sm text-slate-500">新建并完成一次分析后，这里会展示指标、图表和入选钱包。</p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">分析结果</h1>
          <p className="mt-1 text-sm text-slate-500">用图表和钱包表现看本次筛选结果；后台诊断文件不在这里展示。</p>
          <p className="mt-2 text-sm font-medium text-slate-700">{runDisplayName(selectedRun)}</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <RunPicker runs={runs} selectedRunId={selectedRunId} onRunSelected={onRunSelected} />
          <button
            onClick={() => selectedRunId && loadResult(selectedRunId)}
            className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
          >
            <RefreshCcw className="mr-2 h-4 w-4 text-slate-500" />
            刷新
          </button>
        </div>
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <FinderAiRunSummaryStrip
        summary={summary?.finder_ai_summary}
        previewItems={finderAiPreviewItems}
        onPreviewWalletOpen={onWalletSelected}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <section key={stat.label} className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
            <div className="text-sm font-medium text-slate-500">{stat.label}</div>
            <div className="mt-2 text-3xl font-bold tracking-tight text-slate-900">{loading ? '-' : stat.value}</div>
            <div className="mt-2 text-sm text-slate-500">{stat.caption}</div>
          </section>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.75fr)]">
        <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-5 flex flex-col gap-1">
            <h2 className="text-base font-semibold text-slate-900">标签分布</h2>
            <p className="text-sm text-slate-500">入选钱包被打上的策略标签，数量越高代表该类型越集中。</p>
          </div>
          <div className="h-72">
            {labelData.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={labelData} margin={{top: 4, right: 8, left: -20, bottom: 0}}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="name" tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} dy={10} />
                  <YAxis allowDecimals={false} tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{fill: '#f1f5f9'}} contentStyle={{borderRadius: '8px', border: '1px solid #e2e8f0'}} />
                  <Bar dataKey="value" fill="#2E5CFF" radius={[4, 4, 0, 0]} barSize={34} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <EmptyPanel text={loading ? '正在加载标签分布...' : '本次分析暂无标签数据。'} />
            )}
          </div>
        </section>

        <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-5">
            <h2 className="text-base font-semibold text-slate-900">筛选漏斗</h2>
            <p className="mt-1 text-sm text-slate-500">从排行榜记录到最终入选钱包的收敛过程。</p>
          </div>
          <div className="h-56">
            {funnelData.some((item) => item.value > 0) ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={funnelData} layout="vertical" margin={{top: 10, right: 16, left: 10, bottom: 10}}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                  <XAxis type="number" hide domain={[0, 'dataMax']} />
                  <YAxis type="category" dataKey="name" width={108} tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{fill: '#f8fafc'}} contentStyle={{borderRadius: '8px', border: '1px solid #e2e8f0'}} />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={28}>
                    {funnelData.map((item, index) => (
                      <BarCell key={item.name} fill={chartColors[index % chartColors.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <EmptyPanel text={loading ? '正在加载筛选数据...' : '本次分析暂无筛选统计。'} />
            )}
          </div>
          <div className="mt-2 space-y-3">
            {funnelData.map((item, index) => (
              <div key={item.name} className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2 text-slate-600">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{backgroundColor: chartColors[index % chartColors.length]}} />
                  {item.name}
                </div>
                <span className="font-semibold text-slate-900">{formatNumber(item.value)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="rounded-md border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-slate-100 px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">入选钱包表现</h2>
            <p className="mt-1 text-sm text-slate-500">按盈亏排序展示本次打标后的核心钱包。</p>
          </div>
          {onNavigate && (
            <button
              onClick={() => onNavigate('wallet_list')}
              className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
            >
              <Wallet className="mr-2 h-4 w-4" />
              查看完整钱包列表
            </button>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                <Header>钱包</Header>
                <Header align="right">盈亏</Header>
                <Header align="right">胜率</Header>
                <Header align="right">交易数</Header>
                <Header>标签</Header>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-10 text-center text-sm text-slate-500">
                    正在加载钱包表现...
                  </td>
                </tr>
              ) : topWallets.length ? (
                topWallets.map((wallet) => (
                  <tr key={wallet.wallet} className="hover:bg-slate-50">
                    <td className="px-6 py-4 text-sm text-slate-900">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-slate-900">
                          {preferredReportWalletName(wallet) || shortAddress(wallet.wallet)}
                        </div>
                        {preferredReportWalletName(wallet) && (
                          <div className="truncate font-mono text-xs text-slate-500">{shortAddress(wallet.wallet)}</div>
                        )}
                        {preferredReportAiBrief(wallet) && (
                          <div className="mt-1 max-w-[340px] truncate text-xs text-slate-500">{preferredReportAiBrief(wallet)}</div>
                        )}
                      </div>
                    </td>
                    <TableCell align="right">{formatCurrency(wallet.pnl)}</TableCell>
                    <TableCell align="right">{formatPercent(wallet.closed_position_win_rate)}</TableCell>
                    <TableCell align="right">{formatNumber(wallet.trade_count)}</TableCell>
                    <td className="min-w-[260px] px-6 py-4">
                      <div className="flex flex-wrap gap-1.5">
                        {(wallet.labels || []).slice(0, 4).map((label) => (
                          <span key={label} className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                            {label}
                          </span>
                        ))}
                        {(wallet.labels || []).length > 4 && <span className="text-xs text-slate-400">+{(wallet.labels || []).length - 4}</span>}
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="px-6 py-10 text-center text-sm text-slate-500">
                    本次分析暂无入选钱包。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">导出结果</h2>
            <p className="mt-1 text-sm text-slate-500">只保留用户真正会用到的两个文件：文字报告和打标钱包数据。</p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              onClick={exportReport}
              disabled={exporting === 'report'}
              className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <FileText className="mr-2 h-4 w-4" />
              {exporting === 'report' ? '导出中...' : '导出文字报告'}
            </button>
            <button
              onClick={exportTaggedWallets}
              disabled={!wallets.length || exporting === 'wallets'}
              className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Table2 className="mr-2 h-4 w-4" />
              {exporting === 'wallets' ? '导出中...' : '导出打标钱包数据'}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function Header({children, align = 'left'}: {children: string; align?: 'left' | 'right'}) {
  return (
    <th className={`px-6 py-3 text-xs font-medium uppercase tracking-wider text-slate-500 ${align === 'right' ? 'text-right' : 'text-left'}`}>
      {children}
    </th>
  );
}

function TableCell({
  children,
  align = 'left',
  mono = false,
}: {
  children: ReactNode;
  align?: 'left' | 'right';
  mono?: boolean;
}) {
  return (
    <td className={`whitespace-nowrap px-6 py-4 text-sm text-slate-600 ${align === 'right' ? 'text-right' : 'text-left'} ${mono ? 'font-mono font-medium text-slate-900' : ''}`}>
      {children}
    </td>
  );
}

function EmptyPanel({text}: {text: string}) {
  return <div className="flex h-full items-center justify-center rounded-md border border-dashed border-slate-200 text-sm text-slate-500">{text}</div>;
}

function preferredReportWalletName(wallet: WalletRow): string | undefined {
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

function preferredReportAiBrief(wallet: WalletRow): string | undefined {
  for (const value of [wallet.ai_brief_short, wallet.ai_strategy_focus]) {
    const text = String(value || '').trim();
    if (text) return text;
  }
  return undefined;
}

function downloadBlob(filename: string, content: string, type: string) {
  const blob = new Blob([content], {type});
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function safeFileName(value: string): string {
  return value.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'analysis-result';
}

function walletsToCsv(wallets: WalletRow[]): string {
  const headers = ['钱包地址', '用户名', '排名', '盈亏', '成交量', '交易数', '天气占比', '胜率', '标签'];
  const rows = wallets.map((wallet) => [
    wallet.wallet,
    wallet.user_name,
    wallet.rank,
    wallet.pnl,
    wallet.volume,
    wallet.trade_count,
    wallet.weather_notional_ratio,
    wallet.closed_position_win_rate,
    (wallet.labels || []).join(' / '),
  ]);
  return `\ufeff${[headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\n')}`;
}

function csvCell(value: unknown): string {
  const text = value == null ? '' : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}
