// Neues Spiel: Spielername optional, Spielerliste (inline bearbeitbar, sortierbar, entfernen mit
// Rückgängig), frühere Spieler per Dropdown, Punkteziel als Segmentsteuerung, „Spiel starten“.
import { api, ApiError, isAdmin } from '../core/api.js';
import { $, fmt, fmtShortDate, h, replace } from '../core/dom.js';
import '../core/prefs.js';
import { confirmDialog } from '../components/modal.js';
import { finishNight, nightStandings, nightTitle, startNextGame } from '../components/night.js';
import { toast } from '../components/toast.js';

const root = $('.setup');
const DEFAULT_TARGET = Number(root.dataset.defaultTarget) || 200;
const DEFAULT_ROWS = Number(root.dataset.defaultPlayers) || 4;
const TARGETS = [150, 200, 250, 300];

let known = []; // [{id, display_name}]
let rows = []; // [{uid, id|null, name, originalName}]
let target = DEFAULT_TARGET;
let customTarget = !TARGETS.includes(DEFAULT_TARGET);
let game = null; // vorbereitetes Spiel (CREATED)
let uidSeq = 0;
const list = $('#players');

const norm = (s) => s.trim().replace(/\s+/g, ' ').toLocaleLowerCase('de-DE');
const newRow = (p = null) => ({ uid: ++uidSeq, id: p?.id ?? null, name: p?.display_name ?? '', originalName: p?.display_name ?? null });

function renderRows(focusUid = null) {
  replace(list, rows.map((r, i) => {
    const input = h('input.input.setup__name', { value: r.name, maxlength: 40, placeholder: `Spieler ${i + 1}`,
      'aria-label': `Name Spieler ${i + 1}`, autocomplete: 'off', enterkeyhint: 'next', dataset: { uid: r.uid } });
    input.addEventListener('input', () => {
      r.name = input.value;
      renderKnown();
      updateHint();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const next = rows[i + 1];
      if (next) list.querySelector(`[data-uid="${next.uid}"]`)?.focus();
      else addRow();
    });
    return h('li.setup__row', { dataset: { key: r.uid } },
      h('div.pip.setup__pip', h('span.pip__rank', i + 1)),
      input,
      h('div.setup__rowbtns',
        h('button.btn.btn--secondary.btn--small', { type: 'button', 'aria-label': `${r.name || `Spieler ${i + 1}`} nach oben`,
          disabled: i === 0, onclick: () => move(i, -1) }, '↑'),
        h('button.btn.btn--secondary.btn--small', { type: 'button', 'aria-label': `${r.name || `Spieler ${i + 1}`} nach unten`,
          disabled: i === rows.length - 1, onclick: () => move(i, 1) }, '↓'),
        h('button.btn.btn--danger.btn--small', { type: 'button', 'aria-label': `${r.name || `Spieler ${i + 1}`} entfernen`,
          onclick: () => remove(i) }, '✕')));
  }));
  if (focusUid) list.querySelector(`[data-uid="${focusUid}"]`)?.focus();
  renderKnown();
  updateHint();
}

function renderKnown() {
  const used = new Set(rows.map((r) => norm(r.name)).filter(Boolean));
  const avail = known.filter((p) => !used.has(norm(p.display_name)));
  const host = $('#known-players');
  if (!avail.length) {
    replace(host, h('select.input.setup__known', { disabled: true, 'aria-label': 'Bisherige Spieler' },
      h('option', known.length ? 'Alle bisherigen Spieler sind eingetragen' : 'Noch keine bisherigen Spieler')));
    return;
  }
  const select = h('select.input.setup__known', { 'aria-label': 'Bisherigen Spieler hinzufügen' },
    h('option', { value: '' }, `Bisherige Spieler (${avail.length}) …`),
    avail.map((p) => h('option', { value: String(p.id) },
      p.last_played_at ? `${p.display_name} · zuletzt ${fmtShortDate(p.last_played_at)}` : p.display_name)));
  select.addEventListener('change', () => {
    const p = avail.find((x) => String(x.id) === select.value);
    if (p) takeOver(p);
  });
  replace(host, select);
}

