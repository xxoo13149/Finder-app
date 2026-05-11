import {Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {ArrowUp, FileText, Play, RefreshCcw} from 'lucide-react';
import {useEffect, useMemo, useState} from 'react';
import {FinderAiRunSummaryStrip, hasFinderAiPreview, toFinderAiPreviewItem} from '../components/FinderAiRunSummaryStrip';
import {RunPicker} from '../components/RunPicker';
import {
  AnalysisSummary,
  RunRecord,
  WalletRankSummary,
  WalletRow,
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatPercent,
  getRun,
  getSummary,
  getWallets,
  hasReadableRunResult,
  listRuns,
  resolveSelectedRunId,
  runDisplayName,
  shortAddress,
  statusLabel,
  statusTone,
  walletDisplayPnl,
} from '../lib/api';

export function Dashboard({
  activeRunId,
  onRunSelected,
  onNavigate,
  onWalletSelected,
}: {
  activeRunId?: string;
  onRunSelected: (runId: string) => void;
  onNavigate: (page: string) => void;
  onWalletSelected?: (wallet: string) => void;
}) {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [run, setRun] = useState<RunRecord>();
  const [summary, setSummary] = useState<AnalysisSummary>();
  const [previewWallets, setPreviewWallets] = useState<WalletRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const selectedRunId = resolveSelectedRunId(runs, activeRunId);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
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
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRunId, onRunSelected]);

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    Promise.all([getRun(selectedRunId), getSummary(selectedRunId)])
      .then(([runRecord, runSummary]) => {
        if (cancelled) return;
        setRun(runRecord);
        setSummary(runSummary);
        setError(undefined);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      setPreviewWallets([]);
      return;
    }
    let cancelled = false;
    getWallets(selectedRunId, {limit: 24})
      .then((payload) => {
        if (!cancelled) setPreviewWallets(payload.items || []);
      })
      .catch(() => {
        if (!cancelled) setPreviewWallets([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const tagData = useMemo(
    () =>
      Object.entries(summary?.label_counts || {})
        .slice(0, 8)
        .map(([name, value]) => ({name, value})),
    [summary],
  );

  const topWallets = summary?.top_wallets_by_pnl || [];
  const averages = summary?.averages || {};
  const finderAiPreviewItems = useMemo(
    () =>
      [...previewWallets]
        .filter(hasFinderAiPreview)
        .sort((left, right) => Number(walletDisplayPnl(right) || 0) - Number(walletDisplayPnl(left) || 0))
        .slice(0, 10)
        .map(toFinderAiPreviewItem),
    [previewWallets],
  );
  const falconAverageWinRate = averages.falcon_win_rate;
  const stats = [
    {title: '排行榜记录', value: formatNumber(summary?.leaderboard_rows_fetched), trend: '最近运行'},
    {title: '已筛选钱包', value: formatNumber(summary?.wallets_screened), trend: '本次分析'},
    {title: '入选钱包', value: formatNumber(summary?.wallets_selected), trend: `${summary?.errors || 0} 个错误`},
    {
      title: '平均胜率',
      value: formatPercent(falconAverageWinRate),
      trend: falconAverageWinRate != null ? summary?.falcon_display?.win_rate_window_label || 'Falcon 标准口径' : 'Falcon 数据缺失',
    },
  ];

  if (loading && !selectedRunId) {
    return <PanelMessage title="正在加载工作区" body="正在查找已有分析结果..." />;
  }

  if (!selectedRunId) {
    return (
      <PanelMessage
        title="还没有分析记录"
        body="先新建一次分析，控制台会连接本地 Polymarket 天气分析工具并展示结果。"
        actionLabel="新建分析"
        onAction={() => onNavigate('new_task')}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">控制台总览</h1>
          <p className="text-sm text-slate-500">默认展示最近完成的分析结果；需要回看旧结果时再切换历史记录。</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <RunPicker runs={runs} selectedRunId={selectedRunId} onRunSelected={onRunSelected} />
          <button
            onClick={() => onNavigate('new_task')}
            className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
          >
            <Play className="mr-2 h-4 w-4" />
            新建分析
          </button>
        </div>
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.title} className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="mb-1 text-sm font-medium text-slate-500">{stat.title}</h3>
            <div className="mb-2 text-3xl font-bold text-slate-900">{stat.value}</div>
            <div className="flex items-center text-sm">
              <ArrowUp className="mr-1 h-4 w-4 text-emerald-500" />
              <span className="font-medium text-emerald-600">{stat.trend}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex flex-col gap-3 border-b border-slate-100 pb-6 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
            <div>
              <span className="text-sm text-slate-500">当前分析：</span>
              <span className="text-sm font-medium text-slate-900">{runDisplayName(run)}</span>
            </div>
            <div>
              <span className="text-sm text-slate-500">更新时间：</span>
              <span className="text-sm font-medium text-slate-900">{formatDateTime(run?.finished_at || run?.created_at)}</span>
            </div>
            <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusTone(run?.status)}`}>
              <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-current" />
              {statusLabel(run?.status)}
            </span>
          </div>
          <button
            onClick={() => onNavigate('task_running')}
            className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <RefreshCcw className="mr-2 h-4 w-4" />
            查看进度
          </button>
        </div>

        <FinderAiRunSummaryStrip
          summary={summary?.finder_ai_summary}
          previewItems={finderAiPreviewItems}
          onPreviewWalletOpen={onWalletSelected}
          compact
          embedded
          className="mb-6"
        />

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">标签分布</h3>
              <button onClick={() => onNavigate('reports')} className="inline-flex items-center text-sm font-medium text-[#2E5CFF] hover:text-blue-700">
                <FileText className="mr-1.5 h-4 w-4" />
                查看完整报告
              </button>
            </div>
            <div className="h-64">
              {tagData.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={tagData} margin={{top: 0, right: 0, left: -20, bottom: 0}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                    <XAxis dataKey="name" tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} dy={10} />
                    <YAxis tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} />
                    <Tooltip cursor={{fill: '#f1f5f9'}} contentStyle={{borderRadius: '8px', border: 'none'}} />
                    <Bar dataKey="value" fill="#2E5CFF" radius={[4, 4, 0, 0]} barSize={36} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center rounded-md border border-dashed border-slate-200 text-sm text-slate-500">
                  本次运行暂无标签数据。
                </div>
              )}
            </div>
          </div>

          <div>
            <h3 className="mb-4 text-base font-semibold text-slate-900">按盈亏排名的钱包</h3>
            <div className="overflow-hidden">
              <table className="min-w-full">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="px-2 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-500">排名</th>
                    <th className="px-2 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-500">钱包</th>
                    <th className="px-2 py-3 text-right text-xs font-medium uppercase tracking-wider text-slate-500">盈亏</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {topWallets.map((wallet) => (
                    <tr key={wallet.wallet} className="cursor-pointer hover:bg-slate-50/80" onClick={() => onWalletSelected?.(wallet.wallet)}>
                      <td className="whitespace-nowrap px-2 py-2.5 text-sm text-slate-600">{wallet.rank || '-'}</td>
                      <td className="px-2 py-2.5 text-sm text-slate-900">
                        <div className="min-w-0">
                          <div className="truncate font-medium text-slate-900">{preferredDashboardWalletName(wallet) || shortAddress(wallet.wallet)}</div>
                          <div className="truncate font-mono text-xs text-slate-500">{shortAddress(wallet.wallet)}</div>
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-2 py-2.5 text-right text-sm text-slate-600">{formatCurrency(walletDisplayPnl(wallet))}</td>
                    </tr>
                  ))}
                  {!topWallets.length && (
                    <tr>
                      <td className="px-2 py-8 text-center text-sm text-slate-500" colSpan={3}>
                        暂无钱包摘要。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
function preferredDashboardWalletName(wallet: WalletRankSummary): string | undefined {
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
function PanelMessage({
  title,
  body,
  actionLabel,
  onAction,
}: {
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="mx-auto mt-12 max-w-xl rounded-md border border-slate-200 bg-white p-8 text-center shadow-sm">
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="mt-2 text-sm text-slate-500">{body}</p>
      {actionLabel && (
        <button onClick={onAction} className="mt-6 rounded-md bg-[#2E5CFF] px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
          {actionLabel}
        </button>
      )}
    </div>
  );
}
