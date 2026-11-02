// Smartphone-Steuerung. Punkte aller Spieler eintragen, Runde speichern, Runden korrigieren.
//
// Unbestätigte Speicherung: Es gibt höchstens EINEN offenen Speichervorgang (keine Warteschlange). Er behält seinen
// Idempotenzschlüssel, bis der Server antwortet – „Erneut senden“ kann daher nie eine doppelte Runde erzeugen.
import { api, ApiError, newKey } from '../core/api.js';
import { $, fmt, gameTitle, h, replace } from '../core/dom.js';
import '../core/prefs.js';
import { connectLive, onLive, requestState } from '../core/realtime.js';
import { confirmDialog } from '../components/modal.js';
import { finishNight, nightStandings, startNextGame } from '../components/night.js';
import { connectionChip } from '../components/statusChip.js';
import { dismissToast, toast } from '../components/toast.js';

const MAX = Number($('#ctl').dataset.maxPoints) || 500;
const form = $('#ctl-form');
const saveBtn = $('#save-btn');
const PENDING_KEY = 'flip7live.pending';
const DRAFT_KEY = 'flip7live.draft';

let state = null;
let live = 'connecting';
let mode = { kind: 'new' }; // {kind:'new'} | {kind:'edit', roundId, number}
let sending = false;
let ownSavedRound = null; // {gameId, roundNumber} – zuletzt von DIESEM Gerät gespeichert
let inputs = new Map(); // player_id → {input, zero, row, msg}

const store = {
  get(key) {
    try { return JSON.parse(sessionStorage.getItem(key) || 'null'); } catch { return null; }
  },
  set(key, value) {
    try { value === null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* ohne Speicher */ }
  },
};

// ---------------------------------------------------------------- Rendering

function headFor() {
  const head = $('#ctl-head');
  const edit = mode.kind === 'edit';
  head.classList.toggle('is-edit', edit);
  if (!state || state.game.status === 'CANCELLED') {
    $('#ctl-mode').textContent = 'Kein Spiel';
    $('#ctl-round').textContent = '';
    $('#ctl-hint').textContent = '';
    return;
  }
  $('#ctl-mode').textContent = edit ? 'Runde bearbeiten' : state.game.status === 'ACTIVE' ? 'Runde' : 'Spiel';
  $('#ctl-round').textContent = edit ? mode.number : state.game.status === 'ACTIVE' ? state.game.current_round : '';
  $('#ctl-hint').textContent = edit ? 'Werte ändern und speichern' : state.game.status === 'ACTIVE'
    ? `${gameTitle(state.game, state.night)} · Punkte eintragen · 0 bei Bust` : gameTitle(state.game, state.night);
}

function draftKey() {
  return mode.kind === 'edit' ? `edit-${mode.roundId}` : `${state.game.id}-${state.game.current_round}`;
}

function buildForm() {
  const draft = store.get(DRAFT_KEY);
  const values = draft && draft.key === draftKey() ? draft.values : {};
  let preset = {};
  if (mode.kind === 'edit') {
    const r = state.rounds.find((x) => x.id === mode.roundId);
    preset = r ? r.scores : {};
  }
  const pending = store.get(PENDING_KEY);
  const pendingHere = pending && pending.gameId === state.game.id && (mode.kind === 'edit'
    ? pending.kind === 'update' && pending.roundId === mode.roundId
    : pending.kind === 'create' && pending.roundNumber === state.game.current_round);
  const totals = new Map(state.leaderboard.map((r) => [r.player_id, r]));
  inputs = new Map();
  const rows = state.players.map((p, i) => {
    const t = totals.get(p.player_id);
    const input = h('input.input-points', {
      type: 'text', inputmode: 'numeric', pattern: '[0-9]*', maxlength: String(String(MAX).length),
      placeholder: '–', 'aria-label': `Punkte für ${p.name}`, id: `pts-${p.player_id}`,
      enterkeyhint: i === state.players.length - 1 ? 'done' : 'next',
    });
    const v = pendingHere ? pending.scores[p.player_id] : values[p.player_id] ?? preset[String(p.player_id)];
    if (v !== undefined && v !== null && v !== '') input.value = String(v);
    const zero = h('button.btn-zero', { type: 'button', 'aria-label': `${p.name}: 0 Punkte (Bust)` }, '0');
    const msg = h('span.ctl__msg', { id: `msg-${p.player_id}` });
    const row = h('div.ctl__row',
      h('label.ctl__who', { for: `pts-${p.player_id}` },
        h('span.ctl__name.ellipsis', { title: p.name }, p.name),
        h('span.ctl__total', `${fmt(t?.total ?? 0)} Pkt · Platz ${t?.rank ?? '–'}`), msg),
      input, zero);
    inputs.set(p.player_id, { input, zero, row, msg });
    return row;
  });
  replace(form, rows);
  for (const [, f] of inputs) {
    syncZero(f);
    f.input.disabled = !!pendingHere;
    f.zero.disabled = !!pendingHere;
  }
  renderPendingBanner(pendingHere ? pending : null);
  updateCount();
}

