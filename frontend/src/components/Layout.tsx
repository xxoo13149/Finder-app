import {
  Activity,
  Bell,
  FileText,
  LayoutDashboard,
  List,
  Loader2,
  Menu,
  PlusCircle,
  Power,
  Settings,
  SlidersHorizontal,
  Trash2,
  X,
} from 'lucide-react';
import type {ReactNode} from 'react';
import {useState} from 'react';
import {shutdownApplication} from '../lib/api';
import type {ConsolePreferences} from '../lib/preferences';
import {cn} from '../lib/utils';

const navItems = [
  {id: 'dashboard', label: '控制台总览', icon: LayoutDashboard},
  {id: 'new_task', label: '新建分析', icon: PlusCircle},
  {id: 'task_running', label: '运行状态', icon: Activity},
  {id: 'wallet_list', label: '钱包列表', icon: List},
  {id: 'reports', label: '分析结果', icon: FileText},
  {id: 'rule_config', label: '标签规则', icon: SlidersHorizontal},
  {id: 'history_cleanup', label: '历史清理', icon: Trash2},
];

const pageLabels: Record<string, string> = {
  dashboard: '控制台总览',
  new_task: '新建分析',
  task_running: '运行状态',
  wallet_list: '钱包列表',
  wallet_detail: '钱包详情',
  reports: '分析结果',
  rule_config: '标签规则',
  history_cleanup: '历史清理',
  settings: '系统设置',
};

