import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {AlertTriangle, ArrowUpRight, CheckCircle2, ChevronRight, RefreshCcw, Sparkles, X} from 'lucide-react';
import {type FinderAiRunSummary, type WalletRow, formatDateTime, formatNumber, shortAddress} from '../lib/api';
import {cn} from '../lib/utils';

export type FinderAiPreviewItem = {
  wallet: string;
  userName?: string;
  xUsername?: string;
  brief?: string;
  strategyFocus?: string;
  evidenceLevel?: string;
  needsReview?: boolean;
  hasConflict?: boolean;
};

export function hasFinderAiPreview(wallet: WalletRow): boolean {
  return Boolean(textValue(wallet.ai_brief_short) || textValue(wallet.ai_strategy_focus));
}

export function toFinderAiPreviewItem(wallet: WalletRow): FinderAiPreviewItem {
  return {
    wallet: wallet.wallet,
    userName: textValue(wallet.user_name),
    xUsername: normalizedHandle(wallet.x_username),
    brief: textValue(wallet.ai_brief_short),
    strategyFocus: textValue(wallet.ai_strategy_focus),
    evidenceLevel: textValue(wallet.ai_evidence_level),
    needsReview: Boolean(wallet.ai_needs_review),
    hasConflict: Boolean(wallet.ai_has_conflict),
  };
}