function syncZero(f) {
  f.zero.classList.toggle('is-active', f.input.value.trim() === '0');
}

function parseField(f) {
  const raw = f.input.value.trim();
  if (raw === '') return { empty: true };
  if (!/^\d+$/.test(raw)) return { error: `nur ganze Zahlen 0–${MAX}` };
  const n = Number(raw);
  if (n > MAX) return { error: `nur ganze Zahlen 0–${MAX}` };
  return { value: n };
}

// Leeres Feld = Runde nicht mitgespielt (wird so gespeichert, sobald mindestens ein Spieler Punkte hat).
function markSkipped() {
  const anyFilled = [...inputs.values()].some((f) => f.input.value.trim() !== '');
  for (const f of inputs.values()) {
    if (f.input.classList.contains('is-invalid')) continue;
    const skip = anyFilled && f.input.value.trim() === '';
    f.row.classList.toggle('is-skipped', skip);
    f.msg.classList.toggle('is-skip', skip);
    f.msg.textContent = skip ? 'Runde nicht mitgespielt' : '';
  }
}

function updateCount() {
  const total = inputs.size;
  const filled = [...inputs.values()].filter((f) => f.input.value.trim() !== '').length;
  $('#save-count').textContent = total ? `${filled} / ${total}` : '';
  saveBtn.classList.toggle('is-incomplete', filled === 0);
  markSkipped();
  const values = Object.fromEntries([...inputs].map(([pid, f]) => [pid, f.input.value.trim()]));
  if (state && inputs.size) store.set(DRAFT_KEY, { key: draftKey(), values });
}

function renderRounds() {
  const box = $('#ctl-rounds');
  const list = $('#rounds-list');
  if (!state || !state.rounds.length || state.game.status === 'CANCELLED') {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const names = new Map(state.players.map((p) => [p.player_id, p.name]));
  const rounds = [...state.rounds].reverse();
  replace(list, rounds.map((r) => h('li.ctl__roundrow',
    h('span.num.ctl__roundno', `R${r.round_number}`),
    h('span.ctl__roundpts.ellipsis', state.players.map((p) => `${names.get(p.player_id)} ${r.scores[String(p.player_id)] ?? 'nicht mitgespielt'}`).join(' · ')),
    h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: () => startEdit(r) }, 'Bearbeiten'))));
}

