// Führungsanzeige + Rangliste. Zeilen bleiben über Updates erhalten (Schlüssel = Spieler-ID),
// damit Umsortierung (FLIP) und Hochzählen ohne Layoutsprünge funktionieren.
import { fmt, h, replace } from '../core/dom.js';
import { reducedMotion } from '../core/prefs.js';
import { countUp } from './countUp.js';
import { measure, play } from './flipList.js';
import { segments } from './segments.js';

const CROWN = () => {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'lead__crown');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', 'M3 18h18M4 15l-1-9 5 4 4-6 4 6 5-4-1 9z');
  svg.append(p);
  return svg;
};

function moveText(m) {
  if (m === undefined) return { text: '–', cls: '' };
  if (m > 0) return { text: `▲${m}`, cls: 'is-up', label: `${m} Platz besser` };
  if (m < 0) return { text: `▼${-m}`, cls: 'is-down', label: `${-m} Platz schlechter` };
  return { text: '–', cls: '' };
}

export class Leaderboard {
  constructor(root) {
    this.root = root;
    this.lead = h('section.lead-wrap', { 'aria-label': 'Führung' });
    this.list = h('ol.ranklist', { 'aria-label': 'Rangliste' });
    replace(root, this.lead, this.list);
    this.rows = new Map();
    this.prevLeader = null;
    this.prevRound = null;
  }

  update(state, d) {
    this.renderLead(state, d);
    this.renderRows(state, d);
  }

  renderLead(state, d) {
    const target = state.game.target_score;
    const board = state.leaderboard;
    const leaders = board.filter((r) => d.leaderIds.includes(r.player_id));
    const key = d.leaderIds.join(',');
    const changed = this.prevLeader !== null && key !== this.prevLeader && leaders.length === 1;
    this.prevLeader = key;

    if (!leaders.length) {
      replace(this.lead, h('div.lead.lead--open.notch-xl',
        h('span.label.lead__label', state.game.status === 'CREATED' ? 'Spiel noch nicht gestartet' : 'Führung noch offen'),
        h('div.lead__name', state.game.status === 'CREATED' ? 'Gleich geht’s los' : `Warte auf Runde ${state.game.current_round}`),
        segments(0, target, { onAccent: true })));
      return;
    }
    if (leaders.length > 1) {
      const card = h('div.lead.lead--tie.notch-xl', h('span.label.lead__label', `Gleichstand · ${fmt(leaders[0].total)} Punkte`),
        h('div.lead__split', leaders.map((r) => h('div.lead__half',
          h('div.lead__name.ellipsis', { title: r.name }, r.name),
          h('div.num.lead__score--half', fmt(r.total))))),
        segments(leaders[0].total, target, { onAccent: true }));
      replace(this.lead, h('div.stack.stack--one', card));
      return;
    }
    const r = leaders[0];
    const scoreEl = h('div.num.lead__score');
    scoreEl.dataset.value = this.lead.querySelector('.lead__score')?.dataset.value ?? r.total;
    scoreEl.textContent = fmt(Number(scoreEl.dataset.value));
    const lastPts = r.last_round_points;
    const card = h('div.lead.notch-xl', { class: `lead notch-xl${changed && !reducedMotion() ? ' is-new-leader' : ''}` },
      h('div.lead__main',
        h('span.label.lead__label', CROWN(), d.leaderSince ? `Führt seit Runde ${d.leaderSince}` : 'Führt'),
        h('div.lead__name.ellipsis', { title: r.name }, r.name),
        segments(r.total, target, { onAccent: true }),
        h('p.lead__meta', r.reached_target ? 'Ziel erreicht' : `noch ${fmt(r.to_target)} bis zum Ziel`,
          d.gapToSecond ? ` · +${fmt(d.gapToSecond)} vor Platz 2` : '')),
      h('div.lead__side',
        lastPts !== null && lastPts !== undefined ? h('div.lead__last', h('span.label', 'Letzte Runde'),
          h('span.num', lastPts === 0 ? '0' : `+${fmt(lastPts)}`)) : null,
        scoreEl));
    replace(this.lead, h(`div.stack.stack--one${changed && !reducedMotion() ? '.is-wobble' : ''}`, card));
    countUp(scoreEl, r.total, { duration: 600 });
  }

  renderRows(state, d) {
    const target = state.game.target_score;
    const rest = state.leaderboard.filter((r) => !d.leaderIds.includes(r.player_id));
    const compact = state.leaderboard.length >= 7;
    this.list.classList.toggle('ranklist--compact', compact);
    this.list.classList.toggle('ranklist--two', state.leaderboard.length >= 10);
    const before = measure(this.list);
    const keep = new Set();
    const newRound = this.prevRound !== null && state.game.rounds_played !== this.prevRound;
    this.prevRound = state.game.rounds_played;
    const rankCounts = new Map();
    for (const r of state.leaderboard) rankCounts.set(r.rank, (rankCounts.get(r.rank) || 0) + 1);

    for (const r of rest) {
      const key = String(r.player_id);
      keep.add(key);
      let row = this.rows.get(key);
      if (!row) {
        row = this.buildRow(key);
        this.rows.set(key, row);
      }
      const mv = moveText(d.movement.get(r.player_id));
      row.el.classList.toggle('rank-row--compact', compact);
      row.pip.classList.toggle('is-tie', rankCounts.get(r.rank) > 1);
      row.rank.textContent = r.rank;
      row.move.textContent = mv.text;
      row.move.className = `pip__move ${mv.cls}`;
      row.move.setAttribute('aria-label', mv.label || 'unverändert');
      row.name.textContent = r.name;
      row.name.title = r.name;
      row.sub.textContent = r.reached_target ? 'Ziel erreicht' : `noch ${fmt(r.to_target)}`;
      const lp = r.last_round_points;
      const skipped = (lp === null || lp === undefined) && state.game.rounds_played > 0;
      row.last.textContent = skipped ? '–' : lp === null || lp === undefined ? '' : lp === 0 ? '0' : `+${fmt(lp)}`;
      row.last.title = skipped ? 'Runde nicht mitgespielt' : '';
      row.last.className = `rank-row__last num ${lp === 0 ? 'is-bust' : newRound ? 'is-fresh' : 'is-old'}`;
      if (newRound && lp !== 0) setTimeout(() => row.last.classList.replace('is-fresh', 'is-old'), 2000);
      replace(row.progress, segments(r.total, target));
      countUp(row.total, r.total);
      this.list.append(row.el); // Reihenfolge = Rangliste
    }
    for (const [key, row] of this.rows) {
      if (!keep.has(key)) {
        row.el.remove();
        this.rows.delete(key);
      }
    }
    play(this.list, before);
  }

  buildRow(key) {
    const rank = h('span.pip__rank');
    const move = h('span.pip__move');
    const pip = h('div.pip', rank, move);
    const name = h('div.rank-row__name.ellipsis');
    const sub = h('div.rank-row__sub');
    const last = h('div.rank-row__last.num');
    const progress = h('div.rank-row__progress');
    const total = h('div.rank-row__total');
    const el = h('li.rank-row', { dataset: { key } }, pip, h('div.rank-row__who', name, sub), last, progress, total);
    return { el, pip, rank, move, name, sub, last, progress, total };
  }
}
