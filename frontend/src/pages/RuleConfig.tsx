import {Plus, RefreshCcw, Save, Trash2} from 'lucide-react';
import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {getDefaultConfig, saveDefaultConfig} from '../lib/api';
import {cn} from '../lib/utils';

type LabelRule = Record<string, any> & {
  key?: string;
  display_name?: string;
  description?: string;
  enabled?: boolean;
};

type RuleDefinition = {
  label: string;
  kind: '系统核心' | '补充标签';
  rule: string;
  fields: string;
};

const ruleDefinitions: RuleDefinition[] = [
  {
    label: '高频交易地区',
    kind: '系统核心',
    rule: '按地址全部交易记录的地区字段统计交易次数；某地区交易次数 / 总交易次数 > 60% 时命中。若所有地区占比均 <= 60% 且地区占比差距 <= 10%，不命中。',
    fields: '地区、地区交易次数、总交易次数、地区占比',
  },
  {
    label: '高暴击',
    kind: '系统核心',
    rule: '按同一地区同一天所有温度区间汇总买入和卖出；整体盈利倍数 = 总卖出金额 / 总买入金额，> 2x 时命中。',
    fields: '地区、交易日期、总买入金额、总卖出金额、整体盈利倍数',
  },
  {
    label: '高胜率',
    kind: '系统核心',
    rule: '按地区统计当日交易记录；正收益天数 / 总交易天数 >= 60% 时命中。',
    fields: '地区、交易日期、正收益天数、总交易天数',
  },
  {
    label: '彩票型选手',
    kind: '系统核心',
    rule: '筹码成本 < 30 的交易次数 / 总交易次数 > 50% 时命中。',
    fields: '筹码成本、低成本交易次数、总交易次数',
  },
  {
    label: '拆分型选手',
    kind: '系统核心',
    rule: '持仓均价接近 5，且 Polymarket 链上记录明确验证存在拆分操作时命中；无明确链上拆分记录或任一条件不满足均不命中。',
    fields: '持仓均价、Polymarket 链上记录、拆分证据数',
  },
  {
    label: '流动型选手',
    kind: '系统核心',
    rule: '特定 swap 次数占总交易次数 < 10% 或 swap 次数 = 0，且卖出主导日期数 / 总交易日期数 > 50% 时命中。卖出主导日指当日该地区已卖出记录占该日该地区总交易次数 > 50%。',
    fields: 'swap 记录、总交易次数、交易日期、地区、已卖出记录',
  },
  {
    label: '活跃',
    kind: '补充标签',
    rule: '最新交易日期距当前日期 <= 3 天时命中活跃；<= 1 天为正常活跃，2-3 天为低活跃。',
    fields: '最新交易日期、当前日期、活跃窗口',
  },
  {
    label: '新钱包',
    kind: '补充标签',
    rule: '注册日期或首笔可验证链上交易日期距当前日期 < 2 个月时命中新钱包；< 10 天时命中隐藏高手新钱包。',
    fields: '注册地址、首笔链上交易日期、当前日期、钱包年龄',
  },
  {
    label: '提前埋伏',
    kind: '补充标签',
    rule: '仅统计某日最高温气温市场交易记录；买入日期与对应最高温当日不同日的交易记录占比 > 50% 时命中。',
    fields: '最高温市场记录、买入日期、最高温日期、不同日买入占比',
  },
];

