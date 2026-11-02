// Einrichtungsassistent: erklärt in sechs kurzen Schritten, was Flip 7 Live braucht, und prüft es gleich mit.
import { api } from '../core/api.js';
import { $, fmt, h, replace } from '../core/dom.js';
import '../core/prefs.js';
import { connectLive, onLive } from '../core/realtime.js';
import { refreshNetwork, renderConnection } from '../components/connection.js';
import { requestAccess } from '../components/firewall.js';
import { toast } from '../components/toast.js';

const STEPS = ['Willkommen', 'Netzwerk', 'Firewall', 'Smartphone', 'Einstellungen', 'Fertig'];
let index = 0;
let settings = null;
let network = null;
let firewall = null;
let phoneConnected = false;

function para(text) {
  return h('p.wiz__text', text);
}

function bullets(items) {
  return h('ul.wiz__list', items.map(([title, text]) => h('li', h('strong', title), h('span.muted', text))));
}

function openWindows(target, label) {
  return h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: async () => {
    try {
      await api.post(target === 'hotspot' ? '/api/v1/system/hotspot/open-settings' : '/api/v1/system/open', { target });
    } catch (e) { toast(e.message, { type: 'error' }); }
  } }, label);
}

// ---------------------------------------------------------------- Schritte

function stepWelcome() {
  return [
    h('h1.wiz__title', 'Willkommen bei Flip 7 Live'),
    para('In zwei Minuten ist alles bereit. Flip 7 Live zeigt die Punkte eures Flip-7-Abends live auf diesem Laptop oder einem Fernseher an.'),
    bullets([
      ['Der Laptop ist die Anzeige.', 'Rangliste, Punkteverlauf und Gewinner – automatisch aktualisiert.'],
      ['Ein Smartphone ist die Eingabe.', 'Nach jeder Runde tippt eine Person die Punkte aller Spieler ein.'],
      ['Kein Internet nötig.', 'Alles bleibt im eigenen WLAN und auf diesem Laptop gespeichert.'],
    ]),
    para('Der Assistent prüft jetzt Netzwerk und Firewall und verbindet dein Smartphone. Du kannst ihn später in den Einstellungen erneut starten.'),
  ];
}

function stepNetwork() {
  const conn = h('div.panel.wiz__panel');
  renderConnection(conn, network, { size: 'small', steps: true, actions: false, warnings: false });
  const pub = network?.adapter?.category === 'Public' && network.mode !== 'hotspot';
  const modes = [['auto', 'Automatisch (empfohlen)'], ['wlan_only', 'Nur bestehendes WLAN'], ['prefer_hotspot', 'Hotspot bevorzugen']];
  const seg = h('div.segctl', { role: 'group', 'aria-label': 'Verbindungsmodus' }, modes.map(([val, text]) => h('button.segctl__opt', {
    type: 'button', 'aria-pressed': String(settings.network_mode === val), onclick: async () => {
      try {
        settings = await api.patch('/api/v1/system/settings', { network_mode: val });
        network = await refreshNetwork();
        render();
      } catch (e) { toast(e.message, { type: 'error' }); }
    } }, text)));
  const refresh = h('button.btn.btn--secondary.btn--small', { type: 'button', onclick: async () => {
    const fresh = await refreshNetwork(refresh);
    if (fresh) { network = fresh; render(); }
  } }, 'Verbindung neu prüfen');
  return [
    h('h1.wiz__title', 'Netzwerk'),
    para('Laptop und Smartphone müssen im selben WLAN sein. Flip 7 Live hat das Netzwerk bereits geprüft:'),
    conn,
    pub ? h('div.notice.notice--warn', h('span.notice__icon', '!'), h('div',
      h('p', `Das WLAN „${network.adapter.profile_name || network.ssid || ''}“ ist in Windows als „Öffentlich“ eingestellt. Dann blockiert Windows meistens das Smartphone.`),
      h('p.muted', 'So änderst du das: „Netzwerkeinstellungen öffnen“ → dein WLAN anklicken → Netzwerkprofil „Privat“ wählen. Danach „Verbindung neu prüfen“.'),
      h('div.wiz__btns', openWindows('network_settings', 'Netzwerkeinstellungen öffnen'), refresh))) : h('div.wiz__btns', refresh),
    network?.mode === 'local' ? h('div.notice', h('span.notice__icon', 'i'), h('div',
      h('p', 'Kein WLAN gefunden. Möglichkeiten:'),
      bullets([['Smartphone-Hotspot', 'Hotspot am Handy einschalten und den Laptop damit verbinden – Internet ist nicht nötig.'],
        ['Windows-Hotspot', 'Den mobilen Hotspot des Laptops einschalten und das Handy damit verbinden.']]),
      h('div.wiz__btns', openWindows('hotspot', 'Windows-Hotspot-Einstellungen'), refresh))) : null,
    h('div.field', h('span.field__label', 'Verbindungsmodus'), seg,
      h('p.field__hint', 'Automatisch: bestehendes WLAN, sonst versucht Flip 7 Live einen eigenen Hotspot „' + settings.hotspot_ssid + '“.')),
  ];
}