export function FinderAiRunSummaryStrip({
  summary,
  previewItems,
  onPreviewWalletOpen,
  compact = false,
  embedded = false,
  className,
}: {
  summary?: FinderAiRunSummary;
  previewItems?: FinderAiPreviewItem[];
  onPreviewWalletOpen?: (wallet: string) => void;
  compact?: boolean;
  embedded?: boolean;
  className?: string;
}) {
  const stats = normalizeSummary(summary);
  const availablePreviewItems = useMemo(
    () => (previewItems || []).filter((item) => textValue(item.brief) || textValue(item.strategyFocus)),
    [previewItems],
  );
  const [previewOpen, setPreviewOpen] = useState(false);
  const [activeWallet, setActiveWallet] = useState<string>();

  useEffect(() => {
    if (!availablePreviewItems.length) {
      setPreviewOpen(false);
      setActiveWallet(undefined);
      return;
    }
    if (!activeWallet || !availablePreviewItems.some((item) => item.wallet === activeWallet)) {
      setActiveWallet(availablePreviewItems[0]?.wallet);
    }
  }, [availablePreviewItems, activeWallet]);

  useEffect(() => {
    if (!previewOpen) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreviewOpen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [previewOpen]);

  if (!stats.selectedWallets && !stats.finderAiPresent) return null;

  const surfaceClass = embedded
    ? 'border-y border-slate-100 py-5'
    : 'rounded-md border border-slate-200 bg-white p-5 shadow-sm';
  const headline = buildHeadline(stats);
  const note = buildNote(stats);
  const coverageCaption =
    stats.available > 0 ? `新生成 ${stats.generated} · 缓存 ${stats.cached}` : '当前还没有可展示的 AI 摘要';
  const activePreview = availablePreviewItems.find((item) => item.wallet === activeWallet) || availablePreviewItems[0];

  return (
    <>
      <section className={cn(surfaceClass, className)}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
              <Sparkles className="h-3.5 w-3.5" />
              AI 运行概览
            </div>
            <p className={cn('mt-3 text-sm text-slate-800', compact && 'text-[13px]')}>{headline}</p>
            <p className={cn('mt-1 text-sm leading-6 text-slate-500', compact && 'text-xs leading-5')}>{note}</p>
          </div>

          <div className="flex flex-col items-start gap-3 lg:items-end">
            {stats.latestGeneratedAt ? (
              <div className="text-xs text-slate-500 lg:text-right">
                <div className="font-medium text-slate-700">最近生成</div>
                <div className="mt-1">{formatDateTime(stats.latestGeneratedAt)}</div>
              </div>
            ) : null}

            {availablePreviewItems.length ? (
              <button
                type="button"
                onClick={() => setPreviewOpen(true)}
                className="inline-flex h-9 items-center justify-center rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
              >
                预览摘要
                <ChevronRight className="ml-1.5 h-4 w-4 text-slate-400" />
              </button>
            ) : null}
          </div>
        </div>

        <div className={cn('mt-4 grid grid-cols-2 gap-3 md:grid-cols-4', compact && 'md:grid-cols-4')}>
          <MetricCell
            label="摘要可用"
            value={`${formatNumber(stats.available)} / ${formatNumber(stats.selectedWallets)}`}
            caption={coverageCaption}
            tone="blue"
            compact={compact}
            icon={<CheckCircle2 className="h-3.5 w-3.5" />}
          />
          <MetricCell
            label="新生成"
            value={formatNumber(stats.generated)}
            caption="本次实际完成生成"
            tone="emerald"
            compact={compact}
            icon={<Sparkles className="h-3.5 w-3.5" />}
          />
          <MetricCell
            label="缓存命中"
            value={formatNumber(stats.cached)}
            caption="沿用已有摘要结果"
            tone="slate"
            compact={compact}
            icon={<RefreshCcw className="h-3.5 w-3.5" />}
          />
          <MetricCell
            label="需复核"
            value={formatNumber(stats.needsReview)}
            caption={stats.hasConflict > 0 ? `冲突信号 ${formatNumber(stats.hasConflict)}` : '建议人工再看一眼'}
            tone={stats.needsReview > 0 || stats.hasConflict > 0 ? 'amber' : 'slate'}
            compact={compact}
            icon={<AlertTriangle className="h-3.5 w-3.5" />}
          />
        </div>
      </section>

      {previewOpen && activePreview ? (
        <FinderAiPreviewDialog
          items={availablePreviewItems}
          activeWallet={activePreview.wallet}
          onSelect={setActiveWallet}
          onOpenWallet={onPreviewWalletOpen}
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </>
  );
}

function FinderAiPreviewDialog({
  items,
  activeWallet,
  onSelect,
  onOpenWallet,
  onClose,
}: {
  items: FinderAiPreviewItem[];
  activeWallet: string;
  onSelect: (wallet: string) => void;
  onOpenWallet?: (wallet: string) => void;
  onClose: () => void;
}) {
  const activeItem = items.find((item) => item.wallet === activeWallet) || items[0];
  const previewText = textValue(activeItem?.brief) || textValue(activeItem?.strategyFocus) || '当前没有可展示的 AI 摘要。';
  const strategyFocus = textValue(activeItem?.strategyFocus);
  const identity = preferredPreviewIdentity(activeItem);
  const status = previewStatus(activeItem);
  const evidence = evidenceLevelLabel(activeItem?.evidenceLevel);

  const openWalletDetail = () => {
    if (!activeItem?.wallet || !onOpenWallet) return;
    onClose();
    onOpenWallet(activeItem.wallet);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" onClick={onClose} role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="AI 摘要预览"
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[82vh] w-full max-w-5xl flex-col overflow-hidden rounded-md border border-slate-200 bg-white shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            <div className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
              <Sparkles className="h-3.5 w-3.5" />
              AI 摘要预览
            </div>
            <h2 className="mt-3 text-base font-semibold text-slate-900">先看几条代表性摘要，再决定要不要深入点开钱包详情</h2>
            <p className="mt-1 text-sm text-slate-500">这里展示当前运行里已经生成 AI 摘要的钱包，点击左侧列表即可切换预览。</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭 AI 摘要预览"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-y-auto border-b border-slate-100 bg-slate-50/70 md:border-b-0 md:border-r md:border-slate-100">
            <div className="border-b border-slate-100 px-4 py-3 text-xs font-medium text-slate-500">已生成摘要 {formatNumber(items.length)} 条</div>
            <div className="p-2">
              {items.map((item) => {
                const selected = item.wallet === activeItem?.wallet;
                const itemIdentity = preferredPreviewIdentity(item);
                const itemStatus = previewStatus(item);
                return (
                  <button
                    key={item.wallet}
                    type="button"
                    onClick={() => {
                      if (selected && onOpenWallet) {
                        openWalletDetail();
                        return;
                      }
                      onSelect(item.wallet);
                    }}
                    className={cn(
                      'mb-2 w-full cursor-pointer rounded-md border px-3 py-3 text-left transition-colors last:mb-0',
                      selected
                        ? 'border-blue-200 bg-white shadow-sm'
                        : 'border-transparent bg-transparent hover:border-slate-200 hover:bg-white',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-slate-900">{itemIdentity}</div>
                        <div className="mt-1 truncate font-mono text-xs text-slate-500">{shortAddress(item.wallet)}</div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <span className={cn('inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium', itemStatus.tone)}>
                          {itemStatus.label}
                        </span>
                        {selected && onOpenWallet ? (
                          <span className="inline-flex items-center gap-0.5 text-[11px] font-medium text-blue-600">
                            详情
                            <ArrowUpRight className="h-3 w-3" />
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-2 line-clamp-2 text-xs leading-5 text-slate-500">
                      {textValue(item.brief) || textValue(item.strategyFocus) || '当前没有可展示的摘要内容。'}
                    </div>
                  </button>
                );
              })}
            </div>
          </aside>

          <div className="min-h-0 overflow-y-auto px-5 py-5">
            <div className="flex flex-col gap-3 border-b border-slate-100 pb-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-lg font-semibold tracking-tight text-slate-900">{identity}</span>
                  <span className="font-mono text-xs text-slate-500">{activeItem.wallet}</span>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className={cn('inline-flex rounded-full border px-2.5 py-1 text-xs font-medium', status.tone)}>{status.label}</span>
                  {evidence ? (
                    <span className="inline-flex rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600">
                      {evidence}
                    </span>
                  ) : null}
                  {activeItem?.hasConflict ? (
                    <span className="inline-flex rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                      存在冲突信号
                    </span>
                  ) : null}
                </div>
              </div>

              {onOpenWallet ? (
                <button
                  type="button"
                  onClick={openWalletDetail}
                  className="inline-flex h-9 shrink-0 items-center justify-center rounded-md bg-[#2E5CFF] px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
                >
                  查看钱包详情
                </button>
              ) : null}
            </div>

            {strategyFocus ? (
              <div className="mt-5">
                <div className="text-xs font-medium text-slate-500">策略焦点</div>
                <div className="mt-2 text-sm font-semibold text-slate-900">{strategyFocus}</div>
              </div>
            ) : null}

            <div className="mt-5">
              <div className="text-xs font-medium text-slate-500">摘要正文</div>
              <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-4 text-sm leading-7 text-slate-700">
                {previewText}
              </div>
            </div>

            <div className="mt-5 rounded-md border border-slate-200 bg-white px-4 py-3 text-xs leading-6 text-slate-500">
              这里只做快速预览，完整标签、证据和交易细节仍以钱包列表和钱包详情页为准。
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricCell({
  label,
  value,
  caption,
  tone,
  compact,
  icon,
}: {
  label: string;
  value: string;
  caption: string;
  tone: 'blue' | 'emerald' | 'amber' | 'slate';
  compact: boolean;
  icon: ReactNode;
}) {
  const toneClass = {
    blue: 'text-blue-700',
    emerald: 'text-emerald-700',
    amber: 'text-amber-700',
    slate: 'text-slate-700',
  }[tone];

  return (
    <div className="min-w-0 border-l border-slate-200 pl-3 first:border-l-0 first:pl-0">
      <div className={cn('flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500', compact && 'tracking-normal')}>
        <span className={toneClass}>{icon}</span>
        {label}
      </div>
      <div className="mt-2 truncate text-2xl font-semibold tracking-tight text-slate-900">{value}</div>
      <div className={cn('mt-1 text-sm text-slate-500', compact && 'text-xs')}>{caption}</div>
    </div>
  );
}

function normalizeSummary(summary?: FinderAiRunSummary) {
  const selectedWallets = numberValue(summary?.selected_wallets);
  const finderAiPresent = numberValue(summary?.finder_ai_present ?? selectedWallets);
  const generated = numberValue(summary?.generated);
  const cached = numberValue(summary?.cached);
  const failed = numberValue(summary?.failed);
  const skipped = numberValue(summary?.skipped);
  const eligible = numberValue(summary?.eligible);
  const needsReview = numberValue(summary?.needs_review);
  const hasConflict = numberValue(summary?.has_conflict);
  const available = generated + cached;

  return {
    selectedWallets,
    finderAiPresent,
    available,
    generated,
    cached,
    failed,
    skipped,
    eligible,
    needsReview,
    hasConflict,
    latestGeneratedAt: textValue(summary?.latest_generated_at),
  };
}

function buildHeadline(summary: ReturnType<typeof normalizeSummary>): string {
  const denominator = Math.max(summary.selectedWallets, summary.finderAiPresent);
  if (summary.available > 0) {
    if (summary.failed > 0) {
      return `本次已为 ${summary.available} / ${denominator} 个入选钱包写入 AI 摘要，另有 ${summary.failed} 个未完成生成。`;
    }
    return `本次已为 ${summary.available} / ${denominator} 个入选钱包写入 AI 摘要。`;
  }
  if (summary.eligible > 0) {
    return `本次有 ${summary.eligible} 个钱包进入 AI 生成链路，但当前还没有可展示的摘要结果。`;
  }
  return '本次任务还没有产生可展示的 AI 摘要结果。';
}

function buildNote(summary: ReturnType<typeof normalizeSummary>): string {
  const parts: string[] = [];
  if (summary.skipped > 0) {
    parts.push(`${summary.skipped} 个因证据或门控条件未生成`);
  }
  if (summary.needsReview > 0) {
    parts.push(`${summary.needsReview} 个建议复核`);
  }
  if (summary.hasConflict > 0) {
    parts.push(`${summary.hasConflict} 个存在冲突信号`);
  }
  if (summary.failed > 0) {
    return `未完成项不会影响本次筛选结果，结构化证据仍可正常查看。${parts.length ? ` 其中${parts.join('，')}。` : ''}`.trim();
  }
  if (parts.length > 0) {
    return `仅对证据充足的钱包生成 AI 摘要；未生成项仍保留结构化结果。其中${parts.join('，')}。`;
  }
  if (summary.cached > 0 && summary.generated > 0) {
    return '本次结果同时使用了新生成与缓存摘要，优先保证处理效率和展示连续性。';
  }
  if (summary.cached > 0) {
    return '本次摘要主要来自缓存结果，已沿用当前任务的结构化分析口径。';
  }
  if (summary.available > 0) {
    return 'AI 摘要已随常规分析链路一并产出，可在列表、详情和预览窗口里直接查看。';
  }
  return '本次仍保留完整的结构化筛选结果，后续可继续结合 AI 生成状态查看。';
}

function previewStatus(item?: FinderAiPreviewItem): {label: string; tone: string} {
  if (textValue(item?.brief)) {
    if (item?.needsReview || item?.hasConflict) {
      return {label: '需复核', tone: 'border-amber-200 bg-amber-50 text-amber-700'};
    }
    return {label: '已生成', tone: 'border-blue-200 bg-blue-50 text-blue-700'};
  }
  if (item?.needsReview || item?.hasConflict) {
    return {label: '需复核', tone: 'border-amber-200 bg-amber-50 text-amber-700'};
  }
  if (textValue(item?.strategyFocus)) {
    return {label: '已提炼', tone: 'border-slate-200 bg-slate-50 text-slate-700'};
  }
  return {label: '待生成', tone: 'border-slate-200 bg-slate-50 text-slate-600'};
}

function preferredPreviewIdentity(item?: FinderAiPreviewItem): string {
  for (const value of [item?.userName, item?.xUsername]) {
    const text = textValue(value);
    if (text) return text;
  }
  return shortAddress(item?.wallet || '');
}

function evidenceLevelLabel(value?: string): string | undefined {
  const key = textValue(value).toLowerCase();
  if (!key) return undefined;
  const labels: Record<string, string> = {
    insufficient: '证据不足',
    structured_only: '结构化证据',
    medium: '中等证据',
    high: '高置信度',
  };
  return labels[key] || value;
}

function normalizedHandle(value: unknown): string | undefined {
  const text = textValue(value).replace(/^@+/, '');
  if (!text) return undefined;
  const lowered = text.toLowerCase();
  if (lowered.startsWith('0x') && lowered.length >= 10) return undefined;
  return `@${text}`;
}

function textValue(value: unknown): string {
  return String(value || '').trim();
}

function numberValue(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
