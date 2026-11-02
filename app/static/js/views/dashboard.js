// Live-Dashboard. Zustände: kein Spiel · vorbereitet (CREATED) · läuft · beendet (Gewinner).
// Daten kommen ausschließlich vom Server (game:updated); Ereignisse lösen nur Animationen aus.
import { api, canWrite, isAdmin } from '../core/api.js';
import { $, fmt, fmtTime, gameTitle, h, replace } from '../core/dom.js';
import '../core/prefs.js';
import { connectLive, onLive } from '../core/realtime.js';
import { ScoreChart } from '../components/chartTheme.js';
import { renderConnection } from '../components/connection.js';
import { derive } from '../components/derive.js';
import { Leaderboard } from '../components/leaderboard.js';
import { connectionChip, controllerChip } from '../components/statusChip.js';
import { finishNight, nightStandings, nightSummaryView, startNextGame } from '../components/night.js';
import { ceremony, celebrate, spotlight, winnerView } from '../components/winner.js';

const body = $('#dash-body');
const overlay = $('#qr-overlay');
let state = null;
let network = null;
let live = 'connecting';
let view = null; // {kind, board, chart, ...}
let pendingState = null;
let animating = false;
let lastLeaderKey = null;
let staleSince = null;
let seenRounds = null; // {gameId, count} – erkennt neu gespeicherte Runden
let spotQueue = [];
let seenNight = null; // {id, status} – erkennt das Ende eines Spieleabends
let spotRunning = false;

// ---------------------------------------------------------------- Statusleiste

function renderBar() {
  $('#game-name').textContent = state ? gameTitle(state.game, state.night) : '';
  const round = state ? Math.min(state.game.current_round, Math.max(1, state.game.rounds_played)) : null;
  const roundEl = $('#round-no');
  const shown = state ? (state.game.status === 'ACTIVE' ? state.game.current_round : state.game.rounds_played || '–') : '–';
  if (roundEl.textContent !== String(shown) && round !== null) {
    roundEl.classList.remove('is-flip');
    void roundEl.offsetWidth;
    roundEl.classList.add('is-flip');
  }
  roundEl.textContent = shown;
  $('#target').textContent = state ? fmt(state.game.target_score) : '–';
  connectionChip($('#live-chip'), { live, network });
  controllerChip($('#ctrl-chip'), network?.clients);
  if (live !== 'online' && state) {
    staleSince = staleSince || new Date();
    $('#live-chip').append(h('span.chip__stale', ` · Stand ${fmtTime(staleSince.toISOString())}`));
  } else {
    staleSince = null;
  }
}

// ---------------------------------------------------------------- Inhalt

function emptyView(kind, title, text) {
  view = { kind };
  const conn = h('div.dash__conn');
  replace(body, h('div.dash__empty',
    h('div.empty', h('p.label', title), h('p.empty__text', text),
      canWrite ? h('a.btn.btn--primary', { href: '/setup' }, kind === 'created' ? 'Spiel vorbereiten' : 'Neues Spiel') : null),
    conn));
  renderConnection(conn, network, { size: 'large' });
  view.conn = conn;
}

function createdView() {
  view = { kind: 'created' };
  const conn = h('div.dash__conn');
  replace(body, h('div.dash__empty',
    h('section.panel.dash__waiting',
      h('p.label', 'Warte auf Runde 1'),
      h('h2', gameTitle(state.game)),
      h('ol.dash__seats', state.players.map((p, i) => h('li', h('span.num', i + 1), h('span.ellipsis', p.name)))),
      h('p.muted', `Ziel ${fmt(state.game.target_score)} Punkte · ${state.players.length} Spieler`),
      canWrite ? h('a.btn.btn--primary', { href: '/setup' }, 'Spiel starten') : null),
    conn));
  renderConnection(conn, network, { size: 'large' });
  view.conn = conn;
}

