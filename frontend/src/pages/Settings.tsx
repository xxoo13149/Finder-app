import {AlertCircle, CheckCircle2, Loader2, RefreshCcw, Save, Trash2} from 'lucide-react';
import type {ReactNode} from 'react';
import {useEffect, useState} from 'react';
import {getSmartProConfig, type SmartProConfigPayload} from '../lib/api';
import {
  ConsolePreferences,
  clearLocalPreferences,
  loadPreferences,
  resetPreferences,
  savePreferences,
} from '../lib/preferences';

export function SettingsPage({onNavigate}: {onNavigate: (page: string) => void}) {
  const [preferences, setPreferences] = useState<ConsolePreferences>(loadPreferences);
  const [message, setMessage] = useState<string>();
  const [smartProConfig, setSmartProConfig] = useState<SmartProConfigPayload>();
  const [smartProLoading, setSmartProLoading] = useState(true);
  const [smartProError, setSmartProError] = useState<string>();

  const updatePreference = <Key extends keyof ConsolePreferences>(key: Key, value: ConsolePreferences[Key]) => {
    setPreferences((current) => ({...current, [key]: value}));
    setMessage(undefined);
  };

  const loadSmartProStatus = () => {
    setSmartProLoading(true);
    setSmartProError(undefined);
    getSmartProConfig()
      .then((payload) => {
        setSmartProConfig(payload);
      })
      .catch((err) => {
        setSmartProError(err instanceof Error ? err.message : 'SmartPro configuration load failed');
      })
      .finally(() => {
        setSmartProLoading(false);
      });
  };

  useEffect(() => {
    loadSmartProStatus();
  }, []);

  const save = () => {
    setPreferences(savePreferences(preferences));
    setMessage('设置已保存。');
  };

  const reset = () => {
    setPreferences(resetPreferences());
    setMessage('已恢复默认设置。');
  };

  const clear = () => {
    setPreferences(clearLocalPreferences());
    setMessage('已清除本地偏好和上次运行记录。');
  };

  return (
    <div className="mx-auto mt-6 flex w-full max-w-4xl flex-col overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 px-6 py-5">
        <h1 className="text-xl font-semibold text-slate-900">系统设置</h1>
        <p className="mt-1 text-sm text-slate-500">调整控制台显示、刷新和本地记录偏好。</p>
      </div>

      <div className="divide-y divide-slate-100 px-6">
        <SettingRow title="界面密度" description="紧凑模式会缩小间距，适合长表格和小屏幕。">
          <select
            value={preferences.density}
            onChange={(event) => updatePreference('density', event.target.value as ConsolePreferences['density'])}
            className="w-44 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700"
          >
            <option value="comfortable">舒适</option>
            <option value="compact">紧凑</option>
          </select>
        </SettingRow>

        <SettingRow title="自动刷新" description="运行状态页面会自动拉取最新进度。">
          <Toggle checked={preferences.autoRefresh} onChange={(value) => updatePreference('autoRefresh', value)} />
        </SettingRow>

        <SettingRow title="记住上次运行" description="重新打开控制台时自动回到最近查看的分析任务。">
          <Toggle checked={preferences.rememberLastRun} onChange={(value) => updatePreference('rememberLastRun', value)} />
        </SettingRow>

        <SettingRow title="表格分页数量" description="钱包列表每页显示的钱包数量。">
          <NumberInput
            value={preferences.tablePageSize}
            min={10}
            max={200}
            step={5}
            onChange={(value) => updatePreference('tablePageSize', value)}
          />
        </SettingRow>
      </div>

      <div className="border-t border-slate-100 px-6 py-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="text-sm font-medium text-slate-900">SmartPro 对接状态</div>
            <div className="mt-1 text-sm text-slate-500">钱包列表页的“同步 SmartPro”按钮会读取本地 `.env` 里的共享地址和 token。</div>
          </div>
          <div className="flex items-center gap-2">
            {smartProLoading ? (
              <span className="inline-flex items-center rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-500">
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                检查中
              </span>
            ) : smartProConfig?.configured ? (
              <span className="inline-flex items-center rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
                <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
                已连接
              </span>
            ) : (
              <span className="inline-flex items-center rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700">
                <AlertCircle className="mr-1.5 h-3.5 w-3.5" />
                待配置
              </span>
            )}
            <button
              onClick={loadSmartProStatus}
              className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
              刷新
            </button>
          </div>
        </div>

        {smartProError ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{smartProError}</div>
        ) : smartProConfig ? (
          <>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <ConfigItem label="SmartPro 地址" value={smartProConfig.base_url || '未配置'} mono />
              <ConfigItem label="提交路径" value={smartProConfig.commit_path} mono />
              <ConfigItem label="共享 Token" value={smartProConfig.token_configured ? '已配置' : '未配置'} />
              <ConfigItem label="Access 凭据" value={smartProConfig.access_service_token_configured ? '已配置' : '未配置'} />
              <ConfigItem label="超时时间" value={`${smartProConfig.timeout_seconds} 秒`} />
            </div>
            {!!smartProConfig.errors.length && (
              <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                {smartProConfig.errors.join('；')}
              </div>
            )}
          </>
        ) : null}
      </div>

      {message && <div className="mx-6 mt-5 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}

      <div className="mt-6 flex flex-wrap justify-between gap-3 border-t border-slate-200 px-6 py-5">
        <button
          onClick={() => onNavigate('dashboard')}
          className="rounded-md px-4 py-2 text-sm font-medium text-slate-500 hover:bg-slate-100"
        >
          返回总览
        </button>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={clear}
            className="inline-flex items-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <Trash2 className="mr-2 h-4 w-4" />
            清除本地记录
          </button>
          <button
            onClick={reset}
            className="inline-flex items-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <RefreshCcw className="mr-2 h-4 w-4" />
            恢复默认
          </button>
          <button onClick={save} className="inline-flex items-center rounded-md bg-[#2E5CFF] px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700">
            <Save className="mr-2 h-4 w-4" />
            保存设置
          </button>
        </div>
      </div>
    </div>
  );
}

function SettingRow({title, description, children}: {title: string; description: string; children: ReactNode}) {
  return (
    <div className="flex flex-col gap-3 py-5 md:flex-row md:items-center md:justify-between">
      <div>
        <div className="text-sm font-medium text-slate-900">{title}</div>
        <div className="mt-1 text-sm text-slate-500">{description}</div>
      </div>
      {children}
    </div>
  );
}

function Toggle({checked, onChange}: {checked: boolean; onChange: (value: boolean) => void}) {
  return (
    <label className="relative inline-flex cursor-pointer items-center">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="peer sr-only" />
      <span className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-slate-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-[#2E5CFF] peer-checked:after:translate-x-full peer-checked:after:border-white" />
    </label>
  );
}

function NumberInput({
  value,
  min,
  max,
  step,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(event) => onChange(Number(event.target.value))}
      className="w-44 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900"
    />
  );
}

function ConfigItem({label, value, mono = false}: {label: string; value: string; mono?: boolean}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-sm text-slate-700 ${mono ? 'font-mono break-all' : ''}`}>{value}</div>
    </div>
  );
}
