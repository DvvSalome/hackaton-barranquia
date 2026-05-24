const $ = (id) => document.getElementById(id);

const CAT_LABELS = {
  social: 'Redes sociales',
  entertainment: 'Entretenimiento',
  news: 'Noticias',
  work: 'Trabajo',
  education: 'Aprendizaje',
  shopping: 'Compras',
  search: 'Búsquedas',
  ai: 'IA',
  other: 'Otro',
};

const CAT_ORDER = [
  'social', 'entertainment', 'news', 'work', 'education',
  'shopping', 'search', 'ai', 'other',
];

function send(type, extra = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, ...extra }, (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        resolve(resp || {});
      }
    });
  });
}

function fmtMin(m) {
  if (!m) return '0 min';
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}min` : `${h}h`;
}

function fmtTimestamp(iso) {
  if (!iso) return 'nunca';
  const d = new Date(iso);
  const diffSec = (Date.now() - d.getTime()) / 1000;
  if (diffSec < 60) return 'hace segundos';
  if (diffSec < 3600) return `hace ${Math.round(diffSec / 60)} min`;
  if (diffSec < 86400) return `hace ${Math.round(diffSec / 3600)} h`;
  return d.toLocaleString();
}

async function refresh() {
  const state = await send('GET_STATE');
  if (!state || !state.settings) return;
  const s = state.settings;

  $('enabled').checked = !!s.enabled;
  $('profileId').value = s.profileId || '';
  $('apiBase').value = s.apiBase || 'http://localhost:8010';

  // categorías
  const stats = state.stats || {};
  const cats = stats.todayMinutesByCat || {};
  const total = Object.values(cats).reduce((a, b) => a + b, 0);
  const list = $('catList');
  list.innerHTML = '';
  for (const cat of CAT_ORDER) {
    const m = cats[cat] || 0;
    if (m === 0 && cat !== 'social' && cat !== 'work') continue;
    const row = document.createElement('div');
    row.className = `cat cat-${cat}`;
    row.innerHTML = `
      <span class="dot"></span>
      <span class="name">${CAT_LABELS[cat] || cat}</span>
      <span class="min">${fmtMin(m)}</span>
    `;
    list.appendChild(row);
  }
  $('totalMin').textContent = fmtMin(total);
  $('curDomain').textContent = state.currentDomain || '—';

  $('bufSessions').textContent = state.bufferCounts?.sessions ?? 0;
  $('bufQueries').textContent = state.bufferCounts?.queries ?? 0;

  $('lastFlush').textContent = s.lastFlushAt
    ? `Último envío: ${fmtTimestamp(s.lastFlushAt)}`
    : 'Aún no se ha enviado nada al backend.';
}

async function onSave() {
  const profileId = $('profileId').value.trim();
  const apiBase = $('apiBase').value.trim() || 'http://localhost:8010';
  const enabled = $('enabled').checked;
  const out = await send('SET_SETTINGS', {
    patch: { profileId, apiBase, enabled },
  });
  const hint = $('savedHint');
  if (out.ok) {
    hint.textContent = 'Guardado.';
    hint.className = 'hint ok';
  } else {
    hint.textContent = 'No pude guardar: ' + (out.error || '?');
    hint.className = 'hint err';
  }
  setTimeout(() => { hint.textContent = ''; hint.className = 'hint'; }, 2500);
}

async function onFlush() {
  const hint = $('flushHint');
  hint.textContent = 'Enviando…';
  hint.className = 'hint';
  const out = await send('FLUSH_NOW');
  if (out.ok) {
    const m = out.metric || {};
    const overall = m?.scores?.digital_overall;
    hint.textContent = overall != null
      ? `Enviado. Digital global: ${overall}/100.`
      : 'Enviado al backend.';
    hint.className = 'hint ok';
  } else {
    hint.textContent = 'Falló: ' + (out.error || out.status || '?');
    hint.className = 'hint err';
  }
  await refresh();
}

async function onClear() {
  await send('CLEAR_BUFFER');
  await refresh();
}

document.addEventListener('DOMContentLoaded', () => {
  $('enabled').addEventListener('change', onSave);
  $('save').addEventListener('click', onSave);
  $('flush').addEventListener('click', onFlush);
  $('clear').addEventListener('click', onClear);
  refresh();
  setInterval(refresh, 5000);
});
