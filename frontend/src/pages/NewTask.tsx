import {ChevronDown, Info, RotateCcw, Save, SlidersHorizontal, Upload, X, Zap} from 'lucide-react';
import {useEffect, useMemo, useState} from 'react';
import type {ChangeEvent, ReactNode} from 'react';
import {
  type ActivityFilterMode,
  type AnalysisMode,
  type CreateRunInput,
  type DeepSeekRelayFilter,
  type RelayCoreLabelFilter,
  type RelayImportBuildResult,
  type RunRecord,
  buildRelayImportPayload,
  getDefaultConfig,
  listRuns,
  runDisplayName,
  saveDefaultConfig,
  startRun,
} from '../lib/api';
import {cn} from '../lib/utils';

type FormState = Required<
  Pick<
    CreateRunInput,
    | 'analysis_mode'
    | 'activity_filter_mode'
    | 'name'
    | 'target_count'
    | 'min_pnl'
    | 'max_pnl'
    | 'min_volume'
    | 'max_volume'
    | 'min_traded_count'
    | 'max_traded_count'
    | 'min_weather_trade_ratio'
    | 'fetch_limit'
    | 'max_weather_events'
    | 'max_wallet_offset'
    | 'concurrent_wallets'
    | 'use_cache'
    | 'enable_chain_validation'
    | 'verbose'
  >
>;

const analysisModeOptions: Array<{value: AnalysisMode; label: string; description: string}> = [
  {
    value: 'standard',
    label: '普通分析',
    description: '按日常排行榜链路抓取候选地址，并按筛选条件完成分析。',
  },
  {
    value: 'weekly_high_profit',
    label: '本周高盈利榜单',
    description: '从本周高盈利榜单抓取候选地址，并按周维度指标完成分析。',
  },
  {
    value: 'smart_wallet_library_refresh',
    label: '后台地址库刷新',
    description: '导入后台地址库，对名单内地址重新抓取并刷新分析结果。',
  },
  {
    value: 'relay_analysis',
    label: '接力分析',
    description: '从历史运行的原始全量地址池接力，可按系统核心标签和 DeepSeek 状态筛选。',
  },
];

const activityFilterOptions: Array<{value: ActivityFilterMode; label: string; description: string}> = [
  {
    value: 'all',
    label: '不筛选',
    description: '不过滤活跃度，导入地址全部参与本轮刷新。',
  },
  {
    value: 'normal_active',
    label: '仅正常活跃',
    description: '只刷新近期仍处于正常活跃状态的地址。',
  },
  {
    value: 'inactive',
    label: '仅不活跃',
    description: '只刷新近期不活跃的地址，方便回看沉寂样本。',
  },
];

const deepSeekRelayFilterOptions: Array<{value: DeepSeekRelayFilter; label: string; description: string}> = [
  {
    value: 'all',
    label: '全部地址',
    description: '不按 DeepSeek 状态筛选，使用来源运行里的全部钱包。',
  },
  {
    value: 'incomplete',
    label: '未完成 DeepSeek',
    description: '只接力还没有 generated/cached 深度解读的钱包。',
  },
  {
    value: 'completed',
    label: '已完成 DeepSeek',
    description: '只接力已经完成 generated/cached 深度解读的钱包。',
  },
];

const relayCoreLabelFilterOptions: Array<{value: RelayCoreLabelFilter; label: string; description: string}> = [
  {
    value: 'all',
    label: '全部标签状态',
    description: '不按系统核心标签状态筛选，使用来源运行里的全部钱包。',
  },
  {
    value: 'core',
    label: '已打系统核心标签',
    description: '只接力来源结果里已经命中系统核心标签的钱包。',
  },
  {
    value: 'non_core',
    label: '未打系统核心标签',
    description: '只接力来源结果里还没有命中系统核心标签的钱包。',
  },
];

type SmartWalletImportSummary = {
  detectedCount: number;
  validAddressCount: number;
  namedAddressCount: number;
  addressOnlyCount: number;
  latestUpdatedAt?: string;
  previewPairs: Array<{name: string; address: string}>;
};

type RelayImportPreview = RelayImportBuildResult & {
  sourceRunId: string;
  coreLabelFilter: RelayCoreLabelFilter;
  deepSeekFilter: DeepSeekRelayFilter;
};

type SmartWalletImportState = {
  fileName?: string;
  payload?: unknown;
  summary?: SmartWalletImportSummary;
};

const fallbackForm: FormState = {
  analysis_mode: 'standard',
  activity_filter_mode: 'all',
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
  max_weather_events: 100000,
  max_wallet_offset: 10000,
  concurrent_wallets: 4,
  use_cache: true,
  enable_chain_validation: false,
  verbose: false,
};

