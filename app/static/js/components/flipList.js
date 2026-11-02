// FLIP-Umsortierung: alte Positionen messen → DOM umsortieren → mit translate auf die neue Position animieren.
// Aufsteiger ziehen leicht nach links (7-Schnitt-Richtung). Nur transform/opacity.
import { reducedMotion } from '../core/prefs.js';

export function measure(container) {
  const map = new Map();
  for (const el of container.children) {
    if (el.dataset.key) map.set(el.dataset.key, el.getBoundingClientRect().top);
  }
  return map;
}

export function play(container, before, { duration = 550, stagger = 40 } = {}) {
  if (reducedMotion() || !before.size) return;
  let i = 0;
  for (const el of container.children) {
    const old = before.get(el.dataset.key);
    if (old === undefined) {
      el.animate([{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }],
        { duration: 280, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'backwards' });
      continue;
    }
    const dy = old - el.getBoundingClientRect().top;
    if (Math.abs(dy) < 1) continue;
    const dx = dy > 0 ? -14 : 0; // Aufsteiger (kommen von unten) ziehen nach links
    el.animate(
      [{ transform: `translate(0, ${dy}px)` }, { transform: `translate(${dx}px, ${dy * 0.3}px)`, offset: 0.6 }, { transform: 'none' }],
      { duration, delay: i++ * stagger, easing: 'cubic-bezier(0.65, 0, 0.35, 1)', fill: 'backwards' });
  }
}
