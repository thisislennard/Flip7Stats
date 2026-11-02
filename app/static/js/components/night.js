// Spieleabend: Abendstand (Partiesiege je Spieler) und Abend-Endstand. Daten kommen vollständig vom Server
// (state.night bzw. /api/v1/nights/<id>).
import { api } from '../core/api.js';
import { fmt, fmtShortDate, h } from '../core/dom.js';
import { confirmDialog } from './modal.js';
import { toast } from './toast.js';

export function nightTitle(night) {
  return night.name || `Spieleabend ${fmtShortDate(night.created_at)}`;
}

/** Kompakter Abendstand (Dashboard, Steuerung, Gewinneranzeige). */
export function nightStandings(night, { title = 'Abendstand', limit = 8 } = {}) {
  const rows = night.standings.slice(0, limit);
  return h('section.nightbox', { 'aria-label': title },
    h('div.nightbox__head', h('h2.label', title),
      h('span.caption', `Partiesiege · ${night.games_finished} ${night.games_finished === 1 ? 'Partie' : 'Partien'} gespielt · Ziel ${fmt(night.target_score)}`)),
    rows.length ? h('ol.nightbox__list', rows.map((r) => h(`li.nightbox__row${r.rank === 1 && r.wins > 0 ? '.is-top' : ''}`,
      h('span.num.nightbox__rank', r.rank),
      h('span.ellipsis', { title: r.name }, r.name),
      h('span.nightbox__wins', { 'aria-label': `${r.wins} Siege` },
        Array.from({ length: Math.min(r.wins, 12) }, () => h('span.nightbox__pip', { 'aria-hidden': 'true' })),
        h('span.num', fmt(r.wins)), h('span.nightbox__unit', r.wins === 1 ? 'Sieg' : 'Siege'))))) : h('p.caption', 'Noch keine Partie beendet.'));
}

/** Ergebnis eines Abends in Worten: Gewinner bzw. Gleichstand. */
export function nightVerdict(night) {
  const top = night.standings.filter((r) => r.rank === 1 && r.wins > 0);
  const wins = top[0]?.wins ?? 0;
  return {
    top,
    kicker: top.length > 1 ? 'Gleichstand am Spieleabend' : top.length ? 'Gewinnt den Spieleabend' : 'Kein Partiesieg',
    detail: top.length ? `${top.length > 1 ? 'je ' : ''}${fmt(wins)} ${wins === 1 ? 'Partiesieg' : 'Partiesiege'}` : '',
  };
}

/** Endstand-Tabelle eines Abends. */
export function nightTable(night) {
  return h('div.table-wrap', h('table.table.night-table',
    h('thead', h('tr', ['Platz', 'Spieler', 'Siege', 'Partien', 'Punkte gesamt', 'Beste Runde'].map((t) => h('th', t)))),
    h('tbody', night.standings.map((r) => h(`tr${r.rank === 1 && r.wins > 0 ? '.is-top' : ''}`, h('td', r.rank), h('td', r.name),
      h('td', `${fmt(r.wins)}${r.shared_wins ? ` (${r.shared_wins} geteilt)` : ''}`), h('td', fmt(r.games_played)),
      h('td', fmt(r.total_points)), h('td', fmt(r.best_round)))))));
}

function gameResult(g) {
  if (g.status === 'CANCELLED') return h('span.tag.tag--cancel', h('span', 'Abgebrochen'));
  if (g.status !== 'FINISHED') return h('span', 'läuft');
  return h('span', g.winners.length ? `${g.winners.join(' & ')} · ${fmt(g.top_total)}` : 'ohne Sieger beendet');
}

/** Partien eines Abends; mit onOpen bekommt jede abgeschlossene Partie „Ansehen“. */
export function nightGames(night, { onOpen = null } = {}) {
  return h('ol.night-games', night.games.map((g) => h('li',
    h('span.label', `Partie ${g.number}`),
    gameResult(g),
    h('span.caption', `${g.rounds_played} ${g.rounds_played === 1 ? 'Runde' : 'Runden'}`),
    onOpen ? (g.status !== 'ACTIVE' ? h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: () => onOpen(g) }, 'Ansehen')
      : h('span')) : null)));
}

/** Abend-Endstand. */
export function nightSummaryView(night, { actions = [], small = false } = {}) {
  const verdict = nightVerdict(night);
  const { top } = verdict;
  const tie = top.length > 1;
  const stack = tie
    ? h('div.win__tie', top.map((r) => h('div.win__card.notch-xl', h('span.label', 'Siege'), h('span.num.win__score', fmt(r.wins)))))
    : h('div.win__stack', h('span.win__layer.win__layer--purple'), h('span.win__layer.win__layer--cyan'),
      h('div.win__card.notch-xl', h('span.label', 'Partiesiege'), h('span.num.win__score', fmt(top[0]?.wins ?? 0))));
  return h(`section.win.win--night${small ? '.win--small' : ''}`, { 'aria-label': 'Abend-Endstand' },
    h('span.win__ghost', { 'aria-hidden': 'true' }, '7'),
    stack,
    h('div.win__main',
      h('span.label.win__kicker', verdict.kicker),
      h('div.win__names', top.length ? top.map((r) => h('div.win__name', { title: r.name }, r.name)) : h('div.win__name', nightTitle(night))),
      nightTable(night),
      nightGames(night),
      actions.length ? h('div.win__actions', actions) : null));
}

export async function startNextGame(night, btn) {
  btn?.classList.add('is-loading');
  try {
    const state = await api.post(`/api/v1/nights/${night.id}/next-game`);
    toast(`Partie ${state.game.night_number} gestartet`, { type: 'ok' });
    return state;
  } catch (e) {
    toast(e.message, { type: 'error' });
    return null;
  } finally {
    btn?.classList.remove('is-loading');
  }
}

export async function finishNight(night, btn) {
  const ok = await confirmDialog({ title: 'Spieleabend beenden?', confirm: 'Abend beenden', danger: false,
    text: 'Der Abend-Endstand wird angezeigt und in der Historie gespeichert. Danach kann ein neuer Spieleabend beginnen.' });
  if (!ok) return null;
  btn?.classList.add('is-loading');
  try {
    const summary = await api.post(`/api/v1/nights/${night.id}/finish`);
    toast('Spieleabend beendet', { type: 'ok' });
    return summary;
  } catch (e) {
    toast(e.message, { type: 'error' });
    return null;
  } finally {
    btn?.classList.remove('is-loading');
  }
}
