// Einstellungen: Allgemein · Netzwerk · Daten & Backup · Anzeige · System.
// Jede Änderung wird sofort gespeichert (PATCH) und bestätigt.
import { api } from '../core/api.js';
import { $, $$, fmt, fmtDate, fmtTime, h, replace } from '../core/dom.js';
import { loadPrefs, savePrefs } from '../core/prefs.js';
import { connectLive, onLive } from '../core/realtime.js';
import { renderConnection } from '../components/connection.js';
import { requestAccess } from '../components/firewall.js';
import { confirmDialog } from '../components/modal.js';
import { toast } from '../components/toast.js';

let settings = null;
let network = null;
let netEl = null;

// ---------------------------------------------------------------- Bausteine

function group(title, ...children) {
  return h('section.sgroup', h('h2.label.sgroup__title', title), ...children);
}

async function save(patch, okText = 'Gespeichert') {
  try {
    settings = await api.patch('/api/v1/system/settings', patch);
    toast(okText, { type: 'ok' });
    return true;
  } catch (e) {
    toast(e.message, { type: 'error' });
    return false;
  }
}

function toggle(label, hint, checked, onChange, { disabled = false } = {}) {
  const input = h('input', { type: 'checkbox', role: 'switch', checked, disabled });
  input.addEventListener('change', async () => {
    const ok = await onChange(input.checked);
    if (ok === false) input.checked = !input.checked;
  });
  return h('label.switch', h('span', h('span.switch__label', label), hint ? h('span.field__hint.switch__hint', hint) : null), input);
}

function numberField(label, hint, value, { min, max, key }) {
  const input = h('input.input.sfield__num', { inputmode: 'numeric', pattern: '[0-9]*', value: String(value), 'aria-label': label });
  input.addEventListener('change', async () => {
    const raw = input.value.trim();
    const n = Number(raw);
    if (!/^\d+$/.test(raw) || n < min || n > max) {
      toast(`Bitte eine ganze Zahl zwischen ${fmt(min)} und ${fmt(max)} eingeben.`, { type: 'error' });
      input.value = String(settings[key]);
      return;
    }
    await save({ [key]: n });
  });
  return h('label.field.sfield', h('span.field__label', label), input, hint ? h('span.field__hint', hint) : null);
}

function segmented(label, options, value, onPick) {
  const host = h('div.segctl', { role: 'group', 'aria-label': label });
  const draw = (v) => replace(host, options.map(([val, text]) => h('button.segctl__opt', { type: 'button', 'aria-pressed': String(val === v),
    onclick: async () => { if (await onPick(val) !== false) draw(val); } }, text)));
  draw(value);
  return h('div.field.sfield', h('span.field__label', label), host);
}

// ---------------------------------------------------------------- Reiter

function renderGeneral() {
  replace($('#allgemein'),
    group('Start',
      toggle('Mit Windows starten', settings.autostart_available ? 'Flip 7 Live startet automatisch nach der Windows-Anmeldung.'
        : 'Nur in der Flip7Live.exe verfügbar.', settings.autostart, (v) => save({ autostart: v }), { disabled: !settings.autostart_available })),
    group('Spiel',
      numberField('Standard-Punkteziel', 'Vorauswahl beim neuen Spiel.', settings.default_target_score, { min: 1, max: 10000, key: 'default_target_score' }),
      numberField('Standard-Spieleranzahl', 'So viele leere Zeilen zeigt „Neues Spiel“.', settings.default_player_count, { min: 2, max: 18, key: 'default_player_count' }),
      numberField('Höchstpunkte pro Runde', 'Eingaben darüber gelten als Tippfehler und werden abgelehnt.', settings.max_round_points, { min: 1, max: 100000, key: 'max_round_points' })),
    group('Sprache', h('p', 'Deutsch')));
}