function liveView() {
  const boardRoot = h('div.dash__board');
  const canvas = h('canvas', { 'aria-label': 'Punkteentwicklung je Spieler' });
  const statHigh = h('div.stat');
  const statLead = h('div.stat');
  const events = h('ul.dash__events', { 'aria-label': 'Ereignisse der letzten Runde' });
  const night = h('div.dash__night');
  replace(body, h('div.dash__grid',
    h('section.dash__left', boardRoot),
    h('section.dash__right',
      h('div.panel.dash__chart', h('div.panel__head', h('h2.label', 'Punkteentwicklung')), h('div.dash__canvas', canvas)),
      h('div.dash__stats', statHigh, statLead),
      night,
      h('div.dash__eventsbox', h('h2.label', 'Letzte Runde'), events))));
  view = { kind: 'live', board: new Leaderboard(boardRoot), chart: new ScoreChart(canvas), statHigh, statLead, events, night };
}

function renderLive(d) {
  view.board.update(state, d);
  view.chart.update(state.statistics.chart, d.leaderIds, state.game.target_score);
  replace(view.statHigh, h('span.label', 'Höchste Runde'),
    h('span.stat__value', d.bestRound ? fmt(d.bestRound.points) : '–'),
    h('span.stat__context', d.bestRound ? `${d.bestRound.name} · R${d.bestRound.round}` : 'noch keine Runde'));
  replace(view.statLead, h('span.label', 'Führungswechsel'),
    h('span.stat__value', fmt(state.statistics.lead_change_count)),
    h('span.stat__context', `${fmt(state.statistics.rounds_played)} Runden gespielt`));
  if (state.night) replace(view.night, nightStandings(state.night));
  else replace(view.night);
  view.night.hidden = !state.night;
  const tags = { bust: ['tag--bust', 'Bust'], lead: ['tag--lead', 'Führung'], fix: ['tag--fix', 'Korrektur'], skip: ['tag--cancel', 'Pause'] };
  replace(view.events, d.events.length
    ? d.events.slice(0, document.documentElement.classList.contains('tv') ? 2 : 6).map((e) => h('li.dash__event',
      h(`span.tag.${tags[e.type][0]}`, h('span', tags[e.type][1])), h('span', e.text)))
    : h('li.caption', state.game.rounds_played ? 'Keine besonderen Ereignisse.' : 'Noch keine Runde gespielt.'));
}

function winnerKey() {
  return `${state.game.id}:${state.night?.status ?? ''}:${state.night?.games_finished ?? ''}`;
}

function winnerScreen(d) {
  view?.chart?.destroy();
  const night = state.night;
  const actions = [];
  if (night && night.status === 'FINISHED') {
    if (canWrite) actions.push(h('a.btn.btn--primary', { href: '/setup' }, 'Neuer Spieleabend'));
    actions.push(h('a.btn.btn--secondary', { href: `/history#n${night.id}` }, 'Zur Historie'));
    if (canWrite) actions.push(h('a.btn.btn--tertiary', { href: '/' }, 'Hauptmenü'));
    view = { kind: 'winner', key: winnerKey() };
    replace(body, nightSummaryView(night, { actions }));
    return;
  }
  if (canWrite && night) {
    const next = h('button.btn.btn--primary', { type: 'button', onclick: async () => {
      const s = await startNextGame(night, next);
      if (s) setState(s);
    } }, `Partie ${night.current_number + 1} starten`);
    const end = h('button.btn.btn--secondary', { type: 'button', onclick: async () => {
      if (await finishNight(night, end)) {
        const s = await api.get('/api/v1/games/current?display=1');
        maybeCeremony(s);
        setState(s);
      }
    } }, 'Abend beenden');
    actions.push(next, end);
  } else if (canWrite) {
    actions.push(h('a.btn.btn--primary', { href: '/setup' }, 'Neuer Spieleabend'));
    actions.push(h('a.btn.btn--secondary', { href: `/history#${state.game.id}` }, 'Spielstatistik'));
  }
  if (canWrite) actions.push(h('a.btn.btn--tertiary', { href: '/' }, 'Hauptmenü'));
  view = { kind: 'winner', key: winnerKey() };
  replace(body, winnerView(state, d, { actions, extra: night ? nightStandings(night) : null }));
}

