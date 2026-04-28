import {ChevronDown, Info, RotateCcw, Save, SlidersHorizontal, Zap} from 'lucide-react';
import {useEffect, useMemo, useState} from 'react';
import type {ReactNode} from 'react';
import {type CreateRunInput, getDefaultConfig, saveDefaultConfig, startRun} from '../lib/api';
import {cn} from '../lib/utils';

type FormState = Required<Omit<CreateRunInput, 'chain_api_key_env' | 'max_fetch_limit'>>;

const fallbackForm: FormState = {
  name: '',
  target_count: 10,
  min_pnl: 0.01,
  max_pnl: 200,
  min_volume: 0,
  max_volume: 40000,
  min_traded_count: 11,
  max_traded_count: 99,
  min_weather_trade_ratio: 0.5,
  fetch_limit: 100,
  max_weather_events: 1000,
  max_wallet_offset: 10000,
  concurrent_wallets: 4,
  use_cache: true,
  enable_chain_validation: false,
  verbose: false,
};

export function NewTask({onRunCreated}: {onRunCreated: (runId: string) => void}) {
  const [config, setConfig] = useState<Record<string, any>>();
  const [form, setForm] = useState<FormState>(fallbackForm);
  const [loading, setLoading] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    getDefaultConfig()
      .then((payload) => {
        if (cancelled) return;
        setConfig(payload);
        setForm(configToForm(payload));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const validationError = useMemo(() => validateForm(form), [form]);
  const budgetNote = useMemo(() => describeBudgetNote(form, config), [form, config]);
  const canSubmit = !validationError;

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({...current, [key]: value}));
    setMessage(undefined);
    setError(undefined);
  };

  const reset = () => {
    setForm(config ? configToForm(config) : fallbackForm);
    setMessage('已恢复为默认配置。');
    setError(undefined);
  };

  const submit = async () => {
    if (validationError) {
      setError(validationError);
      return;
    }
    setSubmitting(true);
    setError(undefined);
    try {
      const run = await startRun(form);
      onRunCreated(run.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const saveConfig = async () => {
    if (!config) return;
    if (validationError) {
      setError(validationError);
      return;
    }
    setSaving(true);
    setError(undefined);
    try {
      const nextConfig = applyFormToConfig(config, form);
      await saveDefaultConfig(nextConfig);
      setConfig(nextConfig);
      setMessage('默认配置已保存。');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">新建分析</h1>
          <p className="mt-1 text-sm text-slate-500">先确认分析条件，再启动任务；启动后会自动进入运行状态页。</p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 shadow-sm">
          下一步：<span className="font-medium text-slate-900">确认并启动分析</span>
        </div>
      </div>

      {loading ? (
        <div className="rounded-md border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
          正在读取默认配置...
        </div>
      ) : (
        <form className="space-y-6" onSubmit={(event) => event.preventDefault()}>
          <SettingSection title="任务信息" description="名称可选；留空时系统会自动按时间生成分析记录。">
            <TextField
              label="分析名称"
              value={form.name}
              placeholder="例如：天气盈利日榜地址筛选"
              onChange={(value) => update('name', value)}
            />
          </SettingSection>

          <SettingSection title="筛选条件" description="这里决定最终保留哪些钱包，是本次分析最重要的确认项。">
            <NumberField
              label="目标钱包数量"
              help="最终希望入选的钱包数量；它决定结果规模，不等于排行榜抓取范围。"
              value={form.target_count}
              onChange={(value) => update('target_count', value)}
            />
            <MoneyField
              label="最低盈利"
              help="只继续分析历史盈利达到这个金额的钱包。"
              value={form.min_pnl}
              onChange={(value) => update('min_pnl', value)}
            />
            <MoneyField
              label="最高盈利"
              help="只保留盈利不超过这个上限的钱包。"
              value={form.max_pnl}
              onChange={(value) => update('max_pnl', value)}
            />
            <MoneyField
              label="最低交易量"
              help="排除成交规模过小、样本不足的钱包。"
              value={form.min_volume}
              onChange={(value) => update('min_volume', value)}
            />
            <MoneyField
              label="最高交易量"
              help="限制单日成交量上限，避免跑到大资金地址。"
              value={form.max_volume}
              onChange={(value) => update('max_volume', value)}
            />
            <NumberField
              label="最低交易笔数"
              help="交易次数低于这个值的钱包不会进入候选列表。"
              value={form.min_traded_count}
              onChange={(value) => update('min_traded_count', value)}
            />
            <NumberField
              label="最高交易笔数"
              help="交易次数超过这个上限的钱包不会进入候选列表。"
              value={form.max_traded_count}
              onChange={(value) => update('max_traded_count', value)}
            />
            <NumberField
              label={'\u5929\u6c14\u8d5b\u9053\u4ea4\u6613\u5360\u6bd4\u4e0b\u9650'}
              help={'\u6309\u4ea4\u6613\u7b14\u6570\u5360\u6bd4\u8ba1\u7b97\uff0c0.5 \u8868\u793a 50%\u3002'}
              value={form.min_weather_trade_ratio}
              onChange={(value) => update('min_weather_trade_ratio', value)}
              min={0}
              max={1}
              step={0.05}
            />
          </SettingSection>

          <section className="rounded-md border border-slate-200 bg-white shadow-sm">
            <button
              type="button"
              onClick={() => setAdvancedOpen((value) => !value)}
              className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <SlidersHorizontal className="h-4 w-4 text-slate-400" />
                  <h2 className="text-base font-semibold text-slate-900">数据范围与分析预算</h2>
                </div>
                <p className="mt-1 text-sm text-slate-500">
                  这里控制候选池大小、事件索引范围和并发预算；默认值适合日常使用。
                </p>
              </div>
              <div className="flex flex-shrink-0 items-center gap-3">
                <span className="hidden text-xs text-slate-500 sm:inline">
                  首轮前 {form.fetch_limit} 名 · 目标入选 {form.target_count} 个 · 并发 {form.concurrent_wallets}
                </span>
                <ChevronDown className={cn('h-4 w-4 text-slate-400 transition-transform', advancedOpen && 'rotate-180')} />
              </div>
            </button>

            {advancedOpen && (
              <div className="border-t border-slate-100 px-6 py-6">
                <BudgetNote tone={budgetNote.tone} title={budgetNote.title} body={budgetNote.body} />
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                  <NumberField
                    label="首轮排行榜范围（前 N 名）"
                    help="先从排行榜前 N 个钱包开始筛选；如果后端还没选满目标钱包，会继续按页扩到系统预算上限。"
                    value={form.fetch_limit}
                    onChange={(value) => update('fetch_limit', value)}
                  />
                  <NumberField
                    label="天气事件上限"
                    help="最多索引多少个天气相关市场。"
                    value={form.max_weather_events}
                    onChange={(value) => update('max_weather_events', value)}
                  />
                  <NumberField
                    label="钱包分页上限"
                    help="限制深分页范围，避免单次运行过慢。"
                    value={form.max_wallet_offset}
                    onChange={(value) => update('max_wallet_offset', value)}
                  />
                  <NumberField
                    label="并发钱包数量"
                    help="同时分析的钱包数量，过高可能触发接口限流。"
                    value={form.concurrent_wallets}
                    onChange={(value) => update('concurrent_wallets', value)}
                  />
                </div>
                <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
                  <Toggle label="启用缓存" checked={form.use_cache} onChange={(value) => update('use_cache', value)} />
                  <Toggle
                    label="启用链上校验"
                    checked={form.enable_chain_validation}
                    onChange={(value) => update('enable_chain_validation', value)}
                  />
                  <Toggle label="详细日志" checked={form.verbose} onChange={(value) => update('verbose', value)} />
                </div>
              </div>
            )}
          </section>

          {message && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
          {error && <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

          <div className="flex flex-col gap-3 rounded-md border border-slate-200 bg-white px-6 py-4 shadow-sm md:flex-row md:items-center md:justify-between">
            <button
              type="button"
              onClick={reset}
              disabled={loading}
              className="inline-flex h-10 items-center justify-center rounded-md px-3 text-sm font-medium text-slate-500 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw className="mr-2 h-4 w-4" />
              恢复默认值
            </button>
            <div className="flex flex-col gap-3 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={saveConfig}
                disabled={saving || loading || !config}
                className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Save className="mr-2 h-4 w-4" />
                {saving ? '保存中...' : '保存为默认配置'}
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={!canSubmit || submitting || loading}
                className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-5 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Zap className="mr-2 h-4 w-4" />
                {submitting ? '启动中...' : '确认并启动分析'}
              </button>
            </div>
          </div>
        </form>
      )}
    </div>
  );
}

function configToForm(config: Record<string, any>): FormState {
  return {
    ...fallbackForm,
    target_count: Number(config.wallet_filter?.target_count ?? fallbackForm.target_count),
    min_pnl: Number(config.wallet_filter?.min_pnl ?? fallbackForm.min_pnl),
    max_pnl: Number(config.wallet_filter?.max_pnl ?? fallbackForm.max_pnl),
    min_volume: Number(config.wallet_filter?.min_volume ?? fallbackForm.min_volume),
    max_volume: Number(config.wallet_filter?.max_volume ?? fallbackForm.max_volume),
    min_traded_count: Number(config.wallet_filter?.min_traded_count ?? fallbackForm.min_traded_count),
    max_traded_count: Number(config.wallet_filter?.max_traded_count ?? fallbackForm.max_traded_count),
    min_weather_trade_ratio: Number(
      config.wallet_filter?.min_weather_trade_ratio ?? fallbackForm.min_weather_trade_ratio,
    ),
    fetch_limit: Number(config.leaderboard?.fetch_limit ?? fallbackForm.fetch_limit),
    max_weather_events: Number(config.weather?.max_events ?? fallbackForm.max_weather_events),
    max_wallet_offset: Number(config.pagination?.max_offset ?? fallbackForm.max_wallet_offset),
    concurrent_wallets: Number(config.analysis?.concurrent_wallets ?? fallbackForm.concurrent_wallets),
    use_cache: Boolean(config.api?.use_cache ?? fallbackForm.use_cache),
    enable_chain_validation: Boolean(config.chain_validation?.enabled ?? fallbackForm.enable_chain_validation),
    verbose: Boolean(config.runtime?.verbose ?? config.logging?.verbose ?? fallbackForm.verbose),
  };
}

function applyFormToConfig(config: Record<string, any>, form: FormState): Record<string, any> {
  const next = structuredClone(config);
  next.wallet_filter = {
    ...(next.wallet_filter || {}),
    target_count: form.target_count,
    min_pnl: form.min_pnl,
    max_pnl: form.max_pnl,
    min_volume: form.min_volume,
    max_volume: form.max_volume,
    min_traded_count: form.min_traded_count,
    max_traded_count: form.max_traded_count,
    min_weather_trade_ratio: form.min_weather_trade_ratio,
  };
  next.leaderboard = {...(next.leaderboard || {}), fetch_limit: form.fetch_limit};
  next.weather = {...(next.weather || {}), max_events: form.max_weather_events};
  next.pagination = {...(next.pagination || {}), max_offset: form.max_wallet_offset};
  next.analysis = {...(next.analysis || {}), concurrent_wallets: form.concurrent_wallets};
  next.api = {...(next.api || {}), use_cache: form.use_cache};
  next.chain_validation = {...(next.chain_validation || {}), enabled: form.enable_chain_validation};
  next.runtime = {...(next.runtime || {}), verbose: form.verbose};
  return next;
}

function validateForm(form: FormState): string | null {
  if (form.target_count <= 0) return '目标钱包数量必须大于 0。';
  if (form.fetch_limit <= 0) return '首轮排行榜范围必须大于 0。';
  if (form.max_weather_events <= 0) return '天气事件上限必须大于 0。';
  if (form.max_wallet_offset <= 0) return '钱包分页上限必须大于 0。';
  if (form.concurrent_wallets <= 0) return '并发钱包数量必须大于 0。';
  if (form.min_pnl < 0 || form.max_pnl < 0) return '盈利区间不能小于 0。';
  if (form.max_pnl < form.min_pnl) return '最高盈利不能小于最低盈利。';
  if (form.min_volume < 0 || form.max_volume < 0) return '交易量区间不能小于 0。';
  if (form.max_volume < form.min_volume) return '最高交易量不能小于最低交易量。';
  if (form.min_traded_count < 0 || form.max_traded_count < 0) return '交易笔数区间不能小于 0。';
  if (form.max_traded_count < form.min_traded_count) return '最高交易笔数不能小于最低交易笔数。';
  if (form.min_weather_trade_ratio < 0 || form.min_weather_trade_ratio > 1) {
    return '\u5929\u6c14\u8d5b\u9053\u4ea4\u6613\u5360\u6bd4\u4e0b\u9650\u5fc5\u987b\u5728 0 \u5230 1 \u4e4b\u95f4\u3002';
  }
  return null;
}

function describeBudgetNote(
  form: FormState,
  config?: Record<string, any>,
): {tone: 'blue' | 'amber' | 'emerald'; title: string; body: string} {
  const leaderboard = config?.leaderboard || {};
  const autoExtendToTarget = Boolean(leaderboard.auto_extend_to_target ?? true);
  const maxFetchLimit = Number(leaderboard.max_fetch_limit);
  const hasMaxFetchLimit = Number.isFinite(maxFetchLimit) && maxFetchLimit > 0;

  if (hasMaxFetchLimit && form.target_count > maxFetchLimit) {
    return {
      tone: 'amber',
      title: '目标数量高于系统预算上限',
      body: `当前目标是入选 ${form.target_count} 个钱包，但后端最多只会把排行榜候选扩到 ${maxFetchLimit} 个；这种情况下通常很难选满结果。`,
    };
  }

  if (autoExtendToTarget && hasMaxFetchLimit && maxFetchLimit > form.fetch_limit) {
    return {
      tone: 'blue',
      title: '当前填写的是首轮范围，不是最终上限',
      body: `系统会先从排行榜前 ${form.fetch_limit} 个钱包开始筛选；如果还没找到 ${form.target_count} 个入选钱包，会继续按页扩展，最多扩到 ${maxFetchLimit} 个候选。`,
    };
  }

  return {
    tone: 'emerald',
    title: '当前范围与预算较均衡',
    body: `系统会先从排行榜前 ${form.fetch_limit} 个钱包开始筛选，找到 ${form.target_count} 个入选钱包后就会提前停止；最终结果规模仍由目标钱包数量决定。`,
  };
}

function BudgetNote({
  tone,
  title,
  body,
}: {
  tone: 'blue' | 'amber' | 'emerald';
  title: string;
  body: string;
}) {
  const toneClass = {
    blue: 'border-blue-200 bg-blue-50 text-blue-800',
    amber: 'border-amber-200 bg-amber-50 text-amber-800',
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  }[tone];

  return (
    <div className={cn('mb-6 rounded-md border px-4 py-3', toneClass)}>
      <div className="flex gap-3">
        <Info className="mt-0.5 h-4 w-4 flex-none" />
        <div className="space-y-1">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-sm leading-6">{body}</p>
        </div>
      </div>
    </div>
  );
}

function SettingSection({title, description, children}: {title: string; description: string; children: ReactNode}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-6 py-5">
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        <p className="mt-1 text-sm text-slate-500">{description}</p>
      </div>
      <div className="divide-y divide-slate-100 px-6">{children}</div>
    </section>
  );
}

function FieldRow({label, help, children}: {label: string; help?: string; children: ReactNode}) {
  return (
    <div className="grid grid-cols-1 gap-3 py-5 md:grid-cols-[190px_minmax(0,1fr)] md:items-start md:gap-6">
      <div>
        <label className="text-sm font-medium text-slate-800">{label}</label>
        {help && <p className="mt-1 text-xs leading-5 text-slate-500">{help}</p>}
      </div>
      {children}
    </div>
  );
}

function TextField({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <FieldRow label={label}>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="block h-10 w-full max-w-xl rounded-md border-0 px-3 text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-[#2E5CFF] sm:text-sm"
      />
    </FieldRow>
  );
}

function NumberField({
  label,
  help,
  value,
  onChange,
  min = 0,
  max,
  step = 1,
}: {
  label: string;
  help?: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <FieldRow label={label} help={help}>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="block h-10 w-full max-w-[180px] rounded-md border-0 px-3 text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-inset focus:ring-[#2E5CFF] sm:text-sm"
      />
    </FieldRow>
  );
}

function MoneyField({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help?: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <FieldRow label={label} help={help}>
      <div className="relative w-full max-w-[180px] rounded-md shadow-sm">
        <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
          <span className="text-slate-500 sm:text-sm">$</span>
        </div>
        <input
          type="number"
          min={0}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="block h-10 w-full rounded-md border-0 pl-7 pr-3 text-slate-900 ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-inset focus:ring-[#2E5CFF] sm:text-sm"
        />
      </div>
    </FieldRow>
  );
}

function Toggle({label, checked, onChange}: {label: string; checked: boolean; onChange: (value: boolean) => void}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded-md border border-slate-200 px-4 py-3">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <span className="relative inline-flex cursor-pointer items-center">
        <input type="checkbox" className="peer sr-only" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-slate-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-[#2E5CFF] peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300" />
      </span>
    </label>
  );
}