function stepFirewall() {
  const state = firewall?.state;
  const text = {
    allowed: ['ok', `Freigegeben${firewall?.profiles?.length ? ` (${firewall.profiles.join(', ')})` : ''}. Das Smartphone darf den Laptop erreichen.`],
    blocked: ['error', 'Die Windows-Firewall blockiert Flip 7 Live. Bitte die Freigabe für private Netzwerke anlegen.'],
    none: ['busy', 'Noch keine Freigabe. Windows hat beim Start vielleicht schon gefragt – dort „Private Netzwerke“ erlauben. Sonst hier anlegen.'],
    unknown: ['busy', 'Der Firewall-Status lässt sich nicht automatisch prüfen. Falls das Smartphone nicht verbinden kann, hier die Freigabe anlegen.'],
  }[state] || ['busy', 'Wird geprüft …'];
  const pub = network?.adapter?.category === 'Public' && network?.mode !== 'hotspot';
  const netName = network?.ssid || network?.adapter?.profile_name || 'dieses Netzwerk';
  const add = h('button.btn.btn--secondary', { type: 'button', onclick: () => requestAccess(pub, netName, () => setTimeout(async () => {
    try { network = await api.post('/api/v1/system/network/refresh'); } catch { /* egal */ }
    loadFirewall();
  }, 6000)) }, pub ? 'Netzwerk auf Privat stellen und freigeben' : 'Freigabe für private Netzwerke anlegen');
  return [
    h('h1.wiz__title', 'Windows-Firewall'),
    para('Damit das Smartphone den Laptop erreicht, muss Windows Flip 7 Live im WLAN zulassen. Erlaubt wird nur der Zugriff aus privaten Netzwerken (z. B. zu Hause) – nie aus öffentlichen.'),
    h('ol.steps', h(`li.step.is-${text[0]}`, h('span.step__mark', { 'aria-hidden': 'true' }, { ok: '✓', error: '✕', busy: '…' }[text[0]]),
      h('div', h('div.step__title', 'Firewall'), h('div.caption', text[1]))),
    pub ? h('li.step.is-error', h('span.step__mark', { 'aria-hidden': 'true' }, '!'),
      h('div', h('div.step__title', 'Netzwerk ist „Öffentlich“'),
        h('div.caption', `„${netName}“ ist in Windows als öffentliches Netzwerk eingestuft – dort lässt die Firewall das Smartphone nicht durch. `
          + 'Für das WLAN zu Hause bitte auf „Privat“ umstellen (geht mit demselben Knopf).'))) : null),
    state !== 'allowed' || pub ? h('div.wiz__btns', add, h('button.btn.btn--tertiary', { type: 'button', onclick: loadFirewall }, 'Erneut prüfen')) : null,
    para('Tipp: Erscheint ein Windows-Fenster „Zugriff zulassen?“, dort „Private Netzwerke“ anhaken und „Zugriff zulassen“ klicken.'),
  ];
}

function stepPhone() {
  const local = !network?.ip;
  return [
    h('h1.wiz__title', 'Smartphone verbinden'),
    local ? para('Ohne Netzwerk kann sich kein Smartphone verbinden. Du kannst diesen Schritt überspringen und Punkte direkt am Laptop eingeben (Menü „Eingabe“).')
      : para('Öffne die Kamera deines Smartphones und scanne diesen Code. Die Eingabeseite öffnet sich im Browser – ganz ohne App und Passwort.'),
    local ? null : h('div.wiz__phone',
      h('div.qr.qr--large', h('img', { src: `/api/v1/system/qr/controller.svg?t=${Date.now()}`, alt: 'QR-Code für die Smartphone-Steuerung' })),
      h('div.wiz__phoneinfo',
        h('ol.steps', h(`li.step.is-${phoneConnected ? 'ok' : 'busy'}`, h('span.step__mark', { 'aria-hidden': 'true' }, phoneConnected ? '✓' : '…'),
          h('div', h('div.step__title', phoneConnected ? 'Smartphone verbunden!' : 'Warte auf das Smartphone …'),
            h('div.caption', phoneConnected ? 'Super – die Punkteingabe funktioniert.' : `Adresse: ${network.base_url.replace('http://', '')}`)))),
        network.wifi_qr ? para(`Zuerst mit dem WLAN „${network.ssid}“ verbinden (Passwort in den Einstellungen).`) : null,
        h('p.caption', 'Klappt es nicht? Prüfe, ob das Handy im selben WLAN ist und nicht im Gast-WLAN. Mobile Daten am Handy kurz ausschalten hilft manchmal.'))),
  ];
}

