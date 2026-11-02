// DOM-Helfer. Texte werden ausschließlich über textContent gesetzt (kein innerHTML mit Daten → kein XSS).

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h('div.klasse#id', {attr: wert, onclick: fn}, kind1, 'text', …) */
export function h(spec, attrs = {}, ...children) {
  const [, tag = 'div', rest = ''] = spec.match(/^([a-z0-9-]*)(.*)$/i);
  const el = document.createElement(tag || 'div');
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  if (attrs === null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function replace(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

/** SVG-Markup vom eigenen Server (QR-Codes) sicher einsetzen: als Bild, nicht als HTML. */
export function svgImage(url, alt) {
  return h('img', { src: url, alt, width: 360, height: 360, decoding: 'async' });
}

const nf = new Intl.NumberFormat('de-DE');
export const fmt = (n) => (n === null || n === undefined ? '–' : nf.format(n));

function toDate(iso) {
  return iso ? new Date(iso) : null;
}
export function fmtDate(iso, opts = { day: '2-digit', month: '2-digit', year: 'numeric' }) {
  const d = toDate(iso);
  return d ? d.toLocaleDateString('de-DE', opts) : '–';
}
export function fmtTime(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '–';
}
export function fmtShortDate(iso) {
  return fmtDate(iso, { day: '2-digit', month: '2-digit' }); // „01.11.“ (mit Punkt aus der Formatierung)
}

export function gameTitle(game, night = null) {
  if (!game) return '';
  if (night) return `${night.name || `Spieleabend ${fmtShortDate(night.created_at)}`} · Partie ${game.night_number}`;
  return game.name || `Spieleabend ${fmtShortDate(game.started_at || game.created_at)}`;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function on(el, type, sel, fn) {
  el.addEventListener(type, (ev) => {
    const target = ev.target.closest(sel);
    if (target && el.contains(target)) fn(ev, target);
  });
}