function takeOver(p) {
  const empty = rows.find((r) => !r.name.trim());
  if (empty) Object.assign(empty, newRow(p), { uid: empty.uid });
  else rows.push(newRow(p));
  renderRows();
}

function addRow() {
  const r = newRow();
  rows.push(r);
  renderRows(r.uid);
}

function move(i, dir) {
  const j = i + dir;
  [rows[i], rows[j]] = [rows[j], rows[i]];
  renderRows();
  list.children[j]?.querySelectorAll('button')[dir < 0 ? 0 : 1]?.focus();
}

function remove(i) {
  const [removed] = rows.splice(i, 1);
  renderRows();
  if (removed.name.trim()) {
    toast(`${removed.name} entfernt`, { type: 'info', duration: 5000, action: { label: 'Rückgängig', onClick: () => {
      rows.splice(Math.min(i, rows.length), 0, removed);
      renderRows();
    } } });
  }
}

function renderTargets() {
  const host = $('#target-options');
  replace(host, TARGETS.map((t) => h('button.segctl__opt', { type: 'button', 'aria-pressed': String(!customTarget && target === t),
    onclick: () => { customTarget = false; target = t; renderTargets(); } }, fmt(t))),
  h('button.segctl__opt', { type: 'button', 'aria-pressed': String(customTarget), onclick: () => {
    customTarget = true;
    renderTargets();
    $('#target-custom').focus();
  } }, 'eigenes'));
  const custom = $('#target-custom');
  custom.hidden = !customTarget;
  if (customTarget && !custom.value) custom.value = String(target);
  updateHint();
}

function filledRows() {
  return rows.filter((r) => r.name.trim());
}

function updateHint() {
  const n = filledRows().length;
  const btn = $('#start-game');
  btn.disabled = n < 2;
  $('#start-hint').textContent = n < 2 ? 'Mindestens 2 Spieler eintragen.' : `${n} Spieler · ${fmt(currentTarget() || target)} Punkte pro Partie`;
}

function currentTarget() {
  if (!customTarget) return target;
  const raw = $('#target-custom').value.trim();
  return /^\d+$/.test(raw) ? Number(raw) : null;
}

async function resolvePlayerIds() {
  const filled = filledRows();
  const names = filled.map((r) => norm(r.name));
  const dup = names.find((n, i) => names.indexOf(n) !== i);
  if (dup) throw new ApiError(422, 'duplicate', `„${filled[names.indexOf(dup)].name.trim()}“ steht doppelt in der Liste.`);
  const ids = [];
  for (const r of filled) {
    const name = r.name.trim();
    const match = known.find((p) => norm(p.display_name) === norm(name));
    if (match && (r.id === null || r.id === match.id)) {
      ids.push(match.id);
      continue;
    }
    if (r.id !== null && r.originalName && norm(r.originalName) !== norm(name)) {
      // Name eines übernommenen Spielers geändert → Spieler umbenennen (gleiche Identität, Statistik bleibt).
      await api.patch(`/api/v1/players/${r.id}`, { display_name: name });
      ids.push(r.id);
      continue;
    }
    try {
      const p = await api.post('/api/v1/players', { display_name: name });
      known.push(p);
      ids.push(p.id);
    } catch (e) {
      if (e.code === 'player_exists' && e.details?.player) ids.push(e.details.player.id);
      else throw e;
    }
  }
  return ids;
}

$('#target-custom').addEventListener('input', (e) => {
  e.target.value = e.target.value.replace(/\D+/g, '');
  updateHint();
});
$('#add-player').addEventListener('click', addRow);