function renderGameBox() {
  const box = $('#ctl-game');
  if (!state || state.game.status === 'CANCELLED') {
    box.hidden = false;
    replace(box, h('div.empty', h('p.empty__text', 'Kein laufendes Spiel.'),
      h('a.btn.btn--primary', { href: '/setup' }, 'Neuer Spieleabend')));
    return;
  }
  const g = state.game;
  if (g.status === 'CREATED') {
    box.hidden = false;
    replace(box, h('div.empty', h('p.label', 'Vorbereitet'), h('p.empty__text', `${gameTitle(g)} · ${state.players.length} Spieler · Ziel ${fmt(g.target_score)}`),
      h('div.ctl__gamebtns',
        h('button.btn.btn--primary', { type: 'button', onclick: (e) => action(e.currentTarget, () => api.post(`/api/v1/games/${g.id}/start`), 'Spiel gestartet') }, 'Spiel starten'),
        h('a.btn.btn--secondary', { href: '/setup' }, 'Bearbeiten'))));
    return;
  }
  if (g.status === 'FINISHED') {
    box.hidden = false;
    const night = state.night;
    const names = state.winners.map((w) => w.name).join(' & ');
    if (night && night.status === 'FINISHED') {
      const top = night.standings.filter((r) => r.rank === 1 && r.wins > 0).map((r) => r.name).join(' & ');
      replace(box, h('div.empty', h('p.label', 'Spieleabend beendet'), h('p.empty__text', top ? `Abendsieg: ${top}` : 'Kein Sieger'),
        nightStandings(night, { title: 'Abend-Endstand' }),
        h('div.ctl__gamebtns', h('a.btn.btn--primary', { href: '/setup' }, 'Neuer Spieleabend'))));
      return;
    }
    const buttons = [];
    if (night) {
      const next = h('button.btn.btn--primary', { type: 'button', onclick: async () => {
        const s = await startNextGame(night, next);
        if (s) { state = s; render(); }
      } }, `Partie ${night.current_number + 1} starten`);
      const end = h('button.btn.btn--secondary', { type: 'button', onclick: async () => {
        if (await finishNight(night, end)) applyState(await api.get('/api/v1/games/current?display=1'));
      } }, 'Abend beenden');
      buttons.push(next, end);
    } else {
      buttons.push(h('a.btn.btn--primary', { href: '/setup' }, 'Neuer Spieleabend'));
    }
    buttons.push(h('button.btn.btn--tertiary', { type: 'button', onclick: continueGame }, 'Partie fortsetzen'));
    replace(box, h('div.empty', h('p.label', state.tie ? 'Gleichstand' : night ? `Partie ${g.night_number} gewonnen` : 'Gewonnen'),
      h('p.empty__text', `${names} · ${fmt(state.leaderboard[0]?.total ?? 0)} Punkte`),
      night ? nightStandings(night) : null,
      h('div.ctl__gamebtns', buttons)));
    return;
  }
  box.hidden = false;
  replace(box, h('div.ctl__gamebtns.ctl__gamebtns--quiet',
    h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: finishGame }, 'Partie beenden'),
    h('button.btn.btn--danger.btn--small', { type: 'button', onclick: cancelGame }, 'Partie abbrechen')));
}

function renderBar() {
  const active = state && state.game.status === 'ACTIVE';
  const editing = mode.kind === 'edit';
  const canEdit = state && ['ACTIVE', 'FINISHED'].includes(state.game.status);
  $('#ctl-bar').hidden = !(active || (editing && canEdit));
  form.hidden = !(active || (editing && canEdit));
  const last = state?.rounds.at(-1);
  const fix = $('#fix-last');
  fix.hidden = editing || !last || !active;
  if (last) fix.textContent = `Runde ${last.round_number} korrigieren`;
  $('#cancel-edit').hidden = !editing;
  $('#save-label').textContent = editing ? 'Änderung speichern' : `Runde ${state?.game.current_round ?? ''} speichern`;
  let del = $('#delete-round');
  if (editing && !del) {
    del = h('div.ctl__danger#delete-round', h('button.btn.btn--danger.btn--block', { type: 'button', onclick: deleteRound },
      `Runde ${mode.number} löschen`));
    form.after(del);
  } else if (!editing && del) {
    del.remove();
  }
}

function render() {
  if (mode.kind === 'edit' && !state?.rounds.some((r) => r.id === mode.roundId)) mode = { kind: 'new' };
  headFor();
  renderBar();
  if (state && (state.game.status === 'ACTIVE' || mode.kind === 'edit')) buildForm();
  else {
    replace(form);
    renderPendingBanner(sending ? store.get(PENDING_KEY) : null); // z. B. Partie mit dieser Runde gewonnen
  }
  renderRounds();
  renderGameBox();
  connectionChip($('#live-chip'), { live, network: null });
}

function renderPendingBanner(pending) {
  const host = $('#ctl-banner');
  if (!pending) {
    replace(host);
    return;
  }
  replace(host, h('div.ctl__pending', { role: 'status' },
    h('span.ctl__pendingdot', { 'aria-hidden': 'true' }),
    h('div', h('strong', sending ? 'Wird gesendet …' : 'Nicht bestätigt'),
      h('p.caption', sending ? 'Bitte kurz warten.' : 'Der Laptop hat die Runde noch nicht bestätigt. Deine Punkte bleiben erhalten.')),
    sending ? null : h('div.ctl__pendingbtns',
      h('button.btn.btn--primary.btn--small', { type: 'button', onclick: () => send(store.get(PENDING_KEY)) }, 'Erneut senden'),
      h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: discardPending }, 'Verwerfen'))));
}

// ---------------------------------------------------------------- Aktionen

