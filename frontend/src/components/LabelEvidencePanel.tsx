import {CheckCircle2, XCircle} from 'lucide-react';
import type {ReactNode} from 'react';
import type {LabelEvidence, LabelEvidenceRecord, LabelEvaluation} from '../lib/api';
import {formatCurrency, formatNumber, formatPercent} from '../lib/api';

export type EvidenceRow = {
  key: string;
  title: string;
  description?: string;
  matched: boolean;
  reason: string;
  facts: Array<{label: string; value: string}>;
  records: string[];
};

const evidenceOrder = [
  'high_frequency_region',
  'high_daily_region_profit',
  'regional_high_win_rate',
  'lottery_player',
  'split_player',
  'liquidity_player',
];

export function LabelEvidencePanel({
  evidence,
}: {
  evidence?: LabelEvaluation[] | LabelEvidence[] | Record<string, LabelEvidence>;
}) {
  const rows = normalizeEvidenceRows(evidence);
  const matchedCount = rows.filter((row) => row.matched).length;

  return (
    <section className="rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-2 border-b border-slate-100 px-6 py-5 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-900">标签证据表格</h2>
          <p className="mt-1 text-sm text-slate-500">仅展示后端返回的标签判断、事实字段和证据记录。</p>
        </div>
        <div className="inline-flex w-fit items-center rounded border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600">
          {matchedCount}/{rows.length} 个标签命中
        </div>
      </div>

      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                <Header>标签</Header>
                <Header>结果</Header>
                <Header>后端理由</Header>
                <Header>关键事实</Header>
                <Header>代表记录</Header>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {rows.map((row) => (
                <tr key={row.key}>
                  <td className="w-48 px-6 py-4 align-top">
                    <div className="flex items-center gap-2">
                      {row.matched ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <XCircle className="h-4 w-4 text-slate-400" />}
                      <div>
                        <div className="text-sm font-semibold text-slate-900">{row.title}</div>
                        {row.description && <div className="mt-1 text-xs leading-5 text-slate-500">{row.description}</div>}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 align-top">
                    <span
                      className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${
                        row.matched ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-500'
                      }`}
                    >
                      {row.matched ? '命中' : '未命中'}
                    </span>
                  </td>
                  <td className="max-w-sm px-6 py-4 align-top text-sm leading-6 text-slate-700">{row.reason || '-'}</td>
                  <td className="min-w-72 px-6 py-4 align-top">
                    <div className="flex flex-wrap gap-1.5">
                      {row.facts.length ? (
                        row.facts.slice(0, 8).map((fact) => (
                          <span key={`${row.key}-${fact.label}-${fact.value}`} className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
                            {fact.label}: <span className="font-medium text-slate-900">{fact.value}</span>
                          </span>
                        ))
                      ) : (
                        <span className="text-sm text-slate-400">-</span>
                      )}
                    </div>
                  </td>
                  <td className="min-w-80 px-6 py-4 align-top">
                    {row.records.length ? (
                      <ul className="space-y-1.5">
                        {row.records.slice(0, 4).map((record) => (
                          <li key={`${row.key}-${record}`} className="text-sm leading-6 text-slate-600">
                            {record}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="text-sm text-slate-400">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-6 py-8 text-center text-sm text-slate-500">当前结果还没有后端标签证据。</div>
      )}
    </section>
  );
}

export function normalizeEvidenceRows(
  evidence?: LabelEvaluation[] | LabelEvidence[] | Record<string, LabelEvidence>,
): EvidenceRow[] {
  const values = Array.isArray(evidence) ? evidence : Object.values(evidence || {});
  const rows = values.map((item, index) => normalizeEvidenceRow(item, index));
  const order = new Map(evidenceOrder.map((key, index) => [key, index]));
  return rows.sort((left, right) => {
    const leftOrder = order.get(left.key) ?? 999;
    const rightOrder = order.get(right.key) ?? 999;
    return leftOrder - rightOrder || left.title.localeCompare(right.title);
  });
}

function normalizeEvidenceRow(item: LabelEvaluation | LabelEvidence, index: number): EvidenceRow {
  const details = asRecord(item.details);
  const suppliedFacts = asRecord((item as LabelEvaluation).facts);
  const facts = Object.keys(suppliedFacts).length ? suppliedFacts : details;
  const records = [
    ...arrayValue(item.records),
    ...arrayValue((item as LabelEvidence).evidence),
  ];

  return {
    key: item.key || item.title || `evidence-${index}`,
    title: item.display_name || item.title || item.key || '标签证据',
    description: item.description,
    matched: Boolean(item.matched),
    reason: item.reason || item.decision || '后端未返回文字说明。',
    facts: formatFacts(facts, item.key || ''),
    records: records.map(recordText).filter(Boolean),
  };
}

function formatFacts(facts: Record<string, unknown>, evidenceKey: string): Array<{label: string; value: string}> {
  const preferred = [
    'city',
    'region',
    'date',
    'ratio',
    'multiple',
    'numerator',
    'denominator',
    'buy_amount',
    'sell_amount',
    'buy',
    'sell',
    'ratio_threshold',
    'threshold',
    'top_low_chip_region_ratio',
    'average_chip_cost',
    'target_chip_cost',
    'chain_validation_status',
    'chain_validation_reason',
    'swap_ratio',
  ];
  const rows: Array<{label: string; value: string}> = [];
  const seen = new Set<string>();
  for (const key of preferred) {
    pushFact(rows, seen, key, facts[key], evidenceKey);
  }
  for (const [key, value] of Object.entries(facts)) {
    if (rows.length >= 12) break;
    pushFact(rows, seen, key, value, evidenceKey);
  }
  return rows;
}

function pushFact(rows: Array<{label: string; value: string}>, seen: Set<string>, key: string, value: unknown, evidenceKey: string) {
  if (seen.has(key) || !hasDisplayValue(value)) return;
  if (Array.isArray(value) || (typeof value === 'object' && value !== null)) return;
  rows.push({label: factLabel(key, evidenceKey), value: formatFactValue(key, value, evidenceKey)});
  seen.add(key);
}

function recordText(record: string | LabelEvidenceRecord): string {
  if (typeof record === 'string') return record;
  const values = [
    record.date || record.buy_date || record.high_temperature_date,
    record.city || record.region,
    record.title || record.slug || record.market,
    record.multiple != null ? `${formatNumber(numberValue(record.multiple), 2)}x` : undefined,
    record.profit_multiple != null ? `${formatNumber(numberValue(record.profit_multiple), 2)}x` : undefined,
    record.ratio != null || record.trade_ratio != null ? formatPercent(numberValue(record.ratio ?? record.trade_ratio)) : undefined,
    record.buy_amount != null ? `买入 ${formatCurrency(numberValue(record.buy_amount))}` : undefined,
    record.sell_amount != null ? `卖出 ${formatCurrency(numberValue(record.sell_amount))}` : undefined,
    record.chip_cost != null ? `筹码 ${formatNumber(numberValue(record.chip_cost), 2)}` : undefined,
    record.trade_count != null ? `${formatNumber(numberValue(record.trade_count))} 笔` : undefined,
  ];
  return String(record.text || values.filter(Boolean).join(' · '));
}

function formatFactValue(key: string, value: unknown, evidenceKey = ''): string {
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (key.includes('ratio') || key.includes('rate')) return formatPercent(numberValue(value));
  if (key.includes('multiple')) return `${formatNumber(numberValue(value), 2)}x`;
  if ((key === 'buy' || key === 'sell') && evidenceKey === 'liquidity_player') return `${formatNumber(numberValue(value))} 次`;
  if (key.includes('amount') || key.includes('buy') || key.includes('sell')) return formatCurrency(numberValue(value));
  if (typeof value === 'number') return formatNumber(value, 2);
  return String(value);
}

function factLabel(key: string, evidenceKey = ''): string {
  const labels: Record<string, string> = {
    city: '城市',
    region: '地区',
    date: '日期',
    ratio: evidenceKey === 'lottery_player' ? '低成本占比' : '占比',
    multiple: '倍数',
    numerator: '分子',
    denominator: '分母',
    buy_amount: '买入',
    sell_amount: '卖出',
    buy: evidenceKey === 'liquidity_player' ? '买入次数' : '买入',
    sell: evidenceKey === 'liquidity_player' ? '卖出次数' : '卖出',
    ratio_threshold: '占比阈值',
    threshold: evidenceKey === 'lottery_player' ? '低成本阈值' : '阈值',
    top_low_chip_region_ratio: '低成本地区占比',
    average_chip_cost: '平均筹码',
    target_chip_cost: '目标筹码',
    chain_validation_status: '链上状态',
    chain_validation_reason: '链上说明',
    swap_ratio: 'swap 占比',
  };
  return labels[key] || key.replace(/_/g, ' ');
}

function Header({children}: {children: ReactNode}) {
  return <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-500">{children}</th>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function arrayValue(value: unknown): Array<string | LabelEvidenceRecord> {
  return Array.isArray(value) ? (value as Array<string | LabelEvidenceRecord>) : [];
}

function hasDisplayValue(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (typeof value === 'number') return Number.isFinite(value);
  return true;
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}
