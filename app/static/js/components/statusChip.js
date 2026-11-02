// Verbindungs-Chip: LIVE · WLAN, LIVE · HOTSPOT, VERBINDE…, GETRENNT, LOKAL, n STEUERUNG.
import { h, replace } from '../core/dom.js';

const MODE = { wlan: 'WLAN', ethernet: 'LAN', network: 'NETZ', hotspot: 'HOTSPOT' };

export function connectionChip(el, { live, network, clients }) {
  let cls = 'chip--muted';
  let text = 'Lokal';
  if (live === 'reconnecting' || live === 'connecting') {
    cls = 'chip--warn';
    text = 'Verbinde…';
  } else if (live === 'offline' || live === 'denied') {
    cls = 'chip--error';
    text = live === 'denied' ? 'Kein Zugang' : 'Getrennt';
  } else if (network && MODE[network.mode]) {
    cls = 'chip--live';
    text = `Live · ${MODE[network.mode]}`;
  } else if (network?.mode === 'local') {
    cls = 'chip--muted';
    text = 'Lokal';
  } else if (live === 'online') {
    cls = 'chip--live';
    text = 'Live';
  }
  el.className = `chip ${cls}`;
  replace(el, h('span.chip__dot', { 'aria-hidden': 'true' }), text);
  return el;
}

export function controllerChip(el, clients) {
  const n = clients?.controller || 0;
  el.className = `chip ${n ? 'chip--ok' : 'chip--muted'}`;
  replace(el, h('span.chip__dot', { 'aria-hidden': 'true' }), n === 1 ? '1 Steuerung' : `${n} Steuerungen`);
  return el;
}
