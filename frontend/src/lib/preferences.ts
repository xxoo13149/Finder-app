export type ConsolePreferences = {
  density: 'comfortable' | 'compact';
  autoRefresh: boolean;
  rememberLastRun: boolean;
  tablePageSize: number;
};

export const preferencesKey = 'polymarket-weather-console.preferences';
export const lastRunKey = 'polymarket-weather-console.last-run-id';
export const preferencesChangedEvent = 'polymarket-preferences-changed';

export const fallbackPreferences: ConsolePreferences = {
  density: 'comfortable',
  autoRefresh: true,
  rememberLastRun: true,
  tablePageSize: 25,
};

export function loadPreferences(): ConsolePreferences {
  if (typeof window === 'undefined') return fallbackPreferences;

  try {
    const raw = window.localStorage.getItem(preferencesKey);
    if (!raw) return fallbackPreferences;
    return normalizePreferences(JSON.parse(raw));
  } catch {
    return fallbackPreferences;
  }
}

export function savePreferences(preferences: ConsolePreferences) {
  const normalized = normalizePreferences(preferences);
  window.localStorage.setItem(preferencesKey, JSON.stringify(normalized));
  window.dispatchEvent(new CustomEvent(preferencesChangedEvent, {detail: normalized}));
  return normalized;
}

export function resetPreferences() {
  return savePreferences(fallbackPreferences);
}

export function clearLocalPreferences() {
  window.localStorage.removeItem(preferencesKey);
  window.localStorage.removeItem(lastRunKey);
  window.dispatchEvent(new CustomEvent(preferencesChangedEvent, {detail: fallbackPreferences}));
  return fallbackPreferences;
}

function normalizePreferences(value: Partial<ConsolePreferences>): ConsolePreferences {
  const tablePageSize = Number(value.tablePageSize);

  return {
    density: value.density === 'compact' ? 'compact' : 'comfortable',
    autoRefresh: value.autoRefresh !== false,
    rememberLastRun: value.rememberLastRun !== false,
    tablePageSize: Number.isFinite(tablePageSize) ? Math.max(10, Math.min(200, Math.round(tablePageSize))) : fallbackPreferences.tablePageSize,
  };
}
