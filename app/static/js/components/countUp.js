// Zahlenanimation (requestAnimationFrame). Bei reduzierter Bewegung springt die Zahl direkt auf den Zielwert.
import { fmt } from '../core/dom.js';
import { reducedMotion } from '../core/prefs.js';

const running = new WeakMap();

export function countUp(el, to, { duration = 600, format = fmt } = {}) {
  const from = Number(el.dataset.value ?? to);
  el.dataset.value = String(to);
  cancelAnimationFrame(running.get(el));
  if (reducedMotion() || from === to || !Number.isFinite(from)) {
    el.textContent = format(to);
    return;
  }
  const start = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 4);
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    el.textContent = format(Math.round(from + (to - from) * ease(t)));
    if (t < 1) running.set(el, requestAnimationFrame(step));
  };
  running.set(el, requestAnimationFrame(step));
}
