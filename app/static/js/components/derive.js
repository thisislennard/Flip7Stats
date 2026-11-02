// Anzeige-Ableitungen aus dem serverseitigen Spielstand (keine eigene Spiellogik, nur Darstellung):
// Platzbewegung seit der Vorrunde, „führt seit Runde n“, Abstand zu Platz 2, Ereignisse der letzten Runde.

function ranksFrom(values) {
  const sorted = [...values.entries()].sort((a, b) => b[1] - a[1]);
  const ranks = new Map();
  let prev = null;
  let rank = 0;
  sorted.forEach(([pid, v], i) => {
    if (v !== prev) {
      rank = i + 1;
      prev = v;
    }
    ranks.set(pid, rank);
  });
  return ranks;
}

function valuesAt(series, i) {
  return new Map(series.map((s) => [s.player_id, i < 0 ? 0 : s.data[i] ?? 0]));
}

export function derive(state) {
  const series = state.statistics.chart.series;
  const labels = state.statistics.chart.labels;
  const n = labels.length;
  const nowRanks = new Map(state.leaderboard.map((r) => [r.player_id, r.rank]));
  const movement = new Map();
  if (n >= 2) {
    const prev = ranksFrom(valuesAt(series, n - 2));
    for (const [pid, rank] of nowRanks) movement.set(pid, prev.get(pid) - rank);
  }

  const leaderIds = state.leaders.map((l) => l.player_id);
  let leaderSince = null;
  if (leaderIds.length === 1 && n > 0) {
    const pid = leaderIds[0];
    let i = n - 1;
    const soleLeaderAt = (idx) => {
      const vals = valuesAt(series, idx);
      const me = vals.get(pid);
      return me > 0 && [...vals.entries()].every(([other, v]) => other === pid || v < me);
    };
    while (i - 1 >= 0 && soleLeaderAt(i - 1)) i--;
    leaderSince = labels[i];
  }

  const board = state.leaderboard;
  const firstOther = board.find((r) => !leaderIds.includes(r.player_id));
  const leaderTotal = board[0]?.total ?? 0;
  const gapToSecond = leaderIds.length === 1 && firstOther ? leaderTotal - firstOther.total : 0;

  const names = new Map(state.players.map((p) => [p.player_id, p.name]));
  const events = [];
  const last = state.rounds[state.rounds.length - 1];
  if (last) {
    for (const [pid, pts] of Object.entries(last.scores)) {
      if (pts === 0) events.push({ type: 'bust', text: `${names.get(Number(pid))} – 0 Punkte`, playerId: Number(pid) });
    }
    for (const p of state.players) {
      if (!(String(p.player_id) in last.scores)) {
        events.push({ type: 'skip', text: `${p.name} – Runde nicht mitgespielt`, playerId: p.player_id });
      }
    }
    const lc = state.statistics.lead_changes.at(-1);
    if (lc && lc.round_number === last.round_number) {
      events.unshift({ type: 'lead', text: `${names.get(lc.player_id)} übernimmt die Führung`, playerId: lc.player_id });
    }
    const corrected = state.rounds.filter((r) => r.updated_at && r.updated_at !== r.created_at)
      .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))[0];
    if (corrected && corrected.updated_at >= last.created_at) {
      events.push({ type: 'fix', text: `Runde ${corrected.round_number} korrigiert` });
    }
  }

  let bestRound = null;
  for (const r of state.rounds) {
    for (const [pid, pts] of Object.entries(r.scores)) {
      if (!bestRound || pts > bestRound.points) bestRound = { points: pts, name: names.get(Number(pid)), round: r.round_number };
    }
  }

  return { movement, leaderIds, leaderSince, gapToSecond, events, bestRound, roundsPlayed: n, names };
}
