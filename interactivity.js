/* ════════════════════════════════════════════════════════════
   Kairós — interactive layer (v2)
   - Agentes con info known/missing, persistente
   - Chat multi-agente (recopilación)
   - Chat con Kairós Core (sintetizador)
   - Drawer detalle de agente
   - Tool modal
   - Sidebar wiring
   - Toasts
   ═══════════════════════════════════════════════════════════ */

(function(){
  /* ───── PERSISTENT STATE ───── */
  const STATE_KEY = 'kairos_state_v2';
  const defaults = {
    answers: {},          // { 'animo:mood-now': 'bien', ... }
    checkinDone: false,
    activatedPlaybooks: [],
  };
  const state = Object.assign({}, defaults, JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null') || {});
  const save = () => sessionStorage.setItem(STATE_KEY, JSON.stringify(state));

  /* ───── AGENT MODEL ─────
     Each agent declares:
       - identity (name, color, role, desc)
       - knownAlways: facts they always have from prior days / sensors
       - questions: things to ask today (the missing list). Each question
         has a key, prompt, chips, and the way the answer becomes a known fact.
       - passive: true if no questions today (sensor only)
     The agent is "complete" when every question key has an entry in state.answers.
  */
  const AGENTS = {
    kairos: {
      title: 'Kairós Core',
      color: '#06b6d4',
      role: 'Sintetizador principal',
      desc: 'Conozco lo que aprenden todos los demás agentes y traduzco los patrones en acciones concretas para tu día.',
      principal: true,
      passive: true,
      knownAlways: () => {
        const lines = [];
        for (const id of Object.keys(AGENTS)){
          if (id === 'kairos') continue;
          const facts = factsOf(id);
          if (facts.length) lines.push(`${AGENTS[id].title} · ${facts.length} dato${facts.length>1?'s':''}`);
        }
        return lines;
      },
      questions: [],
    },

    animo: {
      title: 'Ánimo',
      color: '#fb7185',
      role: 'Recopilación diaria',
      desc: 'Rastreo cómo te sientes y los eventos que te marcan. Solo te pido una palabra o un emoji.',
      knownAlways: () => ['Ánimo de ayer · 4/5', 'Tendencia semanal · subiendo', 'Mejor día · viernes'],
      questions: [
        {
          key: 'animo:now',
          ask: 'Para empezar — ¿cómo te sientes ahora?',
          chips: [
            { v:'genial', tx:'😊 Genial' }, { v:'bien', tx:'🙂 Bien' },
            { v:'neutro', tx:'😐 Neutro' }, { v:'bajo', tx:'😔 Bajo' },
          ],
          ack: v => ({ genial:'✨ Anotado.', bien:'Bien.', neutro:'Vale.', bajo:'Gracias por decírmelo.' }[v] || 'Anotado.'),
          factLabel: v => `Estado ahora · ${labelOfChoice('animo:now', v)}`,
        },
        {
          key: 'animo:events',
          ask: '¿Algo que esté rondando tu cabeza hoy? Si no, está bien.',
          chips: [
            { v:'trabajo', tx:'💼 Trabajo' }, { v:'familia', tx:'👪 Familia' },
            { v:'cuerpo', tx:'💪 Cuerpo' }, { v:'nada', tx:'🌿 Nada' },
          ],
          ack: v => v === 'nada' ? 'Día limpio.' : 'Lo tengo presente.',
          factLabel: v => v === 'nada' ? 'Eventos · sin marcar' : `Eventos · ${labelOfChoice('animo:events', v)}`,
        },
      ],
    },

    sueno: {
      title: 'Sueño',
      color: '#60a5fa',
      role: 'Recopilación diaria',
      desc: 'Sé cuándo te dormiste y cuánto descansaste. Cruzo eso con pantalla y cafeína.',
      knownAlways: () => ['Horas anoche · 7.4h', 'Despertar · 7:12'],
      questions: [
        {
          key: 'sueno:quality',
          ask: '¿Cómo sentiste el sueño?',
          chips: [
            { v:'profundo', tx:'😴 Profundo' }, { v:'normal', tx:'🌙 Normal' },
            { v:'interrumpido', tx:'🥱 Interrumpido' }, { v:'mal', tx:'😣 Mal' },
          ],
          ack: v => ({
            profundo:'Perfecto.', normal:'Todo bien.',
            interrumpido:'Activaré modo nocturno hoy.', mal:'Lo trabajamos esta noche.',
          }[v] || 'Anotado.'),
          factLabel: v => `Calidad · ${labelOfChoice('sueno:quality', v)}`,
        },
      ],
    },

    foco: {
      title: 'Foco',
      color: '#a78bfa',
      role: 'Recopilación diaria',
      desc: 'Pregunto qué quieres priorizar. Después protejo tu calendario y silencio lo que sobra.',
      knownAlways: () => ['Sesiones esta semana · 3', 'Récord · 95 min ininterrumpidos'],
      questions: [
        {
          key: 'foco:priority',
          ask: '¿Qué te importa hoy?',
          chips: [
            { v:'trabajo', tx:'🎯 Trabajo profundo' }, { v:'calma', tx:'🧘 Calma' },
            { v:'gente', tx:'🤝 Conectar' }, { v:'cuerpo', tx:'💪 Mover el cuerpo' },
          ],
          ack: v => ({
            trabajo:'Bloqueo tu calendario 10–12.', calma:'Silencio notifs no esenciales.',
            gente:'Recordatorio a las 18h para escribir.', cuerpo:'Pausas activas cada 90 min.',
          }[v] || 'Listo.'),
          factLabel: v => `Intención · ${labelOfChoice('foco:priority', v)}`,
        },
      ],
    },

    energia: {
      title: 'Energía',
      color: '#84cc16',
      role: 'Recopilación diaria',
      desc: 'Comida, hidratación, movimiento. Pregunto poco — solo cuando veo señales raras.',
      knownAlways: () => ['Pasos hoy · 4.2k', 'Hidratación · 1.2L'],
      questions: [
        {
          key: 'energia:breakfast',
          ask: 'Para cerrar — ¿desayunaste hoy?',
          chips: [
            { v:'completo', tx:'🍳 Completo' }, { v:'ligero', tx:'☕ Ligero' },
            { v:'cafe', tx:'☕ Solo café' }, { v:'nada', tx:'🚫 Nada' },
          ],
          ack: v => v === 'nada' ? 'OK. Te recuerdo a las 11.' : 'Anotado.',
          factLabel: v => `Desayuno · ${labelOfChoice('energia:breakfast', v)}`,
        },
      ],
    },

    pantalla: {
      title: 'Pantalla',
      color: '#10b981',
      role: 'Pasivo · sensor',
      desc: 'Cuento minutos por categoría de app. No leo contenido — solo categorías.',
      passive: true,
      knownAlways: () => ['Tiempo hoy · 1h 42m', 'App top · YouTube 45m', 'Pico · 18–19h'],
      questions: [],
    },

    racha: {
      title: 'Racha',
      color: '#f59e0b',
      role: 'Pasivo · cálculo',
      desc: 'Calculo cuántos días seguidos cumples tus mínimos. Te aviso cuando estás a punto de romperla.',
      passive: true,
      knownAlways: () => ['Racha actual · 12 días', 'Récord · 14 días'],
      questions: [],
    },
  };

  // Helpers
  function labelOfChoice(qKey, v){
    for (const id of Object.keys(AGENTS)){
      for (const q of AGENTS[id].questions){
        if (q.key === qKey){
          const c = q.chips.find(c => c.v === v);
          if (c) return c.tx.replace(/^\S+\s/, '');
          return v;
        }
      }
    }
    return v;
  }
  function factsOf(agentId){
    const a = AGENTS[agentId];
    if (!a) return [];
    if (agentId === 'kairos') return a.knownAlways();
    const facts = [...(a.knownAlways ? a.knownAlways() : [])];
    for (const q of a.questions){
      if (state.answers[q.key]) facts.push(q.factLabel(state.answers[q.key]));
    }
    return facts;
  }
  function missingOf(agentId){
    const a = AGENTS[agentId];
    if (!a || a.passive) return [];
    return a.questions.filter(q => !state.answers[q.key]).map(q => promptShort(q.ask));
  }
  function promptShort(s){ return s.length > 38 ? s.slice(0, 36) + '…' : s; }
  function isComplete(agentId){
    const a = AGENTS[agentId];
    if (a.passive) return true;
    return a.questions.every(q => state.answers[q.key]);
  }
  function pctComplete(agentId){
    const a = AGENTS[agentId];
    if (a.passive) return 100;
    if (!a.questions.length) return 100;
    const done = a.questions.filter(q => state.answers[q.key]).length;
    return Math.round(done / a.questions.length * 100);
  }

  /* ═══════════ TOAST ═══════════ */
  const toastHost = document.createElement('div');
  toastHost.className = 'k-toasts';
  document.body.appendChild(toastHost);
  function toast(msg, kind='ok'){
    const el = document.createElement('div');
    el.className = 'k-toast ' + kind;
    el.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
        ${kind === 'ok' ? '<polyline points="20 6 9 17 4 12"/>' : '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'}
      </svg>
      <span>${msg}</span>
    `;
    toastHost.appendChild(el);
    requestAnimationFrame(()=> el.classList.add('show'));
    setTimeout(()=>{ el.classList.remove('show'); setTimeout(()=> el.remove(), 250); }, 3400);
  }

  /* ═══════════ SCRIM + OVERLAY CLOSE ═══════════ */
  const scrim = document.createElement('div');
  scrim.className = 'k-scrim';
  scrim.addEventListener('click', closeOverlays);
  document.body.appendChild(scrim);
  function closeOverlays(){
    document.querySelectorAll('.k-overlay.open').forEach(el => el.classList.remove('open'));
    scrim.classList.remove('open');
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeOverlays(); });

  /* ═══════════ CHAT OVERLAY ═══════════ */
  const chat = document.createElement('aside');
  chat.className = 'k-overlay k-chat';
  chat.innerHTML = `
    <header class="kc-head">
      <div class="kc-id">
        <div class="kc-orb" id="kc-orb">K</div>
        <div>
          <div class="kc-name" id="kc-mode-name">Kairós</div>
          <div class="kc-st"><span class="dot"></span><span id="kc-mode-sub">en línea</span></div>
        </div>
      </div>
      <button class="kc-close" aria-label="cerrar">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </header>
    <div class="kc-progress"><div class="kc-progress-bar" id="kc-pbar" style="width:0%"></div></div>
    <div class="kc-feed" id="kc-feed"></div>
    <footer class="kc-foot" id="kc-foot"></footer>
  `;
  document.body.appendChild(chat);
  chat.querySelector('.kc-close').addEventListener('click', closeOverlays);

  const feed = chat.querySelector('#kc-feed');
  const foot = chat.querySelector('#kc-foot');
  const pbar = chat.querySelector('#kc-pbar');
  const orbEl = chat.querySelector('#kc-orb');
  const modeName = chat.querySelector('#kc-mode-name');
  const modeSub = chat.querySelector('#kc-mode-sub');

  function setMode(mode){
    // mode: 'recopilacion' | 'kairos'
    if (mode === 'kairos'){
      modeName.textContent = 'Kairós Core';
      modeSub.textContent = 'sintetizador · en línea';
      orbEl.style.background = 'linear-gradient(135deg,#5eead4,#22d3ee,#38bdf8)';
      orbEl.textContent = 'K';
    } else {
      modeName.textContent = 'Recopilación del día';
      modeSub.textContent = 'tus 4 agentes';
      orbEl.style.background = 'linear-gradient(135deg,#fb7185 0%, #60a5fa 33%, #a78bfa 66%, #84cc16 100%)';
      orbEl.textContent = '·';
      orbEl.style.fontSize = '38px';
    }
  }

  function bubble(role, text, opts={}){
    const wrap = document.createElement('div');
    wrap.className = 'kc-bubble ' + role;
    if (opts.agent){
      wrap.innerHTML = `<div class="kc-agent" style="--c:${opts.color || '#5eead4'}"><span class="dot"></span>${opts.agent}</div><div class="kc-text">${text}</div>`;
    } else {
      wrap.innerHTML = `<div class="kc-text">${text}</div>`;
    }
    feed.appendChild(wrap);
    feed.scrollTop = feed.scrollHeight;
    return wrap;
  }

  function typing(color='#5eead4'){
    const wrap = document.createElement('div');
    wrap.className = 'kc-bubble bot typing';
    wrap.innerHTML = `<div class="kc-text"><span class="td" style="--c:${color}"><i></i><i></i><i></i></span></div>`;
    feed.appendChild(wrap);
    feed.scrollTop = feed.scrollHeight;
    return wrap;
  }

  function divider(text){
    const d = document.createElement('div');
    d.className = 'kc-divider';
    d.innerHTML = `<span class="ln"></span><span class="tx">${text}</span><span class="ln"></span>`;
    feed.appendChild(d);
    feed.scrollTop = feed.scrollHeight;
  }

  function renderChips(q, agentMeta){
    foot.innerHTML = '';
    const chips = document.createElement('div');
    chips.className = 'kc-chips';
    q.chips.forEach(c => {
      const b = document.createElement('button');
      b.className = 'kc-chip';
      b.textContent = c.tx;
      b.onclick = () => answerQuestion(q, agentMeta, c);
      chips.appendChild(b);
    });
    foot.appendChild(chips);
    // free input
    const inp = document.createElement('div');
    inp.className = 'kc-input';
    inp.innerHTML = `
      <input placeholder="o escríbeme algo…" />
      <button class="kc-send" aria-label="enviar">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
      </button>
    `;
    foot.appendChild(inp);
    const input = inp.querySelector('input');
    const send = () => {
      const v = input.value.trim();
      if (!v) return;
      answerQuestion(q, agentMeta, { v: 'free:'+v, tx: v });
    };
    inp.querySelector('.kc-send').onclick = send;
    input.addEventListener('keydown', e => { if (e.key==='Enter') send(); });
    setTimeout(()=> input.focus(), 100);
  }

  function answerQuestion(q, agentMeta, choice){
    state.answers[q.key] = choice.v;
    save();
    bubble('user', choice.tx);
    foot.innerHTML = '';
    const tp = typing(agentMeta.color);
    setTimeout(()=>{
      tp.remove();
      bubble('bot', q.ack ? q.ack(choice.v) : 'Anotado.',
        { agent: agentMeta.title, color: agentMeta.color });
      nextStep();
    }, 600 + Math.random()*350);
  }

  /* ───── Recopilación flow ───── */
  const FLOW_AGENTS = ['animo', 'sueno', 'foco', 'energia'];
  let flowState = { agentIdx: 0, questionIdx: 0, totalQuestions: 0, doneQuestions: 0 };

  function startRecopilacion(){
    feed.innerHTML = '';
    foot.innerHTML = '';
    setMode('recopilacion');
    flowState = { agentIdx: 0, questionIdx: 0, totalQuestions: 0, doneQuestions: 0 };
    FLOW_AGENTS.forEach(id => flowState.totalQuestions += AGENTS[id].questions.length);
    updateProgress();
    bubble('bot',
      'Buenos días, Alejandro.<br/>Voy a presentarte a 4 agentes — cada uno hace 1 o 2 preguntas cortas. Aproximadamente 4 min.',
      { agent: 'Kairós Core', color: '#5eead4' });
    setTimeout(introAgent, 700);
  }

  function introAgent(){
    const id = FLOW_AGENTS[flowState.agentIdx];
    const a = AGENTS[id];
    divider(`Te presento a ${a.title}`);
    const tp = typing(a.color);
    setTimeout(()=>{
      tp.remove();
      const greet = {
        animo:   'Hola, soy Ánimo. Te haré un par de preguntas.',
        sueno:   'Hola, soy Sueño. Una pregunta muy breve.',
        foco:    'Hola, soy Foco. Cuéntame qué te importa hoy.',
        energia: 'Hola, soy Energía. Última pregunta.',
      }[id];
      bubble('bot', greet, { agent: a.title, color: a.color });
      setTimeout(askCurrent, 500);
    }, 500);
  }

  function askCurrent(){
    const id = FLOW_AGENTS[flowState.agentIdx];
    const a = AGENTS[id];
    const q = a.questions[flowState.questionIdx];
    if (!q){
      // agent done, go next
      flowState.agentIdx++;
      flowState.questionIdx = 0;
      if (flowState.agentIdx < FLOW_AGENTS.length) introAgent();
      else finishRecopilacion();
      return;
    }
    const tp = typing(a.color);
    setTimeout(()=>{
      tp.remove();
      bubble('bot', q.ask, { agent: a.title, color: a.color });
      renderChips(q, a);
    }, 450);
  }

  function nextStep(){
    flowState.doneQuestions++;
    updateProgress();
    flowState.questionIdx++;
    const id = FLOW_AGENTS[flowState.agentIdx];
    const a = AGENTS[id];
    if (flowState.questionIdx >= a.questions.length){
      // agent finished
      flowState.agentIdx++;
      flowState.questionIdx = 0;
      if (flowState.agentIdx < FLOW_AGENTS.length){
        setTimeout(introAgent, 600);
      } else {
        setTimeout(finishRecopilacion, 700);
      }
    } else {
      setTimeout(askCurrent, 500);
    }
  }

  function updateProgress(){
    const pct = flowState.totalQuestions ? Math.round(flowState.doneQuestions / flowState.totalQuestions * 100) : 0;
    pbar.style.width = pct + '%';
  }

  function finishRecopilacion(){
    state.checkinDone = true;
    save();
    divider('Listo');
    const tp = typing();
    setTimeout(()=>{
      tp.remove();
      const summary = FLOW_AGENTS
        .map(id => AGENTS[id].title + ' ✓')
        .join(' · ');
      bubble('bot',
        `<b>Recopilación completa.</b><br/>${summary}<br/><span class="kc-muted">Cada agente actualizó su panel. Habla conmigo cuando quieras una mirada global.</span>`,
        { agent: 'Kairós Core', color: '#5eead4' });
      foot.innerHTML = `
        <div class="kc-finish">
          <button class="kc-done">Cerrar</button>
          <button class="kc-done ghost" id="goto-kairos">↗ Hablar con Kairós Core</button>
        </div>
      `;
      foot.querySelector('.kc-done').onclick = () => {
        closeOverlays();
        renderAgentsBoard();
        reflectCheckinDone();
        toast('Recopilación guardada · los agentes actualizaron sus paneles', 'ok');
      };
      foot.querySelector('#goto-kairos').onclick = () => {
        renderAgentsBoard();
        reflectCheckinDone();
        startKairosCore();
      };
    }, 700);
  }

  /* ───── Kairós Core chat ───── */
  function startKairosCore(){
    feed.innerHTML = '';
    foot.innerHTML = '';
    setMode('kairos');
    pbar.style.width = '100%';
    const factsByAgent = FLOW_AGENTS.map(id => `<b style="color:${AGENTS[id].color}">${AGENTS[id].title}</b> ${isComplete(id) ? '✓' : '○'}`).join(' · ');
    bubble('bot',
      `Soy Kairós Core. Tengo lo que aprendieron los demás hoy:<br/><span class="kc-muted">${factsByAgent}</span>`,
      { agent: 'Kairós Core', color: '#5eead4' });
    setTimeout(()=>{
      bubble('bot', synthesisLine(), { agent: 'Kairós Core', color: '#5eead4' });
      renderKairosFreeform();
    }, 800);
  }

  function synthesisLine(){
    const mood = state.answers['animo:now'];
    const sleep = state.answers['sueno:quality'];
    const focus = state.answers['foco:priority'];
    if (!mood && !sleep && !focus){
      return 'Aún no tengo tu recopilación de hoy. Cuando termines, cruzo todo y te doy una lectura del día.';
    }
    const parts = [];
    if (mood) parts.push(`tu ánimo es <b>${labelOfChoice('animo:now', mood)}</b>`);
    if (sleep) parts.push(`dormiste <b>${labelOfChoice('sueno:quality', sleep)}</b>`);
    if (focus) parts.push(`quieres priorizar <b>${labelOfChoice('foco:priority', focus)}</b>`);
    return 'Hoy ' + parts.join(', ') + '. ¿Qué quieres mirar primero?';
  }

  function renderKairosFreeform(){
    foot.innerHTML = '';
    const suggestions = [
      { tx: 'Resumen del día', a: 'Tu día se ve estable. Pantalla baja, ánimo bien, sueño normal. Mantén el ritmo de mañana — es donde te vas mejor.' },
      { tx: '¿Qué playbook activo?', a: 'Recomiendo <b>"uso nocturno"</b> — corta pantalla a las 22:30 y cruza con tu calidad de sueño. Lo activo si me dices.' },
      { tx: '¿Cómo voy esta semana?', a: 'Sueño 6/7 ✓ · Pantalla 4/7 ✓ · Movimiento 5/7. Tu peor día fue el lunes — coincide con poco sueño y mucha pantalla nocturna.' },
      { tx: 'Hablar con un agente', a: 'Claro. Toca cualquier nombre en tu panel y abro su detalle. Si quieres añadir algo, puedo lanzarte solo esa conversación.' },
    ];
    const chips = document.createElement('div');
    chips.className = 'kc-chips';
    suggestions.forEach(s => {
      const b = document.createElement('button');
      b.className = 'kc-chip';
      b.textContent = s.tx;
      b.onclick = () => {
        bubble('user', s.tx);
        const tp = typing();
        setTimeout(()=>{
          tp.remove();
          bubble('bot', s.a, { agent: 'Kairós Core', color: '#5eead4' });
        }, 700);
      };
      chips.appendChild(b);
    });
    foot.appendChild(chips);
    const inp = document.createElement('div');
    inp.className = 'kc-input';
    inp.innerHTML = `
      <input placeholder="Pregúntame algo…" />
      <button class="kc-send" aria-label="enviar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></button>
    `;
    foot.appendChild(inp);
    const input = inp.querySelector('input');
    const send = () => {
      const v = input.value.trim();
      if (!v) return;
      bubble('user', v);
      input.value = '';
      const tp = typing();
      setTimeout(()=>{
        tp.remove();
        bubble('bot', 'Lo proceso y te respondo con datos de tus agentes. Por ahora — estoy en modo demo y solo respondo las sugerencias de arriba 😉',
          { agent: 'Kairós Core', color: '#5eead4' });
      }, 700);
    };
    inp.querySelector('.kc-send').onclick = send;
    input.addEventListener('keydown', e => { if (e.key==='Enter') send(); });
  }

  /* ───── Single-agent chat (cuando hablas solo con uno) ───── */
  function startSingleAgent(id){
    const a = AGENTS[id];
    feed.innerHTML = '';
    foot.innerHTML = '';
    setMode('recopilacion');
    modeName.textContent = a.title;
    modeSub.textContent = a.role.toLowerCase();
    orbEl.style.background = `linear-gradient(135deg, ${a.color}, ${a.color}dd)`;
    orbEl.textContent = a.title[0];
    orbEl.style.fontSize = '18px';
    pbar.style.width = '0%';
    const missing = a.questions.filter(q => !state.answers[q.key]);
    if (!missing.length){
      bubble('bot', 'Ya tengo tu información de hoy. Si quieres editar algo, pídemelo.', { agent: a.title, color: a.color });
      foot.innerHTML = `<div class="kc-finish"><button class="kc-done">Cerrar</button></div>`;
      foot.querySelector('.kc-done').onclick = closeOverlays;
      return;
    }
    bubble('bot', `Hola, soy ${a.title}.`, { agent: a.title, color: a.color });
    let i = 0;
    const ask = () => {
      if (i >= missing.length){
        bubble('bot', '¡Listo! Actualicé mi panel.', { agent: a.title, color: a.color });
        foot.innerHTML = `<div class="kc-finish"><button class="kc-done">Cerrar</button></div>`;
        foot.querySelector('.kc-done').onclick = () => { closeOverlays(); renderAgentsBoard(); toast(`${a.title} actualizado`, 'ok'); };
        return;
      }
      const q = missing[i];
      const tp = typing(a.color);
      setTimeout(()=>{
        tp.remove();
        bubble('bot', q.ask, { agent: a.title, color: a.color });
        // wire chips
        foot.innerHTML = '';
        const chips = document.createElement('div'); chips.className = 'kc-chips';
        q.chips.forEach(c => {
          const b = document.createElement('button');
          b.className = 'kc-chip'; b.textContent = c.tx;
          b.onclick = () => {
            state.answers[q.key] = c.v; save();
            bubble('user', c.tx);
            foot.innerHTML = '';
            const tp2 = typing(a.color);
            setTimeout(()=>{
              tp2.remove();
              bubble('bot', q.ack ? q.ack(c.v) : 'Anotado.', { agent: a.title, color: a.color });
              i++;
              setTimeout(ask, 500);
            }, 600);
          };
          chips.appendChild(b);
        });
        foot.appendChild(chips);
      }, 500);
    };
    setTimeout(ask, 600);
  }

  function openChat(mode='recopilacion'){
    chat.classList.add('open');
    scrim.classList.add('open');
    if (mode === 'kairos') startKairosCore();
    else if (mode.startsWith('agent:')) startSingleAgent(mode.split(':')[1]);
    else startRecopilacion();
  }

  /* ═══════════ AGENT DRAWER (detail) ═══════════ */
  const drawer = document.createElement('aside');
  drawer.className = 'k-overlay k-drawer';
  drawer.innerHTML = `
    <header class="kd-head">
      <button class="kd-back" aria-label="cerrar">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><path d="m15 18-6-6 6-6"/></svg>
      </button>
      <span class="kd-crumb">Agente</span>
    </header>
    <div class="kd-body" id="kd-body"></div>
  `;
  document.body.appendChild(drawer);
  drawer.querySelector('.kd-back').addEventListener('click', closeOverlays);

  function openAgentDrawer(id){
    const a = AGENTS[id];
    if (!a) return;
    const known = factsOf(id);
    const missing = a.passive ? [] : a.questions.filter(q => !state.answers[q.key]);
    const pct = pctComplete(id);
    drawer.querySelector('#kd-body').innerHTML = `
      <div class="kd-hero" style="--c:${a.color}">
        <div class="kd-avatar" style="background:${a.color}">${a.title[0]}</div>
        <div>
          <h2>${a.title}</h2>
          <div class="kd-meta">
            <span class="kd-role">${a.role}</span>
            ${a.passive ? '<span class="kd-ok">sensor</span>' : (missing.length ? '<span class="kd-pending">'+missing.length+' pendiente'+(missing.length>1?'s':'')+'</span>' : '<span class="kd-ok">al día</span>')}
          </div>
        </div>
      </div>
      <p class="kd-desc">${a.desc}</p>

      <div class="kd-section-t">Información que tengo · <span class="mono" style="color:#0c9a6c">${pct}%</span></div>
      <ul class="kd-flist">
        ${known.length ? known.map(k => `<li class="ok"><span class="ic">✓</span><span>${k}</span></li>`).join('') : '<li class="empty">—</li>'}
      </ul>

      ${missing.length ? `
        <div class="kd-section-t">Lo que me falta</div>
        <ul class="kd-flist">
          ${missing.map(q => `<li class="miss"><span class="ic">○</span><span>${q.ask.replace(/—.*/, '').trim()}</span></li>`).join('')}
        </ul>
        <button class="kd-cta" data-action="chat" data-id="${id}">
          Hablar con ${a.title} ahora
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><path d="M5 12h14M13 5l7 7-7 7"/></svg>
        </button>
      ` : ''}

      <div class="kd-section-t">Acciones</div>
      <div class="kd-links">
        <button class="kd-link" data-act="historial"><span>Ver historial completo</span><span>→</span></button>
        <button class="kd-link" data-act="ajustar"><span>Ajustar tono y frecuencia</span><span>→</span></button>
        ${id !== 'kairos' ? '<button class="kd-link" data-act="pausar"><span>Pausar este agente</span><span>→</span></button>' : ''}
      </div>
    `;
    drawer.querySelectorAll('.kd-link').forEach(l => l.onclick = () => toast(`Próximamente: ${l.querySelector('span').textContent}`, 'ok'));
    const cta = drawer.querySelector('.kd-cta');
    if (cta) cta.onclick = () => { closeOverlays(); openChat('agent:'+id); };
    drawer.classList.add('open');
    scrim.classList.add('open');
  }

  /* ═══════════ TOOL MODAL ═══════════ */
  const modal = document.createElement('div');
  modal.className = 'k-overlay k-modal';
  modal.innerHTML = `<div class="km-body" id="km-body"></div>`;
  document.body.appendChild(modal);
  function openTool(name, desc, soon=false){
    modal.querySelector('#km-body').innerHTML = `
      <button class="km-close" aria-label="cerrar">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
      ${soon ? '<div class="km-tag">PRÓXIMAMENTE</div>' : '<div class="km-tag k-ok">DISPONIBLE</div>'}
      <h2>${name}</h2>
      <p>${desc}</p>
      <div class="km-actions">
        ${soon
          ? `<button class="kc-done">Avísame cuando esté listo</button>`
          : `<button class="kc-done">Abrir ${name}</button>`}
        <button class="cta-ghost" style="color:var(--ink-2);border-color:var(--line)">Cerrar</button>
      </div>
    `;
    modal.querySelector('.km-close').onclick = closeOverlays;
    modal.querySelectorAll('.cta-ghost, .kc-done').forEach(b => b.onclick = () => {
      closeOverlays();
      if (soon) toast(`Te aviso cuando ${name} esté disponible`, 'ok');
      else toast(`Abriendo ${name}…`, 'ok');
    });
    modal.classList.add('open');
    scrim.classList.add('open');
  }

  /* ═══════════ AGENTS BOARD (rendered into placeholder) ═══════════ */
  function renderAgentsBoard(){
    const mount = document.getElementById('agents-board-mount');
    if (!mount) return;

    const all = Object.keys(AGENTS);
    const cards = all.map(id => agentCardHTML(id)).join('');
    const totalPct = Math.round(
      ['animo','sueno','foco','energia']
        .map(id => pctComplete(id))
        .reduce((a,b)=>a+b,0) / 4
    );

    mount.innerHTML = `
      <div class="sect-h fade d4">
        <h3>Tus agentes</h3>
        <span class="ln"></span>
        <span class="sub">Recopilación de hoy · <b style="color:#0c9a6c">${totalPct}%</b></span>
      </div>

      <div class="ab-cta-row fade d4">
        <button class="ab-cta primary" id="ab-start">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          ${state.checkinDone ? 'Repetir recopilación' : 'Empezar recopilación del día'}
          <span class="ab-sub">4 agentes · ≈ 4 min</span>
        </button>
        <button class="ab-cta secondary" id="ab-kairos">
          <span class="ab-orb">K</span>
          Hablar con Kairós Core
          <span class="ab-sub">conoce todo</span>
        </button>
      </div>

      <div class="ab-grid">${cards}</div>
    `;

    mount.querySelector('#ab-start').onclick = () => openChat('recopilacion');
    mount.querySelector('#ab-kairos').onclick = () => openChat('kairos');
    mount.querySelectorAll('.ab-card').forEach(card => {
      const id = card.dataset.id;
      card.querySelector('.ab-card-cta')?.addEventListener('click', e => {
        e.stopPropagation();
        const act = e.currentTarget.dataset.act;
        if (act === 'chat-agent') openChat('agent:' + id);
        else if (act === 'kairos') openChat('kairos');
      });
      card.querySelector('.ab-detail')?.addEventListener('click', e => {
        e.stopPropagation();
        openAgentDrawer(id);
      });
      card.addEventListener('click', () => openAgentDrawer(id));
    });
  }

  function agentCardHTML(id){
    const a = AGENTS[id];
    const known = factsOf(id);
    const missing = a.passive ? [] : a.questions.filter(q => !state.answers[q.key]);
    const pct = pctComplete(id);
    const isMain = id === 'kairos';
    const ctaLabel = isMain
      ? 'Hablar con Kairós' : (missing.length ? `Conversar (${missing.length})` : 'Al día');
    const ctaAct = isMain ? 'kairos' : 'chat-agent';
    const knownItems = known.slice(0, isMain ? 5 : 3).map(k => `<li class="ok"><span class="ic">✓</span><span>${k}</span></li>`).join('');
    const missingItems = missing.slice(0, 3).map(q => `<li class="miss"><span class="ic">○</span><span>${q.ask.replace(/^¿/, '').replace(/\?$/, '').slice(0,46)}</span></li>`).join('');

    return `
      <article class="ab-card ${isMain ? 'principal' : ''} ${missing.length === 0 && !isMain ? 'complete' : ''}" data-id="${id}" style="--c:${a.color}">
        <div class="ab-card-head">
          <span class="ab-card-dot" style="background:${a.color}"></span>
          <div>
            <div class="ab-card-name">${a.title}${isMain ? ' <span class="ab-tag">principal</span>' : ''}</div>
            <div class="ab-card-role">${a.role}</div>
          </div>
          <div class="ab-card-pct">${pct}%</div>
        </div>
        <div class="ab-card-meter"><div class="ab-card-meter-fill" style="width:${pct}%; background:${a.color}"></div></div>
        <ul class="ab-info-list">
          ${knownItems}
          ${missingItems}
          ${(!known.length && !missing.length) ? '<li class="empty">Sin datos aún</li>' : ''}
        </ul>
        <div class="ab-card-actions">
          <button class="ab-card-cta" data-act="${ctaAct}">${ctaLabel} →</button>
          ${!isMain ? '<button class="ab-detail">Detalle</button>' : ''}
        </div>
      </article>
    `;
  }

  /* ═══════════ REFLECT CHECK-IN DONE IN HERO ═══════════ */
  function reflectCheckinDone(){
    if (!state.checkinDone) return;
    const ci = document.querySelector('.checkin');
    if (ci && !ci.dataset.done){
      ci.dataset.done = '1';
      ci.classList.add('checkin-done');
      ci.innerHTML = `
        <div>
          <div class="cm-meta">
            <span class="cm-badge cm-badge-ok">Recopilación completa</span>
            <span class="cm-time">próxima · mañana 9:00</span>
          </div>
          <h2>Hoy <em>tus agentes</em><br/>tienen contexto fresco.</h2>
          <p>Todos los datos están sincronizados con Kairós Core. Pídeme una lectura del día cuando quieras.</p>
          <div class="cm-actions">
            <button class="cta" id="ci-kairos">↗ Hablar con Kairós Core</button>
            <button class="cta-ghost" id="ci-edit">Editar respuestas</button>
          </div>
          <div class="cm-agents">
            <span class="lbl">HOY PARTICIPARON</span>
            <span class="cm-chip cm-chip-ok" style="--c:#fb7185">Ánimo ✓</span>
            <span class="cm-chip cm-chip-ok" style="--c:#60a5fa">Sueño ✓</span>
            <span class="cm-chip cm-chip-ok" style="--c:#a78bfa">Foco ✓</span>
            <span class="cm-chip cm-chip-ok" style="--c:#84cc16">Energía ✓</span>
          </div>
        </div>
        <div class="checkin-orb" aria-hidden="true">
          <div class="orb-glow"></div>
          <div class="orb-K">K</div>
        </div>
      `;
      ci.querySelector('#ci-kairos').onclick = () => openChat('kairos');
      ci.querySelector('#ci-edit').onclick = () => openChat('recopilacion');
    }
    // Right panel agents
    document.querySelectorAll('.right .agent .status').forEach(s => {
      if (s.textContent.toLowerCase().includes('pendiente')){
        s.textContent = 'al día ✓';
        s.style.color = '#0c9a6c';
      }
    });
  }

  /* ═══════════ WIRING ═══════════ */
  function wire(){
    // Hero CTA + topbar CTA + chat CTA → open recopilacion (or Kairós if done)
    document.querySelectorAll('.checkin .cta:not(.cta-ghost), .topbar .cta, .chat-cta').forEach(el => {
      el.removeAttribute('onclick');
      el.addEventListener('click', e => {
        e.preventDefault();
        if (state.checkinDone) openChat('kairos');
        else openChat('recopilacion');
      });
    });
    // Recordar más tarde
    document.querySelectorAll('.cta-ghost').forEach(el => {
      if (el.textContent.trim() === 'Recordar más tarde') el.addEventListener('click', () => toast('Te aviso en 2 horas', 'ok'));
    });
    // Agent chips inside check-in
    document.querySelectorAll('.cm-chip').forEach(el => {
      const tx = el.textContent.trim().toLowerCase().split(' ')[0];
      const map = { 'ánimo':'animo', 'animo':'animo', 'sueño':'sueno', 'sueno':'sueno', 'foco':'foco', 'energía':'energia', 'energia':'energia' };
      const key = map[tx];
      if (!key) return;
      el.style.cursor = 'pointer';
      el.addEventListener('click', () => openAgentDrawer(key));
    });
    // Right-panel agents
    const order = ['kairos','animo','sueno','pantalla','foco','energia'];
    document.querySelectorAll('.right .agent').forEach((el, i) => {
      el.style.cursor = 'pointer';
      el.addEventListener('click', () => openAgentDrawer(order[i] || 'kairos'));
    });
    // Right panel "ver todos →"
    document.querySelectorAll('.right .panel-title a').forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        document.getElementById('agents-board-mount')?.scrollIntoView({ behavior:'smooth', block:'start' });
        flashAgentsBoard();
      });
    });
    // Insight Activar
    document.querySelectorAll('.insight .cta').forEach(el => {
      el.addEventListener('click', () => {
        state.activatedPlaybooks.push('uso-nocturno'); save();
        toast('Playbook "Uso nocturno" activado · te aviso a las 22:30', 'ok');
        el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Activado`;
        el.style.background = 'rgba(16,185,129,.15)';
        el.style.color = '#0c9a6c';
        el.style.boxShadow = 'inset 0 0 0 1px rgba(16,185,129,.3)';
        el.disabled = true;
      });
    });
    // Tools
    const toolMeta = {
      finanzas:      ['Finanzas', 'Conecta tu banco. Kairós cruza gastos con tu ánimo para encontrar patrones emocionales.', true],
      meditacion:    ['Meditación', 'Sesiones guiadas. El agente de Foco sugiere cuál hacer según tu día.', false],
      lectura:       ['Lectura', 'Tu lista activa con 3 libros en curso.', false],
      fitness:       ['Fitness', 'Rutinas y registro. Conecta Google Fit o Apple Health.', false],
      productividad: ['Productividad', 'Tareas, rituales y bloqueos automáticos de calendario.', true],
      diario:        ['Diario', 'Tu bitácora privada. Aquí se guardan check-ins y notas libres.', false],
    };
    document.querySelectorAll('.tool').forEach(el => {
      const key = (el.getAttribute('href') || '').replace('#','');
      const meta = toolMeta[key];
      if (!meta) return;
      el.addEventListener('click', e => {
        e.preventDefault();
        openTool(meta[0], meta[1], meta[2]);
      });
    });
    // Sidebar nav items
    const sideItems = document.querySelectorAll('.side .nav-item');
    const navMap = ['dashboard', 'chat', 'habitos', 'agentes', 'herramientas', 'analytics', 'ajustes'];
    sideItems.forEach((el, i) => {
      const k = navMap[i];
      if (!k) return;
      el.addEventListener('click', () => {
        if (k === 'chat'){
          // Chat opens overlay, keep current view active
          openChat(state.checkinDone ? 'kairos' : 'recopilacion');
          return;
        }
        showView(k);
      });
    });
    // Avatar
    const avatar = document.querySelector('.side .avatar');
    if (avatar) avatar.addEventListener('click', () => toast('Próximamente: tu perfil', 'ok'));
    // Side logo → Kairós Core
    const logo = document.querySelector('.side .logo');
    if (logo){
      logo.style.cursor = 'pointer';
      logo.addEventListener('click', () => openChat('kairos'));
    }
    // Initial paint
    renderAgentsBoard();
    reflectCheckinDone();
    // Initial view from hash
    const initialHash = location.hash.replace('#','').split('?')[0];
    showView(initialHash || 'dashboard');
    // Wire herramientas tab filters
    wireHerramientasTabs();
  }

  function wireHerramientasTabs(){
    const tabs = document.querySelectorAll('.hr-tab');
    if (!tabs.length) return;
    tabs.forEach(t => {
      t.addEventListener('click', () => {
        tabs.forEach(tt => tt.classList.remove('active'));
        t.classList.add('active');
        const f = t.dataset.filter;
        document.querySelectorAll('.hr-card').forEach(c => {
          const st = c.dataset.status;
          let show = true;
          if (f === 'connected') show = st === 'connected';
          else if (f === 'available') show = st === 'available';
          else if (f === 'soon') show = st === 'soon';
          c.style.display = show ? '' : 'none';
        });
        // hide section headers if their grid is empty
        document.querySelectorAll('.hr-section-t').forEach(h => {
          const g = h.nextElementSibling;
          if (!g || !g.classList.contains('hr-grid')) return;
          const visible = Array.from(g.children).some(c => c.style.display !== 'none');
          h.style.display = visible ? '' : 'none';
          g.style.display = visible ? '' : 'none';
        });
      });
    });

    // Wire hr-card buttons + featured
    document.querySelectorAll('.hr-card').forEach(card => {
      const name = card.querySelector('.hr-card-name')?.textContent.trim() || 'herramienta';
      const status = card.dataset.status;
      card.querySelectorAll('button').forEach(b => {
        b.addEventListener('click', e => {
          e.stopPropagation();
          const label = b.textContent.trim();
          if (status === 'connected'){
            toast(`Abriendo ${name}…`, 'ok');
          } else if (status === 'available' && label.includes('Conectar')){
            b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" width="12" height="12"><polyline points="20 6 9 17 4 12"/></svg> Conectado';
            b.classList.remove('primary');
            b.disabled = true;
            b.style.background = 'rgba(16,185,129,.12)';
            b.style.color = '#0c9a6c';
            toast(`${name} conectado · sincronizando datos`, 'ok');
          } else if (status === 'soon'){
            toast(`Te aviso cuando ${name} esté disponible`, 'ok');
          } else {
            toast(`${label}: ${name}`, 'ok');
          }
        });
      });
    });

    // Featured card buttons
    document.querySelectorAll('.hr-feat-cta, .hr-feat-ghost').forEach(b => {
      b.addEventListener('click', () => {
        if (b.classList.contains('hr-feat-cta')) toast('Iniciando sesión de meditación · 8 min', 'ok');
        else toast('Buscando otra sugerencia para ti…', 'ok');
      });
    });

    // Hábitos checks
    document.querySelectorAll('.hb-check').forEach(c => {
      c.addEventListener('click', () => {
        const row = c.closest('.hb-row');
        const done = row.dataset.done === '1';
        row.dataset.done = done ? '0' : '1';
        const today = row.querySelector('.hb-week .d.today');
        if (today){
          today.classList.toggle('done');
        }
        toast(done ? 'Hábito desmarcado' : '¡Bien! Hábito completado', 'ok');
      });
    });

    // Hábitos add
    document.querySelector('.hb-add')?.addEventListener('click', () => toast('Próximamente: editor de hábitos', 'ok'));

    // Settings toggles + buttons
    document.querySelectorAll('.set-toggle').forEach(t => t.addEventListener('click', () => {
      t.classList.toggle('on');
      const on = t.classList.contains('on');
      t.setAttribute('aria-checked', on);
      toast(on ? 'Notificación activada' : 'Notificación desactivada', 'ok');
    }));
    document.querySelectorAll('.set-seg button').forEach(b => b.addEventListener('click', () => {
      b.parentElement.querySelectorAll('button').forEach(bb => bb.classList.remove('on'));
      b.classList.add('on');
      toast(`Tono: ${b.textContent.trim()}`, 'ok');
    }));
    document.querySelectorAll('.set-btn').forEach(b => b.addEventListener('click', e => {
      e.stopPropagation();
      const tx = b.textContent.trim();
      if (tx.toLowerCase().includes('eliminar')) toast('No vamos a hacer eso 🙂 — modo demo', 'warn');
      else toast(tx + '…', 'ok');
    }));

    // Request herramienta
    document.querySelector('.hr-request .hr-btn')?.addEventListener('click', () => toast('Cuéntanos qué herramienta necesitas en tu próximo check-in', 'ok'));

  function flashAgentsBoard(){
    const mount = document.getElementById('agents-board-mount');
    if (!mount) return;
    mount.style.transition = 'background .4s ease';
    mount.style.background = 'linear-gradient(180deg, rgba(16,185,129,.08), transparent)';
    setTimeout(()=> mount.style.background = '', 700);
  }

  /* ═══════════ ROUTER ═══════════ */
  const NAV_TO_INDEX = { dashboard:0, chat:1, habitos:2, agentes:3, herramientas:4, analytics:5, ajustes:6 };
  function showView(name){
    if (!name || !document.querySelector(`[data-view="${name}"]`)) name = 'dashboard';
    document.querySelectorAll('[data-view]').forEach(v => v.classList.toggle('view-active', v.dataset.view === name));
    document.body.dataset.view = name;
    // sidebar active state
    const sideItems = document.querySelectorAll('.side .nav-item');
    sideItems.forEach(s => s.classList.remove('active'));
    const idx = NAV_TO_INDEX[name];
    if (sideItems[idx]) sideItems[idx].classList.add('active');
    // scroll to top of main column
    document.querySelector('main')?.scrollTo?.({ top:0 });
    window.scrollTo({ top: 0, behavior: 'instant' });
    // re-render agents board when entering /agentes (in case state changed)
    if (name === 'agentes') renderAgentsBoard();
    // Update hash without triggering scroll
    if (location.hash.slice(1).split('?')[0] !== name){
      history.replaceState(null, '', '#' + name);
    }
  }
  window.addEventListener('hashchange', () => {
    const h = location.hash.replace('#','').split('?')[0];
    showView(h || 'dashboard');
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();

  // Dev reset: Shift+R
  document.addEventListener('keydown', e => {
    if (e.key === 'R' && e.shiftKey){
      sessionStorage.removeItem(STATE_KEY);
      location.reload();
    }
  });

  // Expose for debug
  window.__kairos = { state, AGENTS, openChat, openAgentDrawer, renderAgentsBoard };
})();