export function NewTask({onRunCreated}: {onRunCreated: (runId: string) => void}) {
  const [config, setConfig] = useState<Record<string, any>>();
  const [form, setForm] = useState<FormState>(fallbackForm);
  const [smartWalletImport, setSmartWalletImport] = useState<SmartWalletImportState>({});
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [relaySourceRunId, setRelaySourceRunId] = useState('');
  const [relayCoreLabelFilter, setRelayCoreLabelFilter] = useState<RelayCoreLabelFilter>('all');
  const [deepSeekRelayFilter, setDeepSeekRelayFilter] = useState<DeepSeekRelayFilter>('all');
  const [relayImportPreview, setRelayImportPreview] = useState<RelayImportPreview>();
  const [loading, setLoading] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [importingFile, setImportingFile] = useState(false);
  const [buildingRelayImport, setBuildingRelayImport] = useState(false);
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

  useEffect(() => {
    let cancelled = false;
    listRuns()
      .then((items) => {
        if (cancelled) return;
        setRuns(items);
        const firstReadable = items.find((item) => item.wallet_detail_count || item.selected_wallet_count);
        if (firstReadable) {
          setRelaySourceRunId((current) => current || firstReadable.run_id);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const isSmartWalletImportMode = form.analysis_mode === 'smart_wallet_library_refresh';
  const isRelayAnalysisMode = form.analysis_mode === 'relay_analysis';
  const isImportedWalletMode = isSmartWalletImportMode || isRelayAnalysisMode;
  const validationError = useMemo(() => validateForm(form), [form]);
  const smartWalletImportNotice = useMemo(
    () =>
      isSmartWalletImportMode && smartWalletImport.fileName
        ? describeSmartWalletImportNotice(smartWalletImport.summary)
        : null,
    [isSmartWalletImportMode, smartWalletImport.fileName, smartWalletImport.summary],
  );
  const smartWalletImportValidationError = useMemo(
    () =>
      isSmartWalletImportMode && smartWalletImport.fileName
        ? validateSmartWalletImport(smartWalletImport.summary, smartWalletImport.payload)
        : null,
    [isSmartWalletImportMode, smartWalletImport.fileName, smartWalletImport.summary, smartWalletImport.payload],
  );
  const relayImportValidationError = useMemo(
    () =>
      isRelayAnalysisMode && relayImportPreview
        ? validateRelayImport(relayImportPreview)
        : null,
    [isRelayAnalysisMode, relayImportPreview],
  );
  const submitValidationError = useMemo(
    () => validateSubmit(form, smartWalletImport.summary, smartWalletImport.payload, {
      sourceRunId: relaySourceRunId,
      preview: relayImportPreview,
      coreLabelFilter: relayCoreLabelFilter,
      deepSeekFilter: deepSeekRelayFilter,
    }),
    [
      form,
      smartWalletImport.summary,
      smartWalletImport.payload,
      relaySourceRunId,
      relayImportPreview,
      relayCoreLabelFilter,
      deepSeekRelayFilter,
    ],
  );
  const budgetNote = useMemo(() => describeBudgetNote(form, config), [form, config]);
  const canSubmit = !submitValidationError;

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({...current, [key]: value}));
    setMessage(undefined);
    setError(undefined);
  };

  const updateAnalysisMode = (value: AnalysisMode) => {
    setForm((current) => {
      const modeDefaults = config ? configToForm(config, value) : {...fallbackForm, analysis_mode: value};
      return {
        ...modeDefaults,
        name: current.name,
        analysis_mode: value,
        activity_filter_mode: value === 'relay_analysis' ? 'all' : modeDefaults.activity_filter_mode,
      };
    });
    setSmartWalletImport({});
    setRelayImportPreview(undefined);
    setMessage(undefined);
    setError(undefined);
  };

  const clearRelayImportForFilterChange = () => {
    setRelayImportPreview(undefined);
    setMessage(undefined);
    setError(undefined);
  };

  const updateRelaySourceRunId = (value: string) => {
    setRelaySourceRunId(value);
    clearRelayImportForFilterChange();
  };

  const updateRelayCoreLabelFilter = (value: RelayCoreLabelFilter) => {
    setRelayCoreLabelFilter(value);
    clearRelayImportForFilterChange();
  };

  const updateDeepSeekRelayFilter = (value: DeepSeekRelayFilter) => {
    setDeepSeekRelayFilter(value);
    clearRelayImportForFilterChange();
  };

  const clearSmartWalletImport = () => {
    setSmartWalletImport({});
    setRelayImportPreview(undefined);
    setMessage(undefined);
    setError(undefined);
  };

  const handleSmartWalletImportChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;

    setImportingFile(true);
    setMessage(undefined);
    setError(undefined);

    try {
      const text = await file.text();
      const payload = JSON.parse(text) as unknown;
      setSmartWalletImport({
        fileName: file.name,
        payload,
        summary: summarizeSmartWalletImport(payload),
      });
    } catch (err) {
      setSmartWalletImport({});
      setError(err instanceof Error ? `地址库导入失败：${err.message}` : '地址库导入失败，请确认 JSON 格式正确。');
    } finally {
      setImportingFile(false);
      input.value = '';
    }
  };

  const buildRelayImport = async () => {
    if (!relaySourceRunId) {
      setError('请先选择一个已有分析运行。');
      return;
    }
    setBuildingRelayImport(true);
    setMessage(undefined);
    setError(undefined);
    try {
      const result = await buildRelayImportPayload({
        sourceRunId: relaySourceRunId,
        coreLabelFilter: relayCoreLabelFilter,
        deepSeekFilter: deepSeekRelayFilter,
      });
      if ((result.matched_count || 0) <= 0) {
        setRelayImportPreview(undefined);
        setError('当前筛选条件下没有可接力的钱包。');
        return;
      }
      setRelayImportPreview({
        ...result,
        sourceRunId: relaySourceRunId,
        coreLabelFilter: relayCoreLabelFilter,
        deepSeekFilter: deepSeekRelayFilter,
      });
      setMessage(`已确认接力来源：原始地址 ${result.source_total} 个，当前筛选命中 ${result.matched_count} 个钱包。`);
    } catch (err) {
      setError(err instanceof Error ? `接力来源确认失败：${err.message}` : '接力来源确认失败。');
    } finally {
      setBuildingRelayImport(false);
    }
  };

  const reset = () => {
    setForm(config ? configToForm(config) : fallbackForm);
    setSmartWalletImport({});
    setRelayImportPreview(undefined);
    setMessage('已恢复为默认配置。');
    setError(undefined);
  };

  const submit = async () => {
    if (submitValidationError) {
      setError(submitValidationError);
      return;
    }
    setSubmitting(true);
    setError(undefined);
    try {
      const input: CreateRunInput = isRelayAnalysisMode
        ? {
            ...form,
            relay_import: {
              sourceRunId: relaySourceRunId,
              coreLabelFilter: relayCoreLabelFilter,
              deepSeekFilter: deepSeekRelayFilter,
            },
          }
        : isSmartWalletImportMode
          ? {
              ...form,
              wallet_import_payload: smartWalletImport.payload,
              wallet_import_file_name: smartWalletImport.fileName,
            }
          : form;
      const run = await startRun(input);
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
            <AnalysisModeField
              value={form.analysis_mode}
              onChange={updateAnalysisMode}
            />
            <TextField
              label="分析名称"
              value={form.name}
              placeholder="例如：天气盈利日榜地址筛选"
              onChange={(value) => update('name', value)}
            />
          </SettingSection>

          {isSmartWalletImportMode && (
            <SettingSection
              title="后台地址库输入"
              description="上传后台地址库导出的 JSON，作为这次地址库刷新任务的输入源。"
            >
              <SmartWalletImportField
                fileName={smartWalletImport.fileName}
                summary={smartWalletImport.summary}
                importing={importingFile}
                noticeMessage={smartWalletImportNotice}
                validationMessage={smartWalletImportValidationError}
                onFileChange={handleSmartWalletImportChange}
                onClear={clearSmartWalletImport}
              />
            </SettingSection>
          )}

          {isRelayAnalysisMode && (
            <SettingSection
              title="接力来源"
              description="从来源运行原始全量地址池确认接力范围，可选系统核心标签和 DeepSeek 状态筛选；启动后仍走统一轻量筛选、核心标签和 DeepSeek gate。"
            >
              <RelayImportField
                runs={runs}
                sourceRunId={relaySourceRunId}
                coreFilter={relayCoreLabelFilter}
                filter={deepSeekRelayFilter}
                loading={buildingRelayImport}
                preview={relayImportPreview}
                validationMessage={relayImportValidationError}
                onSourceRunChange={updateRelaySourceRunId}
                onCoreFilterChange={updateRelayCoreLabelFilter}
                onFilterChange={updateDeepSeekRelayFilter}
                onBuild={buildRelayImport}
                onClear={clearSmartWalletImport}
              />
            </SettingSection>
          )}

          {isSmartWalletImportMode && (
            <SettingSection
              title="活跃度筛选"
              description="地址库刷新模式下只保留这一项简单筛选，用来区分正常活跃与不活跃地址。"
            >
              <ActivityFilterField
                value={form.activity_filter_mode}
                onChange={(value) => update('activity_filter_mode', value)}
              />
            </SettingSection>
          )}

          {!isImportedWalletMode && (
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
          )}

          <section className="rounded-md border border-slate-200 bg-white shadow-sm">
            <button
              type="button"
              onClick={() => setAdvancedOpen((value) => !value)}
              className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <SlidersHorizontal className="h-4 w-4 text-slate-400" />
                  <h2 className="text-base font-semibold text-slate-900">
                    {isImportedWalletMode ? '处理范围与运行参数' : '数据范围与分析预算'}
                  </h2>
                </div>
                <p className="mt-1 text-sm text-slate-500">
                  {isImportedWalletMode
                    ? '这里控制输入地址的处理范围、事件索引上限和并发参数。'
                    : '这里控制候选池大小、事件索引范围和并发预算；默认值适合日常使用。'}
                </p>
              </div>
              <div className="flex flex-shrink-0 items-center gap-3">
                <span className="hidden text-xs text-slate-500 sm:inline">
                  {isRelayAnalysisMode
                    ? `接力地址 · DeepSeek ${deepSeekRelayFilterOptions.find((option) => option.value === deepSeekRelayFilter)?.label ?? '全部地址'} · 并发 ${form.concurrent_wallets}`
                    : isSmartWalletImportMode
                      ? `后台地址库 · 活跃度 ${activityFilterOptions.find((option) => option.value === form.activity_filter_mode)?.label ?? '不筛选'} · 并发 ${form.concurrent_wallets}`
                      : `首轮前 ${form.fetch_limit} 名 · 目标入选 ${form.target_count} 个 · 并发 ${form.concurrent_wallets}`}
                </span>
                <ChevronDown className={cn('h-4 w-4 text-slate-400 transition-transform', advancedOpen && 'rotate-180')} />
              </div>
            </button>

            {advancedOpen && (
              <div className="border-t border-slate-100 px-6 py-6">
                <BudgetNote tone={budgetNote.tone} title={budgetNote.title} body={budgetNote.body} />
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                  {!isImportedWalletMode && (
                  <NumberField
                    label="首轮排行榜范围（前 N 名）"
                    help="先从排行榜前 N 个钱包开始筛选；如果后端还没选满目标钱包，会继续按页扩到系统预算上限。"
                    value={form.fetch_limit}
                    onChange={(value) => update('fetch_limit', value)}
                  />
                )}
                  <NumberField
                    label="天气事件上限"
                    help="最多索引多少个天气相关市场。"
                    value={form.max_weather_events}
                    onChange={(value) => update('max_weather_events', value)}
                  />
                  {!isImportedWalletMode && (
                  <NumberField
                    label="钱包分页上限"
                    help="限制深分页范围，避免单次运行过慢。"
                    value={form.max_wallet_offset}
                    onChange={(value) => update('max_wallet_offset', value)}
                  />
                )}
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

function configToForm(config: Record<string, any>, analysisMode: AnalysisMode = 'standard'): FormState {
  const resolvedConfig = configForAnalysisMode(config, analysisMode);
  return {
    ...fallbackForm,
    analysis_mode: analysisMode,
    activity_filter_mode: String(resolvedConfig.wallet_filter?.activity_filter_mode || fallbackForm.activity_filter_mode)
      .trim()
      .toLowerCase() as ActivityFilterMode,
    target_count: Number(resolvedConfig.wallet_filter?.target_count ?? fallbackForm.target_count),
    min_pnl: Number(resolvedConfig.wallet_filter?.min_pnl ?? fallbackForm.min_pnl),
    max_pnl: Number(resolvedConfig.wallet_filter?.max_pnl ?? fallbackForm.max_pnl),
    min_volume: Number(resolvedConfig.wallet_filter?.min_volume ?? fallbackForm.min_volume),
    max_volume: Number(resolvedConfig.wallet_filter?.max_volume ?? fallbackForm.max_volume),
    min_traded_count: Number(resolvedConfig.wallet_filter?.min_traded_count ?? fallbackForm.min_traded_count),
    max_traded_count: Number(resolvedConfig.wallet_filter?.max_traded_count ?? fallbackForm.max_traded_count),
    min_weather_trade_ratio: Number(
      resolvedConfig.wallet_filter?.min_weather_trade_ratio ?? fallbackForm.min_weather_trade_ratio,
    ),
    fetch_limit: Number(resolvedConfig.leaderboard?.fetch_limit ?? fallbackForm.fetch_limit),
    max_weather_events: Number(resolvedConfig.weather?.max_events ?? fallbackForm.max_weather_events),
    max_wallet_offset: Number(resolvedConfig.pagination?.max_offset ?? fallbackForm.max_wallet_offset),
    concurrent_wallets: Number(resolvedConfig.analysis?.concurrent_wallets ?? fallbackForm.concurrent_wallets),
    use_cache: Boolean(resolvedConfig.api?.use_cache ?? fallbackForm.use_cache),
    enable_chain_validation: Boolean(resolvedConfig.chain_validation?.enabled ?? fallbackForm.enable_chain_validation),
    verbose: Boolean(resolvedConfig.runtime?.verbose ?? resolvedConfig.logging?.verbose ?? fallbackForm.verbose),
  };
}

function configForAnalysisMode(config: Record<string, any>, analysisMode: AnalysisMode): Record<string, any> {
  if (analysisMode === 'standard') {
    return config;
  }
  const modeConfig = config.analysis_modes?.[analysisMode];
  if (!isRecord(modeConfig)) {
    return config;
  }
  return mergeConfigSections(config, modeConfig);
}

function mergeConfigSections(base: Record<string, any>, patch: Record<string, any>): Record<string, any> {
  const merged = structuredClone(base);
  for (const [key, value] of Object.entries(patch)) {
    if (isRecord(value) && isRecord(merged[key])) {
      merged[key] = mergeConfigSections(merged[key], value);
    } else {
      merged[key] = structuredClone(value);
    }
  }
  return merged;
}

function applyFormToConfig(config: Record<string, any>, form: FormState): Record<string, any> {
  const next = structuredClone(config);
  const target = configTargetForForm(next, form.analysis_mode);

  target.wallet_filter = {
    ...(target.wallet_filter || {}),
    target_count: form.target_count,
    min_pnl: form.min_pnl,
    max_pnl: form.max_pnl,
    min_volume: form.min_volume,
    max_volume: form.max_volume,
    min_traded_count: form.min_traded_count,
    max_traded_count: form.max_traded_count,
    min_weather_trade_ratio: form.min_weather_trade_ratio,
    activity_filter_mode: form.activity_filter_mode,
  };
  target.leaderboard = {...(target.leaderboard || {}), fetch_limit: form.fetch_limit};
  target.weather = {...(target.weather || {}), max_events: form.max_weather_events};
  target.pagination = {...(target.pagination || {}), max_offset: form.max_wallet_offset};
  target.analysis = {...(target.analysis || {}), concurrent_wallets: form.concurrent_wallets};
  target.api = {...(target.api || {}), use_cache: form.use_cache};
  target.chain_validation = {...(target.chain_validation || {}), enabled: form.enable_chain_validation};
  target.runtime = {...(target.runtime || {}), verbose: form.verbose};
  return next;
}

function configTargetForForm(config: Record<string, any>, analysisMode: AnalysisMode): Record<string, any> {
  if (analysisMode !== 'weekly_high_profit') {
    return config;
  }
  config.analysis_modes = {...(config.analysis_modes || {})};
  config.analysis_modes.weekly_high_profit = {...(config.analysis_modes.weekly_high_profit || {})};
  return config.analysis_modes.weekly_high_profit;
}

function isPositiveNumber(value: number): boolean {
  return Number.isFinite(value) && value > 0;
}

function validateForm(form: FormState): string | null {
  if (form.analysis_mode === 'smart_wallet_library_refresh') {
    if (!activityFilterOptions.some((option) => option.value === form.activity_filter_mode)) {
      return '活跃度筛选选项无效。';
    }
    if (!isPositiveNumber(form.max_weather_events)) return '天气事件上限必须大于 0。';
    if (!isPositiveNumber(form.concurrent_wallets)) return '并发钱包数量必须大于 0。';
    return null;
  }
  if (form.analysis_mode === 'relay_analysis') {
    if (!isPositiveNumber(form.max_weather_events)) return '天气事件上限必须大于 0。';
    if (!isPositiveNumber(form.concurrent_wallets)) return '并发钱包数量必须大于 0。';
    return null;
  }
  if (!activityFilterOptions.some((option) => option.value === form.activity_filter_mode)) {
    return '活跃度筛选选项无效。';
  }
  if (!isPositiveNumber(form.target_count)) return '目标钱包数量必须大于 0。';
  if (!isPositiveNumber(form.fetch_limit)) return '首轮排行榜范围必须大于 0。';
  if (!isPositiveNumber(form.max_weather_events)) return '天气事件上限必须大于 0。';
  if (!isPositiveNumber(form.max_wallet_offset)) return '钱包分页上限必须大于 0。';
  if (!isPositiveNumber(form.concurrent_wallets)) return '并发钱包数量必须大于 0。';
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

function validateSmartWalletImport(summary?: SmartWalletImportSummary, payload?: unknown): string | null {
  if (payload == null) {
    return '请先上传地址库 JSON 文件。';
  }
  if ((summary?.detectedCount ?? 0) <= 0) {
    return '导入文件中未识别到地址库记录，请确认导出文件内容正确。';
  }
  if ((summary?.validAddressCount ?? 0) <= 0) {
    return '导入文件中未识别到有效钱包地址，请确认地址为标准 EVM 地址（0x 开头，42 位长度）。';
  }
  return null;
}

function validateRelayImport(preview?: RelayImportPreview): string | null {
  if (!preview) {
    return '请先确认接力来源。';
  }
  if ((preview.source_total ?? 0) <= 0) {
    return '来源运行里没有可恢复的原始地址池，请换一个历史运行。';
  }
  if ((preview.matched_count ?? 0) <= 0) {
    return '当前筛选条件下没有可接力的钱包。';
  }
  return null;
}

function describeSmartWalletImportNotice(summary?: SmartWalletImportSummary): string | null {
  if ((summary?.validAddressCount ?? 0) <= 0) {
    return null;
  }
  if ((summary?.namedAddressCount ?? 0) <= 0) {
    return '当前文件已识别到有效地址，但未识别到对应用户名或显示名；任务仍可按地址刷新，后续人工确认可能不方便。';
  }
  if ((summary?.addressOnlyCount ?? 0) > 0) {
    return `还有 ${summary?.addressOnlyCount ?? 0} 条地址未识别到用户名或显示名，运行时会继续按地址刷新。`;
  }
  return null;
}

function validateSubmit(
  form: FormState,
  smartWalletImportSummary?: SmartWalletImportSummary,
  smartWalletImportPayload?: unknown,
  relay?: {
    sourceRunId: string;
    preview?: RelayImportPreview;
    coreLabelFilter: RelayCoreLabelFilter;
    deepSeekFilter: DeepSeekRelayFilter;
  },
): string | null {
  const formError = validateForm(form);
  if (formError) return formError;
  if (form.analysis_mode === 'relay_analysis') {
    if (!relay?.sourceRunId) {
      return '请先选择一个历史运行作为接力来源。';
    }
    if (
      !relay.preview ||
      relay.preview.sourceRunId !== relay.sourceRunId ||
      relay.preview.coreLabelFilter !== relay.coreLabelFilter ||
      relay.preview.deepSeekFilter !== relay.deepSeekFilter
    ) {
      return '请先按当前筛选条件确认接力来源。';
    }
    return validateRelayImport(relay.preview);
  }
  if (form.analysis_mode === 'smart_wallet_library_refresh') {
    return validateSmartWalletImport(smartWalletImportSummary, smartWalletImportPayload);
  }
  return null;
}

function describeBudgetNote(
  form: FormState,
  config?: Record<string, any>,
): {tone: 'blue' | 'amber' | 'emerald'; title: string; body: string} {
  if (form.analysis_mode === 'smart_wallet_library_refresh') {
    return {
      tone: 'blue',
      title: '当前将刷新后台地址库',
      body: '系统会按导入地址库逐个刷新分析结果，不套用盈利、交易量和交易笔数筛选；这里只保留活跃度这一项简单筛选。',
    };
  }
  if (form.analysis_mode === 'relay_analysis') {
    return {
      tone: 'blue',
      title: '当前将运行接力分析',
      body: '接力筛选会从来源 run 的原始全量地址池里挑地址，默认不限制标签或 DeepSeek 状态；启动后会重新走统一链路，先轻量筛选和打核心标签，再决定是否进入重链路。',
    };
  }
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function textValue(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value).trim();
  return '';
}

const EVM_ADDRESS_PATTERN = /^0x[a-f0-9]{40}$/;

function normalizeImportAddress(value: unknown): string {
  const text = textValue(value).toLowerCase();
  if (!text) return '';
  const normalized = text.startsWith('0x') ? text : `0x${text}`;
  return EVM_ADDRESS_PATTERN.test(normalized) ? normalized : '';
}

function importWalletRows(payload: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(payload)) {
    return payload.filter(isRecord);
  }
  if (!isRecord(payload)) {
    return [];
  }

  for (const key of ['wallets', 'items', 'records', 'rows']) {
    const value = payload[key];
    if (Array.isArray(value)) {
      return value.filter(isRecord);
    }
  }

  for (const key of ['data', 'payload', 'result']) {
    const nested = importWalletRows(payload[key]);
    if (nested.length > 0) {
      return nested;
    }
  }

  if (
    'wallet' in payload ||
    'address' in payload ||
    'normalizedAddress' in payload ||
    'normalized_address' in payload
  ) {
    return [payload];
  }

  return [];
}

function normalizedAddressFromImportRow(row: Record<string, unknown>): string {
  const wallet = isRecord(row.wallet) ? row.wallet : undefined;
  return normalizeImportAddress(
    wallet?.normalizedAddress ??
      wallet?.normalized_address ??
      wallet?.address ??
      row.normalizedAddress ??
      row.normalized_address ??
      row.address ??
      row.wallet_address,
  );
}

function pickImportIdentityName(row: Record<string, unknown>): string {
  const wallet = isRecord(row.wallet) ? row.wallet : undefined;
  const sourceMeta = isRecord(row.sourceMeta) ? row.sourceMeta : isRecord(row.source_meta) ? row.source_meta : undefined;
  const xUsername = textValue(
    row.xUsername ?? row.x_username ?? sourceMeta?.xUsername ?? sourceMeta?.x_username ?? sourceMeta?.twitter,
  ).replace(/^@+/, '');

  return (
    textValue(row.userName) ||
    textValue(row.user_name) ||
    textValue(sourceMeta?.userName) ||
    textValue(sourceMeta?.username) ||
    textValue(wallet?.displayName) ||
    textValue(wallet?.display_name) ||
    textValue(row.displayName) ||
    textValue(row.display_name) ||
    textValue(wallet?.alias) ||
    textValue(row.alias) ||
    (xUsername ? `@${xUsername}` : '')
  );
}

function latestUpdatedAtFromImportRows(rows: Array<Record<string, unknown>>): string | undefined {
  let latestValue: string | undefined;
  let latestTime = Number.NEGATIVE_INFINITY;

  for (const row of rows) {
    const wallet = isRecord(row.wallet) ? row.wallet : undefined;
    const candidates = [
      textValue(wallet?.updatedAt),
      textValue(wallet?.updated_at),
      textValue(row.updatedAt),
      textValue(row.updated_at),
    ].filter(Boolean);

    for (const candidate of candidates) {
      const parsed = Date.parse(candidate);
      if (Number.isNaN(parsed) || parsed <= latestTime) {
        continue;
      }
      latestTime = parsed;
      latestValue = candidate;
    }
  }

  return latestValue;
}

function relaySourcePoolLabel(value: unknown): string {
  switch (String(value || '')) {
    case 'smart_wallet_import_rows':
      return '原始地址库导入';
    case 'relay_import_rows':
      return '上次接力输入';
    case 'leaderboard':
      return '原始排行榜';
    case 'selected_wallets':
      return '旧版已选快照';
    default:
      return '来源运行';
  }
}

function summarizeSmartWalletImport(payload: unknown): SmartWalletImportSummary {
  const rows = importWalletRows(payload);
  const previewPairs: Array<{name: string; address: string}> = [];
  let validAddressCount = 0;
  let namedAddressCount = 0;

  for (const row of rows) {
    const address = normalizedAddressFromImportRow(row);
    if (!address) {
      continue;
    }
    validAddressCount += 1;
    const name = pickImportIdentityName(row);
    if (!name) {
      continue;
    }
    namedAddressCount += 1;
    if (previewPairs.length < 5) {
      previewPairs.push({name, address});
    }
  }

  return {
    detectedCount: rows.length,
    validAddressCount,
    namedAddressCount,
    addressOnlyCount: Math.max(0, validAddressCount - namedAddressCount),
    latestUpdatedAt: latestUpdatedAtFromImportRows(rows),
    previewPairs,
  };
}

function formatImportTimestamp(value?: string): string {
  if (!value) return '未识别';
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(parsed));
}

function AnalysisModeField({
  value,
  onChange,
}: {
  value: AnalysisMode;
  onChange: (value: AnalysisMode) => void;
}) {
  const selectedMode = analysisModeOptions.find((option) => option.value === value) ?? analysisModeOptions[0];
  const modeNote =
    value === 'weekly_high_profit'
      ? '按周榜单找地址，盈利、交易量和交易笔数都按周口径衡量。'
      : value === 'smart_wallet_library_refresh'
        ? '只处理后台地址库 JSON，用于刷新 Smart Pro/后台钱包库对应地址的分析结果。'
        : value === 'relay_analysis'
          ? '从已有 run 的原始全量地址池接力，可选核心标签和 DeepSeek 状态后重新建立一轮独立分析。'
          : '按日常分析链路运行，盈利、交易量和交易笔数都按日口径衡量。';

  return (
    <FieldRow label="分析模式" help="先确定这次任务走哪条分析链路。">
      <div className="space-y-3">
        <div className="space-y-2">
          {analysisModeOptions.map((option) => {
            const checked = option.value === value;
            return (
              <label
                key={option.value}
                className={cn(
                  'flex cursor-pointer gap-3 rounded-md border px-4 py-3 transition-colors',
                  checked ? 'border-[#2E5CFF] bg-blue-50/60' : 'border-slate-200 hover:border-slate-300',
                )}
              >
                <input
                  type="radio"
                  name="analysis_mode"
                  value={option.value}
                  checked={checked}
                  onChange={() => onChange(option.value)}
                  className="mt-1 h-4 w-4 border-slate-300 text-[#2E5CFF] focus:ring-[#2E5CFF]"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-500">{option.description}</span>
                </span>
              </label>
            );
          })}
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
          <span className="font-medium text-slate-800">{selectedMode.label}</span>
          <span className="ml-2">{modeNote}</span>
        </div>
      </div>
    </FieldRow>
  );
}

function RelayImportField({
  runs,
  sourceRunId,
  coreFilter,
  filter,
  loading,
  preview,
  validationMessage,
  onSourceRunChange,
  onCoreFilterChange,
  onFilterChange,
  onBuild,
  onClear,
}: {
  runs: RunRecord[];
  sourceRunId: string;
  coreFilter: RelayCoreLabelFilter;
  filter: DeepSeekRelayFilter;
  loading: boolean;
  preview?: RelayImportPreview;
  validationMessage?: string | null;
  onSourceRunChange: (value: string) => void;
  onCoreFilterChange: (value: RelayCoreLabelFilter) => void;
  onFilterChange: (value: DeepSeekRelayFilter) => void;
  onBuild: () => void;
  onClear: () => void;
}) {
  const selectableRuns = runs.filter((run) => run.wallet_detail_count || run.selected_wallet_count);

  return (
    <FieldRow
      label="历史结果接力"
      help="从来源运行的原始全量地址池接力，核心标签和 DeepSeek 状态都可以按需筛选。"
    >
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_190px]">
          <select
            value={sourceRunId}
            onChange={(event) => onSourceRunChange(event.target.value)}
            className="block h-10 w-full rounded-md border-0 px-3 text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-inset focus:ring-[#2E5CFF] sm:text-sm"
          >
            <option value="">选择已有分析运行</option>
            {selectableRuns.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {runDisplayName(run)} · 钱包 {run.selected_wallet_count ?? run.wallet_detail_count ?? 0}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={onBuild}
            disabled={!sourceRunId || loading}
            className="inline-flex h-10 items-center justify-center rounded-md bg-[#2E5CFF] px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? '确认中...' : '确认接力来源'}
          </button>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {relayCoreLabelFilterOptions.map((option) => {
            const checked = option.value === coreFilter;
            return (
              <label
                key={option.value}
                className={cn(
                  'flex cursor-pointer gap-3 rounded-md border px-4 py-3 transition-colors',
                  checked ? 'border-[#2E5CFF] bg-blue-50/60' : 'border-slate-200 hover:border-slate-300',
                )}
              >
                <input
                  type="radio"
                  name="relay_core_label_filter"
                  value={option.value}
                  checked={checked}
                  onChange={() => onCoreFilterChange(option.value)}
                  className="mt-1 h-4 w-4 border-slate-300 text-[#2E5CFF] focus:ring-[#2E5CFF]"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-500">{option.description}</span>
                </span>
              </label>
            );
          })}
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {deepSeekRelayFilterOptions.map((option) => {
            const checked = option.value === filter;
            return (
              <label
                key={option.value}
                className={cn(
                  'flex cursor-pointer gap-3 rounded-md border px-4 py-3 transition-colors',
                  checked ? 'border-[#2E5CFF] bg-blue-50/60' : 'border-slate-200 hover:border-slate-300',
                )}
              >
                <input
                  type="radio"
                  name="deepseek_relay_filter"
                  value={option.value}
                  checked={checked}
                  onChange={() => onFilterChange(option.value)}
                  className="mt-1 h-4 w-4 border-slate-300 text-[#2E5CFF] focus:ring-[#2E5CFF]"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-500">{option.description}</span>
                </span>
              </label>
            );
          })}
        </div>

        {preview && (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
            <div className="font-medium text-slate-900">已确认接力来源</div>
            <div className="mt-2 grid grid-cols-1 gap-2 text-xs text-slate-600 sm:grid-cols-3">
              <div>原始地址池：{preview.source_total}</div>
              <div>当前筛选命中：{preview.matched_count}</div>
              <div>来源池：{relaySourcePoolLabel(preview.summary?.source_pool)}</div>
              <div>DeepSeek 已完成：{preview.completed_count}</div>
              <div>DeepSeek 未完成：{preview.incomplete_count}</div>
              <div>系统核心标签：{preview.core_labeled_count}</div>
            </div>
            <button
              type="button"
              onClick={onClear}
              className="mt-3 inline-flex h-9 items-center justify-center rounded-md px-3 text-sm font-medium text-slate-500 hover:bg-slate-100"
            >
              <X className="mr-2 h-4 w-4" />
              清除接力来源
            </button>
          </div>
        )}

        {validationMessage && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {validationMessage}
          </div>
        )}
      </div>
    </FieldRow>
  );
}

function ActivityFilterField({
  value,
  onChange,
}: {
  value: ActivityFilterMode;
  onChange: (value: ActivityFilterMode) => void;
}) {
  return (
    <FieldRow
      label="活跃度筛选"
      help="对导入地址库做活跃状态筛选，可选择仅正常活跃、仅不活跃或不筛选。"
    >
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {activityFilterOptions.map((option) => {
          const checked = option.value === value;
          return (
            <label
              key={option.value}
              className={cn(
                'flex cursor-pointer gap-3 rounded-md border px-4 py-3 transition-colors',
                checked ? 'border-[#2E5CFF] bg-blue-50/60' : 'border-slate-200 hover:border-slate-300',
              )}
            >
              <input
                type="radio"
                name="activity_filter_mode"
                value={option.value}
                checked={checked}
                onChange={() => onChange(option.value)}
                className="mt-1 h-4 w-4 border-slate-300 text-[#2E5CFF] focus:ring-[#2E5CFF]"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                <span className="mt-1 block text-xs leading-5 text-slate-500">{option.description}</span>
              </span>
            </label>
          );
        })}
      </div>
    </FieldRow>
  );
}

function SmartWalletImportField({
  fileName,
  summary,
  importing,
  noticeMessage,
  validationMessage,
  onFileChange,
  onClear,
}: {
  fileName?: string;
  summary?: SmartWalletImportSummary;
  importing: boolean;
  noticeMessage?: string | null;
  validationMessage?: string | null;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onClear: () => void;
}) {
  return (
    <FieldRow
      label="地址库 JSON"
      help="支持上传后台导出的地址库 JSON，地址提取与结构兼容由系统自动处理。"
    >
      <div className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <label className="inline-flex h-10 cursor-pointer items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50">
            <Upload className="mr-2 h-4 w-4" />
            {importing ? '导入中...' : '选择地址库 JSON'}
            <input
              type="file"
              accept=".json,application/json"
              onChange={onFileChange}
              className="sr-only"
            />
          </label>
          {fileName && (
            <button
              type="button"
              onClick={onClear}
              className="inline-flex h-10 items-center justify-center rounded-md px-3 text-sm font-medium text-slate-500 hover:bg-slate-100"
            >
              <X className="mr-2 h-4 w-4" />
              清除文件
            </button>
          )}
        </div>

        {fileName ? (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="flex flex-col gap-2 text-sm text-slate-700">
              <div className="font-medium text-slate-900">{fileName}</div>
              <div className="grid grid-cols-1 gap-2 text-xs text-slate-600 sm:grid-cols-2 xl:grid-cols-5">
                <div>检测记录数：{summary?.detectedCount ?? 0}</div>
                <div>可用地址数：{summary?.validAddressCount ?? 0}</div>
                <div>用户名/显示名配对数：{summary?.namedAddressCount ?? 0}</div>
                <div>仅地址无名称：{summary?.addressOnlyCount ?? 0}</div>
                <div>最新更新时间：{formatImportTimestamp(summary?.latestUpdatedAt)}</div>
              </div>

              {(summary?.previewPairs.length ?? 0) > 0 && (
                <div className="space-y-2 border-t border-slate-200 pt-3">
                  <div className="text-xs font-medium text-slate-700">已识别用户名/显示名样例</div>
                  <div className="space-y-2">
                    {summary?.previewPairs.map((item) => (
                      <div
                        key={`${item.address}:${item.name}`}
                        className="rounded-md border border-slate-200 bg-white px-3 py-2"
                      >
                        <div className="text-sm font-medium text-slate-900">{item.name}</div>
                        <div className="break-all font-mono text-xs text-slate-500">{item.address}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-slate-200 px-4 py-3 text-sm text-slate-500">
            请先上传地址库 JSON 文件，导入完成后即可开始这次地址库刷新。
          </div>
        )}

        {fileName && noticeMessage && !validationMessage && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
            {noticeMessage}
          </div>
        )}

        {fileName && validationMessage && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {validationMessage}
          </div>
        )}
      </div>
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
