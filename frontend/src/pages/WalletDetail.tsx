import {ArrowDown, ArrowUp, CheckCircle2, Copy} from 'lucide-react';
import type {ReactNode} from 'react';
import {useEffect, useMemo, useState} from 'react';
import {Pie, PieChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid} from 'recharts';
import {LabelEvidencePanel, normalizeEvidenceRows, type EvidenceRow} from '../components/LabelEvidencePanel';
import {
  MarketRecord,
  OperationAudit,
  WalletDetail as WalletDetailPayload,
  WalletRow,
  formatCurrency,
  formatNumber,
  formatPercent,
  getWalletDetail,
  getWallets,
  shortAddress,
} from '../lib/api';
import {Sparkles} from 'lucide-react';
import {Cell as PieSliceCell} from 'recharts';
import {type FinderAiMetric, type FinderAiResult, formatDateTime} from '../lib/api';

export function WalletDetail({
  activeRunId,
  wallet,
  onNavigate,
}: {
  activeRunId?: string;
  wallet?: string;
  onNavigate: (page: string) => void;
}) {
  const [fallbackWallet, setFallbackWallet] = useState<string>();
  const [detail, setDetail] = useState<WalletDetailPayload>();
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  const targetWallet = wallet || fallbackWallet;

  useEffect(() => {
    if (!activeRunId || wallet) return;
    let cancelled = false;
    getWallets(activeRunId, {limit: 1})
      .then((payload) => {
        if (!cancelled) setFallbackWallet(payload.items[0]?.wallet);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRunId, wallet]);

  useEffect(() => {
    if (!activeRunId || !targetWallet) return;
    let cancelled = false;
    setLoading(true);
    getWalletDetail(activeRunId, targetWallet)
      .then((payload) => {
        if (!cancelled) {
          setDetail(payload);
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
  }, [activeRunId, targetWallet]);

  const metrics = detail?.metrics || {};
  const selection = detail?.selection_record || detail?.screening || {};
  const selectionMeta = safeRecord(detail?.selection_record || detail?.screening || {});
  const leaderboardEntry = (detail?.leaderboard_entry || {}) as Record<string, unknown>;
  const profile = (detail?.profile || metrics.profile || {}) as Record<string, unknown>;
  const profileWallet = safeRecord(profile.wallet);
  const evidenceSummary = (detail?.evidence_summary || {}) as Record<string, unknown>;
  const operationAudit = (detail?.operation_audit || metrics.operation_audit || {}) as OperationAudit;
  const finderAi = detail?.finder_ai;
  const costData = useMemo(() => costBasisData(profile, metrics), [profile, metrics]);
  const frequencyData = useMemo(() => tradeFrequencyData(metrics), [metrics]);
  const serverEvidence = detail?.label_evaluations || detail?.label_evidence || detail?.label_match_details;
  const evidenceRows = useMemo(() => normalizeEvidenceRows(serverEvidence), [serverEvidence]);
  const walletXUserName = preferredWalletHandle(
    selectionMeta.x_username,
    selectionMeta.xUsername,
    leaderboardEntry.xUsername,
    profile.x_username,
    profile.xUsername,
  );
  const walletUserName =
    preferredWalletUserName(
      selection.user_name,
      selectionMeta.userName,
      leaderboardEntry.userName,
      profile.user_name,
      profile.username,
      profile.userName,
      profile.display_name,
      profile.displayName,
      profileWallet.displayName,
      profileWallet.alias,
      walletXUserName,
      targetWallet,
    ) || '未设置用户名';

  const copyWallet = async () => {
    if (!targetWallet) return;
    await navigator.clipboard?.writeText(targetWallet);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  if (!activeRunId) {
    return <Empty title="还没有选择运行" body="请先从已完成运行中打开钱包列表。" onAction={() => onNavigate('wallet_list')} />;
  }

  if (!targetWallet) {
    return (
      <Empty
        title="还没有选择钱包"
        body="请从钱包表格中选择一个地址，查看头寸、标签和指标。"
        onAction={() => onNavigate('wallet_list')}
      />
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 pb-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="mb-2 text-xl font-bold text-slate-900">钱包详情</h1>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
            <span className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-500">用户名</span>
            <span className="text-lg font-semibold tracking-tight text-slate-900">{walletUserName}</span>
            {walletXUserName && !sameIdentity(walletUserName, walletXUserName) && (
              <span className="text-sm text-slate-500">X：{walletXUserName}</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="font-mono text-lg tracking-tight text-slate-800">{targetWallet}</span>
            <button onClick={copyWallet} className="rounded p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600" title="复制钱包地址">
              <Copy className="h-4 w-4" />
            </button>
            <span className="text-slate-500">{copied ? '已复制' : shortAddress(targetWallet)}</span>
            <span className="inline-flex items-center rounded border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
              <CheckCircle2 className="mr-1 h-3 w-3" />
              {chainStatusLabel(metrics.chain_validation_status)}
            </span>
          </div>
        </div>
        <button
          onClick={() => onNavigate('wallet_list')}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          返回钱包列表
        </button>
      </div>

      {loading && <div className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-500">正在加载钱包详情...</div>}
      {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Stat title="总盈亏" value={formatCurrency(numberValue(selection.pnl ?? metrics.leaderboard_pnl))} up />
        <Stat title="胜率" value={formatPercent(numberValue(selection.closed_position_win_rate ?? metrics.closed_position_win_rate))} up />
        <Stat title="中位交易额" value={formatCurrency(numberValue(selection.median_trade_notional ?? metrics.median_trade_notional))} />
        <Stat
          title="天气占比"
          value={formatPercent(numberValue(selection.weather_notional_ratio ?? metrics.weather_notional_ratio))}
          up={numberValue(selection.weather_notional_ratio ?? metrics.weather_notional_ratio) >= 0.5}
        />
      </div>

      <EvidenceSummaryStrip selection={selection} metrics={metrics} rows={evidenceRows} summary={evidenceSummary} finderAi={finderAi} />

      <FinderAiPanel finderAi={finderAi} />

      <AuditSummaryPanel audit={operationAudit} />

      <RegionPathTable profile={profile} />

      <LabelEvidencePanel evidence={serverEvidence} />

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <Panel title="成本分布">
          <div className="flex h-[190px] items-center">
            {costData.length ? (
              <>
                <div className="h-full w-1/2">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={costData} dataKey="value" nameKey="name" innerRadius={42} outerRadius={72} paddingAngle={2}>
                        {costData.map((entry) => (
                          <PieSliceCell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="w-1/2 space-y-3">
                  {costData.map((item) => (
                    <div key={item.name} className="flex items-center justify-between text-sm">
                      <div className="flex items-center">
                        <span className="mr-2 h-3 w-3 rounded-sm" style={{backgroundColor: item.color}} />
                        <span className="text-slate-600">{item.name}</span>
                      </div>
                      <span className="font-medium text-slate-900">{formatNumber(item.value)}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <EmptyInline text="当前结果暂无成本分布。" />
            )}
          </div>
        </Panel>

        <Panel title="交易频率">
          <div className="h-[190px]">
            {frequencyData.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={frequencyData} margin={{top: 5, right: 10, left: -20, bottom: 0}}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="day" tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} dy={10} />
                  <YAxis tick={{fontSize: 12, fill: '#64748b'}} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{borderRadius: '8px', border: 'none'}} />
                  <Line type="monotone" dataKey="value" stroke="#2E5CFF" strokeWidth={3} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <EmptyInline text="当前结果暂无带日期的交易记录。" />
            )}
          </div>
        </Panel>
      </div>

      {detail?.strategy_notes?.length ? (
        <Panel title="策略备注">
          <ul className="space-y-2 text-sm text-slate-600">
            {detail.strategy_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Panel>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Panel title="当前持仓">
            <MarketTable records={detail?.top_positions || []} mode="open" />
          </Panel>
          <Panel title="已平仓头寸">
            <MarketTable records={detail?.top_closed_positions || []} mode="closed" />
          </Panel>
        </div>
        <Panel title="重点交易">
          <MarketTable records={detail?.top_trades || []} mode="trade" compact />
        </Panel>
      </div>
    </div>
  );
}

function Stat({title, value, up}: {title: string; value: string; up?: boolean}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-1 text-sm font-medium text-slate-500">{title}</h3>
      <div className="mb-2 flex items-baseline gap-2 text-2xl font-bold text-slate-900">
        {value}
        {up != null && (
          <span className={`flex items-center text-sm font-medium ${up ? 'text-emerald-600' : 'text-red-600'}`}>
            {up ? <ArrowUp className="mr-0.5 h-3 w-3" /> : <ArrowDown className="mr-0.5 h-3 w-3" />}
          </span>
        )}
      </div>
    </div>
  );
}

function preferredWalletUserName(...values: Array<unknown>): string | undefined {
  for (const value of values) {
    const text = String(value || '').trim();
    if (!text) continue;
    const lowered = text.toLowerCase();
    if (lowered.startsWith('0x') && lowered.length >= 10) continue;
    return text;
  }
  return undefined;
}

function preferredWalletHandle(...values: Array<unknown>): string | undefined {
  for (const value of values) {
    const text = String(value || '').trim().replace(/^@+/, '');
    if (!text) continue;
    const lowered = text.toLowerCase();
    if (lowered.startsWith('0x') && lowered.length >= 10) continue;
    return `@${text}`;
  }
  return undefined;
}

function sameIdentity(left: string, right: string): boolean {
  return left.replace(/^@+/, '').trim().toLowerCase() === right.replace(/^@+/, '').trim().toLowerCase();
}

function EvidenceSummaryStrip({
  selection,
  metrics,
  rows,
  summary,
  finderAi,
}: {
  selection: WalletRow;
  metrics: Record<string, unknown>;
  rows: EvidenceRow[];
  summary: Record<string, unknown>;
  finderAi?: FinderAiResult;
}) {
  const matched = rows.filter((row) => row.matched);
  const latestReason = String(
    finderAi?.aiBriefNote || finderAi?.sourceExcerpt || summary.headline || matched[0]?.reason || rows[0]?.reason || '后端尚未返回标签证据摘要。',
  );
  const aiState = resolveFinderAiDisplayState(finderAi);
  const summaryItems = [
    {
      label: '命中标签',
      value: `${numberValue(summary.matched_label_count ?? matched.length)}/${numberValue(summary.label_count ?? rows.length ?? 0)}`,
    },
    {
      label: 'AI 状态',
      value: aiState.label,
    },
    {
      label: '主地区',
      value: String(summary.main_region || selection.main_region || selection.dominant_region || metrics.dominant_region || '-'),
    },
    {
      label: '最近证据日',
      value: String(summary.latest_evidence_date || selection.recent_evidence_date || selection.highest_burst_date || metrics.latest_trade_date || '-'),
    },
  ];

  return (
    <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
      <div className="grid gap-4 md:grid-cols-[1.4fr_repeat(4,minmax(0,1fr))]">
        <div>
          <h2 className="text-base font-semibold text-slate-900">研判摘要</h2>
          <p className="mt-1 line-clamp-3 text-sm leading-6 text-slate-600">{latestReason}</p>
        </div>
        {summaryItems.map((item) => (
          <div key={item.label} className="border-t border-slate-100 pt-3 md:border-l md:border-t-0 md:pl-4 md:pt-0">
            <div className="text-xs text-slate-500">{item.label}</div>
            <div className="mt-1 truncate text-sm font-semibold text-slate-900" title={item.value}>
              {item.value}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function FinderAiPanel({finderAi}: {finderAi?: FinderAiResult}) {
  const hasAiContent =
    !!finderAi &&
    Boolean(
      String(finderAi.aiBriefNote || '').trim() ||
        String(finderAi.aiDeepNote || '').trim() ||
        String(finderAi.strategyFocus || '').trim() ||
        (finderAi.primarySignals || []).length ||
        (finderAi.keyMetrics || []).length,
    );
  if (!hasAiContent) return null;

  const aiState = resolveFinderAiDisplayState(finderAi);
  const primarySignals = (finderAi?.primarySignals || []).filter((item) => item?.label || item?.reason).slice(0, 4);
  const labels = (finderAi?.labels || []).filter((item) => item?.value).slice(0, 6);
  const keyMetrics = (finderAi?.keyMetrics || []).filter((item) => item?.label && item?.value != null).slice(0, 6);
  const briefNote = String(finderAi?.aiBriefNote || '').trim();
  const deepNote = String(finderAi?.aiDeepNote || '').trim();
  const sourceExcerpt = String(finderAi?.sourceExcerpt || '').trim();

  return (
    <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-col gap-4 border-b border-slate-100 pb-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
              <Sparkles className="h-3.5 w-3.5" />
              AI 研判
            </span>
            <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${aiState.tone}`}>
              {aiState.label}
            </span>
            <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600">
              {finderAiEvidenceLevelLabel(finderAi?.evidenceLevel)}
            </span>
          </div>
          <div className="mt-3 text-sm leading-6 text-slate-500">{aiState.note}</div>
          <div className="mt-4 rounded-md border border-blue-100 bg-blue-50/70 px-4 py-3 text-sm leading-6 text-slate-700">
            <span className="font-medium text-slate-900">这是 AI 研判结果。</span>
            <span className="ml-1">文案基于下方结构化信号、关键指标和证据摘录生成；即使 AI 未写出自然语言解读，结构化证据底座仍然有效。</span>
          </div>
        </div>
        <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-2 lg:min-w-[250px] lg:grid-cols-1">
          <MetaRow label="研判状态" value={aiState.label} />
          <MetaRow label="模型" value={finderAiModelName(finderAi?.providerMeta?.model)} />
          <MetaRow label="证据底座" value={finderAiEvidenceLevelLabel(finderAi?.evidenceLevel)} />
          <MetaRow label="生成时间" value={formatDateTime(finderAi?.providerMeta?.generatedAt || null)} />
          <MetaRow label="来源" value={finderAiProviderLabel(finderAi)} />
        </div>
      </div>

      <div className="grid gap-6 pt-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <div className="space-y-5">
          {finderAi?.strategyFocus ? (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">策略焦点</div>
              <div className="mt-2 text-sm font-semibold leading-6 text-slate-900">{finderAi.strategyFocus}</div>
            </div>
          ) : null}

          {briefNote ? (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">AI 短摘</div>
              <p className="mt-2 text-[15px] leading-7 text-slate-700">{briefNote}</p>
            </div>
          ) : null}

          {deepNote ? (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">深度解读</div>
              <div className="mt-2 rounded-md border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm leading-7 text-slate-700">
                {deepNote}
              </div>
            </div>
          ) : null}

          {sourceExcerpt ? (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">结构化证据摘录</div>
              <p className="mt-2 text-sm leading-6 text-slate-600">{sourceExcerpt}</p>
            </div>
          ) : null}

          {primarySignals.length ? (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">AI 使用的主要信号</div>
              <ul className="mt-3 space-y-3">
                {primarySignals.map((signal, index) => (
                  <li key={`${signal.key || signal.label || 'signal'}-${index}`} className="border-b border-slate-100 pb-3 last:border-b-0 last:pb-0">
                    <div className="text-sm font-medium text-slate-900">{signal.label || signal.key || '未命名信号'}</div>
                    {signal.reason ? <div className="mt-1 text-sm leading-6 text-slate-600">{signal.reason}</div> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {labels.length ? (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">研判标签</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {labels.map((label, index) => (
                  <span key={`${label.kind || 'tag'}-${label.value || index}`} className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700">
                    {label.value}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>

        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">AI 使用的关键指标</div>
          {keyMetrics.length ? (
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              {keyMetrics.map((metric, index) => (
                <div key={`${metric.key || metric.label || 'metric'}-${index}`} className="border-b border-slate-100 pb-3">
                  <div className="text-xs text-slate-500">{metric.label || metric.key || '指标'}</div>
                  <div className="mt-1 text-sm font-semibold text-slate-900">{formatFinderAiMetricValue(metric)}</div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyInline text="当前结果暂无可展示的 AI 指标底座。" />
          )}
        </div>
      </div>
    </section>
  );
}

function AuditSummaryPanel({audit}: {audit: OperationAudit}) {
  const profit = safeRecord(audit.profit_summary);
  const operations = safeRecord(audit.operations);
  const operationRows = ['convert', 'split', 'redeem', 'swap'].map((key) => safeRecord(operations[key]));
  const collectionStatus = safeRecord(audit.collection_status);
  const stopReasons = Object.entries(collectionStatus)
    .map(([key, value]) => `${key}: ${String(safeRecord(value).stop_reason || '-')}`)
    .join(' / ');

  return (
    <Panel title="流水审计">
      <div className="grid gap-4 md:grid-cols-4">
        <MiniStat title="抓取完整性" value={audit.complete ? '完整' : '可能截断'} />
        <MiniStat title="交易流动性收益" value={formatCurrency(numberValue(profit.trade_liquidity_profit))} />
        <MiniStat title="最终兑换收益" value={formatCurrency(numberValue(profit.final_settlement_profit))} />
        <MiniStat title="统一收益" value={formatCurrency(numberValue(profit.unified_profit))} />
      </div>
      <div className="mt-4 text-sm text-slate-600">{stopReasons || '后端未返回抓取停止原因。'}</div>
      <div className="mt-4 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-100">
          <thead>
            <tr>
              <th className="py-2 pr-2 text-left text-xs font-medium uppercase text-slate-500">操作</th>
              <th className="px-2 py-2 text-left text-xs font-medium uppercase text-slate-500">状态</th>
              <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">证据数</th>
              <th className="py-2 pl-2 text-left text-xs font-medium uppercase text-slate-500">说明</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {operationRows.map((row, index) => (
              <tr key={String(row.operation || index)}>
                <td className="py-3 pr-2 text-sm font-medium text-slate-900">{String(row.operation || '-')}</td>
                <td className="px-2 py-3 text-sm text-slate-600">{String(row.status || '-')}</td>
                <td className="px-2 py-3 text-right text-sm text-slate-600">{formatNumber(numberValue(row.count))}</td>
                <td className="py-3 pl-2 text-sm text-slate-600">{String(row.reason || '-')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function RegionPathTable({profile}: {profile: Record<string, unknown>}) {
  const cityDistribution = safeRecord(profile.city_distribution);
  const rows = safeArray(cityDistribution.cities).map(safeRecord).slice(0, 8);

  return (
    <Panel title="地区路径表">
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100">
            <thead>
              <tr>
                <th className="py-2 pr-2 text-left text-xs font-medium uppercase text-slate-500">地区</th>
                <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">交易数</th>
                <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">胜率</th>
                <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">买入</th>
                <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">卖出</th>
                <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">交易现金流</th>
                <th className="py-2 pl-2 text-right text-xs font-medium uppercase text-slate-500">已平仓盈亏</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {rows.map((row) => (
                <tr key={String(row.city || row.region || 'unknown')} className="hover:bg-slate-50/50">
                  <td className="py-3 pr-2 text-sm font-medium text-slate-900">{String(row.city || row.region || '-')}</td>
                  <td className="px-2 py-3 text-right text-sm text-slate-600">{formatNumber(numberValue(row.trade_count))}</td>
                  <td className="px-2 py-3 text-right text-sm text-slate-600">
                    {formatPercent(numberValue(row.positive_return_day_ratio))}
                    <span className="ml-1 text-xs text-slate-400">
                      ({formatNumber(numberValue(row.positive_return_days), 0)}/{formatNumber(numberValue(row.total_trade_days), 0)})
                    </span>
                  </td>
                  <td className="px-2 py-3 text-right text-sm text-slate-600">{formatCurrency(numberValue(row.buy_amount))}</td>
                  <td className="px-2 py-3 text-right text-sm text-slate-600">{formatCurrency(numberValue(row.sell_amount))}</td>
                  <td className="px-2 py-3 text-right text-sm text-slate-600">{formatCurrency(numberValue(row.net_trade_cashflow))}</td>
                  <td className="py-3 pl-2 text-right text-sm font-medium text-slate-900">{formatCurrency(numberValue(row.realized_pnl))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyInline text="当前结果暂无后端地区画像。" />
      )}
    </Panel>
  );
}

function MiniStat({title, value}: {title: string; value: string}) {
  return (
    <div className="rounded border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="text-xs text-slate-500">{title}</div>
      <div className="mt-1 text-sm font-semibold text-slate-900">{value}</div>
    </div>
  );
}

function MetaRow({label, value}: {label: string; value: string}) {
  return (
    <div className="border-b border-slate-100 pb-2 last:border-b-0 last:pb-0">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-900">{value || '-'}</div>
    </div>
  );
}

function Panel({title, children}: {title: string; children: ReactNode}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
      <h3 className="mb-4 text-base font-semibold text-slate-900">{title}</h3>
      {children}
    </div>
  );
}

function MarketTable({records, mode, compact = false}: {records: MarketRecord[]; mode: 'open' | 'closed' | 'trade'; compact?: boolean}) {
  if (!records.length) {
    return <EmptyInline text="当前结果暂无记录。" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-100">
        <thead>
          <tr>
            <th className="py-2 pr-2 text-left text-xs font-medium uppercase text-slate-500">市场</th>
            {!compact && <th className="px-2 py-2 text-left text-xs font-medium uppercase text-slate-500">方向</th>}
            <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">数量</th>
            {mode !== 'trade' && <th className="px-2 py-2 text-right text-xs font-medium uppercase text-slate-500">价格</th>}
            <th className="py-2 pl-2 text-right text-xs font-medium uppercase text-slate-500">盈亏</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {records.map((record, index) => {
            const pnl = numberValue(record.cashPnl ?? record.realizedPnl);
            return (
              <tr key={`${index}-${record.slug || record.title || record.eventSlug || 'market'}`} className="hover:bg-slate-50/50">
                <td className="max-w-sm py-3 pr-2 text-sm text-slate-900">
                  <div className="truncate" title={record.title || record.slug || ''}>
                    {record.title || record.slug || record.eventSlug || '-'}
                  </div>
                </td>
                {!compact && <td className="px-2 py-3 text-sm text-slate-600">{record.outcome || record.side || '-'}</td>}
                <td className="px-2 py-3 text-right text-sm text-slate-600">{formatNumber(numberValue(record.size), 2)}</td>
                {mode !== 'trade' && (
                  <td className="px-2 py-3 text-right text-sm text-slate-600">
                    {formatNumber(numberValue(record.curPrice ?? record.avgPrice ?? record.price), 3)}
                  </td>
                )}
                <td className={`py-3 pl-2 text-right text-sm font-medium ${pnl >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                  {formatCurrency(pnl)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Empty({title, body, onAction}: {title: string; body: string; onAction: () => void}) {
  return (
    <div className="mx-auto mt-12 max-w-xl rounded-md border border-slate-200 bg-white p-8 text-center shadow-sm">
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="mt-2 text-sm text-slate-500">{body}</p>
      <button onClick={onAction} className="mt-6 rounded-md bg-[#2E5CFF] px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
        打开钱包列表
      </button>
    </div>
  );
}

function EmptyInline({text}: {text: string}) {
  return <div className="flex min-h-28 items-center justify-center rounded-md border border-dashed border-slate-200 text-sm text-slate-500">{text}</div>;
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function chainStatusLabel(value: unknown): string {
  const status = String(value || 'local');
  const labels: Record<string, string> = {
    local: '本地结果',
    skipped: '未启用链上校验',
    disabled: '未启用链上校验',
    missing_api_key: '缺少链上 API Key',
    no_split_evidence: '未找到链上拆分证据',
    partial: '链上证据不完整',
    verified: '链上校验通过',
    request_failed: '链上请求失败',
    not_found: '未找到链上证据',
    validated: '链上校验通过',
    ok: '链上校验通过',
    failed: '链上校验失败',
    error: '链上校验异常',
  };
  return labels[status] || status;
}

function costBasisData(profile: Record<string, unknown>, metrics: Record<string, unknown>) {
  const colors = ['#2E5CFF', '#10b981', '#f59e0b', '#64748b'];
  const profileCost = safeRecord(profile.buy_price_distribution);
  const metricCost = safeRecord(metrics.cost_basis_distribution);
  const cost = Object.keys(profileCost).length ? profileCost : metricCost;
  return safeArray(cost.buckets)
    .map(safeRecord)
    .filter((bucket) => Number(bucket.count || 0) > 0)
    .map((bucket, index) => ({
      name: `${formatNumber(numberValue(bucket.min), 2)}-${formatNumber(numberValue(bucket.max), 2)}`,
      value: Number(bucket.count || 0),
      color: colors[index % colors.length],
    }));
}

function tradeFrequencyData(metrics: Record<string, unknown>) {
  const frequency = metrics.trade_frequency as {by_day?: Record<string, number>} | undefined;
  return Object.entries(frequency?.by_day || {})
    .slice(-14)
    .map(([day, value]) => ({day: day.slice(5), value}));
}

function formatMultiple(value: unknown): string {
  if (value == null || value === '') return '-';
  const number = numberValue(value);
  return Number.isFinite(number) ? `${formatNumber(number, 2)}x` : '-';
}

function formatFinderAiMetricValue(metric: FinderAiMetric): string {
  const key = String(metric.key || '').toLowerCase();
  const value = metric.value;
  if (value == null || value === '') return '-';
  if (typeof value === 'string') {
    const numeric = Number(value);
    if (!Number.isNaN(numeric) && value.trim() !== '') {
      return formatFinderAiMetricValue({...metric, value: numeric});
    }
    return value;
  }
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value !== 'number' || Number.isNaN(value)) return String(value);
  if (key.includes('ratio') || key.includes('win_rate')) return formatPercent(value);
  if (key.includes('pnl') || key.includes('volume')) return formatCurrency(value);
  return formatNumber(value, value % 1 === 0 ? 0 : 2);
}

function finderAiProviderLabel(finderAi?: FinderAiResult): string {
  const provider = String(finderAi?.providerMeta?.provider || '').trim();
  if (!provider) return '本次分析';
  return provider.toLowerCase() === 'deepseek' ? 'DeepSeek' : provider;
}

function finderAiModelName(model?: string): string {
  const value = String(model || '').trim();
  if (!value) return '-';
  if (value === 'deepseek-v4-flash') return 'DeepSeek V4 Flash';
  return value;
}

function finderAiEvidenceLevelLabel(value?: string): string {
  const labels: Record<string, string> = {
    insufficient: '证据不足',
    structured_only: '结构化证据已齐备',
    medium: '证据较完整',
    high: '证据较强',
  };
  return labels[String(value || '').trim()] || String(value || '未标注');
}

function resolveFinderAiDisplayState(finderAi?: FinderAiResult): {label: string; tone: string; note: string} {
  const status = String(finderAi?.briefGeneration?.status || '').trim().toLowerCase();
  if (status === 'cached') {
    return {
      label: '已载入缓存解读',
      tone: 'border-blue-200 bg-blue-50 text-blue-700',
      note: '当前展示的是已缓存的 AI 解读，仍然基于这次任务的结构化证据底座组织。',
    };
  }
  if (status === 'generated' || String(finderAi?.aiBriefNote || '').trim()) {
    return {
      label: '已生成 AI 解读',
      tone: 'border-blue-200 bg-blue-50 text-blue-700',
      note: '当前已经生成自然语言研判，可直接结合下方结构化信号与证据摘录一起阅读。',
    };
  }
  if (status === 'failed') {
    return {
      label: 'AI 生成未完成',
      tone: 'border-red-200 bg-red-50 text-red-700',
      note: '这次没有成功产出自然语言解读，但下方结构化证据底座仍可正常用于判断。',
    };
  }
  if (finderAi?.needsReview || status === 'needs_review') {
    return {
      label: '建议人工复核',
      tone: 'border-amber-200 bg-amber-50 text-amber-700',
      note: '结构化信号已经具备，但当前更适合先由人复核，再决定是否采纳这条 AI 研判。',
    };
  }
  if (finderAi?.evidenceLevel === 'insufficient' || status === 'insufficient') {
    return {
      label: '暂不生成 AI 文案',
      tone: 'border-slate-200 bg-slate-50 text-slate-600',
      note: '当前结构化证据还不够稳定，所以系统没有强行生成“像 AI 的一段话”。',
    };
  }
  if (finderAi?.hasConflict) {
    return {
      label: '信号存在冲突',
      tone: 'border-amber-200 bg-amber-50 text-amber-700',
      note: '不同信号之间存在分歧，建议重点对照下方证据摘录和标签证据表。',
    };
  }
  if (status === 'ready') {
    return {
      label: '待写入 AI 文案',
      tone: 'border-slate-200 bg-slate-50 text-slate-600',
      note: '结构化底座已经齐备，但当前还没有拿到自然语言解读结果。',
    };
  }
  return {
    label: '仅有结构化结果',
    tone: 'border-slate-200 bg-slate-50 text-slate-600',
    note: '当前页先展示规则与指标层的结果；AI 文案会在结构化底座满足条件后补上。',
  };
}

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function safeArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