export function Layout({
  currentPage,
  density,
  setCurrentPage,
  children,
}: {
  currentPage: string;
  density: ConsolePreferences['density'];
  setCurrentPage: (page: string) => void;
  children: ReactNode;
}) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [shutdownOpen, setShutdownOpen] = useState(false);
  const [shutdownState, setShutdownState] = useState<'idle' | 'closing' | 'failed'>('idle');
  const [shutdownError, setShutdownError] = useState<string>();
  const currentLabel = pageLabels[currentPage] || '控制台总览';
  const settingsActive = currentPage === 'settings';
  const compact = density === 'compact';
  const showSidebarLabels = sidebarOpen || mobileSidebarOpen;

  const openNavigation = () => {
    if (window.matchMedia('(min-width: 768px)').matches) {
      setSidebarOpen((value) => !value);
      return;
    }
    setMobileSidebarOpen(true);
  };

  const navigateTo = (page: string) => {
    setCurrentPage(page);
    setMobileSidebarOpen(false);
  };

  const requestShutdown = async () => {
    setShutdownState('closing');
    setShutdownError(undefined);
    void shutdownApplication().catch(() => undefined);
    window.setTimeout(() => window.close(), 200);
    window.setTimeout(() => {
      setShutdownError('正在关闭后台服务。如果窗口没有自动退出，可以直接关闭这个窗口。');
    }, 1800);
  };

  return (
    <div className={cn('flex h-screen w-full overflow-hidden bg-slate-100 text-slate-900', compact && 'text-[14px]')}>
      {mobileSidebarOpen && (
        <button
          className="fixed inset-0 z-30 bg-slate-950/40 md:hidden"
          onClick={() => setMobileSidebarOpen(false)}
          aria-label="关闭导航遮罩"
        />
      )}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-[#0b1220] text-slate-300 shadow-2xl transition-all duration-300 md:relative md:inset-auto md:z-20 md:translate-x-0 md:shadow-xl',
          mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full',
          sidebarOpen ? 'md:w-64' : 'md:w-20',
        )}
      >
        <div className="mb-4 flex h-16 flex-shrink-0 items-center border-b border-white/10 px-4">
          <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center overflow-hidden rounded-md shadow-sm">
            <img src="/polymarket-icon.svg" alt="Polymarket 官方图标" className="h-9 w-9" />
          </div>
          {showSidebarLabels && (
            <div className="ml-3 flex flex-col items-start overflow-hidden whitespace-nowrap">
              <span className="text-sm font-semibold leading-tight text-white">Polymarket</span>
              <span className="text-sm font-semibold leading-tight text-white">天气分析工具</span>
              <span className="text-[10px] leading-tight text-slate-400">本地控制台</span>
            </div>
          )}
          <button
            onClick={() => setMobileSidebarOpen(false)}
            className="ml-auto rounded-md p-1.5 text-slate-400 hover:bg-white/10 hover:text-white md:hidden"
            aria-label="关闭导航"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-2">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = currentPage === item.id;
            return (
              <button
                key={item.id}
                onClick={() => navigateTo(item.id)}
                className={cn(
                  'group flex items-center rounded-md transition-colors',
                  showSidebarLabels ? 'px-3 py-2.5' : 'justify-center p-2.5',
                  isActive ? 'bg-[#2E5CFF] text-white shadow-sm' : 'text-slate-400 hover:bg-white/10 hover:text-white',
                )}
                title={!showSidebarLabels ? item.label : undefined}
              >
                <Icon
                  className={cn(
                    'h-5 w-5 flex-shrink-0',
                    isActive ? 'text-white' : 'text-slate-400 group-hover:text-white',
                  )}
                />
                {showSidebarLabels && <span className="ml-3 whitespace-nowrap text-sm font-medium">{item.label}</span>}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto border-t border-white/10 p-3">
          <button
            className={cn(
              'flex w-full items-center rounded-md transition-colors',
              showSidebarLabels ? 'px-3 py-2.5' : 'justify-center p-2.5',
              settingsActive ? 'bg-[#2E5CFF] text-white shadow-sm' : 'text-slate-400 hover:bg-white/10 hover:text-white',
            )}
            onClick={() => navigateTo('settings')}
            title={!showSidebarLabels ? '系统设置' : undefined}
          >
            <Settings className="h-5 w-5 flex-shrink-0" />
            {showSidebarLabels && <span className="ml-3 whitespace-nowrap text-sm font-medium">系统设置</span>}
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col bg-slate-100">
        <header className="z-10 flex h-14 flex-shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6 shadow-sm">
          <div className="flex items-center text-sm font-medium text-slate-700">
            <button
              onClick={openNavigation}
              className="mr-4 rounded-md p-1.5 text-slate-500 hover:bg-slate-100"
              aria-label="切换导航栏"
            >
              <Menu className="h-5 w-5" />
            </button>
            {currentLabel}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCurrentPage('task_running')}
              className="relative rounded-full p-2 text-slate-500 transition-colors hover:bg-slate-100"
              title="查看运行状态"
            >
              <Bell className="h-5 w-5" />
              <span className="absolute right-2.5 top-2 h-1.5 w-1.5 rounded-full border border-white bg-emerald-500" />
            </button>
            <button
              onClick={() => {
                setShutdownOpen(true);
                setShutdownState('idle');
                setShutdownError(undefined);
              }}
              className="rounded-full p-2 text-slate-500 transition-colors hover:bg-red-50 hover:text-red-600"
              title="全部关闭"
              aria-label="全部关闭"
            >
              <Power className="h-5 w-5" />
            </button>
          </div>
        </header>
        <main className={cn('flex-1 overflow-auto', compact ? 'p-4' : 'p-6')}>
          <div className={cn('mx-auto h-full w-full max-w-[1400px]', compact ? 'space-y-4' : 'space-y-6')}>{children}</div>
        </main>
      </div>

      {shutdownOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4">
          <div className="w-full max-w-md rounded-md bg-white p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-slate-900">全部关闭</h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">关闭管理页面、前端服务和本地后端 API。</p>
              </div>
              <button
                onClick={() => setShutdownOpen(false)}
                disabled={shutdownState === 'closing'}
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="取消关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {shutdownError && (
              <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {shutdownError}
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setShutdownOpen(false)}
                disabled={shutdownState === 'closing'}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                取消
              </button>
              <button
                onClick={requestShutdown}
                disabled={shutdownState === 'closing'}
                className="inline-flex items-center rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-70"
              >
                {shutdownState === 'closing' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Power className="mr-2 h-4 w-4" />}
                {shutdownState === 'closing' ? '正在关闭' : '确认关闭'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
