/* ═══════════════════════════════════════════════════════════════
   APEX Dashboard — Frontend Logic
   WebSocket real-time updates + signal rendering
═══════════════════════════════════════════════════════════════ */

'use strict';

// ─── State ───────────────────────────────────────────────────
const State = {
  ws: null,
  wsReconnectTimer: null,
  signals: [],
  filter: 'all',
  selectedPair: null,
  scanInProgress: false,
};

// ─── DOM shortcuts ────────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};

// ─── Init ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  startClock();
});

// ═══════════════════════════════════════════════════════════════
// WEBSOCKET
// ═══════════════════════════════════════════════════════════════
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/ws`;

  State.ws = new WebSocket(url);

  State.ws.onopen = () => {
    setConnStatus(true);
    clearTimeout(State.wsReconnectTimer);
    toast('APEX 연결됨', 'success');
  };

  State.ws.onclose = () => {
    setConnStatus(false);
    State.wsReconnectTimer = setTimeout(connectWS, 3000);
  };

  State.ws.onerror = () => setConnStatus(false);

  State.ws.onmessage = e => {
    try { handleMessage(JSON.parse(e.data)); }
    catch(err) { console.error('WS parse error', err); }
  };
}

function wsSend(obj) {
  if (State.ws && State.ws.readyState === WebSocket.OPEN) {
    State.ws.send(JSON.stringify(obj));
  }
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'init':
      applyStatus(msg.data.status);
      if (msg.data.signals?.length) {
        State.signals = msg.data.signals;
        renderSignals();
        buildPairList();
      }
      updateScanBar(msg.data.last_scan, !msg.data.scan_in_progress);
      break;

    case 'scan_started':
      onScanStarted();
      break;

    case 'scan_progress':
      onScanProgress(msg.symbol, msg.current, msg.total);
      break;

    case 'pair_result':
      onPairResult(msg.symbol, msg.data);
      break;

    case 'scan_complete':
      State.signals = msg.signals || [];
      onScanComplete(msg.valid_count, msg.total, msg.ts);
      break;

    case 'scan_error':
      setScanInProgress(false);
      toast(`스캔 오류: ${msg.message}`, 'error');
      break;

    case 'single_signal':
      onSingleSignal(msg.symbol, msg.data);
      break;

    case 'analysing':
      toast(`${msg.symbol} 분석 중...`, 'info');
      break;

    case 'ping':
      wsSend({ action: 'pong' });
      break;
  }
}

// ═══════════════════════════════════════════════════════════════
// SCAN ACTIONS
// ═══════════════════════════════════════════════════════════════
function triggerScan() {           // called from HTML
  if (State.scanInProgress) return;
  wsSend({ action: 'scan' });
}

function analyseSingle() {         // called from HTML
  const sym = $('pair-input').value.trim();
  if (!sym) return;
  wsSend({ action: 'analyse_pair', symbol: sym });
}

function onScanStarted() {
  setScanInProgress(true);
  $('scan-overlay').classList.remove('hidden');
  $('scan-overlay-pair').textContent = '페어 목록 불러오는 중...';
  $('scan-overlay-progress').textContent = '0 / 0';
}

function onScanProgress(symbol, current, total) {
  const pct = Math.round(current / total * 100);
  $('scan-progress-fill').style.width = pct + '%';
  $('scan-overlay-pair').textContent = symbol.replace(':USDT','');
  $('scan-overlay-progress').textContent = `${current} / ${total}`;
  $('scan-bar-label').textContent = `스캔 중: ${symbol.replace(':USDT','')} (${current}/${total})`;
}

function onPairResult(symbol, data) {
  // Update or add to signals list
  const idx = State.signals.findIndex(s => s.pair === symbol);
  if (idx >= 0) State.signals[idx] = data;
  else State.signals.push(data);

  // Refresh one card
  renderSignals();
  buildPairList();
}

function onScanComplete(validCount, total, ts) {
  setScanInProgress(false);
  $('scan-overlay').classList.add('hidden');
  $('scan-progress-fill').style.width = '100%';
  updateScanBar(ts ? new Date(ts * 1000).toISOString() : null, true);
  $('scan-valid-count').textContent = `✓ ${validCount}개 시그널`;
  $('scan-total-count').textContent = `${total}페어 스캔`;
  renderSignals();
  buildPairList();
  toast(`스캔 완료 — ${validCount}개 시그널 (총 ${total}페어)`, validCount > 0 ? 'success' : 'info');
}

function onSingleSignal(symbol, data) {
  // Merge into signals
  const idx = State.signals.findIndex(s => s.pair === (symbol || data.pair));
  if (idx >= 0) State.signals[idx] = data;
  else State.signals.unshift(data);

  renderSignals();
  buildPairList();
  if (data.valid) openDetail(data);
  toast(data.valid
    ? `${data.pair.replace(':USDT','')} ${data.direction} ${data.grade} 시그널 생성됨`
    : `${data.pair.replace(':USDT','')} — 시그널 없음: ${data.reason}`,
    data.valid ? 'success' : 'warning'
  );
}

function setScanInProgress(v) {
  State.scanInProgress = v;
  const btn = $('btn-scan');
  btn.disabled = v;
  btn.classList.toggle('scanning', v);
  btn.querySelector('.btn-scan-icon').textContent = v ? '◎' : '◎';
}

function updateScanBar(isoTime, done) {
  if (isoTime) {
    const d = new Date(isoTime);
    $('scan-bar-label').textContent = `마지막 스캔: ${d.toLocaleTimeString('ko-KR')}`;
  }
  if (done) $('scan-progress-fill').style.width = '100%';
}

// ═══════════════════════════════════════════════════════════════
// STATUS / RISK
// ═══════════════════════════════════════════════════════════════
function applyStatus(s) {
  if (!s) return;

  // Header pills
  setText('hdr-balance',    fmtUSDT(s.account_balance));
  setValColor('hdr-daily-pnl',  s.daily_pnl,  fmtPnL(s.daily_pnl));
  setValColor('hdr-weekly-pnl', s.weekly_pnl, fmtPnL(s.weekly_pnl));
  setText('hdr-positions', `${s.open_positions} / ${s.max_positions}`);

  // Risk gauges
  const dPct = Math.min(100, s.daily_drawdown_pct  / s.daily_drawdown_limit_pct  * 100);
  const wPct = Math.min(100, s.weekly_drawdown_pct / s.weekly_drawdown_limit_pct * 100);

  setGauge('gauge-daily',  dPct, 'daily');
  setGauge('gauge-weekly', wPct, 'weekly');
  setText('daily-dd-val',  s.daily_drawdown_pct.toFixed(2) + '%');
  setText('weekly-dd-val', s.weekly_drawdown_pct.toFixed(2) + '%');

  // Halt banner
  const banner = $('halt-banner');
  if (s.trading_halted) {
    banner.classList.remove('hidden');
    setText('halt-reason', s.halt_reason || '—');
  } else {
    banner.classList.add('hidden');
  }

  // Badge count
  setText('pos-count', s.open_positions);

  // Positions list
  renderPositions(s.positions || []);
}

function setGauge(gaugeId, pct, type) {
  const fill = $(gaugeId);
  fill.style.width = pct + '%';
  fill.className = `gauge-fill gauge-fill--${type}`;
  if (pct >= 80) fill.classList.add('danger');
  else if (pct >= 60) fill.classList.add('warning');
}

function renderPositions(positions) {
  const list = $('positions-list');
  list.innerHTML = '';
  if (!positions.length) {
    list.innerHTML = '<div class="empty-state">오픈 포지션 없음</div>';
    return;
  }
  positions.forEach(p => {
    const dir = p.direction.toLowerCase();
    const pnlSign = p.unrealized_pnl >= 0 ? 'positive' : 'negative';
    const div = el('div', `pos-card ${dir}`);
    div.innerHTML = `
      <div class="pos-card-header">
        <span class="pos-symbol">${p.symbol.replace(':USDT','')}</span>
        <span class="pos-dir ${dir}">${p.direction}</span>
      </div>
      <div class="pos-stats">
        <span>진입 ${fmtPrice(p.entry_price)}</span>
        <span class="pos-pnl ${pnlSign}">${fmtPnL(p.unrealized_pnl)}</span>
      </div>
      <div class="pos-stats">
        <span>SL ${fmtPrice(p.stop_loss)}</span>
        <span>${p.leverage}x · ${p.grade}</span>
      </div>`;
    list.appendChild(div);
  });
}

// ═══════════════════════════════════════════════════════════════
// SIGNAL CARDS RENDERING
// ═══════════════════════════════════════════════════════════════
function renderSignals() {
  const grid = $('signals-grid');
  const empty = $('empty-signals');

  const filtered = filterList(State.signals);

  if (!filtered.length && !State.signals.length) {
    empty.classList.remove('hidden');
    // Remove all cards
    [...grid.querySelectorAll('.signal-card')].forEach(c => c.remove());
    return;
  }
  empty.classList.add('hidden');

  // Remove cards not in filtered
  [...grid.querySelectorAll('.signal-card')].forEach(card => {
    const pair = card.dataset.pair;
    if (!filtered.find(s => s.pair === pair)) card.remove();
  });

  filtered.forEach((sig, i) => {
    let card = grid.querySelector(`.signal-card[data-pair="${sig.pair}"]`);
    if (!card) {
      card = el('div', 'signal-card');
      card.dataset.pair = sig.pair;
      card.addEventListener('click', () => openDetail(sig));
      // Insert at correct position
      const existing = [...grid.querySelectorAll('.signal-card')];
      if (i < existing.length) grid.insertBefore(card, existing[i]);
      else grid.appendChild(card);
    }
    updateCard(card, sig);
    card.classList.toggle('selected', State.selectedPair === sig.pair);
  });
}

function updateCard(card, sig) {
  if (!sig.valid) {
    card.className = 'signal-card no-trade';
    card.innerHTML = `
      <div class="card-header">
        <div class="card-pair">
          ${fmtSymbol(sig.pair)}
          <small>NO TRADE</small>
        </div>
      </div>
      <div style="font-size:10px;color:var(--text-muted);line-height:1.5;">${sig.reason || '—'}</div>`;
    return;
  }

  const dir  = sig.direction.toLowerCase();
  const grade = sig.grade === 'A+' ? 'aplus' : 'a';

  card.className = `signal-card ${dir}`;

  // Build module vote dots
  const mods = sig.module_votes || {};
  const dotHtml = Object.entries(mods).map(([name, info]) => {
    const v = (info.vote || '').toLowerCase();
    const label = name.split(' ').map(w=>w[0]).join('');
    return `<div class="vote-dot ${v}" title="${name}: ${info.vote} (${info.confidence?.toFixed(0)}%)"></div>`;
  }).join('');

  const entryP = sig.entry?.primary || 0;
  const entryS = sig.entry?.secondary || 0;
  const sl     = sig.stop_loss || 0;
  const tp2    = sig.take_profits?.tp2?.price || 0;

  card.innerHTML = `
    <div class="card-header">
      <div class="card-pair">
        ${fmtSymbol(sig.pair)}
        <small>${sig.timeframe || '1h'} · ${sig.market_condition || ''}</small>
      </div>
      <div class="card-badges">
        <span class="dir-chip ${dir}">${sig.direction}</span>
        <span class="grade-chip ${grade}">${sig.grade}</span>
      </div>
    </div>
    <div class="card-conf">
      <div class="card-conf-bar-track">
        <div class="card-conf-bar-fill ${dir}" style="width:${sig.confidence || 0}%"></div>
      </div>
      <div class="card-conf-label">
        <span>신뢰도</span>
        <span class="card-conf-value">${(sig.confidence || 0).toFixed(0)}%</span>
      </div>
    </div>
    <div class="card-levels">
      <div class="card-level">
        <span class="cl-label">진입</span>
        <span class="cl-value">${fmtPrice(entryP)}</span>
      </div>
      <div class="card-level">
        <span class="cl-label">손절</span>
        <span class="cl-value" style="color:var(--red)">${fmtPrice(sl)}</span>
      </div>
      <div class="card-level">
        <span class="cl-label">TP2</span>
        <span class="cl-value" style="color:var(--green)">${fmtPrice(tp2)}</span>
      </div>
      <div class="card-level">
        <span class="cl-label">R:R</span>
        <span class="cl-value">${(sig.rr_ratio || 0).toFixed(1)}:1</span>
      </div>
    </div>
    <div class="card-votes">
      ${dotHtml}
      <span class="vote-label">${Object.keys(mods).length}/4 모듈</span>
    </div>`;
}

// ═══════════════════════════════════════════════════════════════
// DETAIL PANEL
// ═══════════════════════════════════════════════════════════════
function openDetail(sig) {
  State.selectedPair = sig.pair;
  // Mark selected card
  document.querySelectorAll('.signal-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.pair === sig.pair);
  });

  $('detail-placeholder').classList.add('hidden');
  const det = $('signal-detail');
  det.classList.remove('hidden');

  if (!sig.valid) {
    det.innerHTML = `
      <div class="detail-header">
        <div class="detail-pair">${fmtSymbol(sig.pair)}</div>
      </div>
      <div class="det-section">
        <div class="det-label">NO TRADE 이유</div>
        <div class="analysis-text">${sig.reason || '—'}</div>
      </div>`;
    if (sig.module_votes) renderDetailModules(sig.module_votes, '');
    return;
  }

  const dir   = sig.direction.toLowerCase();
  const grade = sig.grade === 'A+' ? 'aplus' : 'a';

  setText('det-pair', fmtSymbol(sig.pair));

  const dirBadge = $('det-dir');
  dirBadge.textContent = sig.direction;
  dirBadge.className = `dir-badge ${dir}`;

  const gradeBadge = $('det-grade');
  gradeBadge.textContent = sig.grade;
  gradeBadge.className = `grade-badge ${grade}`;

  // Confidence
  const fill = $('det-conf-fill');
  fill.style.width = (sig.confidence || 0) + '%';
  fill.className = `conf-bar-fill ${dir}`;
  setText('det-conf-val', (sig.confidence || 0).toFixed(0) + '%');

  // Levels
  const ep = sig.entry?.primary    || 0;
  const es = sig.entry?.secondary  || 0;
  const sl = sig.stop_loss         || 0;
  const tp1 = sig.take_profits?.tp1?.price || 0;
  const tp2 = sig.take_profits?.tp2?.price || 0;
  const tp3 = sig.take_profits?.tp3?.price || 0;

  setText('det-entry-p', fmtPrice(ep));
  setText('det-entry-s', fmtPrice(es));
  setText('det-sl',  fmtPrice(sl));
  setText('det-tp1', fmtPrice(tp1));
  setText('det-tp2', fmtPrice(tp2));
  setText('det-tp3', fmtPrice(tp3));

  // Params
  setText('det-lev', `${sig.leverage || '—'}x`);
  setText('det-rr',  `1 : ${(sig.rr_ratio || 0).toFixed(2)}`);
  setText('det-mkt', sig.market_condition || '—');
  setText('det-atr', fmtPrice(sig.atr || 0));

  // Modules
  renderDetailModules(sig.module_votes || {}, dir);

  // Summary
  setText('det-summary', sig.analysis_summary || '—');

  // Risks
  const riskList = $('det-risks');
  riskList.innerHTML = '';
  const risks = Array.isArray(sig.key_risks) ? sig.key_risks : (sig.key_risks || '').split('\n').filter(Boolean);
  risks.forEach(r => {
    const li = el('li', '', r.replace(/^•\s*/, ''));
    riskList.appendChild(li);
  });
}

function renderDetailModules(votes, direction) {
  const container = $('det-modules');
  if (!container) return;
  container.innerHTML = '';
  const names = {
    'Trend Master': 'TM',
    'Momentum Scanner': 'MS',
    'Smart Money Tracker': 'SMT',
    'Market Structure Analyst': 'MSA',
  };
  Object.entries(votes).forEach(([name, info]) => {
    const v = (info.vote || '').toLowerCase();
    const conf = (info.confidence || 0).toFixed(0);
    const row = el('div', 'mod-vote-row');
    row.innerHTML = `
      <span class="mod-name">${name}</span>
      <span class="mod-vote ${v}">${v === 'long' ? '▲ LONG' : v === 'short' ? '▼ SHORT' : '◆ NEUTRAL'}</span>
      <span class="mod-conf-mini">${conf}%</span>`;
    container.appendChild(row);
  });
}

// ═══════════════════════════════════════════════════════════════
// PAIR LIST (Sidebar)
// ═══════════════════════════════════════════════════════════════
function buildPairList() {
  const list = $('pair-list');
  list.innerHTML = '';
  const sorted = [...State.signals].sort((a, b) => (b.confidence||0) - (a.confidence||0));
  sorted.slice(0, 20).forEach(sig => {
    const item = el('div', `pair-item ${sig.valid ? sig.direction.toLowerCase() : 'none'}`);
    const sym = fmtSymbol(sig.pair);
    const badge = sig.valid
      ? `<span class="pair-item-badge ${sig.direction.toLowerCase()}">${sig.direction} ${sig.grade}</span>`
      : `<span class="pair-item-badge none">—</span>`;
    item.innerHTML = `<span class="pair-item-name">${sym}</span>${badge}`;
    item.addEventListener('click', () => {
      $('pair-input').value = sig.pair;
      openDetail(sig);
    });
    list.appendChild(item);
  });
}

// ═══════════════════════════════════════════════════════════════
// FILTER
// ═══════════════════════════════════════════════════════════════
function filterSignals(type, btn) {     // called from HTML
  State.filter = type;
  document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  renderSignals();
}

function filterList(signals) {
  if (State.filter === 'all') return signals;
  if (State.filter === 'LONG')  return signals.filter(s => s.valid && s.direction === 'LONG');
  if (State.filter === 'SHORT') return signals.filter(s => s.valid && s.direction === 'SHORT');
  if (State.filter === 'A+')    return signals.filter(s => s.valid && s.grade === 'A+');
  return signals;
}

// ═══════════════════════════════════════════════════════════════
// CLOCK
// ═══════════════════════════════════════════════════════════════
function startClock() {
  function tick() {
    const now = new Date();
    const hh = String(now.getUTCHours()).padStart(2, '0');
    const mm = String(now.getUTCMinutes()).padStart(2, '0');
    const ss = String(now.getUTCSeconds()).padStart(2, '0');
    setText('server-time', `${hh}:${mm}:${ss} UTC`);
  }
  tick();
  setInterval(tick, 1000);
}

// ═══════════════════════════════════════════════════════════════
// TOAST
// ═══════════════════════════════════════════════════════════════
function toast(msg, type = 'info') {
  const container = $('toast-container');
  const div = el('div', `toast ${type}`, msg);
  container.appendChild(div);
  setTimeout(() => div.remove(), 4500);
}

// ═══════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════
function setText(id, txt) {
  const e = $(id);
  if (e) e.textContent = txt;
}

function setValColor(id, val, txt) {
  const e = $(id);
  if (!e) return;
  e.textContent = txt;
  e.classList.remove('text-green', 'text-red');
  if (val > 0) e.classList.add('text-green');
  else if (val < 0) e.classList.add('text-red');
}

function setConnStatus(connected) {
  const dot   = document.querySelector('.conn-dot');
  const label = document.querySelector('.conn-label');
  if (!dot || !label) return;
  dot.classList.toggle('connected', connected);
  dot.classList.toggle('disconnected', !connected);
  label.textContent = connected ? 'LIVE' : '재연결 중...';
}

function fmtPrice(p) {
  if (!p && p !== 0) return '—';
  if (p >= 10000) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 100)   return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 });
  if (p >= 1)     return p.toFixed(4);
  return p.toFixed(6);
}

function fmtUSDT(v) {
  if (v === undefined || v === null) return '—';
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPnL(v) {
  if (v === undefined || v === null) return '—';
  const sign = v >= 0 ? '+' : '';
  return sign + '$' + v.toFixed(2);
}

function fmtSymbol(pair) {
  return (pair || '').replace(':USDT', '').replace('/USDT', '/USDT');
}