export function RuleConfig() {
  const [config, setConfig] = useState<Record<string, any>>();
  const [rules, setRules] = useState<LabelRule[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>();
  const [editorText, setEditorText] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();

  const selectedRule = useMemo(
    () => rules.find((rule) => String(rule.key || '') === selectedKey),
    [rules, selectedKey],
  );

  const activeRules = rules.filter((rule) => rule.enabled !== false).length;

  const load = async () => {
    setLoading(true);
    setError(undefined);
    try {
      const payload = await getDefaultConfig();
      const nextRules = normalizeRules(payload.labels);
      setConfig(payload);
      setRules(nextRules);
      setSelectedKey((current) => current && nextRules.some((rule) => rule.key === current) ? current : nextRules[0]?.key);
      setMessage(undefined);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    setEditorText(selectedRule ? formatRule(selectedRule) : '');
    setDirty(false);
  }, [selectedRule]);

  const persistRules = async (nextRules: LabelRule[], successMessage: string) => {
    if (!config) return;
    setSaving(true);
    setError(undefined);
    try {
      const nextConfig = {...config, labels: nextRules};
      await saveDefaultConfig(nextConfig);
      setConfig(nextConfig);
      setRules(nextRules);
      setMessage(successMessage);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const addRule = () => {
    const key = uniqueRuleKey(rules);
    const nextRule: LabelRule = {
      key,
      display_name: '自定义标签',
      description: '按自定义指标条件命中。',
      enabled: true,
      all: [{field: 'trade_count', op: '>=', value: 1}],
    };
    setRules((current) => [...current, nextRule]);
    setSelectedKey(key);
    setEditorText(formatRule(nextRule));
    setMessage('新规则已创建，保存后会写入底层配置。');
    setError(undefined);
    setDirty(true);
  };

  const saveSelectedRule = async () => {
    if (!config) return;
    let parsed: LabelRule;
    try {
      parsed = JSON.parse(editorText);
    } catch (err) {
      setError(err instanceof Error ? `JSON 格式错误：${err.message}` : 'JSON 格式错误');
      return;
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('规则必须是一个 JSON 对象。');
      return;
    }
    const key = String(parsed.key || '').trim();
    if (!key) {
      setError('规则必须包含 key。');
      return;
    }
    const duplicate = rules.some((rule) => String(rule.key || '') === key && String(rule.key || '') !== selectedKey);
    if (duplicate) {
      setError(`key「${key}」已经存在。`);
      return;
    }

    const nextRules = selectedKey && rules.some((rule) => String(rule.key || '') === selectedKey)
      ? rules.map((rule) => (String(rule.key || '') === selectedKey ? parsed : rule))
      : [...rules, parsed];
    await persistRules(nextRules, `规则「${parsed.display_name || key}」已保存到底层配置。`);
    setSelectedKey(key);
  };

  const deleteSelectedRule = async () => {
    if (!selectedRule) return;
    const nextRules = rules.filter((rule) => String(rule.key || '') !== selectedKey);
    setSelectedKey(nextRules[0]?.key);
    await persistRules(nextRules, `规则「${selectedRule.display_name || selectedRule.key}」已删除，并已写入底层配置。`);
  };

  const clearRules = async () => {
    setSelectedKey(undefined);
    setEditorText('');
    await persistRules([], '所有默认标签规则已清空，并已写入底层配置。');
  };

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">标签规则</h1>
          <p className="mt-1 text-sm text-slate-500">系统核心标签固定按下方口径判断；补充标签保存在默认配置里。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading || saving}
            className="inline-flex h-10 items-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCcw className="mr-2 h-4 w-4 text-slate-500" />
            重新读取
          </button>
          <button
            type="button"
            onClick={addRule}
            disabled={loading || saving}
            className="inline-flex h-10 items-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Plus className="mr-2 h-4 w-4" />
            新增规则
          </button>
        </div>
      </div>

      {message && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
      {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="rounded-md border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="text-sm font-semibold text-slate-900">地址标签规则总览</div>
          <div className="mt-1 text-xs text-slate-500">规则命中必须同时满足该标签全部条件；未满足或部分满足均不命中。</div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                <RuleHeader>标签</RuleHeader>
                <RuleHeader>类型</RuleHeader>
                <RuleHeader>判定口径</RuleHeader>
                <RuleHeader>关联字段</RuleHeader>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {ruleDefinitions.map((rule) => (
                <tr key={rule.label}>
                  <td className="w-40 px-5 py-4 align-top text-sm font-semibold text-slate-900">{rule.label}</td>
                  <td className="w-28 px-5 py-4 align-top">
                    <span className={cn('inline-flex rounded border px-2 py-0.5 text-xs font-medium', rule.kind === '系统核心' ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700')}>
                      {rule.kind}
                    </span>
                  </td>
                  <td className="min-w-[420px] px-5 py-4 align-top text-sm leading-6 text-slate-700">{rule.rule}</td>
                  <td className="min-w-[260px] px-5 py-4 align-top text-sm leading-6 text-slate-600">{rule.fields}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid min-h-[620px] grid-cols-1 gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <section className="flex min-h-0 flex-col rounded-md border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-5 py-4">
            <div className="text-sm font-semibold text-slate-900">当前规则</div>
            <div className="mt-1 text-xs text-slate-500">{activeRules}/{rules.length} 已启用</div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {loading ? (
              <div className="px-3 py-8 text-center text-sm text-slate-500">正在读取规则...</div>
            ) : rules.length ? (
              rules.map((rule) => {
                const key = String(rule.key || '');
                const selected = key === selectedKey;
                return (
                  <button
                    key={key || rule.display_name}
                    type="button"
                    onClick={() => setSelectedKey(key)}
                    className={cn(
                      'mb-1 block w-full rounded-md px-3 py-3 text-left transition-colors',
                      selected ? 'bg-blue-50 text-blue-700' : 'hover:bg-slate-50',
                    )}
                  >
                    <span className="block truncate text-sm font-medium">{rule.display_name || key || '未命名规则'}</span>
                    <span className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                      <span className="truncate font-mono">{key || '-'}</span>
                      <span className={rule.enabled === false ? 'text-slate-400' : 'text-emerald-600'}>
                        {rule.enabled === false ? '停用' : '启用'}
                      </span>
                    </span>
                  </button>
                );
              })
            ) : (
              <div className="px-3 py-8 text-center text-sm text-slate-500">当前没有默认标签规则。</div>
            )}
          </div>
          <div className="border-t border-slate-100 p-3">
            <button
              type="button"
              onClick={clearRules}
              disabled={!rules.length || saving || loading}
              className="inline-flex h-10 w-full items-center justify-center rounded-md border border-red-200 bg-white px-3 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 className="mr-2 h-4 w-4" />
              清空全部规则
            </button>
          </div>
        </section>

        <section className="flex min-h-0 flex-col rounded-md border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-sm font-semibold text-slate-900">{selectedRule?.display_name || '规则 JSON'}</div>
              <div className="mt-1 font-mono text-xs text-slate-500">{selectedRule?.key || '未选择规则'}</div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={deleteSelectedRule}
                disabled={!selectedRule || saving || loading}
                className="inline-flex h-10 items-center rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Trash2 className="mr-2 h-4 w-4 text-slate-500" />
                删除
              </button>
              <button
                type="button"
                onClick={saveSelectedRule}
                disabled={!editorText.trim() || saving || loading || !dirty}
                className="inline-flex h-10 items-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Save className="mr-2 h-4 w-4" />
                {saving ? '保存中...' : '保存规则'}
              </button>
            </div>
          </div>
          <textarea
            value={editorText}
            onChange={(event) => {
              setEditorText(event.target.value);
              setDirty(true);
              setError(undefined);
              setMessage(undefined);
            }}
            disabled={loading}
            spellCheck={false}
            className="min-h-[520px] flex-1 resize-none border-0 bg-slate-950 p-5 font-mono text-sm leading-6 text-slate-50 outline-none ring-0 placeholder:text-slate-500 disabled:bg-slate-100 disabled:text-slate-400"
            placeholder="选择或新增一条规则后编辑 JSON。"
          />
        </section>
      </div>
    </div>
  );
}

function normalizeRules(value: unknown): LabelRule[] {
  return Array.isArray(value)
    ? value.filter((item): item is LabelRule => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    : [];
}

function formatRule(rule: LabelRule): string {
  return JSON.stringify(rule, null, 2);
}

function RuleHeader({children}: {children: ReactNode}) {
  return <th className="px-5 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-500">{children}</th>;
}

function uniqueRuleKey(rules: LabelRule[]): string {
  let index = rules.length + 1;
  let key = `custom_label_${index}`;
  const existing = new Set(rules.map((rule) => String(rule.key || '')));
  while (existing.has(key)) {
    index += 1;
    key = `custom_label_${index}`;
  }
  return key;
}