function renderNetwork() {
  const panel = $('#netzwerk');
  netEl = h('div.panel.snet');
  const hsName = h('input.input', { value: settings.hotspot_ssid, maxlength: 32, 'aria-label': 'Hotspot-Name' });
  hsName.addEventListener('change', () => save({ hotspot_ssid: hsName.value.trim() }));
  const hsPass = h('input.input', { type: 'password', value: settings.hotspot_password, maxlength: 63, minlength: 8, 'aria-label': 'Hotspot-Passwort', autocomplete: 'off' });
  hsPass.addEventListener('change', () => {
    if (hsPass.value.length < 8) {
      toast('Das Passwort braucht mindestens 8 Zeichen.', { type: 'error' });
      hsPass.value = settings.hotspot_password;
      return;
    }
    save({ hotspot_password: hsPass.value });
  });
  const showPass = h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: () => {
    hsPass.type = hsPass.type === 'password' ? 'text' : 'password';
    showPass.textContent = hsPass.type === 'password' ? 'Anzeigen' : 'Verbergen';
  } }, 'Anzeigen');
  const fw = h('div.sfw', h('p.caption', 'Status wird geprüft …'));
  replace(panel,
    group('Verbindung', netEl),
    group('Verbindungsmodus',
      segmented('Modus', [['auto', 'Automatisch'], ['wlan_only', 'Nur bestehendes WLAN'], ['prefer_hotspot', 'Hotspot bevorzugen']],
        settings.network_mode, (v) => save({ network_mode: v }, 'Modus gespeichert – Netzwerk wird neu geprüft')),
      h('p.field__hint', 'Automatisch: bestehendes WLAN/LAN, sonst Windows-Hotspot, sonst Hinweise für den Smartphone-Hotspot. '
        + '„Nur bestehendes WLAN“ startet nie einen Hotspot.')),
    group('Windows-Hotspot',
      h('label.field.sfield', h('span.field__label', 'Netzwerkname'), hsName),
      h('label.field.sfield', h('span.field__label', 'Passwort'), h('div.sfield__row', hsPass, showPass)),
      h('p.field__hint', 'Flip 7 Live übernimmt Name und Passwort beim Start des Windows-Hotspots (dabei werden die Hotspot-Einstellungen von '
        + 'Windows auf diese Werte gesetzt).'),
      h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: async () => {
        try { await api.post('/api/v1/system/hotspot/open-settings'); } catch (e) { toast(e.message, { type: 'error' }); }
      } }, 'Windows-Hotspot-Einstellungen öffnen')),
    group('Serverport',
      numberField('Port', `Aktiv: ${settings.active_port}. Eine Änderung gilt ab dem nächsten Start von Flip 7 Live.`, settings.port,
        { min: 1024, max: 65535, key: 'port' })),
    group('Zugangscodes',
      h('p.field__hint', 'Erneuern macht alte QR-Codes ungültig. Bereits verbundene Geräte müssen danach neu scannen.'),
      h('div.sbtns',
        h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: () => rotate('controller') }, 'Steuerungscode erneuern'),
        h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: () => rotate('viewer') }, 'Zuschauercode erneuern')),
      network?.viewer_url ? h('div.sviewer', h('div.qr.qr--small', h('img', { src: `/api/v1/system/qr/viewer.svg?t=${Date.now()}`, alt: 'QR-Code Zuschaueransicht' })),
        h('div', h('p.label', 'Zuschauer-QR-Code'), h('p.muted', 'Nur ansehen, keine Eingabe.'))) : null),
    group('Windows-Firewall', fw));
  renderConnection(netEl, network, { size: 'large', onRefresh: (info) => { network = info; renderConnection(netEl, network, { size: 'large' }); } });
  loadFirewall(fw);
}