$('#setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#start-game');
  const t = currentTarget();
  if (!t || t < 1 || t > 10000) {
    toast('Bitte ein Punkteziel zwischen 1 und 10.000 eingeben.', { type: 'error' });
    $('#target-custom').focus();
    return;
  }
  btn.classList.add('is-loading');
  try {
    const player_ids = await resolvePlayerIds();
    if (game) await api.post(`/api/v1/games/${game.id}/cancel`); // vorbereitetes Einzelspiel (ältere Version) ersetzen
    await api.post('/api/v1/nights', { name: $('#game-name').value.trim() || null, target_score: t, player_ids });
    location.href = isAdmin ? '/dashboard' : '/controller';
  } catch (err) {
    toast(err.message, { type: 'error' });
    btn.classList.remove('is-loading');
  }
});

function blockedNight(state) {
  const night = state.night;
  const box = $('#setup-blocked');
  box.hidden = false;
  $('#setup-form').hidden = true;
  const next = h('button.btn.btn--primary', { type: 'button', onclick: async () => {
    if (await startNextGame(night, next)) location.href = isAdmin ? '/dashboard' : '/controller';
  } }, `Partie ${night.current_number + 1} starten`);
  const end = h('button.btn.btn--secondary', { type: 'button', onclick: async () => {
    if (await finishNight(night, end)) location.reload();
  } }, 'Abend beenden');
  replace(box, h('div.empty', h('p.label', 'Spieleabend läuft'),
    h('p.empty__text', `${nightTitle(night)} · ${night.games_finished} ${night.games_finished === 1 ? 'Partie' : 'Partien'} gespielt.`),
    nightStandings(night), h('div.setup__blockbtns', next, end)));
}

function blocked(state) {
  const box = $('#setup-blocked');
  box.hidden = false;
  $('#setup-form').hidden = true;
  replace(box, h('div.empty', h('p.label', 'Es läuft bereits ein Spiel'),
    h('p.empty__text', `${state.game.night_number ? `Partie ${state.game.night_number} · ` : ''}Runde ${state.game.current_round} · ${state.players.length} Spieler. Bitte die laufende Partie zuerst beenden oder abbrechen.`),
    h('div.setup__blockbtns',
      h('a.btn.btn--primary', { href: isAdmin ? '/dashboard' : '/controller' }, 'Zur laufenden Partie'),
      h('button.btn.btn--danger', { type: 'button', onclick: async () => {
        const ok = await confirmDialog({ title: 'Laufende Partie abbrechen?', confirm: 'Partie abbrechen',
          text: 'Es wird als „abgebrochen“ in der Historie gespeichert und zählt nicht für die Statistik.' });
        if (!ok) return;
        try {
          await api.post(`/api/v1/games/${state.game.id}/cancel`);
          location.reload();
        } catch (err) { toast(err.message, { type: 'error' }); }
      } }, 'Partie abbrechen'))));
}

(async () => {
  $('#game-name').placeholder = `Spieleabend ${fmtShortDate(new Date().toISOString())}`;
  try {
    const [players, current] = await Promise.all([api.get('/api/v1/players'), api.get('/api/v1/games/current?display=1')]);
    known = players;
    if (current?.game.status === 'ACTIVE') {
      blocked(current);
      return;
    }
    if (current?.night?.status === 'ACTIVE') {
      blockedNight(current);
      return;
    }
    if (current?.night) {
      // Spieler des letzten Abends vorschlagen
      rows = current.players.map((p) => newRow({ id: p.player_id, display_name: p.name }));
      target = current.night.target_score;
      customTarget = !TARGETS.includes(target);
    }
    if (current?.game.status === 'CREATED') {
      game = current.game;
      $('#game-name').value = game.name || '';
      target = game.target_score;
      customTarget = !TARGETS.includes(target);
      rows = current.players.map((p) => newRow({ id: p.player_id, display_name: p.name }));
    }
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
  if (!rows.length) rows = Array.from({ length: DEFAULT_ROWS }, () => newRow());
  renderTargets();
  renderRows();
})();
