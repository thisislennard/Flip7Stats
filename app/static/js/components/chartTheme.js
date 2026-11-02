// Liniendiagramm „Punkteentwicklung“ (Chart.js, lokal). Farben aus tokens.css, Direktbeschriftung am Linienende
// statt Legende, Punkteziel als gelbe Strichlinie. Führender Spieler: Gelb und dicker.
import { reducedMotion } from '../core/prefs.js';

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function applyDefaults() {
  const C = window.Chart;
  C.defaults.font.family = css('--font-ui');
  C.defaults.color = css('--color-text-secondary');
  C.defaults.borderColor = css('--color-line');
}

const endLabels = {
  id: 'endLabels',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    ctx.save();
    ctx.font = `600 14px ${css('--font-display')}`;
    ctx.textBaseline = 'middle';
    const placed = [];
    chart.data.datasets.forEach((ds, i) => {
      if (ds.isTarget || !chart.isDatasetVisible(i)) return;
      const meta = chart.getDatasetMeta(i);
      const pt = meta.data[meta.data.length - 1];
      if (!pt) return;
      let y = pt.y;
      for (const p of placed) if (Math.abs(p - y) < 16) y = p + 16; // Überlappung vermeiden
      placed.push(y);
      ctx.fillStyle = ds.borderColor;
      ctx.fillText(ds.label, pt.x + 10, y);
    });
    ctx.restore();
  },
};

export class ScoreChart {
  constructor(canvas, { compact = false } = {}) {
    applyDefaults();
    this.compact = compact;
    this.chart = new window.Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets: [] },
      plugins: [endLabels],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reducedMotion() ? false : { duration: 500, easing: 'easeOutQuart' },
        layout: { padding: { right: 84, top: 8 } },
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: css('--color-surface-raised'), borderColor: css('--color-line-strong'), borderWidth: 1,
            titleFont: { family: css('--font-display'), weight: 600, size: 15 },
            bodyFont: { family: css('--font-ui'), size: 14 }, padding: 12, cornerRadius: 0,
            displayColors: true, boxWidth: 10, boxHeight: 10,
            filter: (item) => !item.dataset.isTarget,
            callbacks: { title: (items) => `Runde ${items[0]?.label ?? ''}` },
          },
        },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { font: { size: 13 } },
               title: { display: !compact, text: 'Runde', color: css('--color-text-muted') } },
          y: { min: 0, grid: { color: css('--color-line'), drawTicks: false }, border: { display: false },
               ticks: { padding: 10 }, title: { display: !compact, text: 'Gesamtpunkte', color: css('--color-text-muted') } },
        },
        elements: {
          line: { tension: 0, borderWidth: 2.5, borderJoinStyle: 'round' },
          point: { radius: 0, hoverRadius: 6, hitRadius: 12 },
        },
      },
    });
  }

  /** chart = statistics.chart aus dem Spielstand; leaderIds = aktuell Führende; target = Punkteziel */
  update(chart, leaderIds = [], target = null, { fromZero = true } = {}) {
    const palette = [2, 3, 4, 5, 6, 7, 8].map((n) => css(`--chart-${n}`));
    let next = 0;
    const labels = fromZero ? [0, ...chart.labels] : [...chart.labels];
    const datasets = chart.series.map((s) => {
      const lead = leaderIds.includes(s.player_id);
      const color = lead ? css('--chart-1') : palette[next++ % palette.length];
      return {
        label: s.name, data: fromZero ? [0, ...s.data] : [...s.data], borderColor: color, backgroundColor: color,
        borderWidth: lead ? 4.5 : 2.5, order: lead ? 0 : 1,
        // Verlauf je Runde: nur Endpunkt des Führenden; Entwicklung über Partien: jede Partie als Punkt.
        pointRadius: fromZero ? (ctx) => (lead && ctx.dataIndex === labels.length - 1 ? 7 : 0) : lead ? 6 : 4,
      };
    });
    if (target) {
      datasets.push({ label: 'Ziel', isTarget: true, data: labels.map(() => target), borderColor: css('--color-yellow'),
        borderDash: [6, 6], borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 0, order: 2 });
    }
    const maxVal = Math.max(target || 0, ...chart.series.flatMap((s) => s.data).filter((v) => v !== null), 10);
    this.chart.options.scales.y.suggestedMax = Math.ceil((maxVal * 1.08) / 50) * 50;
    this.chart.data.labels = labels;
    this.chart.data.datasets = datasets;
    this.chart.update();
  }

  destroy() {
    this.chart.destroy();
  }
}