function render() {
  renderBar();
  if (!state || state.game.status === 'CANCELLED') {
    view?.chart?.destroy();
    if (view?.kind !== 'none') emptyView('none', 'Kein laufendes Spiel', canWrite
      ? 'Lege ein neues Spiel an – am Laptop oder per Smartphone.' : 'Sobald ein Spiel startet, erscheint hier die Rangliste.');
    else renderConnection(view.conn, network, { size: 'large' });
    return;
  }
  const d = derive(state);
  if (state.game.status === 'CREATED') {
    view?.chart?.destroy();
    createdView();
    return;
  }
  if (state.game.status === 'FINISHED') {
    if (view?.kind !== 'winner' || view.key !== winnerKey()) winnerScreen(d);
    return;
  }
  if (view?.kind !== 'live') liveView();
  renderLive(d);
  const key = d.leaderIds.join(',');
  if (lastLeaderKey !== null && key !== lastLeaderKey && d.leaderIds.length === 1) {
    $('#live-region').textContent = `${d.names.get(d.leaderIds[0])} führt mit ${fmt(state.leaderboard[0].total)} Punkten`;
  }
  lastLeaderKey = key;
}

// Während einer Ranglistenanimation eintreffende Updates zusammenfassen – der letzte Stand gewinnt.
function setState(next) {
  if (animating) {
    pendingState = next;
    return;
  }
  state = next;
  render();
  animating = true;
  setTimeout(() => {
    animating = false;
    if (pendingState) {
      const p = pendingState;
      pendingState = null;
      setState(p);
    }
  }, 650);
}

// ---------------------------------------------------------------- QR-Overlay, Vollbild, Tastatur

function openOverlay() {
  const panel = h('div.qr-overlay__panel.notch-l');
  replace(overlay, h('div.qr-overlay__inner',
    h('div.qr-overlay__head', h('h2#qr-overlay-title', 'Smartphone verbinden'),
      h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: closeOverlay }, 'Schließen (Esc)')),
    panel));
  renderConnection(panel, network, { size: 'large' });
  overlay.hidden = false;
  overlay.querySelector('button')?.focus();
}
function closeOverlay() {
  overlay.hidden = true;
  $('#live-chip').focus();
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.();
  else document.documentElement.requestFullscreen?.().catch(() => {});
}
document.addEventListener('fullscreenchange', () => document.body.classList.toggle('is-fullscreen', !!document.fullscreenElement));

$('#live-chip').addEventListener('click', openOverlay);
$('#fs-btn').addEventListener('click', toggleFullscreen);
document.addEventListener('keydown', (e) => {
  if (e.target.closest('input, textarea, select')) return;
  if (e.key === 'f' || e.key === 'F') toggleFullscreen();
  else if ((e.key === 'q' || e.key === 'Q') && network) overlay.hidden ? openOverlay() : closeOverlay();
  else if (e.key === 'Escape' && !overlay.hidden) closeOverlay();
});
overlay.addEventListener('click', (e) => e.target === overlay && closeOverlay());

// ---------------------------------------------------------------- Live

onLive('connection', ({ status }) => {
  live = status;
  renderBar();
});
onLive('game:updated', (p) => {
  queueRoundEvents(p.state);
  maybeCeremony(p.state);
  setState(p.state);
});

// Spieleabend gerade beendet → Siegerehrung über dem (darunter schon gerenderten) Abend-Endstand.
function maybeCeremony(next) {
  const night = next?.night;
  const prev = seenNight;
  seenNight = night ? { id: night.id, status: night.status } : null;
  if (!night || !prev || prev.id !== night.id || prev.status !== 'ACTIVE' || night.status !== 'FINISHED') return;
  spotQueue = [];
  ceremony($('#celebration'), night);
}
onLive('game:finished', () => {
  spotQueue = [];
  celebrate($('#celebration'));
});

