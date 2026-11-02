// Spielhistorie: Archivkarten, Sortierung, Detail mit Gewinner, Endrangliste, Punkteverlauf,
// Rundentabelle, Kennzahlen, Änderungsprotokoll, Export und Löschen (nur Laptop, mit Bestätigung).
import { api, canWrite, isAdmin } from '../core/api.js';
import { $, $$, fmt, fmtDate, fmtShortDate, fmtTime, gameTitle, h, replace } from '../core/dom.js';
import '../core/prefs.js';
import { ScoreChart } from '../components/chartTheme.js';
import { derive } from '../components/derive.js';
import { confirmDialog } from '../components/modal.js';
import { toast } from '../components/toast.js';
import { nightGames, nightTable, nightTitle, nightVerdict } from '../components/night.js';
import { winnerView } from '../components/winner.js';

let games = []; // Einzelspiele ohne Spieleabend
let nights = [];
let sort = 'date';
let desc = true;
let chart = null;
let selected = null;

const EVENT_TEXT = {
  game_created: 'Spiel angelegt', game_started: 'Spiel gestartet', game_updated: 'Spiel geändert',
  game_finished: 'Spiel beendet', game_cancelled: 'Spiel abgebrochen', game_continued: 'Spiel fortgesetzt',
  game_reopened: 'Spielende durch Korrektur aufgehoben', round_created: 'Runde gespeichert',
  round_updated: 'Runde korrigiert', round_deleted: 'Runde gelöscht',
};

function entries() {
  return [...nights.map((n) => ({ kind: 'night', key: `n${n.id}`, date: n.created_at, rounds: n.games.length,
    score: n.standings[0]?.wins ?? 0, night: n })),
  ...games.map((g) => ({ kind: 'game', key: String(g.id), date: g.created_at, rounds: g.rounds_played,
    score: g.leaderboard[0]?.total ?? 0, game: g }))];
}

function sorted() {
  const key = { date: (e) => e.date, rounds: (e) => e.rounds, score: (e) => e.score }[sort];
  return entries().sort((a, b) => {
    const x = key(a);
    const y = key(b);
    return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1);
  });
}

function renderList() {
  const list = $('#list');
  if (!games.length && !nights.length) {
    replace(list, h('li', h('div.empty', h('p.empty__text', 'Noch keine Spieleabende.'),
      canWrite ? h('a.btn.btn--primary', { href: '/setup' }, 'Neuen Spieleabend starten') : null)));
    replace($('#detail'));
    return;
  }
  replace(list, sorted().map((e) => {
    if (e.kind === 'night') {
      const n = e.night;
      const top = n.standings.filter((r) => r.rank === 1 && r.wins > 0);
      return h('li', h('button.archive.archive--night', { type: 'button', 'aria-pressed': String(selected === e.key),
        onclick: () => openNight(n.id) },
      h('span.archive__date.num', fmtShortDate(n.created_at)),
      h('span.archive__body',
        h('span.archive__name.ellipsis', nightTitle(n)),
        n.status === 'ACTIVE' ? h('span.tag.tag--lead', h('span', 'Läuft'))
          : h('span.archive__winner', h('span.archive__card', { 'aria-hidden': 'true' }), top.map((r) => r.name).join(' & ') || '–'),
        h('span.caption', `${n.standings.length} Spieler · ${n.games_finished} Partien · Ziel ${fmt(n.target_score)}`)),
      h('span.archive__score.num', { title: 'Partiesiege des Abendsiegers' }, top.length ? `${top[0].wins}×` : '–')));
    }
    const g = e.game;
    const cancelled = g.status === 'CANCELLED';
    const top = g.leaderboard[0];
    return h('li', h(`button.archive${cancelled ? '.is-cancelled' : ''}`, { type: 'button', 'aria-pressed': String(selected === e.key),
      onclick: () => open(g.id) },
    h('span.archive__date.num', fmtShortDate(g.started_at || g.created_at)),
    h('span.archive__body',
      h('span.archive__name.ellipsis', gameTitle(g)),
      cancelled ? h('span.tag.tag--cancel', h('span', 'Abgebrochen'))
        : h('span.archive__winner', h('span.archive__card', { 'aria-hidden': 'true' }), g.winners.join(' & ') || '–'),
      h('span.caption', `${g.player_count} Spieler · ${g.rounds_played} Runden · Ziel ${fmt(g.target_score)}`)),
    h('span.archive__score.num', top ? fmt(top.total) : '–')));
  }));
}

function roundTable(state) {
  const players = state.players;
  return h('div.table-wrap', h('table.table.rounds-table',
    h('thead', h('tr', h('th', 'Runde'), players.map((p) => h('th', { title: p.name }, p.name)))),
    h('tbody', state.rounds.map((r) => {
      const vals = players.map((p) => r.scores[String(p.player_id)]);
      const max = Math.max(...vals.map((v) => v ?? 0));
      return h('tr', h('td', `R${r.round_number}${r.updated_at !== r.created_at ? ' ·' : ''}`),
        vals.map((v) => (v === undefined || v === null
          ? h('td.is-skipped', { title: 'Runde nicht mitgespielt' }, 'nicht mitgespielt')
          : h(`td${v === 0 ? '.is-bust' : ''}${v === max && max > 0 ? '.is-best' : ''}`, v === 0 ? '0 Bust' : fmt(v)))));
    })),
    h('tfoot', h('tr', h('th', 'Gesamt'), players.map((p) => h('th', fmt(state.leaderboard.find((r) => r.player_id === p.player_id)?.total)))))));
}

