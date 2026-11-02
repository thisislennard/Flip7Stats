// Toasts: unten mittig, 3 s; Fehler bleiben bis zur Aktion oder bis sie ersetzt werden.
import { h } from '../core/dom.js';

let host = null;
const ICONS = { ok: '✓', error: '✕', warn: '!', info: 'i' };

function container() {
  if (!host) {
    host = h('div.toasts', { role: 'status', 'aria-live': 'polite' });
    document.body.append(host);
  }
  return host;
}

/** toast('Gespeichert', {type:'ok'}) · Fehler: {type:'error', action:{label, onClick}, sticky:true} */
export function toast(text, { type = 'info', action = null, sticky = type === 'error', duration = 3000, id = null } = {}) {
  const box = container();
  if (id) box.querySelector(`[data-id="${id}"]`)?.remove();
  const el = h(`div.toast.toast--${type}`, { dataset: id ? { id } : {} },
    h('span.toast__icon', { 'aria-hidden': 'true' }, ICONS[type] || ''),
    h('span.toast__text', text));
  const close = () => el.remove();
  if (action) {
    el.append(h('button.btn.btn--tertiary.toast__action', { type: 'button', onclick: () => { close(); action.onClick(); } },
      action.label));
  } else if (sticky) {
    el.append(h('button.btn.btn--tertiary.toast__action', { type: 'button', 'aria-label': 'Schließen', onclick: close }, 'OK'));
  }
  box.append(el);
  if (!sticky) setTimeout(close, duration);
  return close;
}

export function dismissToast(id) {
  host?.querySelector(`[data-id="${id}"]`)?.remove();
}
