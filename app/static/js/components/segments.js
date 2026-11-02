// Fortschritt als 20 geneigte Segmente (1 Segment = Ziel/20 Punkte). Teilsegmente werden nicht angezeigt.
import { h } from '../core/dom.js';

export function segments(total, target, { onAccent = false } = {}) {
  const per = target / 20;
  const filled = per > 0 ? Math.min(20, Math.floor(total / per)) : 0;
  const reached = total >= target;
  const el = h('div.segments', {
    role: 'img',
    'aria-label': reached ? 'Ziel erreicht' : `${Math.min(100, Math.round((total / target) * 100))} % des Punkteziels`,
  });
  if (onAccent) el.classList.add('segments--on-accent');
  if (reached && !onAccent) el.classList.add('is-reached');
  for (let i = 0; i < 20; i++) el.append(h(i < filled ? 'span.segments__seg.is-filled' : 'span.segments__seg'));
  return el;
}