function collect() {
  let firstBad = null;
  const scores = {};
  for (const [pid, f] of inputs) {
    const r = parseField(f);
    f.input.classList.toggle('is-invalid', !!r.error);
    f.msg.classList.toggle('is-skip', !!r.empty);
    f.msg.textContent = r.error || (r.empty ? 'Runde nicht mitgespielt' : '');
    f.input.setAttribute('aria-invalid', r.error ? 'true' : 'false');
    f.input.setAttribute('aria-describedby', `msg-${pid}`);
    if (r.error && !firstBad) firstBad = f.input;
    if (!r.empty && !r.error) scores[pid] = r.value;
  }
  if (firstBad) {
    firstBad.focus();
    return null;
  }
  if (!Object.keys(scores).length) {
    for (const f of inputs.values()) {
      f.msg.classList.remove('is-skip');
      f.msg.textContent = '';
    }
    toast('Bitte für mindestens einen Spieler Punkte eintragen (auch 0).', { type: 'error' });
    inputs.values().next().value?.input.focus();
    return null;
  }
  return scores;
}

async function send(pending) {
  if (!pending || sending) return;
  sending = true;
  saveBtn.classList.add('is-loading');
  renderPendingBanner(pending);
  dismissToast('send-error');
  const body = { idempotency_key: pending.key,
    scores: Object.entries(pending.scores).map(([pid, pts]) => ({ player_id: Number(pid), points: pts })) };
  try {
    let result;
    if (pending.kind === 'create') {
      result = await api.post(`/api/v1/games/${pending.gameId}/rounds`, { ...body, round_number: pending.roundNumber });
    } else {
      result = await api.patch(`/api/v1/rounds/${pending.roundId}`, body);
    }
    store.set(PENDING_KEY, null);
    store.set(DRAFT_KEY, null);
    sending = false;
    if (pending.kind === 'create') ownSavedRound = { gameId: pending.gameId, roundNumber: pending.roundNumber };
    await success(pending.kind === 'create' ? `Runde ${pending.roundNumber} gespeichert` : `Runde ${result.round.round_number} geändert`);
    mode = { kind: 'new' };
    state = result.state;
    render();
    inputs.values().next().value?.input.focus();
  } catch (e) {
    sending = false;
    saveBtn.classList.remove('is-loading');
    handleSendError(e, pending);
  }
}

function handleSendError(e, pending) {
  if (e instanceof ApiError && e.isNetwork) {
    renderPendingBanner(pending);
    toast('Nicht gespeichert – keine Verbindung. Deine Punkte bleiben erhalten.', {
      type: 'error', id: 'send-error', action: { label: 'Erneut senden', onClick: () => send(store.get(PENDING_KEY)) } });
    return;
  }
  // Server hat geantwortet → die Runde wurde nicht gespeichert; der Schlüssel ist verbraucht.
  store.set(PENDING_KEY, null);
  if (e.status === 401) {
    toast(e.message, { type: 'error' });
  } else if (e.code === 'round_mismatch' || e.code === 'invalid_status') {
    toast(e.message, { type: 'warn', duration: 5000 });
    requestState();
    api.get('/api/v1/games/current?display=1').then((s) => { state = s; render(); }).catch(() => {});
    return;
  } else {
    toast(e.message, { type: 'error' });
  }
  for (const f of inputs.values()) {
    f.input.disabled = false;
    f.zero.disabled = false;
  }
  const missing = e.details?.players || [];
  for (const [pid, f] of inputs) {
    const name = state.players.find((p) => p.player_id === pid)?.name;
    if (missing.includes(name) && e.code === 'points_out_of_range') {
      f.input.classList.add('is-invalid');
      f.msg.classList.remove('is-skip');
      f.msg.textContent = `nur ganze Zahlen 0–${MAX}`;
    }
  }
  renderPendingBanner(null);
}

async function success(text) {
  saveBtn.classList.remove('is-loading');
  const label = $('#save-label');
  const old = label.textContent;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'btn__check');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', 'M4 12.5l5 5L20 6.5');
  svg.append(path);
  label.textContent = 'Gespeichert';
  saveBtn.classList.add('is-done');
  label.before(svg);
  toast(text, { type: 'ok' });
  await new Promise((r) => setTimeout(r, 600));
  svg.remove();
  saveBtn.classList.remove('is-done');
  label.textContent = old;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!state || sending) return;
  const existing = store.get(PENDING_KEY);
  if (existing) return send(existing);
  const scores = collect();
  if (!scores) return;
  const skipped = state.players.filter((p) => !(p.player_id in scores)).map((p) => p.name);
  if (skipped.length) {
    const ok = await confirmDialog({ title: 'Runde nicht mitgespielt?', danger: false, confirm: 'Ja, so speichern', cancel: 'Punkte nachtragen',
      text: `${skipped.join(', ')} ${skipped.length === 1 ? 'hat' : 'haben'} diese Runde nicht mitgespielt und ${skipped.length === 1 ? 'bekommt' : 'bekommen'} keine Punkte – nicht einmal 0. Stimmt das?` });
    if (!ok) {
      [...inputs.values()].find((f) => f.input.value.trim() === '')?.input.focus();
      return;
    }
    if (sending || store.get(PENDING_KEY)) return;
  }
  const pending = mode.kind === 'edit'
    ? { kind: 'update', gameId: state.game.id, roundId: mode.roundId, key: newKey(), scores }
    : { kind: 'create', gameId: state.game.id, roundNumber: state.game.current_round, key: newKey(), scores };
  store.set(PENDING_KEY, pending);
  for (const f of inputs.values()) {
    f.input.disabled = true;
    f.zero.disabled = true;
  }
  send(pending);
});

