// Live-Verbindung (Socket.IO). Bei Abbruch verbindet sich der Client selbst neu und fordert den aktuellen Stand an.
// Über den Socket wird nur gelesen; Änderungen laufen über die REST-API.

const listeners = new Map();
let socket = null;
let status = 'connecting';
let lastSync = null;

function emitLocal(event, payload) {
  for (const fn of listeners.get(event) || []) {
    try {
      fn(payload);
    } catch (e) {
      console.error(e);
    }
  }
}

function setStatus(s, detail) {
  status = s;
  emitLocal('connection', { status: s, detail, lastSync });
}

export function onLive(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export const connectionStatus = () => ({ status, lastSync });

const EVENTS = [
  'game:created', 'game:started', 'game:updated', 'game:finished',
  'round:created', 'round:updated', 'round:deleted',
  'scores:updated', 'leaderboard:updated',
  'client:connected', 'client:disconnected',
  'network:changed', 'system:status',
];

export function connectLive() {
  if (socket || typeof window.io !== 'function') return socket;
  socket = window.io({ reconnection: true, reconnectionDelay: 1000, reconnectionDelayMax: 5000, timeout: 8000 });
  socket.on('connect', () => {
    setStatus('online');
    socket.emit('state:request');
  });
  socket.on('disconnect', (reason) => setStatus('offline', reason));
  socket.io.on('reconnect_attempt', () => setStatus('reconnecting'));
  socket.on('connect_error', (err) => {
    const msg = err?.message || '';
    if (['unauthenticated', 'expired', 'revoked', 'admin_remote', 'host_not_allowed'].includes(msg)) {
      setStatus('denied', msg);
      socket.io.opts.reconnection = false;
    } else {
      setStatus('reconnecting', msg);
    }
  });
  for (const ev of EVENTS) {
    socket.on(ev, (payload) => {
      if (ev === 'game:updated') lastSync = new Date();
      emitLocal(ev, payload);
    });
  }
  socket.on('error', (payload) => emitLocal('error', payload));
  return socket;
}

export function requestState() {
  socket?.connected && socket.emit('state:request');
}
