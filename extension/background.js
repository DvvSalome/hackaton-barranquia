// Kairós Sensor — service worker (MV3)
//
// Responsabilidad:
//   1. Detectar la pestaña ACTIVA del usuario y medir el tiempo que pasa
//      en cada dominio (solo cuando el SO no está idle).
//   2. Capturar consultas en motores de búsqueda (Google, Bing, DDG, Perplexity).
//   3. Bufferizar todo en chrome.storage.local y empujar al backend Kairós
//      en lotes (cada N minutos o cuando el buffer crece).
//
// No envía nada hasta que el usuario configure la API base y el profile_id
// desde el popup.

import { categorize, normalizeDomain, detectSearch } from './categories.js';

// ─── Constantes ───────────────────────────────────────────────
const DEFAULTS = {
  apiBase: 'http://localhost:8010',
  profileId: '',
  enabled: true,
  flushIntervalMin: 5,
  idleDetectionSec: 60,
  maxSessionGapSec: 3,        // si <=3s pasaron sin foco, se considera la misma sesión
};

const STORAGE_KEY = 'kairos_settings';
const BUFFER_KEY = 'kairos_buffer';
const STATE_KEY = 'kairos_runtime';   // {currentDomain, currentUrl, currentTitle, sinceMs, lastFlushMs}
const STATS_KEY = 'kairos_stats';     // {todayMinutesByCat, lastUpdatedMs}

const FLUSH_ALARM = 'kairos-flush';

// ─── Helpers de storage ───────────────────────────────────────
async function getSettings() {
  const raw = await chrome.storage.local.get(STORAGE_KEY);
  return { ...DEFAULTS, ...(raw[STORAGE_KEY] || {}) };
}
async function setSettings(patch) {
  const cur = await getSettings();
  const next = { ...cur, ...patch };
  await chrome.storage.local.set({ [STORAGE_KEY]: next });
  return next;
}
async function getBuffer() {
  const raw = await chrome.storage.local.get(BUFFER_KEY);
  return raw[BUFFER_KEY] || { sessions: [], queries: [] };
}
async function setBuffer(buf) {
  await chrome.storage.local.set({ [BUFFER_KEY]: buf });
}
async function getState() {
  const raw = await chrome.storage.local.get(STATE_KEY);
  return raw[STATE_KEY] || {};
}
async function setState(state) {
  await chrome.storage.local.set({ [STATE_KEY]: state });
}
async function getStats() {
  const raw = await chrome.storage.local.get(STATS_KEY);
  return raw[STATS_KEY] || { todayMinutesByCat: {}, lastUpdatedMs: 0, todayDate: null };
}
async function setStats(stats) {
  await chrome.storage.local.set({ [STATS_KEY]: stats });
}

// ─── Tracking de pestaña activa ───────────────────────────────
function isTrackableUrl(url) {
  if (!url) return false;
  return /^https?:\/\//.test(url);
}

async function closeCurrentSession(reason) {
  const state = await getState();
  if (!state || !state.currentDomain || !state.sinceMs) return;
  const endMs = Date.now();
  const durSec = Math.max(0, Math.floor((endMs - state.sinceMs) / 1000));
  if (durSec < 1) {
    await setState({});
    return;
  }
  const session = {
    domain: state.currentDomain,
    url: state.currentUrl,
    title: state.currentTitle,
    category: categorize(state.currentDomain),
    started_at: new Date(state.sinceMs).toISOString(),
    ended_at: new Date(endMs).toISOString(),
    duration_sec: durSec,
    active: state.idle !== true,
    source: 'extension',
  };
  const buf = await getBuffer();
  buf.sessions.push(session);
  await setBuffer(buf);

  // stats locales (para el popup, no autoritativo)
  await bumpLocalStats(session);

  await setState({});
}

async function bumpLocalStats(session) {
  const stats = await getStats();
  const today = new Date().toISOString().slice(0, 10);
  if (stats.todayDate !== today) {
    stats.todayMinutesByCat = {};
    stats.todayDate = today;
  }
  const mins = Math.max(0, Math.round(session.duration_sec / 60));
  if (mins > 0) {
    stats.todayMinutesByCat[session.category] =
      (stats.todayMinutesByCat[session.category] || 0) + mins;
  }
  stats.lastUpdatedMs = Date.now();
  await setStats(stats);
}

async function openSession(url, title) {
  if (!isTrackableUrl(url)) {
    await closeCurrentSession('non-trackable');
    return;
  }
  const domain = normalizeDomain(url);
  if (!domain) return;
  const state = await getState();
  if (
    state.currentDomain === domain &&
    state.currentUrl === url
  ) {
    // misma pestaña/url, nada que hacer
    return;
  }
  await closeCurrentSession('switch');
  await setState({
    currentDomain: domain,
    currentUrl: url,
    currentTitle: title || '',
    sinceMs: Date.now(),
    idle: false,
  });
}