form.addEventListener('input', (e) => {
  const f = [...inputs.values()].find((x) => x.input === e.target);
  if (!f) return;
  f.input.value = f.input.value.replace(/\D+/g, '');
  f.input.classList.remove('is-invalid');
  f.msg.textContent = '';
  syncZero(f);
  updateCount();
});

form.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || !e.target.matches('.input-points')) return;
  e.preventDefault();
  focusNext(e.target);
});

form.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn-zero');
  if (!btn) return;
  const f = [...inputs.values()].find((x) => x.zero === btn);
  f.input.value = '0';
  f.input.classList.remove('is-invalid');
  f.msg.textContent = '';
  syncZero(f);
  updateCount();
  focusNext(f.input);
});

form.addEventListener('focusin', (e) => {
  if (e.target.matches('.input-points')) {
    e.target.closest('.ctl__row')?.classList.add('is-focus');
    setTimeout(() => e.target.scrollIntoView({ block: 'center', behavior: 'smooth' }), 250);
  }
});
form.addEventListener('focusout', (e) => e.target.closest('.ctl__row')?.classList.remove('is-focus'));

function focusNext(input) {
  const all = [...inputs.values()].map((f) => f.input);
  const next = all[all.indexOf(input) + 1];
  if (next) next.focus();
  else input.blur();
}

function startEdit(r) {
  if (store.get(PENDING_KEY)) {
    toast('Bitte zuerst den offenen Speichervorgang abschließen.', { type: 'warn' });
    return;
  }
  mode = { kind: 'edit', roundId: r.id, number: r.round_number };
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  inputs.values().next().value?.input.focus();
}

$('#fix-last').addEventListener('click', () => {
  const last = state?.rounds.at(-1);
  if (last) startEdit(last);
});
$('#cancel-edit').addEventListener('click', () => {
  store.set(DRAFT_KEY, null);
  mode = { kind: 'new' };
  render();
});

