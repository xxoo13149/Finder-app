import {CheckCircle2, FileText, List, PlayCircle, RefreshCcw, XCircle} from 'lucide-react';
import {useCallback, useEffect, useMemo, useState} from 'react';
import {RunPicker} from '../components/RunPicker';
import {
  ActiveSlowStep,
  AnalysisSummary,
  RunDiagnostics,
  RunRecord,
  formatDateTime,
  getArtifact,
  getRun,
  getSummary,
  hasReadableRunResult,
  listRuns,
  resolveSelectedRunId,
  resumeRun,
  runDisplayName,
  statusLabel as formatStatusLabel,
  statusTone,
} from '../lib/api';

type PipelineError = {wallet?: string; error?: string; type?: string; message?: string};
type ActiveDiagnostics = NonNullable<RunRecord['active_diagnostics']>;
type ActiveStepDiagnostics = NonNullable<ActiveDiagnostics['hydration']>;
type ActiveProgressStage = NonNullable<ActiveDiagnostics['current_stage']>;

export function TaskRunning({
  activeRunId,
  autoRefresh,
  onRunSelected,
  onNavigate,
}: {
  activeRunId?: string;
  autoRefresh: boolean;
  onRunSelected: (runId: string) => void;
  onNavigate: (page: string) => void;
}) {
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [run, setRun] = useState<RunRecord>();
  const [summary, setSummary] = useState<AnalysisSummary>();
  const [errors, setErrors] = useState<PipelineError[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [resuming, setResuming] = useState(false);

  const selectedRunId = resolveSelectedRunId(runs, activeRunId);
  const selectedRun = (run?.run_id === selectedRunId ? run : undefined) || runs.find((item) => item.run_id === selectedRunId);

  const reloadRuns = useCallback(async () => {
    const items = await listRuns();
    setRuns(items);
    return items;
  }, []);

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
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRunId, onRunSelected]);

  const loadRun = useCallback(async () => {
    if (!selectedRunId) return;
    try {
      const nextRun = await getRun(selectedRunId);
      setRun(nextRun);
      setSummary(nextRun.summary);
      setError(undefined);

      getSummary(selectedRunId)
        .then(setSummary)
        .catch(() => undefined);

      try {
        const text = await getArtifact(selectedRunId, 'errors.json');
        setErrors(JSON.parse(text));
      } catch {
        setErrors((nextRun.result?.errors as PipelineError[]) || []);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [selectedRunId]);

  useEffect(() => {
    setRun(undefined);
    setSummary(undefined);
    setErrors([]);
    setError(undefined);
    if (selectedRunId) void loadRun();
  }, [loadRun, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId || !autoRefresh) return;
    const interval = window.setInterval(() => {
      if (run?.status === 'running' || run?.status === 'queued') void loadRun();
    }, 2500);
    return () => {
      window.clearInterval(interval);
    };
  }, [autoRefresh, loadRun, run?.status, selectedRunId]);

  const activeSummary = summary || run?.summary;
  const diagnostics = activeSummary?.diagnostics;
  const weatherEvents = diagnostics?.weather_events;
  const hydration = diagnostics?.hydration;
  const coreLabels = diagnostics?.core_labels;
  const finderAiSummary = diagnostics?.finder_ai || activeSummary?.finder_ai_summary;
  const activeDiagnostics = run?.active_diagnostics;
  const activeWallets = activeDiagnostics?.wallets || {};
  const activeRelaySource = activeDiagnostics?.relay_source || {};
  const activeStage = activeDiagnostics?.current_stage;
  const activeHydration = activeDiagnostics?.hydration;
  const activeDeepSeek = activeDiagnostics?.deepseek;
  const recentSlowSteps = activeDiagnostics?.recent_slow_steps || [];
  const activeCandidateProgress = activeCandidateProgressCount(activeWallets);

  const progress = Math.max(0, Math.min(100, run?.percent ?? 0));
  const logs = run?.progress || [];
  const statusLabel = run?.status || 'artifact';
  const isComplete = run?.status === 'succeeded';
  const isFailed = run?.status === 'failed';
  const canResume = Boolean(run?.resumable || selectedRun?.resumable);
  const displayPhase = activeStage?.label || run?.phase || '等待 API 状态';
  const stageHint = formatStageHint(activeStage, recentSlowSteps[0]);

  const handleResume = async () => {
    if (!selectedRunId || resuming) return;
    setResuming(true);
    setError(undefined);
    try {
      const nextRun = await resumeRun(selectedRunId);
      setRun(nextRun);
      setSummary(nextRun.summary);
      onRunSelected(nextRun.run_id);
      await reloadRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setResuming(false);
    }
  };

  const refreshSelectedRun = async () => {
    if (!selectedRunId) return;
    const [nextRun, nextSummary] = await Promise.all([
      getRun(selectedRunId),
      getSummary(selectedRunId).catch(() => undefined),
      reloadRuns(),
    ]);
    setRun(nextRun);
    setSummary(nextSummary || nextRun.summary);
  };

  const stats = useMemo(
    () => [
      {label: '进度', value: `${progress}%`, tone: 'text-[#2E5CFF]'},
      {label: '入选钱包', value: String(run?.selected_wallet_count ?? run?.result?.selected_wallet_count ?? '-'), tone: 'text-slate-900'},
      {label: '详情文件', value: String(run?.wallet_detail_count ?? '-'), tone: 'text-slate-900'},
      {
        label: '天气事件',
        value: formatIndexedMax(weatherEvents?.indexed ?? activeSummary?.weather_events_indexed, weatherEvents?.max),
        tone: weatherEvents?.cap_hit ? 'text-amber-600' : 'text-slate-900',
      },
      {
        label: '核心命中',
        value: formatIndexedMax(coreLabels?.wallets ?? activeSummary?.wallets_core_labeled, run?.selected_wallet_count ?? activeSummary?.wallets_selected),
        tone: 'text-emerald-700',
      },
      {
        label: 'DeepSeek',
        value: formatDeepSeekStat(activeDeepSeek, finderAiSummary),
        tone: 'text-indigo-700',
      },
      {label: '错误数', value: String(errors.length || run?.result?.errors?.length || 0), tone: 'text-red-600'},
    ],
    [activeDeepSeek, activeSummary, coreLabels, errors.length, finderAiSummary, progress, run, weatherEvents],
  );

  if (loading && !selectedRunId) {
    return <Message title="正在读取运行记录" body="正在检查已有分析结果和当前任务。" />;
  }

  if (!selectedRunId) {
    return (
      <Message
        title="还没有选择任务"
        body="先新建一次分析，这里会显示进度、日志、卡点和错误信息。"
        actionLabel="新建分析"
        onAction={() => onNavigate('new_task')}
      />
    );
  }

  return (
    <div className="mx-auto mt-6 flex w-full max-w-6xl flex-col overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-4 border-b border-slate-100 px-6 py-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900">运行状态</h1>
          <p className="mt-1 text-sm text-slate-500">{runDisplayName(selectedRun)}</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <RunPicker runs={runs} selectedRunId={selectedRunId} onRunSelected={onRunSelected} />
          <button
            onClick={refreshSelectedRun}
            className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <RefreshCcw className="mr-2 h-4 w-4" />
            刷新
          </button>
        </div>
      </div>

      <div className="space-y-8 p-6">
        {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

        <div>
          <div className="mb-2 flex items-end justify-between">
            <div>
              <div className="mb-1 text-sm font-medium text-slate-500">当前阶段</div>
              <div className="text-lg font-bold text-slate-900">{displayPhase}</div>
              {stageHint && <div className="mt-1 text-sm text-slate-500">{stageHint}</div>}
            </div>
            <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusTone(statusLabel)}`}>
              {isComplete ? <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> : null}
              {isFailed ? <XCircle className="mr-1 h-3.5 w-3.5" /> : null}
              {formatStatusLabel(statusLabel)}
            </span>
          </div>
          <div className="relative mb-6 h-3 w-full overflow-hidden rounded-full bg-slate-100">
            <div className="h-3 rounded-full bg-[#2E5CFF] transition-all duration-500" style={{width: `${progress}%`}} />
          </div>

          <div className="grid grid-cols-1 gap-4 border-t border-slate-100 pt-6 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            {stats.map((stat) => (
              <div key={stat.label}>
                <div className="mb-1 text-sm text-slate-500">{stat.label}</div>
                <div className={`text-xl font-semibold ${stat.tone}`}>{stat.value}</div>
              </div>
            ))}
          </div>

          <div className="mt-4 grid grid-cols-1 gap-2 text-sm text-slate-500 md:grid-cols-3">
            <span>创建时间：{formatDateTime(run?.created_at)}</span>
            <span>开始时间：{formatDateTime(run?.started_at)}</span>
            <span>完成时间：{formatDateTime(run?.finished_at)}</span>
          </div>

          <div className="mt-5 grid grid-cols-1 gap-3 text-sm lg:grid-cols-3">
            <DiagnosticPanel
              title="轻量筛选"
              rows={[
                ['来源地址池', formatRelaySource(activeRelaySource)],
                ['接力筛选', formatRelayFilters(activeRelaySource)],
                ['候选预筛', formatActiveCount(activeWallets.prefilter_kept, activeWallets.prefilter_total)],
                ['当前批次', formatActiveBatch(activeWallets)],
                ['天气索引', formatIndexedMax(weatherEvents?.indexed ?? activeSummary?.weather_events_indexed, weatherEvents?.max)],
                ['抓取方式', [weatherEvents?.fetch_mode || '-', weatherEvents?.reused_existing ? '复用旧索引' : '新抓取'].join(' · ')],
                ['停止原因', weatherEvents?.stop_reason || '-'],
                ['抓取页数', formatWeatherPageCount(weatherEvents)],
                ['末页游标', formatWeatherCursor(weatherEvents?.terminal_next_cursor_present)],
                ['覆盖提示', weatherEvents?.coverage_note || weatherEvents?.shortfall_hint || '-'],
                ['标签门槛', '命中任意系统核心标签才进重链路'],
              ]}
            />
            <DiagnosticPanel
              title="重链路"
              rows={[
                ['候选进度', formatActiveCount(activeCandidateProgress ?? run?.selected_wallet_count, activeWallets.current_batch_total)],
                ['详情文件', String(run?.wallet_detail_count ?? activeWallets.detail_files ?? '-')],
                ['失败钱包', String(activeWallets.failed_from_log ?? errors.length ?? 0)],
                ['Full hydration', formatHydrationProgress(activeHydration, hydration)],
                ['Hydration 最近', formatStepLast(activeHydration)],
                ['历史范围', topCountsLabel(hydration?.history_scopes)],
                ['跳过原因', topCountsLabel(hydration?.reason_counts)],
              ]}
            />
            <DiagnosticPanel
              title="DeepSeek"
              rows={[
                ['生成结果', formatDeepSeekProgress(activeDeepSeek, finderAiSummary)],
                ['DeepSeek 最近', formatStepLast(activeDeepSeek)],
                ['状态分布', topCountsLabel(activeDeepSeek?.status_counts || finderAiSummary?.status_counts, 3)],
                ['需复核', `${finderAiSummary?.needs_review || 0}`],
                ['Gate reason', topCountsLabel(finderAiSummary?.reason_counts)],
              ]}
            />
          </div>

          {recentSlowSteps.length > 0 && <RecentSlowSteps steps={recentSlowSteps} />}

          {isComplete && (
            <div className="mt-6 flex flex-col gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-semibold text-emerald-900">分析已完成</div>
                <div className="mt-1 text-sm text-emerald-700">可以查看报告，或进入钱包列表继续筛选。</div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <button
                  onClick={() => onNavigate('reports')}
                  className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
                >
                  <FileText className="mr-2 h-4 w-4" />
                  查看分析结果
                </button>
                <button
                  onClick={() => onNavigate('wallet_list')}
                  className="inline-flex h-10 items-center justify-center rounded-md border border-emerald-300 bg-white px-4 text-sm font-medium text-emerald-800 shadow-sm hover:bg-emerald-50"
                >
                  <List className="mr-2 h-4 w-4" />
                  查看钱包列表
                </button>
              </div>
            </div>
          )}

          {canResume && (
            <div className="mt-6 flex flex-col gap-3 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-semibold text-blue-950">可以继续这批分析</div>
                <div className="mt-1 text-sm text-blue-800">已写入的钱包详情会保留，继续时会跳过已经完成的钱包。</div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <button
                  onClick={handleResume}
                  disabled={resuming}
                  className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <PlayCircle className="mr-2 h-4 w-4" />
                  {resuming ? '接力中...' : '继续分析'}
                </button>
                <button
                  onClick={() => onNavigate('wallet_list')}
                  className="inline-flex h-10 items-center justify-center rounded-md border border-blue-300 bg-white px-4 text-sm font-medium text-blue-800 shadow-sm hover:bg-blue-50"
                >
                  <List className="mr-2 h-4 w-4" />
                  查看已跑钱包
                </button>
              </div>
            </div>
          )}
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-base font-semibold text-slate-900">进度日志</h3>
            <span className="text-xs text-slate-500">{autoRefresh ? '自动刷新已开启' : '自动刷新已关闭'}</span>
          </div>
          <div className="h-52 overflow-y-auto rounded-md bg-[#1e1e1e] p-4 font-mono text-xs leading-relaxed">
            {logs.length ? (
              logs.map((item, index) => (
                <div key={`${item.time}-${index}`} className="text-slate-400">
                  {item.time || '-'} <span className="text-green-400">[信息]</span> {item.message}
                </div>
              ))
            ) : (
              <div className="text-slate-400">本次运行暂无进度日志。</div>
            )}
          </div>
        </div>

        <div>
          <h3 className="mb-3 text-base font-semibold text-slate-900">错误记录</h3>
          <div className="overflow-hidden rounded-md border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-500">钱包</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-500">消息</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {errors.map((item, index) => (
                  <tr key={index}>
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-sm text-slate-600">{item.wallet || '-'}</td>
                    <td className="px-4 py-3 text-sm text-slate-600">{item.error || item.message || item.type || '-'}</td>
                  </tr>
                ))}
                {!errors.length && (
                  <tr>
                    <td colSpan={2} className="px-4 py-8 text-center text-sm text-slate-500">
                      暂无错误记录。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function DiagnosticPanel({title, rows}: {title: string; rows: Array<[string, string]>}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="mb-2 font-semibold text-slate-900">{title}</div>
      <div className="space-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-3">
            <span className="w-24 shrink-0 text-slate-500">{label}</span>
            <span className="min-w-0 break-words text-slate-800">{value || '-'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RecentSlowSteps({steps}: {steps: ActiveSlowStep[]}) {
  const visible = steps.slice(0, 3);
  return (
    <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm">
      <div className="mb-2 font-semibold text-amber-950">最近较慢步骤</div>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
        {visible.map((step, index) => (
          <div key={`${step.kind}-${step.wallet}-${step.started_at}-${index}`} className="min-w-0">
            <div className="font-medium text-amber-950">
              {step.label || step.kind || '步骤'} · {formatDuration(step.duration_seconds)}
            </div>
            <div className="mt-0.5 break-words text-amber-800">
              {formatStepStatus(step.status)} · {shortHash(step.wallet)}
              {step.detail ? ` · ${step.detail}` : ''}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatIndexedMax(value?: number | null, max?: number | null): string {
  const left = value == null ? '-' : String(value);
  return max == null || max === 0 ? left : `${left}/${max}`;
}

function formatDeepSeekStat(active?: ActiveStepDiagnostics, summary?: AnalysisSummary['finder_ai_summary']): string {
  const summaryDone = (summary?.generated || 0) + (summary?.cached || 0);
  const activeDone = (active?.generated || 0) + (active?.cached || 0) + (active?.fallback || 0) + (active?.completed || 0);
  const done = summaryDone || activeDone;
  const total = summary?.eligible || active?.started;
  return formatIndexedMax(done, total);
}

function formatHydrationProgress(active?: ActiveStepDiagnostics, summary?: RunDiagnostics['hydration']): string {
  const useActive = hasActiveStepSignals(active);
  const started = useActive ? finiteNumber(active?.started) : undefined;
  const inProgress = useActive ? finiteNumber(active?.in_progress) : undefined;
  const completed = (useActive ? finiteNumber(active?.completed) : undefined) ?? finiteNumber(summary?.completed) ?? 0;
  const skipped = (useActive ? finiteNumber(active?.skipped) : undefined) ?? finiteNumber(summary?.skipped) ?? 0;
  const failed = (useActive ? finiteNumber(active?.failed) : undefined) ?? finiteNumber(summary?.failed) ?? 0;
  const parts = started != null ? [`开始 ${started}`] : [];
  parts.push(`完成 ${completed}`, `跳过 ${skipped}`, `失败 ${failed}`);
  if (inProgress) parts.push(`进行中 ${inProgress}`);
  return parts.join(' / ');
}

function formatDeepSeekProgress(active?: ActiveStepDiagnostics, summary?: AnalysisSummary['finder_ai_summary']): string {
  const useActive = hasActiveStepSignals(active);
  const started = useActive ? finiteNumber(active?.started) : undefined;
  const generated = (useActive ? finiteNumber(active?.generated) : undefined) ?? finiteNumber(summary?.generated) ?? 0;
  const cached = (useActive ? finiteNumber(active?.cached) : undefined) ?? finiteNumber(summary?.cached) ?? 0;
  const fallback = (useActive ? finiteNumber(active?.fallback) : undefined) ?? finiteNumber(summary?.fallback) ?? 0;
  const failed = (useActive ? finiteNumber(active?.failed) : undefined) ?? finiteNumber(summary?.failed) ?? 0;
  const parts = started != null ? [`开始 ${started}`] : [];
  parts.push(`生成 ${generated}`, `缓存 ${cached}`);
  if (fallback) parts.push(`兜底 ${fallback}`);
  if (failed) parts.push(`失败 ${failed}`);
  return parts.join(' / ');
}

function hasActiveStepSignals(step?: ActiveStepDiagnostics): boolean {
  if (!step) return false;
  return [
    step.started,
    step.finished,
    step.completed,
    step.generated,
    step.cached,
    step.fallback,
    step.needs_review,
    step.skipped,
    step.failed,
    step.in_progress,
  ].some((value) => typeof value === 'number' && Number.isFinite(value) && value > 0) || Boolean(step.last_status || step.last_wallet);
}

function formatStepLast(step?: ActiveStepDiagnostics): string {
  if (!step?.last_status && !step?.last_wallet) return '-';
  const parts = [formatStepStatus(step.last_status)];
  if (step.last_wallet) parts.push(shortHash(step.last_wallet));
  if (step.last_detail) parts.push(step.last_detail);
  return parts.filter(Boolean).join(' · ');
}

function formatStageHint(stage?: ActiveProgressStage, slowStep?: ActiveSlowStep): string {
  const parts: string[] = [];
  if (stage?.wallet) parts.push(shortHash(stage.wallet));
  if (stage?.detail) parts.push(stage.detail);
  if (slowStep?.duration_seconds != null) {
    parts.push(`最慢 ${slowStep.label || slowStep.kind || '步骤'} ${formatDuration(slowStep.duration_seconds)}`);
  }
  return parts.join(' · ');
}

function formatDuration(seconds?: number): string {
  if (seconds == null || !Number.isFinite(seconds)) return '-';
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const minuteRest = minutes % 60;
  return minuteRest ? `${hours}h ${minuteRest}m` : `${hours}h`;
}

function formatStepStatus(status?: string): string {
  const labels: Record<string, string> = {
    started: '已开始',
    in_progress: '进行中',
    completed: '已完成',
    generated: '已生成',
    cached: '命中缓存',
    fallback: '本地兜底',
    needs_review: '需复核',
    skipped: '已跳过',
    failed: '失败',
  };
  return labels[status || ''] || status || '-';
}

function shortHash(value?: string): string {
  if (!value) return '-';
  return value.length <= 14 ? value : `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function formatActiveCount(value?: number, total?: number): string {
  const hasValue = typeof value === 'number' && Number.isFinite(value) && value > 0;
  const hasTotal = typeof total === 'number' && Number.isFinite(total) && total > 0;
  if (!hasValue && !hasTotal) return '-';
  if (!hasTotal) return String(value ?? 0);
  return `${value ?? 0}/${total}`;
}

function formatActiveBatch(wallets?: Record<string, number>): string {
  const start = wallets?.current_batch_start || 0;
  const end = wallets?.current_batch_end || 0;
  const total = wallets?.current_batch_total || 0;
  if (!start || !end || !total) return '-';
  return `${start}-${end}/${total}`;
}

function activeCandidateProgressCount(wallets?: Record<string, number>): number | undefined {
  const currentBatchStart = finiteNumber(wallets?.current_batch_start);
  const completedFromLog = finiteNumber(wallets?.completed_from_log);
  if (currentBatchStart && currentBatchStart > 1) {
    return Math.max(currentBatchStart - 1, completedFromLog ?? 0);
  }
  return completedFromLog;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function formatWeatherPageCount(weatherEvents?: RunDiagnostics['weather_events']): string {
  if (!weatherEvents || weatherEvents.page_count == null) return '-';
  const pageCount = weatherEvents.page_count;
  const lastPageSize = weatherEvents.last_page_size;
  return lastPageSize == null ? `${pageCount} 页` : `${pageCount} 页 · 末页 ${lastPageSize}`;
}

function formatWeatherCursor(value?: boolean | null): string {
  if (value == null) return '-';
  return value ? '仍有下一页' : '无下一页';
}

function formatRelaySource(source?: Record<string, unknown>): string {
  if (!source || !Object.keys(source).length) return '-';
  const sourceTotal = Number(source.source_total || source.wallet_count || 0);
  const matched = Number(source.matched_count || source.wallet_count || 0);
  const pool = relaySourcePoolLabel(source.source_pool);
  if (sourceTotal > 0 && matched > 0 && sourceTotal !== matched) {
    return `${matched}/${sourceTotal} · ${pool}`;
  }
  return `${matched || sourceTotal || '-'} · ${pool}`;
}

function formatRelayFilters(source?: Record<string, unknown>): string {
  if (!source || !Object.keys(source).length) return '-';
  const core = relayCoreFilterLabel(source.core_label_filter);
  const deepseek = relayDeepSeekFilterLabel(source.deepseek_filter);
  return `${core} · ${deepseek}`;
}

function relaySourcePoolLabel(value: unknown): string {
  switch (String(value || '')) {
    case 'smart_wallet_import_rows':
      return '原始地址库';
    case 'relay_import_rows':
      return '接力输入';
    case 'selected_wallets':
      return '已选快照';
    case 'leaderboard':
      return '排行榜';
    default:
      return '当前链路';
  }
}

function relayCoreFilterLabel(value: unknown): string {
  switch (String(value || 'all')) {
    case 'core':
      return '核心标签';
    case 'non_core':
      return '非核心';
    default:
      return '全部标签';
  }
}

function relayDeepSeekFilterLabel(value: unknown): string {
  switch (String(value || 'all')) {
    case 'completed':
      return 'DeepSeek 已完成';
    case 'incomplete':
      return 'DeepSeek 未完成';
    default:
      return '全部 DeepSeek';
  }
}

function topCountsLabel(counts?: Record<string, number>, limit = 2): string {
  if (!counts) return '-';
  const entries = Object.entries(counts)
    .filter(([, value]) => Number(value) > 0)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .slice(0, limit);
  if (!entries.length) return '-';
  return entries.map(([key, value]) => `${key}: ${value}`).join(' · ');
}

function Message({
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
