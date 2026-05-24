/*
 * Kairós — panel digital
 *
 * Componente vanilla que se inyecta encima del dashboard. Lee el perfil de
 * sessionStorage (lo deja el flujo de auth), consulta:
 *   - GET  /digital/{profile_id}/today
 *   - GET  /recommendations/{profile_id}?status=pending
 *   - POST /recommendations/generate          (si no hay nada)
 *   - POST /recommendations/{id}/accept-habit (botón "Adoptar hábito")
 *   - POST /recommendations/{id}/action       (botón "Dismiss")
 *
 * Se monta en el body como un Floating Action Button + un sidepanel.
 * No depende de nada del dashboard salvo `sessionStorage.kairos_profile`.
 */

(function () {
  if (window.__kairosDigitalPanelMounted) return;
  window.__kairosDigitalPanelMounted = true;

  const API = window.KAIROS_API_URL || 'http://127.0.0.1:8787';

  const CAT_LABELS = {
    social: 'Social', entertainment: 'Entretenimiento', news: 'Noticias',
    work: 'Trabajo', education: 'Aprendizaje', shopping: 'Compras',
    search: 'Buscadores', ai: 'IA', other: 'Otro',
  };
  const CAT_COLORS = {
    social: '#f87171', entertainment: '#fbbf24', news: '#38bdf8',
    work: '#5eead4', education: '#a78bfa', shopping: '#fb7185',
    search: '#c4b5fd', ai: '#8b6fff', other: '#94a3b8',
  };
  const KIND_LABELS = {
    habit: 'Hábito sugerido', tip: 'Tip', warning: 'Alerta',
    reflection: 'Reflexión',
  };

  // ─── Estilos ──────────────────────────────────────────────
  const css = `
  .kd-fab {
    position: fixed; right: 18px; bottom: 18px; z-index: 9998;
    width: 52px; height: 52px; border-radius: 50%;
    background: linear-gradient(135deg, #8b6fff, #6d4cff);
    color: #fff; border: none; cursor: pointer;
    box-shadow: 0 12px 32px -10px rgba(139,111,255,.7), inset 0 1px 0 rgba(255,255,255,.25);
    display: grid; place-items: center;
    transition: transform .15s, filter .15s;
  }
  .kd-fab:hover { transform: translateY(-2px); filter: brightness(1.05); }
  .kd-fab svg { width: 22px; height: 22px; }

  .kd-overlay {
    position: fixed; inset: 0; z-index: 9997;
    background: rgba(5, 4, 20, 0.55);
    backdrop-filter: blur(4px);
    opacity: 0; pointer-events: none;
    transition: opacity .2s;
  }
  .kd-overlay.on { opacity: 1; pointer-events: auto; }

  .kd-panel {
    position: fixed; top: 0; right: 0; bottom: 0;
    width: min(440px, 95vw);
    z-index: 9999;
    background: linear-gradient(180deg, rgba(20, 15, 50, .92) 0%, rgba(10, 8, 30, .96) 100%);
    color: #e6e9ff;
    font-family: 'Inter', -apple-system, sans-serif;
    border-left: 1px solid rgba(167,139,250,.2);
    box-shadow: -30px 0 60px -20px rgba(0,0,0,.5);
    transform: translateX(100%);
    transition: transform .25s cubic-bezier(.2,.7,.2,1);
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .kd-panel.on { transform: translateX(0); }

  .kd-hdr {
    padding: 18px 18px 12px;
    display: flex; align-items: center; gap: 10px;
    border-bottom: 1px solid rgba(255,255,255,.06);
  }
  .kd-hdr .kd-logo {
    width: 30px; height: 30px; border-radius: 9px;
    background: linear-gradient(135deg, #8b6fff, #6d4cff);
    display: grid; place-items: center; color: #fff; font-weight: 800;
  }
  .kd-hdr h2 { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -.01em; }
  .kd-hdr .sub { font-size: 11px; color: rgba(230,233,255,.55); }
  .kd-hdr .kd-close {
    margin-left: auto; background: transparent; border: none; color: rgba(230,233,255,.6);
    font-size: 22px; cursor: pointer; line-height: 1;
  }

  .kd-body { padding: 14px 18px 18px; overflow-y: auto; flex: 1; }
  .kd-body::-webkit-scrollbar { width: 6px; }
  .kd-body::-webkit-scrollbar-thumb { background: rgba(255,255,255,.1); border-radius: 3px; }

  .kd-section { margin-bottom: 22px; }
  .kd-section h3 {
    margin: 0 0 10px; font-size: 11px; letter-spacing: .12em;
    text-transform: uppercase; color: rgba(230,233,255,.55);
    display: flex; align-items: center; gap: 8px;
  }
  .kd-section h3 .pill {
    background: rgba(167,139,250,.15); border: 1px solid rgba(167,139,250,.3);
    color: #c4b5fd; font-size: 10px; padding: 2px 8px; border-radius: 999px;
    letter-spacing: .04em; text-transform: none;
  }

  .kd-score-card {
    background: linear-gradient(135deg, rgba(139,111,255,.18), rgba(109,76,255,.1));
    border: 1px solid rgba(167,139,250,.3);
    border-radius: 16px; padding: 16px;
    display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: center;
  }
  .kd-score-ring {
    width: 64px; height: 64px; border-radius: 50%;
    background: conic-gradient(#8b6fff calc(var(--p, 0) * 1%), rgba(255,255,255,.08) 0);
    display: grid; place-items: center;
    position: relative;
  }
  .kd-score-ring::before {
    content: ''; position: absolute; inset: 6px;
    background: #0a0820; border-radius: 50%;
  }
  .kd-score-ring span { position: relative; font-weight: 700; font-size: 18px; }
  .kd-score-card h4 { margin: 0; font-size: 14px; font-weight: 600; }
  .kd-score-card p { margin: 4px 0 0; font-size: 12px; color: rgba(230,233,255,.65); line-height: 1.4; }

  .kd-sub-scores {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
    margin-top: 10px;
  }
  .kd-sub {
    background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.06);
    border-radius: 10px; padding: 8px 10px; text-align: center;
  }
  .kd-sub .lbl { font-size: 10px; color: rgba(230,233,255,.55); text-transform: uppercase; letter-spacing: .08em; }
  .kd-sub .val { font-size: 18px; font-weight: 700; margin-top: 2px; }

  .kd-bar {
    display: grid; grid-template-columns: 88px 1fr 48px; gap: 8px; align-items: center;
    padding: 6px 0; font-size: 12px;
  }
  .kd-bar .name { color: rgba(230,233,255,.85); }
  .kd-bar .track {
    height: 6px; background: rgba(255,255,255,.05); border-radius: 999px; overflow: hidden;
  }
  .kd-bar .fill { height: 100%; border-radius: 999px; }
  .kd-bar .val { text-align: right; color: rgba(230,233,255,.7); font-variant-numeric: tabular-nums; }

  .kd-rec {
    background: rgba(255,255,255,.03);
    border: 1px solid rgba(167,139,250,.18);
    border-radius: 14px;
    padding: 12px 14px;
    margin-bottom: 10px;
  }
  .kd-rec.kind-habit { border-color: rgba(94,234,212,.3); background: rgba(94,234,212,.05); }
  .kd-rec.kind-warning { border-color: rgba(252,165,165,.3); background: rgba(252,165,165,.05); }
  .kd-rec.kind-reflection { border-color: rgba(196,181,253,.3); background: rgba(196,181,253,.04); }
  .kd-rec .meta {
    display: flex; align-items: center; gap: 6px;
    font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
    color: rgba(230,233,255,.55);
    margin-bottom: 6px;
  }
  .kd-rec .meta .impact { margin-left: auto; color: #c4b5fd; }
  .kd-rec h4 { margin: 0 0 4px; font-size: 13px; font-weight: 600; }
  .kd-rec p { margin: 0 0 6px; font-size: 12px; line-height: 1.45; color: rgba(230,233,255,.78); }
  .kd-rec .rationale { font-size: 11px; color: rgba(230,233,255,.55); font-style: italic; margin-bottom: 8px; }
  .kd-rec .actions { display: flex; gap: 6px; }
  .kd-rec .btn {
    border: none; cursor: pointer; font-family: inherit;
    font-size: 11px; font-weight: 600; padding: 6px 10px; border-radius: 8px;
  }
  .kd-rec .btn.accept {
    background: linear-gradient(135deg, #8b6fff, #6d4cff);
    color: #fff;
  }
  .kd-rec .btn.dismiss {
    background: rgba(255,255,255,.05); color: rgba(230,233,255,.7);
    border: 1px solid rgba(255,255,255,.08);
  }

  .kd-empty {
    text-align: center; padding: 24px 8px;
    color: rgba(230,233,255,.5); font-size: 12px;
  }
  .kd-cta {
    width: 100%;
    background: linear-gradient(135deg, #8b6fff, #6d4cff);
    color: #fff; border: none; font-family: inherit; font-weight: 600; font-size: 13px;
    padding: 10px 12px; border-radius: 10px; cursor: pointer;
    margin-top: 10px;
  }
  .kd-cta:disabled { opacity: .55; cursor: not-allowed; }

  .kd-toast {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: rgba(20,15,50,.95); color: #e6e9ff;
    border: 1px solid rgba(167,139,250,.3);
    padding: 10px 16px; border-radius: 999px; font-size: 12px;
    z-index: 10000; opacity: 0; transition: opacity .2s;
  }
  .kd-toast.on { opacity: 1; }
  `;

  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ─── DOM ──────────────────────────────────────────────────
  const fab = document.createElement('button');
  fab.className = 'kd-fab';
  fab.title = 'Espacio digital';
  fab.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <path d="M2 12h20"/>
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
    </svg>
  `;

  const overlay = document.createElement('div');
  overlay.className = 'kd-overlay';

  const panel = document.createElement('aside');
  panel.className = 'kd-panel';
  panel.innerHTML = `
    <div class="kd-hdr">
      <div class="kd-logo">D</div>
      <div>
        <h2>Espacio digital</h2>
        <div class="sub">Tu día medido por la extensión</div>
      </div>
      <button class="kd-close" aria-label="Cerrar">×</button>
    </div>
    <div class="kd-body" id="kd-body"></div>
  `;
  const toast = document.createElement('div');
  toast.className = 'kd-toast';

  document.body.appendChild(fab);
  document.body.appendChild(overlay);
  document.body.appendChild(panel);
  document.body.appendChild(toast);

  // ─── Helpers ──────────────────────────────────────────────
  function profile() {
    try {
      return JSON.parse(sessionStorage.getItem('kairos_profile') || 'null');
    } catch {
      return null;
    }
  }
  function showToast(msg, ms = 2200) {
    toast.textContent = msg;
    toast.classList.add('on');
    setTimeout(() => toast.classList.remove('on'), ms);
  }
  async function api(path, opts = {}) {
    const r = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    if (!r.ok) {
      let d = '';
      try { const j = await r.json(); d = j.detail || JSON.stringify(j); } catch { d = await r.text().catch(() => ''); }
      const err = new Error(d || ('error ' + r.status));
      err.status = r.status;
      throw err;
    }
    return r.json();
  }
  function fmtMin(m) {
    if (!m) return '0m';
    if (m < 60) return m + 'm';
    return Math.floor(m / 60) + 'h ' + (m % 60 ? (m % 60 + 'm') : '');
  }

  // ─── Render ───────────────────────────────────────────────
  function renderEmpty(body, msg) {
    body.innerHTML = `<div class="kd-empty">${msg}</div>`;
  }

  function renderMetric(body, metric) {
    if (!metric) {
      renderEmpty(body, 'Todavía no hay datos digitales. Instala la extensión Kairós Sensor y empieza a navegar.');
      return;
    }
    const scoreOverall = metric.score_digital_overall ?? 0;
    const sSocial = metric.score_social ?? 0;
    const sFocus = metric.score_focus ?? 0;
    const sBalance = metric.score_balance ?? 0;

    const mins = [
      ['social', metric.minutes_social || 0],
      ['entertainment', metric.minutes_entertainment || 0],
      ['news', metric.minutes_news || 0],
      ['work', metric.minutes_work || 0],
      ['education', metric.minutes_education || 0],
      ['shopping', metric.minutes_shopping || 0],
      ['search', metric.minutes_search || 0],
      ['ai', metric.minutes_ai || 0],
      ['other', metric.minutes_other || 0],
    ].filter(([, m]) => m > 0);
    const maxMin = Math.max(1, ...mins.map(([, m]) => m));

    const bars = mins.map(([cat, m]) => `
      <div class="kd-bar">
        <span class="name">${CAT_LABELS[cat] || cat}</span>
        <span class="track"><span class="fill" style="width:${Math.min(100, (m / maxMin) * 100)}%; background:${CAT_COLORS[cat]}"></span></span>
        <span class="val">${fmtMin(m)}</span>
      </div>
    `).join('');

    const themes = (metric.search_themes || []).slice(0, 4).map(t => `${t.theme}×${t.n}`).join(' · ');
    const top = (metric.top_domains || []).slice(0, 3).map(d => `${d.domain} (${fmtMin(d.minutes)})`).join(' · ');

    body.innerHTML = `
      <div class="kd-section">
        <h3>Score digital de hoy <span class="pill">${metric.date || ''}</span></h3>
        <div class="kd-score-card">
          <div class="kd-score-ring" style="--p:${scoreOverall}"><span>${Math.round(scoreOverall)}</span></div>
          <div>
            <h4>Tu balance digital</h4>
            <p>${
              scoreOverall >= 75 ? 'Día equilibrado — bien distribuido.' :
              scoreOverall >= 50 ? 'Está OK, hay espacio para optimizar foco.' :
              'Hoy se inclinó al consumo pasivo. Mañana es otra oportunidad.'
            }</p>
          </div>
        </div>
        <div class="kd-sub-scores">
          <div class="kd-sub"><div class="lbl">Social</div><div class="val">${Math.round(sSocial)}</div></div>
          <div class="kd-sub"><div class="lbl">Foco</div><div class="val">${Math.round(sFocus)}</div></div>
          <div class="kd-sub"><div class="lbl">Balance</div><div class="val">${Math.round(sBalance)}</div></div>
        </div>
      </div>

      <div class="kd-section">
        <h3>Minutos por categoría</h3>
        ${bars || '<div class="kd-empty">Sin actividad registrada hoy.</div>'}
      </div>

      ${top ? `<div class="kd-section"><h3>Top dominios</h3><p style="font-size:12px;color:rgba(230,233,255,.7);margin:0">${top}</p></div>` : ''}
      ${themes ? `<div class="kd-section"><h3>Temas que buscaste</h3><p style="font-size:12px;color:rgba(230,233,255,.7);margin:0">${themes}</p></div>` : ''}

      <div class="kd-section">
        <h3>Recomendaciones para mañana</h3>
        <div id="kd-recs"></div>
        <button class="kd-cta" id="kd-gen">Generar nuevas con el LLM</button>
      </div>
    `;
  }

  function renderRecs(container, items) {
    if (!items || !items.length) {
      container.innerHTML = `<div class="kd-empty">No hay recomendaciones pendientes. Toca "Generar nuevas".</div>`;
      return;
    }
    container.innerHTML = items.map(rec => {
      const impact = rec.score_impact != null ? `+${Math.round(rec.score_impact)} impacto` : '';
      const accept = rec.kind === 'habit'
        ? `<button class="btn accept" data-id="${rec.id}" data-action="accept-habit">Adoptar hábito</button>`
        : `<button class="btn accept" data-id="${rec.id}" data-action="mark-done">Hecho</button>`;
      return `
        <div class="kd-rec kind-${rec.kind}">
          <div class="meta">
            <span>${KIND_LABELS[rec.kind] || rec.kind}</span>
            <span class="impact">${impact}</span>
          </div>
          <h4>${rec.title}</h4>
          <p>${rec.body}</p>
          ${rec.rationale ? `<div class="rationale">${rec.rationale}</div>` : ''}
          <div class="actions">
            ${accept}
            <button class="btn dismiss" data-id="${rec.id}" data-action="dismiss">Descartar</button>
          </div>
        </div>
      `;
    }).join('');

    container.querySelectorAll('button[data-id]').forEach(btn => {
      btn.addEventListener('click', () => onRecAction(btn.dataset.id, btn.dataset.action));
    });
  }

  // ─── Loaders ──────────────────────────────────────────────
  async function loadAll() {
    const body = document.getElementById('kd-body');
    const p = profile();
    if (!p || !p.id) {
      renderEmpty(body, 'Inicia sesión primero para ver tu espacio digital.');
      return;
    }

    body.innerHTML = `<div class="kd-empty">Cargando tu métrica…</div>`;
    let metricRow = null;
    try {
      const r = await api(`/digital/${p.id}/today`);
      metricRow = r.metric;
    } catch (e) {
      renderEmpty(body, 'No pude leer la métrica: ' + (e.message || e));
      return;
    }
    renderMetric(body, metricRow);

    const recsBox = document.getElementById('kd-recs');
    if (recsBox) {
      try {
        const list = await api(`/recommendations/${p.id}?status=pending`);
        renderRecs(recsBox, list.items || []);
      } catch (e) {
        recsBox.innerHTML = `<div class="kd-empty">Error: ${e.message}</div>`;
      }
    }
    const gen = document.getElementById('kd-gen');
    if (gen) gen.addEventListener('click', onGenerate);
  }

  async function onGenerate() {
    const p = profile();
    if (!p || !p.id) return;
    const btn = document.getElementById('kd-gen');
    btn.disabled = true; btn.textContent = 'Pensando…';
    try {
      await api('/recommendations/generate', {
        method: 'POST',
        body: JSON.stringify({ profile_id: p.id, save: true, max_items: 5 }),
      });
      showToast('Recomendaciones nuevas generadas.');
      await loadAll();
    } catch (e) {
      showToast('Falló: ' + (e.message || e), 3500);
      btn.disabled = false; btn.textContent = 'Generar nuevas con el LLM';
    }
  }

  async function onRecAction(id, action) {
    try {
      if (action === 'accept-habit') {
        await api(`/recommendations/${id}/accept-habit`, { method: 'POST', body: '{}' });
        showToast('Hábito adoptado.');
      } else if (action === 'dismiss') {
        await api(`/recommendations/${id}/action`, {
          method: 'POST',
          body: JSON.stringify({ status: 'dismissed' }),
        });
      } else if (action === 'mark-done') {
        await api(`/recommendations/${id}/action`, {
          method: 'POST',
          body: JSON.stringify({ status: 'accepted' }),
        });
        showToast('Marcado.');
      }
      await loadAll();
    } catch (e) {
      showToast('Error: ' + (e.message || e), 3500);
    }
  }

  // ─── Apertura / cierre ────────────────────────────────────
  function open() {
    panel.classList.add('on');
    overlay.classList.add('on');
    loadAll();
  }
  function close() {
    panel.classList.remove('on');
    overlay.classList.remove('on');
  }
  fab.addEventListener('click', open);
  overlay.addEventListener('click', close);
  panel.querySelector('.kd-close').addEventListener('click', close);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

  // Exponer para debugging
  window.kairosDigital = { open, close, reload: loadAll };
})();
