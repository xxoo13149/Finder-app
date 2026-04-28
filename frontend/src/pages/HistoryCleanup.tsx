import {
  AlertTriangle,
  Database,
  FileArchive,
  HardDrive,
  Loader2,
  RefreshCcw,
  ShieldAlert,
  Trash2,
} from 'lucide-react';
import {useEffect, useMemo, useState} from 'react';
import {
  CleanupAction,
  CleanupDeleteResult,
  CleanupInventory,
  CleanupItem,
  CleanupSection,
  cleanupItemTypeLabel,
  cleanupItemTypeTone,
  deleteCleanupItems,
  formatBytes,
  formatDateTime,
  getCleanupActionMeta,
  getCleanupInventory,
  isCleanupWalletRosterItemType,
  pruneCleanupItems,
  runCleanupAction,
  shortAddress,
  statusLabel,
  statusTone,
} from '../lib/api';
import {cn} from '../lib/utils';

type ItemOperation = 'delete' | 'prune';
type WalletRosterPresentation = {
  title: string;
  address?: string;
  userName?: string;
  firstSeenAt?: string;
  lastSeenAt?: string;
  runCount?: number;
};

export function HistoryCleanup({
  activeRunId,
  onRunsDeleted,
}: {
  activeRunId?: string;
  onRunsDeleted?: (runIds: string[]) => void;
}) {
  const [inventory, setInventory] = useState<CleanupInventory>();
  const [selectedItemIds, setSelectedItemIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string>();
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const itemIndex = useMemo(() => {
    const entries = (inventory?.sections || []).flatMap((section) => section.items.map((item) => [item.id, item] as const));
    return new Map(entries);
  }, [inventory]);

  const selectedItems = useMemo(
    () => selectedItemIds.map((id) => itemIndex.get(id)).filter((item): item is CleanupItem => Boolean(item)),
    [itemIndex, selectedItemIds],
  );

  const selectedDeleteItems = useMemo(() => selectedItems.filter(canDeleteItem), [selectedItems]);
  const selectedPruneItems = useMemo(() => selectedItems.filter(canPruneItem), [selectedItems]);

  const sectionStats = useMemo(() => {
    const sections = inventory?.sections || [];
    return {
      analysis: sections.find((section) => section.key === 'analysis_runs'),
      diagnostic: sections.find((section) => section.key === 'diagnostic_runs'),
      registry: sections.find((section) => section.key === 'wallet_registry'),
      temp: sections.find((section) => section.key === 'temp_outputs'),
      runtime: sections.find((section) => section.key === 'runtime_storage'),
      totalBytes: sections.reduce((sum, section) => sum + Number(section.size_bytes || 0), 0),
      totalItems: sections.reduce((sum, section) => sum + Number(section.count || 0), 0),
    };
  }, [inventory]);

  useEffect(() => {
    void refreshInventory({initial: true});
  }, []);

  useEffect(() => {
    setSelectedItemIds((current) => current.filter((id) => itemIndex.has(id)));
  }, [itemIndex]);

  async function refreshInventory({initial = false}: {initial?: boolean} = {}) {
    if (initial) {
      setLoading(true);
    } else {
      setBusyKey('refresh');
    }
    setError(undefined);
    try {
      const nextInventory = await getCleanupInventory();
      setInventory(nextInventory);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (initial) {
        setLoading(false);
      } else {
        setBusyKey(undefined);
      }
    }
  }

  function toggleItem(itemId: string) {
    setSelectedItemIds((current) => (
      current.includes(itemId) ? current.filter((value) => value !== itemId) : [...current, itemId]
    ));
  }

  function toggleSection(section: CleanupSection, mode: ItemOperation) {
    const eligibleIds = section.items.filter((item) => (mode === 'delete' ? canDeleteItem(item) : canPruneItem(item))).map((item) => item.id);
    if (!eligibleIds.length) {
      return;
    }
    setSelectedItemIds((current) => {
      const allSelected = eligibleIds.every((id) => current.includes(id));
      if (allSelected) {
        return current.filter((id) => !eligibleIds.includes(id));
      }
      return Array.from(new Set([...current, ...eligibleIds]));
    });
  }

  async function executeItemOperation(items: CleanupItem[], mode: ItemOperation, originKey: string) {
    if (!items.length) {
      return;
    }

    const totalBytes = items.reduce(
      (sum, item) => sum + Number(mode === 'prune' ? item.detail_prunable_bytes || 0 : item.size_bytes || 0),
      0,
    );
    const confirmed = confirmCleanup({
      label: mode === 'delete' ? '确认删除所选清理项？' : '确认清理所选分析明细？',
      description:
        mode === 'delete'
          ? '这会删除所选历史目录、临时产物或运行缓存。'
          : '这会保留摘要和报告，但删除钱包级明细与原始附件。',
      count: items.length,
      sizeBytes: totalBytes,
      confirmPhrase: mode === 'prune' ? '确认删除' : undefined,
    });
    if (!confirmed) {
      return;
    }

    setBusyKey(originKey);
    setError(undefined);
    setNotice(undefined);
    try {
      const result =
        mode === 'delete'
          ? await deleteCleanupItems(items.map((item) => item.id))
          : await pruneCleanupItems(items.map((item) => item.id));
      applyCleanupResult(
        result,
        mode === 'delete'
          ? `已删除 ${result.deleted_count} 项内容，释放 ${formatBytes(result.deleted_bytes)}。`
          : `已清理 ${result.deleted_count} 个分析记录的明细内容，释放 ${formatBytes(result.deleted_bytes)}。`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(undefined);
    }
  }

  async function executePresetAction(action: CleanupAction) {
    if (!action.target_count) {
      return;
    }

    const meta = getCleanupActionMeta(action.key);
    const confirmed = confirmCleanup({
      label: action.label,
      description: [action.description, action.warning, meta.scopeLabel, meta.preserveLabel].filter(Boolean).join('\n\n'),
      count: action.target_count,
      sizeBytes: action.size_bytes,
      confirmPhrase: meta.confirmPhrase,
    });
    if (!confirmed) {
      return;
    }

    setBusyKey(`action:${action.key}`);
    setError(undefined);
    setNotice(undefined);
    try {
      const result = await runCleanupAction(action.key);
      applyCleanupResult(result, `已完成“${action.label}”，释放 ${formatBytes(result.deleted_bytes)}。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(undefined);
    }
  }

  function applyCleanupResult(result: CleanupDeleteResult, nextNotice: string) {
    const nextItemIds = new Set(
      result.inventory.sections.flatMap((section) => section.items.map((item) => item.id)),
    );
    setInventory(result.inventory);
    setSelectedItemIds((current) =>
      current.filter((id) => nextItemIds.has(id) && !result.deleted_item_ids.includes(id)),
    );
    setNotice(nextNotice);
    if (result.deleted_run_ids.length) {
      onRunsDeleted?.(result.deleted_run_ids);
    }
  }

  if (loading && !inventory) {
    return (
      <div className="mx-auto mt-12 max-w-2xl rounded-md border border-slate-200 bg-white p-8 text-center shadow-sm">
        <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#2E5CFF]" />
        <h1 className="mt-4 text-xl font-semibold text-slate-900">正在加载历史清理清单</h1>
        <p className="mt-2 text-sm text-slate-500">正在扫描历史分析、运行缓存和临时产物。</p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6">
      <section className="rounded-md border border-slate-200 bg-white px-6 py-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">历史清理</h1>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
              在删除前统一检查历史分析、临时输出和运行缓存。正在运行的任务会保持锁定，不能在这里清理。
            </p>
            <p className="mt-2 text-xs text-slate-400">
              上次刷新：{formatDateTime(inventory?.generated_at)}
            </p>
          </div>
          <button
            onClick={() => void refreshInventory()}
            disabled={Boolean(busyKey)}
            className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busyKey === 'refresh' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
            刷新
          </button>
        </div>
      </section>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {notice && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {notice}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          title="历史分析"
          value={String(sectionStats.analysis?.count || 0)}
          detail={formatBytes(sectionStats.analysis?.size_bytes || 0)}
          icon={FileArchive}
        />
        <MetricCard
          title="测试诊断"
          value={String(sectionStats.diagnostic?.count || 0)}
          detail={formatBytes(sectionStats.diagnostic?.size_bytes || 0)}
          icon={AlertTriangle}
        />
        <MetricCard
          title="已记录钱包"
          value={String(sectionStats.registry?.count || 0)}
          detail={formatBytes(sectionStats.registry?.size_bytes || 0)}
          icon={Database}
        />
        <MetricCard
          title="临时输出"
          value={String(sectionStats.temp?.count || 0)}
          detail={formatBytes(sectionStats.temp?.size_bytes || 0)}
          icon={Trash2}
        />
        <MetricCard
          title="当前占用"
          value={formatBytes(sectionStats.totalBytes)}
          detail={`${sectionStats.totalItems} 项`}
          icon={HardDrive}
        />
      </div>

      <section className="rounded-md border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-6 py-5">
          <h2 className="text-base font-semibold text-slate-900">快捷清理动作</h2>
          <p className="mt-1 text-sm text-slate-500">把常见清理任务组合成一键操作，并继续沿用后端的安全保护规则。</p>
        </div>
        <div className="grid grid-cols-1 gap-4 p-6 xl:grid-cols-2">
          {(inventory?.actions || []).map((action) => {
            const meta = getCleanupActionMeta(action.key);
            const riskTone =
              meta.risk === 'high'
                ? 'border-red-200 bg-red-50 text-red-700'
                : meta.risk === 'medium'
                  ? 'border-amber-200 bg-amber-50 text-amber-700'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700';
            return (
              <article key={action.key} className="rounded-md border border-slate-200 bg-slate-50/60 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">{action.label}</h3>
                    <p className="mt-1 text-sm leading-6 text-slate-500">{action.description}</p>
                  </div>
                  <span className={cn('inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium', riskTone)}>
                    {meta.risk === 'high' ? <ShieldAlert className="mr-1 h-3.5 w-3.5" /> : <Database className="mr-1 h-3.5 w-3.5" />}
                    {cleanupRiskLabel(meta.risk)}
                  </span>
                </div>
                <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-500">
                  <span>{action.target_count} 项目标</span>
                  <span>{formatBytes(action.size_bytes)}</span>
                  <span>{cleanupCategoryLabel(meta.category)}</span>
                </div>
                {action.warning && <p className="mt-3 text-sm leading-6 text-slate-600">{action.warning}</p>}
                {meta.preserveLabel && <p className="mt-2 text-sm leading-6 text-slate-600">{meta.preserveLabel}</p>}
                <div className="mt-4">
                  <button
                    onClick={() => void executePresetAction(action)}
                    disabled={Boolean(busyKey) || !action.target_count}
                    className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                  >
                    {busyKey === `action:${action.key}` ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                    执行清理
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="rounded-md border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-6 py-5">
          <h2 className="text-base font-semibold text-slate-900">已选条目</h2>
          <p className="mt-1 text-sm text-slate-500">
            {selectedItems.length
              ? `已选择 ${selectedItems.length} 项内容`
              : '可在下方勾选一个或多个条目，直接删除，或仅清理历史分析里的大体积明细附件。'}
          </p>
        </div>
        <div className="flex flex-col gap-4 px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm text-slate-600">
            <span>可删除：{selectedDeleteItems.length} 项</span>
            <span>可清理明细：{selectedPruneItems.length} 项</span>
            <span>删除体积：{formatBytes(selectedDeleteItems.reduce((sum, item) => sum + Number(item.size_bytes || 0), 0))}</span>
            <span>可释放明细：{formatBytes(selectedPruneItems.reduce((sum, item) => sum + Number(item.detail_prunable_bytes || 0), 0))}</span>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              onClick={() => setSelectedItemIds([])}
              disabled={!selectedItems.length || Boolean(busyKey)}
              className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              清空选择
            </button>
            <button
              onClick={() => void executeItemOperation(selectedPruneItems, 'prune', 'prune:selected')}
              disabled={!selectedPruneItems.length || Boolean(busyKey)}
              className="inline-flex h-10 items-center justify-center rounded-md border border-violet-200 bg-violet-50 px-4 text-sm font-medium text-violet-700 shadow-sm hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busyKey === 'prune:selected' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}
              清理所选明细
            </button>
            <button
              onClick={() => void executeItemOperation(selectedDeleteItems, 'delete', 'delete:selected')}
              disabled={!selectedDeleteItems.length || Boolean(busyKey)}
              className="inline-flex h-10 items-center justify-center rounded-md bg-red-600 px-4 text-sm font-medium text-white shadow-sm hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {busyKey === 'delete:selected' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
              删除所选
            </button>
          </div>
        </div>
      </section>

      {(inventory?.sections || []).map((section) => {
        const walletRosterSection = isWalletRosterSection(section);

        return (
          <section key={section.key} className="overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-col gap-4 border-b border-slate-100 px-6 py-5 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <h2 className="text-base font-semibold text-slate-900">{section.label}</h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">{section.description}</p>
                {walletRosterSection && (
                  <p className="mt-2 text-xs text-slate-500">此分组会直接展示历史已记录的钱包用户名与地址。</p>
                )}
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-500">
                  <span>{section.count} 项</span>
                  <span>{formatBytes(section.size_bytes)}</span>
                </div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <button
                  onClick={() => toggleSection(section, 'delete')}
                  disabled={!section.items.some(canDeleteItem) || Boolean(busyKey)}
                  className="inline-flex h-9 items-center justify-center rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  选择可删除项
                </button>
                <button
                  onClick={() => toggleSection(section, 'prune')}
                  disabled={!section.items.some(canPruneItem) || Boolean(busyKey)}
                  className="inline-flex h-9 items-center justify-center rounded-md border border-violet-200 bg-violet-50 px-3 text-sm font-medium text-violet-700 hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  选择可清理明细项
                </button>
              </div>
            </div>

          {section.items.length ? (
            <div className="divide-y divide-slate-100">
              {section.items.map((item) => {
                const isSelected = selectedItemIds.includes(item.id);
                const selectable = canDeleteItem(item) || canPruneItem(item);
                const walletRosterItem = getWalletRosterPresentation(item);
                return (
                  <div key={item.id} className="grid gap-4 px-6 py-5 lg:grid-cols-[auto_minmax(0,1fr)_auto]">
                    <div className="pt-1">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={!selectable || Boolean(busyKey)}
                        onChange={() => toggleItem(item.id)}
                        className="h-4 w-4 rounded border-slate-300 text-[#2E5CFF] focus:ring-[#2E5CFF]"
                      />
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-sm font-semibold text-slate-900">{walletRosterItem?.title || item.label}</div>
                        <span className={cn('inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium', cleanupItemTypeTone(item.item_type))}>
                          {cleanupItemTypeLabel(item.item_type)}
                        </span>
                        {item.status && (
                          <span className={cn('inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium', statusTone(item.status))}>
                            {statusLabel(item.status)}
                          </span>
                        )}
                        {item.run_id && activeRunId === item.run_id && (
                          <span className="inline-flex items-center rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                            当前运行
                          </span>
                        )}
                        {item.locked && (
                          <span className="inline-flex items-center rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                            已锁定
                          </span>
                        )}
                      </div>
                      {walletRosterItem ? (
                        <WalletRosterSummary item={item} roster={walletRosterItem} />
                      ) : (
                        <div className="mt-1 break-all font-mono text-xs text-slate-500">{item.path}</div>
                      )}
                      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-500">
                        <span>{formatBytes(item.size_bytes)}</span>
                        <span>{item.file_count} 个文件</span>
                        <span>{formatDateTime(item.modified_at)}</span>
                      </div>
                      {item.note && <p className="mt-3 text-sm leading-6 text-slate-600">{item.note}</p>}
                      {canPruneItem(item) && (
                        <p className="mt-2 text-sm text-violet-700">
                          可清理明细体积：{formatBytes(item.detail_prunable_bytes || 0)}
                        </p>
                      )}
                      {item.locked_reason && <p className="mt-2 text-sm text-amber-700">{item.locked_reason}</p>}
                    </div>
                    <div className="flex flex-col gap-2 sm:flex-row lg:flex-col">
                      <button
                        onClick={() => void executeItemOperation([item], 'delete', `delete:${item.id}`)}
                        disabled={!canDeleteItem(item) || Boolean(busyKey)}
                        className="inline-flex h-9 items-center justify-center rounded-md border border-red-200 bg-red-50 px-3 text-sm font-medium text-red-700 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {busyKey === `delete:${item.id}` ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                        删除
                      </button>
                      <button
                        onClick={() => void executeItemOperation([item], 'prune', `prune:${item.id}`)}
                        disabled={!canPruneItem(item) || Boolean(busyKey)}
                        className="inline-flex h-9 items-center justify-center rounded-md border border-violet-200 bg-violet-50 px-3 text-sm font-medium text-violet-700 hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {busyKey === `prune:${item.id}` ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}
                        清理明细
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="px-6 py-10 text-center text-sm text-slate-500">当前分组暂无可清理内容。</div>
          )}
          </section>
        );
      })}
    </div>
  );
}

function MetricCard({
  title,
  value,
  detail,
  icon: Icon,
}: {
  title: string;
  value: string;
  detail: string;
  icon: typeof FileArchive;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-slate-500">{title}</div>
        <div className="rounded-md bg-slate-100 p-2 text-slate-600">
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="mt-3 text-2xl font-bold tracking-tight text-slate-900">{value}</div>
      <div className="mt-2 text-sm text-slate-500">{detail}</div>
    </section>
  );
}

function WalletRosterSummary({
  item,
  roster,
}: {
  item: CleanupItem;
  roster: WalletRosterPresentation;
}) {
  return (
    <>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <CleanupDetailField label="用户名" value={roster.userName || '未记录'} />
        <CleanupDetailField label="钱包地址" value={roster.address || '未记录'} mono />
        {roster.firstSeenAt && <CleanupDetailField label="首次记录" value={formatDateTime(roster.firstSeenAt)} />}
        {roster.lastSeenAt && <CleanupDetailField label="最近出现" value={formatDateTime(roster.lastSeenAt)} />}
        {roster.runCount != null && <CleanupDetailField label="涉及分析" value={`${roster.runCount} 次`} />}
      </div>
      <div className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
        <div className="font-medium text-slate-700">来源路径</div>
        <div className="mt-1 break-all font-mono">{item.path}</div>
      </div>
    </>
  );
}

function CleanupDetailField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <div className="text-[11px] font-medium text-slate-500">{label}</div>
      <div className={cn('mt-1 break-all text-sm text-slate-900', mono && 'font-mono text-xs')}>{value}</div>
    </div>
  );
}

function canDeleteItem(item: CleanupItem): boolean {
  return !item.locked;
}

function canPruneItem(item: CleanupItem): boolean {
  return item.item_type === 'analysis_run' && !item.locked && Number(item.detail_prunable_bytes || 0) > 0;
}

function cleanupRiskLabel(risk: string): string {
  if (risk === 'high') return '高风险';
  if (risk === 'medium') return '中风险';
  return '低风险';
}

function cleanupCategoryLabel(category: string): string {
  if (category === 'history') return '历史数据';
  if (category === 'diagnostic') return '测试诊断';
  return '运行缓存';
}

function confirmCleanup({
  label,
  description,
  count,
  sizeBytes,
  confirmPhrase,
}: {
  label: string;
  description?: string;
  count: number;
  sizeBytes?: number;
  confirmPhrase?: string;
}): boolean {
  const summary = [
    label,
    '',
    description || '',
    '',
    `目标条目：${count}`,
    `预计释放：${formatBytes(sizeBytes || 0)}`,
  ]
    .filter(Boolean)
    .join('\n');

  if (confirmPhrase) {
    const value = window.prompt(`${summary}\n\n请输入“${confirmPhrase}”继续。`, '');
    return value === confirmPhrase;
  }
  return window.confirm(summary);
}

function isWalletRosterSection(section: CleanupSection): boolean {
  const normalizedKey = section.key.toLowerCase();
  if (normalizedKey.includes('wallet') || normalizedKey.includes('roster') || normalizedKey.includes('registry')) {
    return true;
  }
  return section.items.some((item) => Boolean(getWalletRosterPresentation(item)));
}

function getWalletRosterPresentation(item: CleanupItem): WalletRosterPresentation | undefined {
  const address = firstDefinedString(
    item.wallet_address,
    item.wallet,
    item.address,
    looksLikeWalletAddress(item.label) ? item.label : undefined,
  );
  const userName = firstDefinedString(item.user_name, item.username, item.display_name, item.wallet_name);
  const firstSeenAt = firstDefinedString(item.first_seen_at, item.first_seen);
  const lastSeenAt = firstDefinedString(item.last_seen_at, item.last_seen);
  const runCount = firstDefinedNumber(item.run_count, item.runs_count, item.related_run_count);
  const hasStructuredMetadata = Boolean(address || userName || firstSeenAt || lastSeenAt || runCount != null);

  if (!hasStructuredMetadata && !isCleanupWalletRosterItemType(item.item_type)) {
    return undefined;
  }

  return {
    title: userName || shortAddress(address) || item.label || '历史钱包记录',
    address,
    userName,
    firstSeenAt,
    lastSeenAt,
    runCount,
  };
}

function firstDefinedString(...values: Array<string | null | undefined>): string | undefined {
  for (const value of values) {
    const normalized = value?.trim();
    if (normalized) {
      return normalized;
    }
  }
  return undefined;
}

function firstDefinedNumber(...values: Array<number | string | null | undefined>): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === 'string') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

function looksLikeWalletAddress(value?: string): boolean {
  return Boolean(value && /^0x[a-fA-F0-9]{40}$/.test(value.trim()));
}