// ─── Listeners de pestañas ────────────────────────────────────
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab || tab.windowId === chrome.windows.WINDOW_ID_NONE) return;
    if (!tab.url) return;
    await openSession(tab.url, tab.title);
    if (tab.url) await maybeRecordSearch(tab.url);
  } catch {
    // ignore
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete' || !tab.active || !tab.url) return;
  await openSession(tab.url, tab.title);
  await maybeRecordSearch(tab.url);
});

chrome.windows.onFocusChanged.addListener(async (windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    await closeCurrentSession('blur-window');
    return;
  }
  try {
    const [tab] = await chrome.tabs.query({ active: true, windowId });
    if (tab && tab.url) {
      await openSession(tab.url, tab.title);
    }
  } catch {
    // ignore
  }
});

// ─── Idle ─────────────────────────────────────────────────────
chrome.idle.setDetectionInterval(DEFAULTS.idleDetectionSec);
chrome.idle.onStateChanged.addListener(async (newState) => {
  // active | idle | locked
  if (newState !== 'active') {
    await closeCurrentSession('idle:' + newState);
  } else {
    try {
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      if (tab && tab.url) await openSession(tab.url, tab.title);
    } catch { /* noop */ }
  }
});

// ─── Captura de búsquedas ─────────────────────────────────────
async function maybeRecordSearch(url) {
  const hit = detectSearch(url);
  if (!hit) return;
  // Evita duplicar la misma query en menos de 4s.
  const buf = await getBuffer();
  const nowMs = Date.now();
  const last = buf.queries[buf.queries.length - 1];
  if (
    last &&
    last.engine === hit.engine &&
    last.query.toLowerCase() === hit.query.toLowerCase() &&
    nowMs - new Date(last.ts).getTime() < 4000
  ) {
    return;
  }
  buf.queries.push({
    engine: hit.engine,
    query: hit.query.slice(0, 280),
    ts: new Date(nowMs).toISOString(),
    source: 'extension',
  });
  await setBuffer(buf);
}

// ─── Flush al backend ─────────────────────────────────────────
async function flushNow(force = false) {
  const settings = await getSettings();
  if (!settings.enabled) return { ok: false, reason: 'disabled' };
  if (!settings.apiBase) return { ok: false, reason: 'no-api-base' };

  // Cerrar la sesión actual para que cuente
  await closeCurrentSession('flush');

  const buf = await getBuffer();
  if (!force && buf.sessions.length === 0 && buf.queries.length === 0) {
    return { ok: true, skipped: true };
  }

  const payload = {
    profile_id: settings.profileId || null,
    sessions: buf.sessions,
    queries: buf.queries,
    recompute_today: true,
  };

  try {
    const res = await fetch(settings.apiBase.replace(/\/$/, '') + '/extension/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      return { ok: false, status: res.status, error: await res.text() };
    }
    const json = await res.json();
    // limpiar buffer solo si el backend lo aceptó
    await setBuffer({ sessions: [], queries: [] });
    await setSettings({ lastFlushAt: new Date().toISOString() });
    return { ok: true, ...json };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ─── Mensajes desde el popup ──────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg?.type === 'GET_STATE') {
        const [settings, buf, stats, state] = await Promise.all([
          getSettings(), getBuffer(), getStats(), getState(),
        ]);
        sendResponse({
          settings,
          bufferCounts: {
            sessions: buf.sessions.length,
            queries: buf.queries.length,
          },
          stats,
          currentDomain: state.currentDomain || null,
        });
      } else if (msg?.type === 'SET_SETTINGS') {
        const next = await setSettings(msg.patch || {});
        sendResponse({ ok: true, settings: next });
      } else if (msg?.type === 'FLUSH_NOW') {
        const out = await flushNow(true);
        sendResponse(out);
      } else if (msg?.type === 'CLEAR_BUFFER') {
        await setBuffer({ sessions: [], queries: [] });
        sendResponse({ ok: true });
      } else {
        sendResponse({ ok: false, error: 'unknown message' });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // async
});

// ─── Alarma de flush periódico ────────────────────────────────
chrome.runtime.onInstalled.addListener(async () => {
  await setSettings({});  // asegura defaults
  chrome.alarms.create(FLUSH_ALARM, {
    delayInMinutes: DEFAULTS.flushIntervalMin,
    periodInMinutes: DEFAULTS.flushIntervalMin,
  });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(FLUSH_ALARM, {
    delayInMinutes: DEFAULTS.flushIntervalMin,
    periodInMinutes: DEFAULTS.flushIntervalMin,
  });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === FLUSH_ALARM) {
    await flushNow(false);
  }
});
