// Gewinneranzeige – auch verkleinert im Historien-Detail. Gleichstand: mehrere gleich große Karten.
import { fmt, h, replace } from '../core/dom.js';
import { reducedMotion } from '../core/prefs.js';
import { countUp } from './countUp.js';
import { nightTitle } from './night.js';

export function winnerView(state, d, { actions = [], small = false, extra = null } = {}) {
  const winners = state.winners.length ? state.winners : state.leaders;
  const board = state.leaderboard;
  const top = board.filter((r) => winners.some((w) => w.player_id === r.player_id));
  const rest = board.filter((r) => !winners.some((w) => w.player_id === r.player_id));
  const tie = top.length > 1;
  const second = rest[0];
  const stats = state.statistics;
  const stack = tie
    ? h('div.win__tie', top.map((r) => h('div.win__card.notch-xl', h('span.num.win__score', fmt(r.total)))))
    : h('div.win__stack', h('span.win__layer.win__layer--purple'), h('span.win__layer.win__layer--cyan'),
      h('div.win__card.notch-xl', h('span.label', 'Endstand'), h('span.num.win__score', fmt(top[0]?.total ?? 0))));
  const facts = [
    ['Runden', fmt(stats.rounds_played)],
    ['Vorsprung', tie || !second ? '–' : `+${fmt((top[0]?.total ?? 0) - second.total)}`],
    ['Beste Runde', d.bestRound ? `${fmt(d.bestRound.points)}` : '–', d.bestRound ? `${d.bestRound.name} · R${d.bestRound.round}` : ''],
    ['Führungswechsel', fmt(stats.lead_change_count)],
  ];
  return h(`section.win${small ? '.win--small' : ''}`, { 'aria-label': 'Gewinner' },
    h('span.win__ghost', { 'aria-hidden': 'true' }, '7'),
    stack,
    h('div.win__main',
      h('span.label.win__kicker', tie ? 'Gleichstand' : state.game.status !== 'FINISHED' ? 'Führt'
        : state.game.night_number ? `Gewinnt Partie ${state.game.night_number}` : 'Gewinnt den Spieleabend'),
      h('div.win__names', top.map((r) => h('div.win__name', { title: r.name }, r.name))),
      h('dl.win__facts', facts.map(([k, v, ctx]) => h('div.stat', h('dt.label', k), h('dd.stat__value', v),
        ctx ? h('dd.stat__context', ctx) : null))),
      rest.length ? h('ol.win__rest', { start: top.length + 1 }, rest.map((r) => h('li.win__restrow',
        h('span.num.win__restrank', r.rank), h('span.ellipsis', { title: r.name }, r.name), h('span.num', fmt(r.total))))) : null,
      extra,
      actions.length ? h('div.win__actions', actions) : null));
}

// ---------------------------------------------------------------- Overlay-Animationen
// Alle Overlays laufen über denselben Host: weich ein- und ausblenden, nur transform/opacity (GPU), nie zwei zugleich.

const FADE_OUT_MS = 380;
let hideTimer = null;
let cleanupTimer = null;
let pending = null;
let ceremonyActive = false; // während der Siegerehrung keine anderen Einblendungen

function show(host, cls, children, durationMs) {
  if (ceremonyActive) return Promise.resolve();
  clearTimeout(hideTimer);
  clearTimeout(cleanupTimer);
  pending?.();
  host.hidden = false;
  host.className = `celebration ${cls}`;
  host.replaceChildren(...children);
  void host.offsetWidth; // Startzustand übernehmen, damit Einblenden und CSS-Animationen neu starten
  host.classList.add('is-on');
  return new Promise((resolve) => {
    pending = resolve;
    hideTimer = setTimeout(() => {
      host.classList.add('is-leaving');
      cleanupTimer = setTimeout(() => {
        host.className = 'celebration';
        host.hidden = true;
        host.replaceChildren();
        pending = null;
        resolve();
      }, FADE_OUT_MS);
    }, durationMs);
  });
}

