// Anzeige-Einstellungen dieses Geräts (TV-Modus, reduzierte Bewegung). Nur Komfort – fehlt der Speicher
// (privates Fenster), gelten die Standardwerte.

const KEY = 'flip7live.display';

export function loadPrefs() {
  try {
    return { tv: false, reduceMotion: false, ...JSON.parse(localStorage.getItem(KEY) || '{}') };
  } catch {
    return { tv: false, reduceMotion: false };
  }
}

export function savePrefs(p) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* ohne Speicher weiter */
  }
  applyPrefs(p);
}

export function applyPrefs(p = loadPrefs()) {
  const tvParam = new URLSearchParams(location.search).get('tv') === '1';
  document.documentElement.classList.toggle('tv', p.tv || tvParam);
  document.documentElement.classList.toggle('reduce-motion', !!p.reduceMotion);
}

export function reducedMotion() {
  return loadPrefs().reduceMotion || matchMedia('(prefers-reduced-motion: reduce)').matches;
}

applyPrefs();