function playerStats(state) {
  return h('div.table-wrap', h('table.table',
    h('thead', h('tr', ['Spieler', 'Platz', 'Gesamt', 'Beste', 'Schlechteste', 'Ø Runde', '0-Runden'].map((t) => h('th', t)))),
    h('tbody', [...state.statistics.players].sort((a, b) => a.rank - b.rank).map((s) => h('tr',
      h('td', s.name), h('td', s.rank), h('td', fmt(s.total)), h('td', fmt(s.best_round)), h('td', fmt(s.worst_round)),
      h('td', s.average_round === null ? '–' : s.average_round.toLocaleString('de-DE')), h('td', s.zero_rounds))))));
}

async function eventLog(state, host) {
  try {
    const events = await api.get(`/api/v1/games/${state.game.id}/events`);
    const names = new Map(state.players.map((p) => [String(p.player_id), p.name]));
    const describe = (e) => {
      const pl = e.payload || {};
      if (e.type === 'round_updated') {
        const changes = Object.keys(pl.after || {}).filter((k) => pl.before?.[k] !== pl.after[k])
          .map((k) => `${names.get(k) || k}: ${pl.before?.[k]} → ${pl.after[k]}`);
        return `Runde ${pl.round_number}: ${changes.join(', ') || 'keine Änderung'}`;
      }
      if (e.type === 'round_created' || e.type === 'round_deleted') return `Runde ${pl.round_number}`;
      if (e.type === 'game_finished') return pl.reason === 'target' ? 'Punkteziel erreicht' : 'manuell';
      return '';
    };
    replace(host, h('ol.eventlog', events.map((e) => h('li',
      h('span.caption', `${fmtDate(e.created_at, { day: '2-digit', month: '2-digit' })} ${fmtTime(e.created_at)}`),
      h('span', EVENT_TEXT[e.type] || e.type), h('span.muted', describe(e)),
      h('span.caption', e.actor === 'admin' ? 'Laptop' : e.actor === 'controller' ? 'Smartphone' : '')))));
  } catch (e) {
    replace(host, h('p.caption', e.message));
  }
}

async function openNight(id) {
  selected = `n${id}`;
  history.replaceState(null, '', `#n${id}`);
  renderList();
  const detail = $('#detail');
  replace(detail, h('div.skeleton'));
  let night;
  try {
    night = await api.get(`/api/v1/nights/${id}`);
  } catch (e) {
    replace(detail, h('div.empty', h('p.empty__text', e.message)));
    return;
  }
  chart?.destroy();
  const actions = [];
  if (canWrite) {
    const ids = night.games.map((g) => g.id).join(',');
    actions.push(h('a.btn.btn--secondary.btn--small', { href: `/api/v1/history/export.csv?game_id=${ids}`, download: '' }, 'CSV'));
    actions.push(h('a.btn.btn--secondary.btn--small', { href: `/api/v1/history/export.json?game_id=${ids}`, download: '' }, 'JSON'));
  }
  if (isAdmin && night.status !== 'ACTIVE') {
    actions.push(h('button.btn.btn--danger.btn--small', { type: 'button', onclick: () => removeNight(night) }, 'Abend löschen'));
  }
  const verdict = nightVerdict(night);
  const finished = night.games.filter((g) => g.status === 'FINISHED').length;
  replace(detail,
    h('div.history__head', h('div', h('p.label', `${fmtDate(night.created_at)} · Spieleabend`), h('h2', nightTitle(night))),
      h('div.history__actions', actions)),
    h('section.history__verdict.notch-l',
      h('span.label.history__kicker', verdict.kicker),
      h('p.history__winners', verdict.top.length ? verdict.top.map((r) => r.name).join(' · ') : '–'),
      h('p.caption', [verdict.detail, `${finished} von ${night.games.length} ${night.games.length === 1 ? 'Partie' : 'Partien'} beendet`,
        `Ziel ${fmt(night.target_score)} Punkte`].filter(Boolean).join(' · '))),
    h('section', h('h3.label.history__sub', 'Endstand'), nightTable(night)),
    h('section', h('h3.label.history__sub', 'Partien'), nightGames(night, { onOpen: (g) => open(g.id, night.id) })));
}