/** Einmalige Feier beim Spielende: Karten fallen auf den Stapel, Segmente platzen nach außen. */
export function celebrate(host, durationMs = 1900) {
  return show(host, 'is-win', [
    h('span.cel__card.cel__card--purple'), h('span.cel__card.cel__card--cyan'), h('span.cel__card.cel__card--yellow'),
    ...Array.from({ length: 10 }, (_, i) => h('span.cel__seg', { dataset: { i } }))], durationMs);
}

function crown() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'spot__crown');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', 'M3 18h18M4 15l-1-9 5 4 4-6 4 6 5-4-1 9z');
  svg.append(p);
  return svg;
}

const KICKERS = { lead: 'Neue Führung', record: 'Beste Runde der Partie', matchball: 'Kurz vor dem Ziel', zero: 'Null Punkte' };

/**
 * Kurze Einblendung zu einem Rundenereignis.
 * kind: 'lead' (neue Führung) · 'record' (beste Runde der Partie) · 'matchball' (kurz vor dem Ziel) · 'zero' (0 Punkte)
 */
export function spotlight(host, { kind, names, value = null, sub = '' }, durationMs = 2300) {
  const calm = reducedMotion();
  const valueEl = h(`span.num.spot__value${value >= 100 ? '.spot__value--long' : ''}`, kind === 'zero' ? '0' : kind === 'record' ? '+0' : fmt(value));
  const card = h('div.spot__card',
    kind === 'lead' ? crown() : null,
    valueEl,
    kind === 'zero' ? Array.from({ length: 3 }, (_, i) => h('span.spot__tear', { dataset: { i } })) : null,
    kind === 'record' ? Array.from({ length: 8 }, (_, i) => h('span.spot__ray', { dataset: { i } })) : null,
    kind === 'matchball' ? h('span.spot__ring') : null);
  const done = show(host, `is-spot is-${kind}`, [h('div.spot',
    card,
    h('p.spot__text', h('span.label.spot__kicker', KICKERS[kind]), h('span.spot__names', names.join(' & ')),
      sub ? h('span.caption.spot__sub', sub) : null))], calm ? 1600 : durationMs);
  if (kind === 'record') {
    if (calm) valueEl.textContent = `+${fmt(value)}`;
    else setTimeout(() => countUp(valueEl, value, { duration: 700, format: (n) => `+${fmt(n)}` }), 260);
  }
  return done;
}

// ---------------------------------------------------------------- Siegerehrung am Abendende
// Vom letzten Platz bis zum Sieger; je besser der Platz, desto größer die Animation. Danach blendet das Overlay aus
// und gibt den Abend-Endstand frei, der darunter schon gerendert ist.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const TIER_MS = { low: 1700, bronze: 2300, silver: 2600, gold: 4600 };
const DRUMROLL_MS = 1700;
// Eigene Animationen für Platz 4 und schlechter – zufällig, ohne Wiederholung innerhalb einer Siegerehrung und ohne
// die Bewegungen der Plätze 1–3 (3D-Aufdrehen, Federn, Fallen von oben).
const LOW_VARIANTS = ['slide-left', 'slide-right', 'rise', 'spin', 'zoom', 'tumble', 'shuffle', 'stamp'];

function shuffled(list) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function ceremonySteps(standings) {
  const byRank = new Map();
  for (const r of standings) {
    if (!byRank.has(r.rank)) byRank.set(r.rank, []);
    byRank.get(r.rank).push(r);
  }
  const ranks = [...byRank.keys()].sort((a, b) => b - a); // schlechtester zuerst
  const low = ranks.filter((rk) => rk > 3);
  const steps = [];
  const variants = shuffled(LOW_VARIANTS);
  if (low.length > 4) {
    // Viele Spieler: hintere Plätze gesammelt auf einer Karte
    const rows = low.flatMap((rk) => byRank.get(rk));
    steps.push({ tier: 'low', rank: `${Math.min(...low)}–${Math.max(...low)}`, rows, group: true, variant: variants[0] });
  } else {
    low.forEach((rk, i) => steps.push({ tier: 'low', rank: rk, rows: byRank.get(rk), variant: variants[i % variants.length] }));
  }
  for (const [rk, tier] of [[3, 'bronze'], [2, 'silver'], [1, 'gold']]) {
    if (byRank.has(rk)) steps.push({ tier, rank: rk, rows: byRank.get(rk) });
  }
  return steps;
}

