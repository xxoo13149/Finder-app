import {useEffect, useState} from 'react';
import {Layout} from './components/Layout';
import {ConsolePreferences, lastRunKey, loadPreferences, preferencesChangedEvent} from './lib/preferences';
import {Dashboard} from './pages/Dashboard';
import {HistoryCleanup} from './pages/HistoryCleanup';
import {NewTask} from './pages/NewTask';
import {Reports} from './pages/Reports';
import {RuleConfig} from './pages/RuleConfig';
import {SettingsPage} from './pages/Settings';
import {TaskRunning} from './pages/TaskRunning';
import {WalletDetail} from './pages/WalletDetail';
import {WalletList} from './pages/WalletList';

export default function App() {
  const [preferences, setPreferences] = useState<ConsolePreferences>(loadPreferences);
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [activeRunId, setActiveRunId] = useState<string>(() => {
    const initialPreferences = loadPreferences();
    return initialPreferences.rememberLastRun ? window.localStorage.getItem(lastRunKey) || undefined : undefined;
  });
  const [selectedWallet, setSelectedWallet] = useState<string>();

  const navigate = (page: string) => setCurrentPage(page);

  useEffect(() => {
    const syncPreferences = () => setPreferences(loadPreferences());
    window.addEventListener(preferencesChangedEvent, syncPreferences);
    window.addEventListener('storage', syncPreferences);
    return () => {
      window.removeEventListener(preferencesChangedEvent, syncPreferences);
      window.removeEventListener('storage', syncPreferences);
    };
  }, []);

  useEffect(() => {
    if (preferences.rememberLastRun && activeRunId) {
      window.localStorage.setItem(lastRunKey, activeRunId);
      return;
    }
    if (!preferences.rememberLastRun) {
      window.localStorage.removeItem(lastRunKey);
    }
  }, [activeRunId, preferences.rememberLastRun]);

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return (
          <Dashboard
            activeRunId={activeRunId}
            onRunSelected={setActiveRunId}
            onNavigate={navigate}
            onWalletSelected={(wallet) => {
              setSelectedWallet(wallet);
              setCurrentPage('wallet_detail');
            }}
          />
        );
      case 'new_task':
        return (
          <NewTask
            onRunCreated={(runId) => {
              setActiveRunId(runId);
              setCurrentPage('task_running');
            }}
          />
        );
      case 'task_running':
        return (
          <TaskRunning
            activeRunId={activeRunId}
            autoRefresh={preferences.autoRefresh}
            onRunSelected={setActiveRunId}
            onNavigate={navigate}
          />
        );
      case 'wallet_list':
        return (
          <WalletList
            activeRunId={activeRunId}
            pageSize={preferences.tablePageSize}
            onRunSelected={setActiveRunId}
            onWalletSelected={(wallet) => {
              setSelectedWallet(wallet);
              setCurrentPage('wallet_detail');
            }}
          />
        );
      case 'wallet_detail':
        return <WalletDetail activeRunId={activeRunId} wallet={selectedWallet} onNavigate={navigate} />;
      case 'reports':
        return (
          <Reports
            activeRunId={activeRunId}
            onRunSelected={setActiveRunId}
            onNavigate={navigate}
          />
        );
      case 'rule_config':
        return <RuleConfig />;
      case 'history_cleanup':
        return (
          <HistoryCleanup
            activeRunId={activeRunId}
            onRunsDeleted={(runIds) => {
              if (activeRunId && runIds.includes(activeRunId)) {
                setActiveRunId(undefined);
              }
            }}
          />
        );
      case 'settings':
        return <SettingsPage onNavigate={navigate} />;
      default:
        return <Dashboard activeRunId={activeRunId} onRunSelected={setActiveRunId} onNavigate={navigate} />;
    }
  };

  return (
    <Layout currentPage={currentPage} density={preferences.density} setCurrentPage={setCurrentPage}>
      {renderPage()}
    </Layout>
  );
}
