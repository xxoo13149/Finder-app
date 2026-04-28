import {ChevronDown, History} from 'lucide-react';
import type {RunRecord} from '../lib/api';
import {isDiagnosticRun, runDetailLabel, runDisplayName} from '../lib/api';
import {cn} from '../lib/utils';

export function RunPicker({
  runs,
  selectedRunId,
  onRunSelected,
  className,
}: {
  runs: RunRecord[];
  selectedRunId?: string;
  onRunSelected: (runId: string) => void;
  className?: string;
}) {
  const selectedRun = runs.find((run) => run.run_id === selectedRunId);
  const visibleRuns = runs.filter((run) => !isDiagnosticRun(run.run_id));
  const diagnosticRuns = runs.filter((run) => isDiagnosticRun(run.run_id));
  const groupedRuns = visibleRuns.length ? visibleRuns : runs;

  return (
    <label className={cn('block min-w-0', className)}>
      <span className="mb-1.5 flex items-center text-xs font-medium text-slate-500">
        <History className="mr-1.5 h-3.5 w-3.5" />
        历史分析记录
      </span>
      <div className="relative">
        <select
          value={selectedRunId || ''}
          onChange={(event) => onRunSelected(event.target.value)}
          className="h-10 w-full min-w-[260px] max-w-[420px] appearance-none rounded-md border border-slate-300 bg-white py-2 pl-3 pr-9 text-sm text-slate-800 shadow-sm outline-none transition focus:border-[#2E5CFF] focus:ring-2 focus:ring-blue-100"
        >
          {groupedRuns.map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {runDisplayName(run)}
            </option>
          ))}
          {visibleRuns.length > 0 && diagnosticRuns.length > 0 && (
            <optgroup label="测试/诊断记录">
              {diagnosticRuns.slice(0, 8).map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {runDisplayName(run)}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      </div>
      {selectedRun && <span className="mt-1 block truncate text-xs text-slate-500">{runDetailLabel(selectedRun)}</span>}
    </label>
  );
}
