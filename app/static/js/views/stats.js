// Langzeitstatistik: Ansicht „Alle“ (Module, Siege-Balken, Entwicklung, Tabelle) oder „Spieler“
// (Auswahl per Dropdown – bleibt auch bei vielen Spielern übersichtlich). Gleichstand-Regel als Fußnote.
import { api, canWrite } from '../core/api.js';
import { $, fmt, fmtShortDate, gameTitle, h, replace } from '../core/dom.js';
import '../core/prefs.js';
import { ScoreChart } from '../components/chartTheme.js';

const root = $('#stats-root');
let data = null;
let history = [];
let view = 'all'; // 'all' | 'player'
let selected = null; // Spieler-ID in der Spieleransicht
let showAllBars = false;
const BAR_LIMIT = 10;
let chart = null;

const dec = (v) => (v === null || v === undefined ? '–' : Number(v).toLocaleString('de-DE', { maximumFractionDigits: 1 }));

function module(label, value, context) {
  return h('div.stat.stats__module', h('span.label', label), h('span.stat__value', value), context ? h('span.stat__context', context) : null);
}

function render() {
  if (!data.games_played) {
    replace(root, h('div.empty', h('p.empty__text', 'Noch keine abgeschlossenen Partien – die Statistik füllt sich nach dem ersten Spieleabend.'),
      canWrite ? h('a.btn.btn--primary', { href: '/setup' }, 'Neues Spiel starten') : null));
    return;
  }
  const players = data.players;
  const sel = view === 'player' ? players.find((p) => p.player_id === selected) || players[0] : null;
  if (sel) selected = sel.player_id;
  const head = h('div.stats__head',
    h('div.segctl', { role: 'group', 'aria-label': 'Ansicht' },
      h('button.segctl__opt', { type: 'button', 'aria-pressed': String(!sel), onclick: () => show('all') }, 'Alle'),
      h('button.segctl__opt', { type: 'button', 'aria-pressed': String(!!sel), onclick: () => show('player', selected) }, 'Spieler')),
    sel ? picker(players, sel) : h('span.caption', `${players.length} Spieler · ${fmt(data.games_played)} Partien`));

  const modules = sel
    ? [module('Partien', fmt(sel.games_played)), module('Siege', fmt(sel.wins), sel.shared_wins ? `davon ${sel.shared_wins} geteilt` : ''),
      module('Siegquote', `${dec(sel.win_rate)} %`), module('Spieleabende', fmt(sel.nights_played), `${fmt(sel.night_wins)} Abendsiege`),
      module('Ø Punkte je Runde', dec(sel.average_round_points)),
      module('Ø Endpunktzahl', dec(sel.average_final_score)), module('Höchste Endpunktzahl', fmt(sel.highest_final_score)),
      module('Beste Einzelrunde', fmt(sel.best_round)), module('Gespielte Runden', fmt(sel.rounds_played))]
    : [module('Spieleabende', fmt(data.nights_played)), module('Partien', fmt(data.games_played)), module('Gespielte Runden', fmt(data.total_rounds)),
      module('Ø Runden je Spiel', dec(data.total_rounds / data.games_played)),
      module('Höchste Einzelrunde', fmt(data.best_round?.points), data.best_round ? `${data.best_round.name} · R${data.best_round.round_number}` : ''),
      module('Höchste Endpunktzahl', fmt(data.highest_final_score?.points), data.highest_final_score?.name)];

  const canvas = h('canvas', { 'aria-label': 'Endpunktzahl je Partie' });
  const chartPanel = h('section.panel', h('div.panel__head', h('h2.label', sel ? `Entwicklung von ${sel.name}` : 'Entwicklung über Partien')),
    h('div.stats__chart', canvas));
  if (sel) {
    const byWins = [...players].sort((a, b) => b.wins - a.wins);
    const place = byWins.findIndex((p) => p.wins === sel.wins) + 1;
    replace(root, head, h('div.stats__modules', modules,
      module('Platz nach Siegen', `${place}.`, `von ${players.length} Spielern`)), chartPanel,
    h('p.caption.stats__foot', `Gleichstand: ${data.tie_rule}`));
  } else {
    const ranked = [...players].sort((a, b) => b.wins - a.wins || a.name.localeCompare(b.name, 'de'));
    const visible = showAllBars ? ranked : ranked.slice(0, BAR_LIMIT);
    const maxWins = Math.max(1, ...players.map((p) => p.wins));
    const bars = h('ol.stats__bars', visible.map((p) => h('li.stats__bar',
      h('button.stats__name.ellipsis', { type: 'button', title: `${p.name} – Einzelstatistik`, onclick: () => show('player', p.player_id) }, p.name),
      h('span.stats__track', h('span.stats__fill')),
      h('span.num', fmt(p.wins)))));
    // Balkenbreiten per CSSOM (CSP erlaubt keine style-Attribute im Markup)
    [...bars.children].forEach((li, i) => { li.querySelector('.stats__fill').style.width = `${(visible[i].wins / maxWins) * 100}%`; });
    const more = ranked.length > BAR_LIMIT
      ? h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: () => { showAllBars = !showAllBars; render(); } },
        showAllBars ? 'Weniger anzeigen' : `Alle ${ranked.length} anzeigen`) : null;

    const table = h('div.table-wrap', h('table.table.stats__table',
      h('thead', h('tr', ['Spieler', 'Abende', 'Abendsiege', 'Partien', 'Siege', 'Quote', 'Ø Endstand', 'Ø Runde', 'Höchster Endstand', 'Beste Runde'].map((t) => h('th', t)))),
      h('tbody', ranked.map((p) => h('tr',
        h('td', h('button.stats__name', { type: 'button', title: 'Einzelstatistik', onclick: () => show('player', p.player_id) }, p.name)),
        h('td', fmt(p.nights_played)), h('td', fmt(p.night_wins)),
        h('td', fmt(p.games_played)), h('td', `${fmt(p.wins)}${p.shared_wins ? ` (${p.shared_wins}=)` : ''}`),
        h('td', `${dec(p.win_rate)} %`), h('td', dec(p.average_final_score)), h('td', dec(p.average_round_points)),
        h('td', fmt(p.highest_final_score)), h('td', fmt(p.best_round)))))));

    replace(root, head, h('div.stats__modules', modules),
      h('div.stats__grid',
        h('section.panel', h('div.panel__head', h('h2.label', 'Partiesiege pro Spieler')), bars, more),
        chartPanel),
      h('section', h('h2.label.stats__sub', 'Alle Spieler'), table),
      h('p.caption.stats__foot', `Gleichstand: ${data.tie_rule}`));
  }

  chart?.destroy();
  // Spieleransicht: nur Partien, in denen der Spieler mitgespielt hat
  const finished = history.filter((g) => g.status === 'FINISHED' && (!sel || g.leaderboard.some((r) => r.player_id === sel.player_id)))
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
  const shown = sel ? [sel] : [...players].sort((a, b) => b.wins - a.wins).slice(0, 8);
  const series = shown.map((p) => ({ player_id: p.player_id, name: p.name,
    data: finished.map((g) => g.leaderboard.find((r) => r.player_id === p.player_id)?.total ?? null) }));
  chart = new ScoreChart(canvas, { compact: true });
  chart.chart.options.spanGaps = true;
  chart.chart.options.scales.x.title = { display: true, text: sel ? `Partien von ${sel.name}` : 'Partie' };
  chart.chart.options.scales.y.title = { display: true, text: 'Endpunktzahl' };
  chart.chart.options.plugins.tooltip.callbacks.title = (items) => {
    const g = finished[items[0].dataIndex];
    return g ? `${fmtShortDate(g.created_at)} · ${gameTitle(g)}` : '';
  };
  chart.update({ labels: finished.map((_, i) => i + 1), series }, sel ? [sel.player_id] : [], null, { fromZero: false });
}

function picker(players, sel) {
  const sorted = [...players].sort((a, b) => a.name.localeCompare(b.name, 'de'));
  const select = h('select.input.stats__picker', { 'aria-label': 'Spieler auswählen' },
    sorted.map((p) => h('option', { value: String(p.player_id), selected: p.player_id === sel.player_id },
      `${p.name} · ${fmt(p.games_played)} ${p.games_played === 1 ? 'Partie' : 'Partien'}`)));
  select.addEventListener('change', () => show('player', Number(select.value)));
  return select;
}

// Ansicht in der Adresse merken (#spieler-12), damit Zurück/Neuladen dieselbe Ansicht zeigt.
function show(next, id = null) {
  view = next;
  selected = id;
  window.history.replaceState(null, '', next === 'player' && id ? `#spieler-${id}` : location.pathname);
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

const fromHash = /^#spieler-(\d+)$/.exec(location.hash);
if (fromHash) {
  view = 'player';
  selected = Number(fromHash[1]);
}

(async () => {
  try {
    [data, history] = await Promise.all([api.get('/api/v1/statistics/overall'), api.get('/api/v1/history')]);
    render();
  } catch (e) {
    replace(root, h('div.empty', h('p.empty__text', e.message)));
  }
})();
