import {CheckCircle2, FileText, List, RefreshCcw, XCircle} from 'lucide-react';
import {useEffect, useMemo, useState} from 'react';
import {RunPicker} from '../components/RunPicker';
import {
  RunRecord,
  formatDateTime,
  getArtifact,
  getRun,
  latestCompletedRun,
  listRuns,
  runDisplayName,
  statusLabel as formatStatusLabel,
  statusTone,
} from '../lib/api';

type PipelineError = {wallet?: string; error?: string; type?: string; message?: string};

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
  const [errors, setErrors] = useState<PipelineError[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const selectedRunId = activeRunId || latestCompletedRun(runs)?.run_id;
  const selectedRun = run || runs.find((item) => item.run_id === selectedRunId);

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

    const load = async () => {
      try {
        const nextRun = await getRun(selectedRunId);
        if (cancelled) return;
        setRun(nextRun);
        setError(undefined);
        try {
          const text = await getArtifact(selectedRunId, 'errors.json');
          if (!cancelled) setErrors(JSON.parse(text));
        } catch {
          if (!cancelled) setErrors((nextRun.result?.errors as PipelineError[]) || []);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };

    load();
    const interval = window.setInterval(() => {
      if (!autoRefresh) return;
      if (run?.status === 'running' || run?.status === 'queued' || !run) load();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [autoRefresh, selectedRunId, run?.status]);

  const progress = Math.max(0, Math.min(100, run?.percent ?? 0));
  const logs = run?.progress || [];
  const statusLabel = run?.status || 'artifact';
  const isComplete = run?.status === 'succeeded';
  const isFailed = run?.status === 'failed';

  const stats = useMemo(
    () => [
      {label: '进度', value: `${progress}%`, tone: 'text-[#2E5CFF]'},
      {label: '入选钱包', value: String(run?.result?.selected_wallet_count ?? '-'), tone: 'text-slate-900'},
      {label: '错误数', value: String(errors.length || run?.result?.errors?.length || 0), tone: 'text-red-600'},
    ],
    [errors.length, progress, run],
  );

  if (loading && !selectedRunId) {
    return <Message title="正在读取运行记录" body="正在检查已有分析结果和当前任务..." />;
  }

  if (!selectedRunId) {
    return (
      <Message
        title="还没有选择任务"
        body="先新建一次分析，这里会显示进度、日志和错误信息。"
        actionLabel="新建分析"
        onAction={() => onNavigate('new_task')}
      />
    );
  }

  return (
    <div className="mx-auto mt-6 flex w-full max-w-4xl flex-col overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-4 border-b border-slate-100 px-6 py-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900">运行状态</h1>
          <p className="mt-1 text-sm text-slate-500">{runDisplayName(selectedRun)}</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <RunPicker runs={runs} selectedRunId={selectedRunId} onRunSelected={onRunSelected} />
          <button
            onClick={() => selectedRunId && getRun(selectedRunId).then(setRun)}
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
              <div className="text-lg font-bold text-slate-900">{run?.phase || '等待 API 状态'}</div>
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

          <div className="grid grid-cols-1 gap-4 border-t border-slate-100 pt-6 md:grid-cols-3">
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

          {isComplete && (
            <div className="mt-6 flex flex-col gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-semibold text-emerald-900">分析已完成</div>
                <div className="mt-1 text-sm text-emerald-700">可以继续查看分析结果，或直接进入钱包列表。</div>
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