async function loadFirewall(host) {
  try {
    const st = await api.get('/api/v1/system/firewall');
    const text = {
      allowed: `Freigegeben (${st.profiles.join(', ') || 'Profil unbekannt'}).`,
      blocked: 'Blockiert – das Smartphone kann den Laptop nicht erreichen.',
      none: 'Keine eigene Regel. Windows fragt beim ersten Start nach – bitte „Private Netzwerke“ erlauben.',
      unknown: 'Status nicht ermittelbar.',
    }[st.state];
    const pub = network?.adapter?.category === 'Public' && network?.mode !== 'hotspot';
    const netName = network?.ssid || network?.adapter?.profile_name || 'dieses Netzwerk';
    replace(host, h('p', text),
      pub ? h('p.sfw__public', `„${netName}“ ist in Windows als „Öffentlich“ eingestuft – dort lässt die Firewall das Smartphone `
        + 'nicht durch. Für das WLAN zu Hause: auf „Privat“ umstellen.') : null,
      h('p.field__hint', 'Freigegeben wird nur für private Netzwerke und nur dieser Port. Nichts passiert ohne deine Zustimmung; '
        + 'Windows fragt einmal nach Administratorrechten.'),
      st.state !== 'allowed' || pub ? h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: () => requestAccess(pub, netName,
        () => setTimeout(refreshNetworkInfo, 6000)) },  // baut den Bereich neu auf und prüft die Firewall erneut
      pub ? 'Netzwerk auf Privat stellen und freigeben' : 'Freigabe für private Netzwerke anlegen') : null);
  } catch (e) {
    replace(host, h('p.caption', e.message));
  }
}

async function refreshNetworkInfo() {
  try {
    network = await api.post('/api/v1/system/network/refresh');
    renderNetwork();
  } catch { /* nächste Hintergrundprüfung liefert */ }
}

async function rotate(role) {
  const ok = await confirmDialog({ title: role === 'controller' ? 'Steuerungscode erneuern?' : 'Zuschauercode erneuern?',
    text: 'Alle Geräte mit dem alten Code verlieren den Zugang und müssen den neuen QR-Code scannen.', confirm: 'Erneuern' });
  if (!ok) return;
  try {
    network = await api.post('/api/v1/system/tokens/rotate', { role });
    toast('Neuer Code erstellt', { type: 'ok' });
    renderNetwork();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}

async function renderData() {
  const panel = $('#daten');
  const list = h('ol.sbackups', h('li.skeleton'));
  const openBtn = (target, text) => h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: async () => {
    try { await api.post('/api/v1/system/open', { target }); } catch (e) { toast(e.message, { type: 'error' }); }
  } }, text);
  replace(panel,
    group('Speicherort',
      h('dl.sinfo', h('dt', 'Daten'), h('dd', settings.data_dir), h('dt', 'Datenbank'), h('dd', settings.database),
        h('dt', 'Sicherungen'), h('dd', settings.backups_dir)),
      h('div.sbtns', openBtn('data', 'Datenordner öffnen'), openBtn('backups', 'Sicherungsordner öffnen'))),
    group('Sicherungen',
      h('p.field__hint', 'Automatisch: vor Datenbank-Updates, bei Spieländerungen (höchstens alle 10 Minuten) und bei jedem Spielende.'),
      h('div.sbtns', h('button.btn.btn--primary', { type: 'button', onclick: async (e) => {
        const b = e.currentTarget;
        b.classList.add('is-loading');
        try {
          const r = await api.post('/api/v1/system/backups');
          toast(`Sicherung erstellt: ${r.name}`, { type: 'ok' });
          loadBackups(list);
        } catch (err) { toast(err.message, { type: 'error' }); } finally { b.classList.remove('is-loading'); }
      } }, 'Jetzt sichern')),
      numberField('Anzahl aufbewahrter Sicherungen', 'Ältere Sicherungen werden automatisch gelöscht.', settings.backup_keep, { min: 1, max: 500, key: 'backup_keep' }),
      list),
    group('Export',
      h('div.sbtns',
        h('a.btn.btn--secondary.btn--small', { href: '/api/v1/history/export.csv', download: '' }, 'Spielhistorie als CSV'),
        h('a.btn.btn--secondary.btn--small', { href: '/api/v1/history/export.json', download: '' }, 'Spielhistorie als JSON'))));
  loadBackups(list);
}