// Neue Runde (Partie läuft weiter) → kurze Einblendungen nacheinander. Beim Spielende hat die Feier Vorrang.
function queueRoundEvents(next) {
  const prev = seenRounds;
  seenRounds = next ? { gameId: next.game.id, count: next.rounds.length } : null;
  if (!next || !prev || prev.gameId !== next.game.id || next.rounds.length !== prev.count + 1) return;
  if (next.game.status !== 'ACTIVE') return;
  const items = roundEvents(next);
  if (!items.length) return;
  spotQueue = items.slice(0, 3); // eine neue Runde ersetzt, was noch wartet
  if (!spotRunning) playQueue();
}

async function playQueue() {
  spotRunning = true;
  while (spotQueue.length) await spotlight($('#celebration'), spotQueue.shift());
  spotRunning = false;
}

function roundEvents(st) {
  const last = st.rounds.at(-1);
  const names = new Map(st.players.map((pl) => [pl.player_id, pl.name]));
  const scores = Object.entries(last.scores).map(([pid, pts]) => [Number(pid), pts ?? 0]);
  const out = [];
  const lc = st.statistics.lead_changes.at(-1);
  if (lc && lc.round_number === last.round_number) {
    const leader = st.leaderboard.find((r) => r.player_id === lc.player_id);
    const second = st.leaderboard.find((r) => r.player_id !== lc.player_id);
    out.push({ kind: 'lead', names: [names.get(lc.player_id)], value: leader?.total ?? 0,
      sub: second ? `vor ${second.name} · +${fmt((leader?.total ?? 0) - second.total)}` : '' });
  }
  if (st.rounds.length >= 2) {
    const best = Math.max(...scores.map(([, p]) => p));
    const before = Math.max(0, ...st.rounds.slice(0, -1).flatMap((r) => Object.values(r.scores).map((p) => p ?? 0)));
    if (best > 0 && best > before) {
      out.push({ kind: 'record', names: scores.filter(([, p]) => p === best).map(([pid]) => names.get(pid)), value: best,
        sub: `bisher beste Runde: ${fmt(before)}` });
    }
  }
  const target = st.game.target_score;
  const threshold = Math.ceil(target * 0.85);
  const closing = st.leaderboard.filter((r) => {
    const pts = last.scores[r.player_id] ?? 0;
    return r.total < target && r.total >= threshold && r.total - pts < threshold;
  });
  if (closing.length) {
    const nearest = Math.min(...closing.map((r) => target - r.total));
    out.push({ kind: 'matchball', names: closing.map((r) => r.name), value: nearest,
      sub: `${closing.length > 1 ? 'höchstens ' : 'nur noch '}${fmt(nearest)} Punkte bis ${fmt(target)}` });
  }
  const zeros = scores.filter(([, p]) => p === 0).map(([pid]) => names.get(pid));
  if (zeros.length) out.push({ kind: 'zero', names: zeros, sub: zeros.length > 1 ? 'gehen diese Runde leer aus' : 'geht diese Runde leer aus' });
  return out;
}

onLive('network:changed', (info) => {
  network = { ...network, ...info };
  renderBar();
  if (!overlay.hidden) openOverlay();
  if (view?.conn) renderConnection(view.conn, network, { size: 'large' });
});
onLive('client:connected', (p) => {
  if (network) network.clients = p.clients;
  renderBar();
});
onLive('client:disconnected', (p) => {
  if (network) network.clients = p.clients;
  renderBar();
});
onLive('system:status', (p) => p.reload && location.reload());

async function init() {
  renderBar();
  replace(body, h('div.dash__grid', h('section.dash__left', h('div.skeleton'), h('div.skeleton'), h('div.skeleton')),
    h('section.dash__right', h('div.skeleton'))));
  try {
    network = await api.get('/api/v1/system/network');
  } catch {
    /* Live-Verbindung liefert später */
  }
  try {
    const first = await api.get('/api/v1/games/current?display=1');
    queueRoundEvents(first);
    maybeCeremony(first);
    setState(first);
  } catch {
    setState(null);
  }
  connectLive();
  if (isAdmin && new URLSearchParams(location.search).get('connect') === '1') openOverlay();
}

init();
