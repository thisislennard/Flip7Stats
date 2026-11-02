// Bestätigungsdialog für destruktive Aktionen. Esc oder „Abbrechen“ schließt; Primäraktion rechts.
import { h } from '../core/dom.js';

export function confirmDialog({ title, text, confirm = 'Bestätigen', cancel: cancelText = 'Abbrechen', danger = true, input = null }) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    let field = null;
    if (input) {
      field = h('input.input', { type: input.type || 'text', inputmode: input.inputmode, placeholder: input.placeholder || '',
        value: input.value ?? '', 'aria-label': input.label || title });
    }
    const done = (value) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
      resolve(value);
    };
    const ok = h(`button.btn.${danger ? 'btn--danger.is-confirm' : 'btn--primary'}`, { type: 'button',
      onclick: () => done(field ? field.value : true) }, confirm);
    const cancel = h('button.btn.btn--secondary', { type: 'button', onclick: () => done(false) }, cancelText);
    const dialog = h('div.modal', { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'modal-title' },
      h('h2#modal-title', title), text ? h('p.muted', text) : null, field, h('div.modal__actions', cancel, ok));
    const backdrop = h('div.modal-backdrop', { onclick: (e) => e.target === backdrop && done(false) }, dialog);
    const onKey = (e) => {
      if (e.key === 'Escape') done(false);
      if (e.key === 'Enter' && field && document.activeElement === field) done(field.value);
    };
    document.addEventListener('keydown', onKey);
    document.body.append(backdrop);
    (field || cancel).focus();
  });
}