function winsText(r) {
  return `${fmt(r.wins)} ${r.wins === 1 ? 'Partiesieg' : 'Partiesiege'} · ${fmt(r.total_points)} Punkte`;
}

function stepView(step) {
  const { tier, rank, rows } = step;
  const tie = rows.length > 1 && !step.group;
  const kicker = tier === 'gold' ? (tie ? 'Gleichstand – den Spieleabend gewinnen' : 'Gewinnt den Spieleabend')
    : `Platz ${rank}${tie ? ' · geteilt' : ''}`;
  const card = h('div.cer__card',
    tier === 'gold' ? crown() : null,
    h('span.cer__place', tier === 'gold' ? '1' : String(rank).split('–')[0]),
    h('span.label.cer__cardlabel', 'Platz'),
    step.variant === 'shuffle' ? [h('span.cer__ghost.cer__ghost--a'), h('span.cer__ghost.cer__ghost--b')] : null,
    step.variant === 'stamp' ? h('span.cer__dust') : null,
    tier === 'bronze' ? Array.from({ length: 6 }, (_, i) => h('span.cer__spark', { dataset: { i } })) : null,
    tier === 'silver' ? Array.from({ length: 8 }, (_, i) => h('span.spot__ray', { dataset: { i } })) : null,
    tier === 'gold' ? [h('span.spot__ring'), ...Array.from({ length: 16 }, (_, i) => h('span.cer__confetti', { dataset: { i } }))] : null);
  const names = step.group
    ? h('ol.cer__group', rows.map((r) => h('li', h('span.num', `${r.rank}.`), h('span', r.name), h('span.caption', winsText(r)))))
    : h('div.cer__names', rows.map((r) => h('span.cer__name', r.name)));
  return h(`div.cer__stage.is-${tier}${step.variant ? `.v-${step.variant}` : ''}`,
    card,
    h('div.cer__text', h('span.label.cer__kicker', kicker), names,
      !step.group ? h('span.caption.cer__sub', rows.length > 1 ? rows.map((r) => `${r.name}: ${winsText(r)}`).join(' · ') : winsText(rows[0])) : null));
}

/** Spielt die Siegerehrung ab; löst auf, wenn das Overlay wieder weg ist. */
export async function ceremony(host, night) {
  const steps = ceremonySteps(night.standings);
  if (!steps.length) return;
  clearTimeout(hideTimer);
  clearTimeout(cleanupTimer);
  pending?.();
  pending = null;
  ceremonyActive = true;
  const calm = reducedMotion();
  const stage = h('div.cer__slot');
  const ladder = h('ol.cer__ladder', { 'aria-label': 'Bisherige Plätze' });
  host.hidden = false;
  host.className = 'celebration is-ceremony';
  host.replaceChildren(h('div.cer', h('p.label.cer__title', `Siegerehrung · ${nightTitle(night)}`), stage, ladder));
  void host.offsetWidth;
  host.classList.add('is-on');
  for (const step of steps) {
    if (step.tier === 'gold' && !calm) {
      replace(stage, h('div.cer__stage.is-drumroll', h('p.cer__drum', 'Und den Spieleabend gewinnt', h('span.cer__dots', h('span'), h('span'), h('span')))));
      await sleep(DRUMROLL_MS);
    }
    replace(stage, stepView(step));
    await sleep(calm ? 1400 : TIER_MS[step.tier] + (step.group ? 600 : 0));
    if (step.tier !== 'gold') {
      for (const r of step.rows) ladder.prepend(h(`li.cer__rung.is-${step.tier}`, h('span.num', `${r.rank}.`), h('span', r.name)));
    }
  }
  host.classList.add('is-leaving');
  await sleep(FADE_OUT_MS);
  host.className = 'celebration';
  host.hidden = true;
  host.replaceChildren();
  ceremonyActive = false;
}
