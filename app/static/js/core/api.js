// REST-Client. Schreibzugriffe tragen das CSRF-Token der Sitzung; Netzwerkfehler werden als ApiError(status 0)
// gemeldet, damit die Oberfläche „unbestätigt“ anzeigen kann.

const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
const TIMEOUT_MS = 10000;

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
  get isNetwork() {
    return this.status === 0;
  }
}

async function request(method, url, body, { timeout = TIMEOUT_MS } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let resp;
  try {
    resp = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    });
  } catch {
    throw new ApiError(0, 'network', 'Keine Verbindung zum Laptop.');
  } finally {
    clearTimeout(timer);
  }
  let json = null;
  try {
    json = await resp.json();
  } catch {
    /* keine JSON-Antwort */
  }
  if (!resp.ok || !json || json.ok === false) {
    const err = json?.error || {};
    throw new ApiError(resp.status, err.code || 'error', err.message || `Fehler ${resp.status}`, err.details);
  }
  return json;
}

export const api = {
  get: (url) => request('GET', url).then((j) => j.data),
  post: (url, body = {}) => request('POST', url, body).then((j) => j.data),
  patch: (url, body) => request('PATCH', url, body).then((j) => j.data),
  del: (url) => request('DELETE', url).then((j) => j.data),
  raw: request,
};

/** Idempotenzschlüssel. crypto.randomUUID gibt es nur in sicheren Kontexten – im WLAN (http) daher selbst erzeugen. */
export function newKey() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export const role = document.body.dataset.role || '';
export const isAdmin = role === 'admin';
export const canWrite = role === 'admin' || role === 'controller';
