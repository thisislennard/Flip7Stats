// Verbindungsanzeige: QR, Adresse, Netzname, Schritte „Netzwerk erkannt → Server erreichbar →
// Smartphone verbunden“, Hinweise, Aktionen. Wird auf Startseite, im Dashboard-Overlay und in den Einstellungen genutzt.
import { api, isAdmin } from '../core/api.js';
import { h, replace } from '../core/dom.js';
import { toast } from './toast.js';

const MODE_TEXT = {
  wlan: (i) => `Scanne den Code mit einem Smartphone im WLAN „${i.ssid || 'dieses Laptops'}“.`,
  ethernet: () => 'Scanne den Code mit einem Smartphone im WLAN desselben Routers.',
  network: () => 'Scanne den Code mit einem Smartphone im selben Netzwerk.',
  hotspot: (i) => `Verbinde dein Smartphone zuerst mit dem Hotspot „${i.ssid || i.hotspot?.ssid || 'Flip7-Live'}“, dann scannen.`,
  local: () => 'Eingabe direkt an diesem Gerät.',
};

function qrImg(kind, size) {
  // Zeitstempel verhindert, dass nach einem Netzwerkwechsel ein alter QR-Code aus dem Cache angezeigt wird.
  return h(`div.qr${size ? `.qr--${size}` : ''}`, h('img', { src: `/api/v1/system/qr/${kind}.svg?t=${Date.now()}`,
    alt: kind === 'wifi' ? 'QR-Code: mit dem Hotspot verbinden' : kind === 'viewer' ? 'QR-Code: Zuschaueransicht' : 'QR-Code: Smartphone-Steuerung' }));
}

function step(state, title, detail) {
  const mark = { ok: '✓', error: '✕', busy: '…', idle: '–' }[state];
  return h(`li.step.is-${state}`, h('span.step__mark', { 'aria-hidden': 'true' }, mark),
    h('div', h('div.step__title', title), detail ? h('div.caption', detail) : null));
}

export async function refreshNetwork(button) {
  button?.classList.add('is-loading');
  try {
    const info = await api.post('/api/v1/system/network/refresh');
    toast(info.ip ? `Verbindung geprüft: ${info.mode_label}` : 'Kein Netzwerk gefunden – siehe Hinweise.', { type: info.ip ? 'ok' : 'warn' });
    return info;
  } catch (e) {
    toast(e.message, { type: 'error' });
    return null;
  } finally {
    button?.classList.remove('is-loading');
  }
}

/** opts: size ('large'|'small'), steps (bool), actions (bool), onRefresh(info) */
export function renderConnection(el, info, opts = {}) {
  if (!info) {
    replace(el, h('div.skeleton'));
    return;
  }
  const local = info.mode === 'local' || !info.ip;
  const hotspot = info.hotspot || {};
  const ctrl = info.clients?.controller || 0;
  const qrKind = info.controller_url ? 'controller' : info.viewer_url ? 'viewer' : null;
  const parts = [];

  const left = h('div.conn__qr');
  if (info.wifi_qr) {
    left.append(h('div.conn__qrstep', h('span.label', '1 · WLAN verbinden'), qrImg('wifi', opts.size === 'large' ? 'small' : 'small')));
  }
  if (!local && qrKind) {
    left.append(h('div.conn__qrstep', info.wifi_qr ? h('span.label', '2 · Steuerung öffnen') : null, qrImg(qrKind, opts.size)));
  } else if (local) {
    left.append(h('div.conn__noqr', h('span.label', 'Kein Netzwerk'), h('p', MODE_TEXT.local())));
  }
  parts.push(left);

  const right = h('div.conn__info');
  if (!local) {
    right.append(h('div.qr-block__url', info.base_url.replace(/^http:\/\//, '')));
    right.append(h('p.muted', MODE_TEXT[info.mode]?.(info) || ''));
  }
  if (opts.steps !== false) {
    const reach = info.reachable === true ? 'ok' : info.reachable === false ? 'error' : local ? 'idle' : 'busy';
    right.append(h('ol.steps',
      step(local ? 'error' : 'ok', 'Netzwerk erkannt', local ? 'Kein WLAN, kein LAN, kein Hotspot' : `${info.mode_label}${info.ssid ? ` · ${info.ssid}` : ''}`),
      step(reach, 'Server erreichbar', info.ip ? `${info.ip}:${info.port}` : `nur lokal · Port ${info.port}`),
      step(ctrl ? 'ok' : 'busy', 'Smartphone verbunden', ctrl ? 'Bereit. Punkte können eingegeben werden.' : 'Warte auf das Smartphone …')));
  }
  for (const w of opts.warnings === false ? [] : info.warnings || []) {
    right.append(h('div.notice.notice--warn', h('span.notice__icon', { 'aria-hidden': 'true' }, '!'), h('p', w)));
  }
  if (local || (hotspot.capability && hotspot.capability !== 'Enabled' && info.mode !== 'wlan' && info.mode !== 'ethernet')) {
    for (const hint of info.hints || []) right.append(h('p.caption', hint));
  }
  if (opts.actions !== false) {
    const actions = h('div.conn__actions');
    const btn = h('button.btn.btn--tertiary', { type: 'button', onclick: async () => {
      const fresh = await refreshNetwork(btn);
      if (fresh) opts.onRefresh?.(fresh);
    } }, 'Verbindung neu prüfen');
    actions.append(btn);
    if (isAdmin && (local || info.mode === 'hotspot' || !hotspot.available)) {
      actions.append(h('button.btn.btn--tertiary', { type: 'button', onclick: async () => {
        try {
          await api.post('/api/v1/system/hotspot/open-settings');
        } catch (e) {
          toast(e.message, { type: 'error' });
        }
      } }, 'Windows-Hotspot-Einstellungen'));
    }
    right.append(actions);
  }
  parts.push(right);
  el.classList.add('conn');
  if (opts.size) el.classList.add(`conn--${opts.size}`);
  replace(el, ...parts);
}
