// Startseite: Neues Spiel, Fortsetzen (nur bei offenem Spiel), Verbindungsbox.
import { api, isAdmin } from '../core/api.js';
import { $, fmt } from '../core/dom.js';
import '../core/prefs.js';
import { connectLive, onLive } from '../core/realtime.js';
import { renderConnection } from '../components/connection.js';
import { connectionChip } from '../components/statusChip.js';

let network = null;
let live = 'connecting';

function renderGame(state) {
  const resume = $('#resume');
  const newBtn = $('#new-game');
  if (!state || !['CREATED', 'ACTIVE'].includes(state.game.status)) {
    resume.hidden = true;
    newBtn.textContent = 'Neuer Spieleabend';
    return;
  }
  resume.hidden = false;
  if (state.game.status === 'CREATED') {
    resume.href = '/setup';
    $('#resume-text').textContent = `Vorbereitet · ${state.players.length} Spieler`;
    newBtn.textContent = 'Spiel vorbereiten';
    newBtn.href = '/setup';
    return;
  }
  resume.href = isAdmin ? '/dashboard' : '/controller';
  const lead = state.leaders.length === 1 ? `${state.leaders[0].name} führt mit ${fmt(state.leaderboard[0].total)}`
    : state.leaders.length > 1 ? `Gleichstand bei ${fmt(state.leaderboard[0].total)}` : 'noch keine Punkte';
  const part = state.game.night_number ? `Partie ${state.game.night_number} · ` : '';
  $('#resume-text').textContent = `${part}Runde ${state.game.current_round} · ${lead}`;
  newBtn.href = '/setup';
}

function renderConn() {
  connectionChip($('#conn-chip'), { live, network });
  renderConnection($('#conn-body'), network, { size: 'small', onRefresh: (info) => { network = info; renderConn(); } });
}

onLive('connection', ({ status }) => { live = status; renderConn(); });
onLive('game:updated', (p) => renderGame(p.state));
onLive('network:changed', (info) => { network = { ...network, ...info }; renderConn(); });
onLive('client:connected', (p) => { if (network) { network.clients = p.clients; renderConn(); } });
onLive('client:disconnected', (p) => { if (network) { network.clients = p.clients; renderConn(); } });

(async () => {
  try {
    network = await api.get('/api/v1/system/network');
  } catch { /* später per Live-Verbindung */ }
  renderConn();
  try {
    renderGame(await api.get('/api/v1/games/current'));
  } catch { renderGame(null); }
  connectLive();
})();