async function loadBackups(list) {
  try {
    const items = await api.get('/api/v1/system/backups');
    if (!items.length) {
      replace(list, h('li.caption', 'Noch keine Sicherungen vorhanden.'));
      return;
    }
    replace(list, items.map((b) => h('li.sbackup',
      h('div', h('div.sbackup__date', `${fmtDate(b.created_at)} ${fmtTime(b.created_at)}`),
        h('div.caption', `${b.reason_label} · ${fmt(Math.round(b.size_bytes / 1024))} KB`)),
      h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: () => restore(b) }, 'Wiederherstellen'))));
  } catch (e) {
    replace(list, h('li.caption', e.message));
  }
}

async function restore(b) {
  const ok = await confirmDialog({ title: 'Sicherung wiederherstellen?', confirm: 'Wiederherstellen',
    text: `Der aktuelle Stand wird durch die Sicherung vom ${fmtDate(b.created_at)} ${fmtTime(b.created_at)} ersetzt. `
      + 'Vorher wird automatisch eine Sicherheitskopie des aktuellen Stands angelegt.' });
  if (!ok) return;
  try {
    const r = await api.post('/api/v1/system/backups/restore', { name: b.name });
    toast(`Wiederhergestellt (${r.games} Spiele). Sicherheitskopie: ${r.safety_backup}`, { type: 'ok', duration: 6000 });
    renderData();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}

function renderDisplay() {
  const prefs = loadPrefs();
  replace($('#anzeige'),
    group('Dieses Gerät',
      toggle('TV-Modus', 'Größere Schrift, höchstens zwei Ereignisse, keine Hover-Effekte. Auch per Adresse „/dashboard?tv=1“.', prefs.tv,
        (v) => { savePrefs({ ...loadPrefs(), tv: v }); toast('Gespeichert', { type: 'ok' }); }),
      toggle('Reduzierte Bewegung', 'Keine Animationen – Zahlen springen direkt auf den neuen Wert.', prefs.reduceMotion,
        (v) => { savePrefs({ ...loadPrefs(), reduceMotion: v }); toast('Gespeichert', { type: 'ok' }); }),
      h('p.field__hint', 'Diese Anzeige-Einstellungen gelten nur für diesen Browser.')),
    group('Dashboard-Tastatur', h('dl.sinfo', h('dt', 'F'), h('dd', 'Vollbild an/aus'), h('dt', 'Q'), h('dd', 'Smartphone verbinden (QR-Code)'),
      h('dt', 'Esc'), h('dd', 'Überlagerung schließen'))));
}

async function renderSystem() {
  const panel = $('#system');
  let st = null;
  try {
    st = await api.get('/api/v1/system/status');
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
  const uptime = st ? `${Math.floor(st.uptime_seconds / 3600)} h ${Math.floor((st.uptime_seconds % 3600) / 60)} min` : '–';
  const roles = { admin: 'Laptop', controller: 'Steuerung', viewer: 'Zuschauer' };
  replace(panel,
    group('Diagnose', h('dl.sinfo',
      h('dt', 'App-Version'), h('dd', settings.version),
      h('dt', 'Server'), h('dd', st ? `läuft seit ${uptime} · Port ${st.port}` : 'unbekannt'),
      h('dt', 'Datenbank'), h('dd', st?.database?.ok ? `in Ordnung · ${st.database.journal_mode?.toUpperCase()} · Stand ${st.database.revision} · ${fmt(Math.round((st.database.size_bytes || 0) / 1024))} KB` : 'Fehler – siehe Logdatei'),
      h('dt', 'Netzwerk'), h('dd', network ? `${network.mode_label}${network.ip ? ` · ${network.ip}` : ''}` : '–'),
      h('dt', 'Letzte Synchronisierung'), h('dd', st?.last_sync ? fmtTime(st.last_sync) : 'noch keine'),
      h('dt', 'Aktive Geräte'), h('dd', st ? `${st.clients.total} (${st.clients.controller} Steuerung, ${st.clients.viewer} Zuschauer, ${st.clients.admin} Laptop)` : '–')),
    st?.client_list?.length ? h('ol.sclients', st.client_list.map((c) => h('li', h('span', roles[c.role] || c.role), h('span.muted', c.ip),
      h('span.caption', `seit ${fmtTime(c.connected_at)}`)))) : null,
    st?.settings_recovered ? h('div.notice.notice--warn', h('span.notice__icon', '!'), h('p', 'Die Einstellungsdatei war beschädigt und wurde auf Standardwerte zurückgesetzt. Die alte Datei liegt im Konfigurationsordner.')) : null,
    h('div.sbtns', h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: async () => {
      try { await api.post('/api/v1/system/open', { target: 'log' }); } catch (e) { toast(e.message, { type: 'error' }); }
    } }, 'Logdatei öffnen'), h('a.btn.btn--secondary.btn--small', { href: '/welcome' }, 'Einrichtungsassistent erneut starten'),
    h('button.btn.btn--tertiary.btn--small', { type: 'button', onclick: renderSystem }, 'Aktualisieren'))),
    group('Anwendung', h('p.field__hint', 'Beendet den Server. Smartphones und Zuschauer verlieren die Verbindung; der Spielstand bleibt gespeichert.'),
      h('button.btn.btn--danger', { type: 'button', onclick: async () => {
        const ok = await confirmDialog({ title: 'Flip 7 Live beenden?', confirm: 'Beenden',
          text: 'Alle Geräte werden getrennt. Der Spielstand bleibt erhalten und wird beim nächsten Start wiederhergestellt.' });
        if (!ok) return;
        try {
          await api.post('/api/v1/system/shutdown');
          replace(document.body, h('main.page', h('div.empty', h('p.label', 'Beendet'), h('p.empty__text', 'Flip 7 Live wurde beendet. Dieses Fenster kann geschlossen werden.'))));
        } catch (e) { toast(e.message, { type: 'error' }); }
      } }, 'Anwendung beenden')));
}