async function deleteRound() {
  const ok = await confirmDialog({ title: `Runde ${mode.number} löschen?`,
    text: 'Die Punkte dieser Runde werden entfernt, nachfolgende Runden rücken auf. Die Änderung wird protokolliert.',
    confirm: 'Runde löschen' });
  if (!ok) return;
  try {
    const res = await api.del(`/api/v1/rounds/${mode.roundId}`);
    toast(`Runde ${res.deleted_round_number} gelöscht`, { type: 'ok' });
    mode = { kind: 'new' };
    state = res.state;
    render();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}

async function action(btn, fn, okText) {
  btn?.classList.add('is-loading');
  try {
    state = await fn();
    toast(okText, { type: 'ok' });
    render();
  } catch (e) {
    toast(e.message, { type: 'error' });
  } finally {
    btn?.classList.remove('is-loading');
  }
}

async function finishGame(e) {
  const btn = e.currentTarget;
  const ok = await confirmDialog({ title: 'Partie jetzt beenden?', text: 'Gewinner ist, wer aktuell die meisten Punkte hat. '
    + 'Eine versehentlich beendete Partie kann danach fortgesetzt werden.', confirm: 'Partie beenden' });
  if (ok) action(btn, () => api.post(`/api/v1/games/${state.game.id}/finish`), 'Partie beendet');
}

async function cancelGame(e) {
  const btn = e.currentTarget;
  const ok = await confirmDialog({ title: 'Partie abbrechen?', text: 'Die Partie wird als „abgebrochen“ gespeichert '
    + 'und zählt nicht für die Statistik.', confirm: 'Partie abbrechen' });
  if (ok) action(btn, () => api.post(`/api/v1/games/${state.game.id}/cancel`), 'Partie abgebrochen');
}

async function continueGame(e) {
  const btn = e.currentTarget;
  const top = state.leaderboard[0]?.total ?? 0;
  const value = await confirmDialog({ title: 'Spiel fortsetzen', danger: false, confirm: 'Fortsetzen',
    text: `Neues Punkteziel eingeben (über ${fmt(top)}) – oder leer lassen, dann endet das Spiel nur noch manuell.`,
    input: { inputmode: 'numeric', placeholder: `z. B. ${Math.ceil((top + 50) / 50) * 50}`, label: 'Neues Punkteziel' } });
  if (value === false) return;
  const raw = String(value).trim();
  if (raw && !/^\d+$/.test(raw)) {
    toast('Bitte eine ganze Zahl eingeben.', { type: 'error' });
    return;
  }
  action(btn, () => api.post(`/api/v1/games/${state.game.id}/continue`, raw ? { target_score: Number(raw) } : {}), 'Spiel geht weiter');
}

function discardPending() {
  store.set(PENDING_KEY, null);
  dismissToast('send-error');
  toast('Speichervorgang verworfen. Falls er doch angekommen ist, erscheint die Runde in der Liste.', { type: 'warn', duration: 5000 });
  render();
}

// Nach Wiederverbindung: Wurde die unbestätigte Runde inzwischen gespeichert?
function reconcilePending() {
  const p = store.get(PENDING_KEY);
  if (!p || !state || p.gameId !== state.game.id || p.kind !== 'create' || sending) return;
  if (state.game.current_round <= p.roundNumber) return;
  const saved = state.rounds.find((r) => r.round_number === p.roundNumber);
  store.set(PENDING_KEY, null);
  store.set(DRAFT_KEY, null);
  dismissToast('send-error');
  const same = saved && Object.entries(p.scores).every(([pid, v]) => saved.scores[pid] === v);
  toast(same ? `Runde ${p.roundNumber} wurde gespeichert.` : `Runde ${p.roundNumber} wurde inzwischen von einem anderen Gerät gespeichert. Bitte prüfen.`,
    { type: same ? 'ok' : 'warn', duration: 6000 });
}

// ---------------------------------------------------------------- Live

function applyState(next) {
  const prevRound = state?.game.current_round;
  const prevId = state?.game.id;
  // Hatte dieses Gerät schon Punkte für die aktuelle Runde getippt (ohne selbst zu speichern)?
  const hadInput = mode.kind === 'new' && !sending && !store.get(PENDING_KEY)
    && [...(inputs?.values() ?? [])].some((f) => f.input.value.trim() !== '');
  state = next;
  reconcilePending();
  const mine = ownSavedRound && ownSavedRound.gameId === prevId && ownSavedRound.roundNumber === prevRound;
  if (hadInput && !mine && prevId === state?.game.id && state.game.current_round > prevRound) {
    toast(`Runde ${prevRound} wurde gerade auf einem anderen Gerät gespeichert. Deine Eingabe dafür wurde verworfen – bitte prüfen.`,
      { type: 'warn', duration: 7000 });
  }
  // Eingaben während des Tippens nicht überschreiben – nur neu aufbauen, wenn sich die Runde/das Spiel änderte.
  const typing = document.activeElement?.matches?.('.input-points');
  if (typing && mode.kind === 'new' && prevId === state?.game.id && prevRound === state?.game.current_round) {
    const totals = new Map(state.leaderboard.map((r) => [r.player_id, r]));
    for (const [pid, f] of inputs) {
      const t = totals.get(pid);
      f.row.querySelector('.ctl__total').textContent = `${fmt(t?.total ?? 0)} Pkt · Platz ${t?.rank ?? '–'}`;
    }
    renderRounds();
    renderGameBox();
    return;
  }
  render();
}

onLive('connection', ({ status }) => {
  live = status;
  connectionChip($('#live-chip'), { live, network: null });
  if (status === 'online') {
    const p = store.get(PENDING_KEY);
    if (p && !sending) renderPendingBanner(p);
  }
});
onLive('game:updated', (p) => applyState(p.state));
onLive('system:status', (p) => p.reload && location.reload());

async function init() {
  try {
    applyState(await api.get('/api/v1/games/current?display=1'));
  } catch (e) {
    if (e.status === 401) toast(e.message, { type: 'error' });
    applyState(null);
  }
  connectLive();
}

init();