function stepSettings() {
  const targets = [150, 200, 250, 300];
  const seg = h('div.segctl', { role: 'group', 'aria-label': 'Punktzahl pro Partie' }, targets.map((t) => h('button.segctl__opt', {
    type: 'button', 'aria-pressed': String(settings.default_target_score === t), onclick: () => save({ default_target_score: t }) }, fmt(t))));
  const sw = (label, hint, key, disabled = false) => {
    const input = h('input', { type: 'checkbox', role: 'switch', checked: settings[key], disabled });
    input.addEventListener('change', () => save({ [key]: input.checked }));
    return h('label.switch', h('span', h('span.switch__label', label), h('span.field__hint.switch__hint', hint)), input);
  };
  return [
    h('h1.wiz__title', 'Grundeinstellungen'),
    para('Diese Werte kannst du jederzeit in den Einstellungen ändern.'),
    h('div.field', h('span.field__label', 'Punktzahl pro Partie (Vorschlag)'), seg,
      h('p.field__hint', 'Nach den offiziellen Regeln endet eine Partie bei 200 Punkten.')),
    sw('Mit Windows starten', settings.autostart_available ? 'Flip 7 Live startet automatisch nach der Anmeldung.' : 'Nur in der Flip7Live.exe verfügbar.',
      'autostart', !settings.autostart_available),
  ];
}

function stepDone() {
  return [
    h('h1.wiz__title', 'Alles bereit!'),
    para('So läuft ein Spieleabend:'),
    bullets([
      ['1. Spieleabend starten', 'Spieler eintragen, Punktzahl wählen – am Laptop oder am Smartphone.'],
      ['2. Punkte eintragen', 'Nach jeder Kartenrunde die Punkte aller Spieler am Smartphone eingeben (0 bei Bust).'],
      ['3. Partie gewonnen', 'Wer die Punktzahl erreicht, gewinnt die Partie – dann startet die nächste.'],
      ['4. Abend beenden', 'Am Ende siehst du, wer wie viele Partien gewonnen hat.'],
    ]),
    para('Flip 7 Live läuft in diesem Fenster. Taste F schaltet das Dashboard in den Vollbildmodus – ideal für den Fernseher. Schließt du das Fenster, wird Flip 7 Live beendet; Spielstände bleiben gespeichert.'),
  ];
}

const RENDER = [stepWelcome, stepNetwork, stepFirewall, stepPhone, stepSettings, stepDone];

async function save(patch) {
  try {
    settings = await api.patch('/api/v1/system/settings', patch);
    render();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}

async function loadFirewall() {
  try {
    firewall = await api.get('/api/v1/system/firewall');
  } catch {
    firewall = { state: 'unknown', profiles: [] };
  }
  if (index === 2) render();
}

function render() {
  replace($('#progress'), STEPS.map((name, i) => h(`li.wiz__seg${i < index ? '.is-done' : i === index ? '.is-current' : ''}`,
    { 'aria-current': i === index ? 'step' : null }, h('span.wiz__segbar', { 'aria-hidden': 'true' }), h('span.wiz__segname', name))));
  replace($('#step'), RENDER[index]());
  $('#back').hidden = index === 0;
  $('#next').textContent = index === 0 ? 'Los geht’s' : index === STEPS.length - 1 ? 'Ersten Spieleabend starten'
    : index === 3 && !phoneConnected ? 'Später verbinden' : 'Weiter';
}

$('#back').addEventListener('click', () => { index = Math.max(0, index - 1); render(); });
$('#next').addEventListener('click', async () => {
  if (index < STEPS.length - 1) {
    index += 1;
    if (index === 2 && !firewall) loadFirewall();
    render();
    $('#step').querySelector('h1')?.focus?.();
    return;
  }
  try {
    await api.patch('/api/v1/system/settings', { setup_completed: true });
  } catch (e) {
    toast(e.message, { type: 'error' });
    return;
  }
  location.href = '/setup';
});

onLive('client:connected', (p) => {
  network = { ...network, clients: p.clients };
  if (p.clients.controller > 0 && !phoneConnected) {
    phoneConnected = true;
    toast('Smartphone verbunden', { type: 'ok' });
    if (index === 3) render();
  }
});
onLive('network:changed', (info) => { network = { ...network, ...info }; if (index === 1 || index === 3) render(); });

(async () => {
  try {
    [settings, network] = await Promise.all([api.get('/api/v1/system/settings'), api.get('/api/v1/system/network')]);
  } catch (e) {
    toast(e.message, { type: 'error' });
    return;
  }
  phoneConnected = (network.clients?.controller || 0) > 0;
  render();
  connectLive();
})();