// ---------------------------------------------------------------- Reiter-Steuerung

function selectTab(id, focus = false) {
  for (const tab of $$('.tabs__tab')) {
    const on = tab.getAttribute('aria-controls') === id;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`#${tab.getAttribute('aria-controls')}`).hidden = !on;
    if (on && focus) tab.focus();
  }
  history.replaceState(null, '', `#${id}`);
  if (id === 'system') renderSystem();
}

$('.tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.tabs__tab');
  if (tab) selectTab(tab.getAttribute('aria-controls'));
});
$('.tabs').addEventListener('keydown', (e) => {
  const tabs = $$('.tabs__tab');
  const i = tabs.indexOf(document.activeElement);
  if (i < 0 || !['ArrowRight', 'ArrowLeft'].includes(e.key)) return;
  const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
  selectTab(next.getAttribute('aria-controls'), true);
});

window.addEventListener('hashchange', () => {
  const id = location.hash.slice(1);
  if ($(`#${CSS.escape(id)}.tabpanel`)) selectTab(id);
});

onLive('network:changed', (info) => {
  network = { ...network, ...info };
  if (netEl) renderConnection(netEl, network, { size: 'large' });
});
onLive('client:connected', (p) => { if (network) { network.clients = p.clients; if (netEl) renderConnection(netEl, network, { size: 'large' }); } });
onLive('client:disconnected', (p) => { if (network) { network.clients = p.clients; if (netEl) renderConnection(netEl, network, { size: 'large' }); } });

(async () => {
  try {
    [settings, network] = await Promise.all([api.get('/api/v1/system/settings'), api.get('/api/v1/system/network')]);
  } catch (e) {
    toast(e.message, { type: 'error' });
    return;
  }
  renderGeneral();
  renderNetwork();
  renderData();
  renderDisplay();
  const start = location.hash.slice(1);
  selectTab(['allgemein', 'netzwerk', 'daten', 'anzeige', 'system'].includes(start) ? start : 'allgemein');
  connectLive();
})();