async function removeNight(night) {
  const ok = await confirmDialog({ title: 'Spieleabend endgültig löschen?', confirm: 'Löschen',
    text: `„${nightTitle(night)}“ wird mit allen Partien und Runden gelöscht. Vorher wird automatisch eine Sicherung angelegt.` });
  if (!ok) return;
  try {
    await api.del(`/api/v1/nights/${night.id}`);
    toast('Spieleabend gelöscht', { type: 'ok' });
    nights = nights.filter((n) => n.id !== night.id);
    selected = null;
    history.replaceState(null, '', location.pathname);
    replace($('#detail'));
    renderList();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}

async function open(id, fromNight = null) {
  selected = fromNight ? `n${fromNight}` : String(id);
  history.replaceState(null, '', fromNight ? `#n${fromNight}` : `#${id}`);
  $$('.archive').forEach((b) => b.setAttribute('aria-pressed', 'false'));
  renderList();
  const detail = $('#detail');
  replace(detail, h('div.skeleton'), h('div.skeleton'));
  let state;
  try {
    state = await api.get(`/api/v1/games/${id}`);
  } catch (e) {
    replace(detail, h('div.empty', h('p.empty__text', e.message)));
    return;
  }
  const d = derive(state);
  chart?.destroy();
  const canvas = h('canvas', { 'aria-label': 'Punkteverlauf' });
  const actions = [];
  if (canWrite) {
    actions.push(h('a.btn.btn--secondary.btn--small', { href: `/api/v1/history/export.csv?game_id=${id}`, download: '' }, 'CSV'));
    actions.push(h('a.btn.btn--secondary.btn--small', { href: `/api/v1/history/export.json?game_id=${id}`, download: '' }, 'JSON'));
  }
  if (isAdmin && !state.game.night_id) {
    actions.push(h('button.btn.btn--danger.btn--small', { type: 'button', onclick: () => remove(state) }, 'Spiel löschen'));
  }
  const logHost = h('div');
  const cancelled = state.game.status === 'CANCELLED';
  replace(detail,
    fromNight ? h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: () => openNight(fromNight) }, '← Zurück zum Spieleabend') : null,
    h('div.history__head',
      h('div', h('p.label', `${fmtDate(state.game.started_at || state.game.created_at)} · ${fmtTime(state.game.started_at)}–${fmtTime(state.game.finished_at)}`),
        h('h2', gameTitle(state.game, state.night))),
      h('div.history__actions', actions)),
    cancelled ? h('div.notice', h('span.tag.tag--cancel', h('span', 'Abgebrochen')), h('p', 'Dieses Spiel wurde abgebrochen und zählt nicht für die Statistik.'))
      : winnerView(state, d, { small: true }),
    state.rounds.length ? h('section.panel', h('div.panel__head', h('h3.label', 'Punkteverlauf')), h('div.history__chart', canvas)) : null,
    h('section', h('h3.label.history__sub', 'Runden'), state.rounds.length ? roundTable(state) : h('p.caption', 'Keine Runden gespielt.')),
    state.rounds.length ? h('section', h('h3.label.history__sub', 'Kennzahlen'), playerStats(state)) : null,
    canWrite ? h('details.history__log', h('summary.label', 'Änderungsprotokoll'), logHost) : null);
  if (state.rounds.length) {
    chart = new ScoreChart(canvas, { compact: true });
    chart.update(state.statistics.chart, state.winners.map((w) => w.player_id), state.game.target_score);
  }
  if (canWrite) logHost.closest('details').addEventListener('toggle', () => eventLog(state, logHost), { once: true });
}

async function remove(state) {
  const ok = await confirmDialog({ title: 'Spiel endgültig löschen?', confirm: 'Löschen',
    text: `„${gameTitle(state.game)}“ wird mit allen Runden gelöscht. Vorher wird automatisch eine Sicherung angelegt.` });
  if (!ok) return;
  try {
    await api.del(`/api/v1/games/${state.game.id}`);
    toast('Spiel gelöscht', { type: 'ok' });
    games = games.filter((g) => g.id !== state.game.id);
    selected = null;
    history.replaceState(null, '', location.pathname);
    replace($('#detail'));
    renderList();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}

$('#sort').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-sort]');
  if (!btn) return;
  sort = btn.dataset.sort;
  $$('#sort [data-sort]').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
  renderList();
});
$('#dir').addEventListener('click', (e) => {
  desc = !desc;
  e.currentTarget.textContent = sort === 'date' ? (desc ? 'Neueste zuerst' : 'Älteste zuerst') : desc ? 'Absteigend' : 'Aufsteigend';
  renderList();
});

(async () => {
  replace($('#list'), h('li.skeleton'), h('li.skeleton'));
  try {
    const [all, ns] = await Promise.all([api.get('/api/v1/history'), api.get('/api/v1/nights')]);
    games = all.filter((g) => !g.night_id);
    nights = ns;
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
  renderList();
  const hash = location.hash.slice(1);
  const first = sorted()[0];
  if (hash.startsWith('n') && nights.some((n) => n.id === Number(hash.slice(1)))) openNight(Number(hash.slice(1)));
  else if (Number(hash)) open(Number(hash));
  else if (first && matchMedia('(min-width: 1024px)').matches) {
    if (first.kind === 'night') openNight(first.night.id);
    else open(first.game.id);
  }
})();
