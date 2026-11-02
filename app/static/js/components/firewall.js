// Firewall-Freigabe (und bei „Öffentlich“ das Umstellen des Netzwerks auf „Privat“) – eine Windows-Abfrage.
import { api } from '../core/api.js';
import { confirmDialog } from './modal.js';
import { toast } from './toast.js';

export async function requestAccess(makePrivate, netName, after) {
  const ok = await confirmDialog({
    title: makePrivate ? 'Netzwerk auf „Privat“ stellen?' : 'Firewall-Freigabe anlegen?',
    danger: false,
    confirm: makePrivate ? 'Umstellen und freigeben' : 'Freigabe anlegen',
    text: makePrivate
      ? `„${netName}“ wird in Windows als privates Netzwerk eingestuft und Flip 7 Live dort freigegeben (nur dieser Port). `
        + 'Das ist richtig für dein WLAN zu Hause – nicht für Café-, Hotel- oder Firmen-WLAN. Windows fragt danach einmal nach '
        + 'Administratorrechten: bitte mit „Ja“ bestätigen.'
      : 'Flip 7 Live wird nur für private Netzwerke freigegeben (nur dieser Port). Windows fragt danach nach Administratorrechten – '
        + 'bitte mit „Ja“ bestätigen.',
  });
  if (!ok) return;
  try {
    await api.post('/api/v1/system/firewall/allow', { make_private: !!makePrivate });
    toast('Windows-Abfrage geöffnet. Nach dem Bestätigen wird neu geprüft.', { type: 'info', duration: 5000 });
    after?.();
  } catch (e) {
    toast(e.message, { type: 'error' });
  }
}
