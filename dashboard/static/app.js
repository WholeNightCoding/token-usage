'use strict';

const state = {
  range: 'this-week',
  intervalSec: 30,
  rtBucketSec: 60,
  rtLabel: '1 min',
};
let dailyChart, modelChart, projectChart, realtimeChart;
let refreshTimer = null;
let realtimeTimer = null;

// ---- helpers ----
const $ = (s) => document.querySelector(s);
const fmt = (n) => {
  if (n >= 1e9) return (n/1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(Math.round(n));
};
const fmtInt = (n) => Number(n).toLocaleString();
const pad2 = (n) => String(n).padStart(2, '0');

// Per-theme model palette. Same model keeps a stable color WITHIN a theme,
// across range switches; switching themes re-seeds the palette intentionally.
const THEME_PALETTES = {
  apple: [
    '#0071e3', '#34c759', '#ff9500', '#af52de', '#ff3b30',
    '#5856d6', '#ff2d55', '#ffcc00', '#5ac8fa', '#ff9f0a',
    '#30d158', '#bf5af2',
  ],
  material: [
    '#0061a4', '#006d3d', '#984061', '#5b5891', '#b22a00',
    '#7d5260', '#386a20', '#0288d1', '#c62828', '#6a1b9a',
    '#2e7d32', '#ef6c00',
  ],
  linear: [
    '#5e6ad2', '#26b5ce', '#eb5757', '#9750dd', '#f2c94c',
    '#27ae60', '#fb923c', '#ec4899', '#06b6d4', '#a78bfa',
    '#10b981', '#f59e0b',
  ],
  terminal: [
    '#00ff41', '#ffb000', '#ff6b35', '#33d6ff', '#b9f25c',
    '#cc99ff', '#ffd700', '#7fff00', '#ff66cc', '#80ff80',
    '#ffaa00', '#66ddff',
  ],
};

let currentPalette = THEME_PALETTES.apple;
const modelColors = new Map();
function colorForModel(m) {
  if (!modelColors.has(m)) {
    modelColors.set(m, currentPalette[modelColors.size % currentPalette.length]);
  }
  return modelColors.get(m);
}
const withAlpha = (hex, a) => hex + Math.round(a * 255).toString(16).padStart(2, '0');

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}
function chartAccent() { return cssVar('--chart-accent') || '#0071e3'; }

let inFlight = 0;
let loadingTimer = null;
function loadingStart() {
  inFlight++;
  if (loadingTimer === null) {
    loadingTimer = setTimeout(() => { $('#loading').hidden = false; loadingTimer = null; }, 300);
  }
}
function loadingEnd() {
  inFlight = Math.max(0, inFlight - 1);
  if (inFlight === 0) {
    if (loadingTimer !== null) { clearTimeout(loadingTimer); loadingTimer = null; }
    $('#loading').hidden = true;
  }
}
function fetchJSON(url) {
  loadingStart();
  return fetch(url).then(r => r.json()).finally(loadingEnd);
}

// ---- dashboard data: client-side cache keyed by range ----
// Re-clicking a range served <60s ago returns instantly with zero network.
// Range buttons also keep the previous panels visible while fresh data loads.
const DASHBOARD_TTL_MS = 60_000;
const dashboardCache = new Map();  // range -> { data, fetchedAt }

async function fetchDashboard(range, { force = false } = {}) {
  const cached = dashboardCache.get(range);
  if (!force && cached && (Date.now() - cached.fetchedAt) < DASHBOARD_TTL_MS) {
    return cached.data;
  }
  const data = await fetchJSON('/api/dashboard?range=' + encodeURIComponent(range));
  dashboardCache.set(range, { data, fetchedAt: Date.now() });
  return data;
}

// ---- efficiency data: same cache pattern, separate endpoint ----
const EFFICIENCY_TTL_MS = 60_000;
const efficiencyCache = new Map();  // range -> { data, fetchedAt }

async function fetchEfficiency(range, { force = false } = {}) {
  const cached = efficiencyCache.get(range);
  if (!force && cached && (Date.now() - cached.fetchedAt) < EFFICIENCY_TTL_MS) {
    return cached.data;
  }
  const data = await fetchJSON('/api/efficiency?range=' + encodeURIComponent(range));
  efficiencyCache.set(range, { data, fetchedAt: Date.now() });
  return data;
}

// ---- range nav ----
document.querySelectorAll('#ranges button').forEach(btn => {
  btn.addEventListener('click', () => {
    if (state.range === btn.dataset.range) return;
    document.querySelectorAll('#ranges button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.range = btn.dataset.range;
    refreshAll();
    scheduleRefresh();
  });
});

$('#refresh').addEventListener('click', () => refreshAll({ force: true }));

$('#interval').addEventListener('change', (e) => {
  state.intervalSec = Number(e.target.value) || 0;
  scheduleRefresh();
});

// ---- theme controller ----
function readThemeStyles() {
  const cs = getComputedStyle(document.body);
  return {
    text: cs.color,
    grid: cs.getPropertyValue('--chart-grid').trim() || 'rgba(0,0,0,0.1)',
    font: cs.getPropertyValue('--font-body').trim() || cs.fontFamily,
    mono: cs.getPropertyValue('--font-mono').trim() || 'monospace',
  };
}

// Walk a chart's options and override every place Chart.js v4 caches color
// on the instance. Just bumping Chart.defaults isn't enough — once a chart
// is built, its scales/legend hold their own resolved values.
function applyChartTheme(chart, s) {
  if (!chart) return;
  for (const scale of Object.values(chart.options.scales || {})) {
    if (!scale.ticks) scale.ticks = {};
    scale.ticks.color = s.text;
    if (!scale.ticks.font) scale.ticks.font = {};
    scale.ticks.font.family = s.font;
    if (scale.grid) scale.grid.color = s.grid;
    if (scale.border) scale.border.color = s.grid;
    if (typeof scale.title === 'object' && scale.title) scale.title.color = s.text;
  }
  const lg = chart.options.plugins?.legend;
  if (lg) {
    if (!lg.labels) lg.labels = {};
    lg.labels.color = s.text;
    if (!lg.labels.font) lg.labels.font = {};
    lg.labels.font.family = s.font;
  }
  const tt = chart.options.plugins?.tooltip;
  if (tt) {
    tt.titleFont = { ...(tt.titleFont || {}), family: s.font };
    tt.bodyFont  = { ...(tt.bodyFont  || {}), family: s.mono };
    tt.footerFont = { ...(tt.footerFont || {}), family: s.font };
  }
}

function applyTheme(name) {
  if (!THEME_PALETTES[name]) name = 'apple';
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem('dashboard-theme', name); } catch {}
  currentPalette = THEME_PALETTES[name];
  modelColors.clear();

  const s = readThemeStyles();
  Chart.defaults.color = s.text;
  Chart.defaults.borderColor = s.grid;
  Chart.defaults.font.family = s.font;

  for (const chart of [dailyChart, modelChart, projectChart, realtimeChart, cdfChart, kdeChart]) {
    applyChartTheme(chart, s);
  }

  // Repaint datasets so model colors / accents pick up the new palette.
  const cached = dashboardCache.get(state.range)?.data;
  if (cached) {
    updateDaily(cached.by_day);
    updateModel(cached.by_model);
    updateProject(cached.by_project);
    updateDetail(cached.detail);
  } else {
    [dailyChart, modelChart, projectChart].forEach(c => c?.update('none'));
  }
  if (realtimeChart && realtimeChart.data.labels.length) {
    const fakeRows = realtimeChart.data.labels.map((b, i) => ({
      bucket: b, tokens: realtimeChart.data.datasets[0].data[i],
    }));
    updateRealtime(fakeRows);
  } else {
    realtimeChart?.update('none');
  }
  // Repaint efficiency distribution (CDF/KDE) with new accent color.
  if (effState.data) renderEffDistribution();
}

// Bootstrap: apply saved theme synchronously BEFORE any chart is built.
const savedTheme = (() => {
  try { return localStorage.getItem('dashboard-theme'); } catch { return null; }
})();
if (savedTheme && THEME_PALETTES[savedTheme]) {
  $('#theme').value = savedTheme;
  document.documentElement.dataset.theme = savedTheme;
  currentPalette = THEME_PALETTES[savedTheme];
}
{
  const s = readThemeStyles();
  Chart.defaults.color = s.text;
  Chart.defaults.borderColor = s.grid;
  Chart.defaults.font.family = s.font;
}

$('#theme').addEventListener('change', (e) => applyTheme(e.target.value));

$('#realtime-interval').addEventListener('change', (e) => {
  const opt = e.target.selectedOptions[0];
  state.rtBucketSec = Number(e.target.value);
  state.rtLabel = opt.textContent.trim();
  refreshRealtime();
});

// Recompute window on window-resize so the chart re-fills as it widens/shrinks.
let rtResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(rtResizeTimer);
  rtResizeTimer = setTimeout(refreshRealtime, 200);
});

// ---- chart update helpers (in-place, no destroy → no flicker) ----
// Draws the stacked total above each bar. Registered only on the daily chart.
const barTotalLabelsPlugin = {
  id: 'barTotalLabels',
  afterDatasetsDraw(chart) {
    const { ctx, data, scales } = chart;
    const meta0 = chart.getDatasetMeta(0);
    if (!meta0 || !meta0.data || !data.labels) return;
    ctx.save();
    const cs = getComputedStyle(document.body);
    const monoFont = cs.getPropertyValue('--font-mono').trim() || 'ui-monospace, monospace';
    ctx.fillStyle = cs.color || '#333';
    ctx.font = `600 11px ${monoFont}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    for (let i = 0; i < data.labels.length; i++) {
      let total = 0;
      for (const ds of data.datasets) total += (ds.data[i] || 0);
      if (total <= 0) continue;
      const bar = meta0.data[i];
      if (!bar) continue;
      const yPixel = scales.y.getPixelForValue(total);
      ctx.fillText(fmt(total), bar.x, yPixel - 4);
    }
    ctx.restore();
  },
};

function ensureDailyChart() {
  if (dailyChart) return dailyChart;
  dailyChart = new Chart($('#chart-daily'), {
    type: 'bar',
    data: { labels: [], datasets: [] },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false, axis: 'x' },
      layout: { padding: { top: 18 } },  // room for the total labels
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, ticks: { callback: (v) => fmt(v) } },
      },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`,
            footer: (items) => 'Total: ' + fmt(items.reduce((s, it) => s + it.parsed.y, 0)),
          },
        },
      },
    },
    plugins: [barTotalLabelsPlugin],
  });
  return dailyChart;
}

function ensureModelChart() {
  if (modelChart) return modelChart;
  modelChart = new Chart($('#chart-model'), {
    type: 'doughnut',
    data: { labels: [], datasets: [{ data: [] }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { position: 'bottom' },
        tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${fmt(ctx.parsed)}` } },
      },
    },
  });
  return modelChart;
}

function ensureProjectChart() {
  if (projectChart) return projectChart;
  projectChart = new Chart($('#chart-project'), {
    type: 'bar',
    data: { labels: [], datasets: [{ label: 'tokens', data: [] }] },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false, axis: 'y' },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => ` ${fmt(ctx.parsed.x)}` } },
      },
      scales: { x: { ticks: { callback: (v) => fmt(v) } } },
    },
  });
  return projectChart;
}

function ensureRealtimeChart() {
  if (realtimeChart) return realtimeChart;
  realtimeChart = new Chart($('#chart-realtime'), {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'tokens/bucket', data: [], fill: true, tension: 0.25 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false, axis: 'x' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => formatRtTooltipTitle(items[0]?.label),
            label: (ctx) => `tokens: ${fmt(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { ticks: { autoSkip: true, maxRotation: 0, callback: rtTickFormatter } },
        y: { ticks: { callback: (v) => fmt(v) } },
      },
    },
  });
  return realtimeChart;
}

// Labels are ISO strings (`YYYY-MM-DDTHH:MM`). Ticks show HH:MM;
// for multi-day windows we include MM-DD. Tooltip title shows the full datetime.
function rtTickFormatter(value, index, ticks) {
  const iso = this.getLabelForValue(value);
  if (!iso) return '';
  const d = new Date(iso);
  const hm = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  if (state.rtBucketSec >= 1800) {
    const md = `${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    return `${md} ${hm}`;
  }
  return hm;
}

function formatRtTooltipTitle(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} `
       + `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ---- panel updaters (reuse chart instances) ----
function updateSummary(s) {
  $('#card-total').textContent   = fmt(s.total);
  $('#card-billing').textContent = fmt(s.billing_equiv);
  $('#card-usd').textContent     = '$' + (s.est_usd ?? 0).toFixed(2);
  $('#card-rate').textContent    = fmtInt(s.rate_per_min);
}

function updateDaily(rows) {
  const chart = ensureDailyChart();
  const models = new Set();
  rows.forEach(r => Object.keys(r.per_model).forEach(m => models.add(m)));
  const modelList = [...models];
  const labels = rows.map(r => r.date);

  const existing = new Map(chart.data.datasets.map(d => [d.label, d]));
  chart.data.labels = labels;
  chart.data.datasets = modelList.map(m => {
    const data = rows.map(r => r.per_model[m] || 0);
    const c = colorForModel(m);
    const old = existing.get(m);
    if (old) {
      old.data = data;
      old.backgroundColor = c;
      return old;
    }
    return {
      label: m, data,
      backgroundColor: c,
      borderWidth: 0,
      borderRadius: 2,
      borderSkipped: false,
    };
  });
  chart.update('none');
}

function updateModel(rows) {
  const chart = ensureModelChart();
  chart.data.labels = rows.map(r => r.model);
  chart.data.datasets[0].data = rows.map(r => r.total);
  chart.data.datasets[0].backgroundColor = rows.map(r => colorForModel(r.model));
  chart.update('none');
}

function updateProject(rows) {
  const chart = ensureProjectChart();
  chart.data.labels = rows.map(r => r.project);
  chart.data.datasets[0].data = rows.map(r => r.total);
  chart.data.datasets[0].backgroundColor = chartAccent();
  chart.update('none');
}

function updateRealtime(rows) {
  const chart = ensureRealtimeChart();
  const c = chartAccent();
  chart.data.labels = rows.map(r => r.bucket);
  chart.data.datasets[0].data = rows.map(r => r.tokens);
  chart.data.datasets[0].borderColor = c;
  chart.data.datasets[0].backgroundColor = withAlpha(c, 0.22);
  chart.update('none');
}

function setUpdatedAt(iso) {
  if (!iso) return;
  const d = new Date(iso);
  const hh = pad2(d.getHours()), mm = pad2(d.getMinutes()), ss = pad2(d.getSeconds());
  $('#updated-at').textContent = `updated ${hh}:${mm}:${ss}`;
}

// ---- efficiency panel ----
const effState = { mode: 'raw', data: null };  // mode: 'raw' | 'bill'
let cdfChart, kdeChart;

// Gaussian KDE on a uniform grid. Bandwidth via Silverman's rule of thumb
// with an IQR-based scale fallback (more robust to long right tails).
function computeKDE(values, grid) {
  if (!values.length || !grid.length) return grid.map(() => 0);
  const n = values.length;
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
  const std = Math.sqrt(variance);
  // values is sorted (server-side), so reuse for IQR
  const q1 = values[Math.floor(n * 0.25)];
  const q3 = values[Math.floor(n * 0.75)];
  const iqrScale = (q3 - q1) / 1.34;
  const sigma = Math.min(std || iqrScale, iqrScale || std) || 1;
  const h = 1.06 * sigma * Math.pow(n, -1 / 5);
  if (h <= 0) return grid.map(() => 0);
  const norm = 1 / (n * h * Math.sqrt(2 * Math.PI));
  return grid.map(x => {
    let sum = 0;
    for (let i = 0; i < n; i++) {
      const u = (x - values[i]) / h;
      sum += Math.exp(-0.5 * u * u);
    }
    return sum * norm;
  });
}

// Build CDF points: each sorted value at empirical probability i/n.
// Prepend (0, 0) so the line starts at the origin.
function buildCDF(sortedValues) {
  if (!sortedValues.length) return [];
  const n = sortedValues.length;
  const pts = [{ x: 0, y: 0 }];
  for (let i = 0; i < n; i++) pts.push({ x: sortedValues[i], y: (i + 1) / n });
  return pts;
}

// Grid spans [0, p99] of values (clip to keep KDE peak visible on screen).
function makeGrid(sortedValues, npoints = 200) {
  if (!sortedValues.length) return [];
  const p99 = sortedValues[Math.min(sortedValues.length - 1, Math.floor(sortedValues.length * 0.99))];
  const top = Math.max(p99, sortedValues[sortedValues.length - 1] * 0.5, 1);
  const step = top / (npoints - 1);
  return Array.from({ length: npoints }, (_, i) => i * step);
}

function ensureCdfChart() {
  if (cdfChart) return cdfChart;
  cdfChart = new Chart($('#chart-eff-cdf'), {
    type: 'line',
    data: { datasets: [{ data: [], borderWidth: 2, fill: false, tension: 0, stepped: false }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      parsing: false,
      interaction: { mode: 'nearest', intersect: false, axis: 'x' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => items[0] ? fmt(items[0].parsed.x) + '/h' : '',
            label: ctx => `累积概率: ${(ctx.parsed.y * 100).toFixed(1)}%`,
          },
        },
      },
      scales: {
        x: {
          type: 'linear', min: 0,
          title: { display: true, text: 'tokens / hour' },
          ticks: { callback: v => fmt(v) },
        },
        y: {
          min: 0, max: 1,
          title: { display: true, text: '累积概率 P(X ≤ x)' },
          ticks: { callback: v => Math.round(v * 100) + '%' },
        },
      },
      elements: { point: { radius: 0 } },
    },
  });
  return cdfChart;
}

function ensureKdeChart() {
  if (kdeChart) return kdeChart;
  kdeChart = new Chart($('#chart-eff-kde'), {
    type: 'line',
    data: { datasets: [{ data: [], borderWidth: 2, fill: true, tension: 0.25 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      parsing: false,
      interaction: { mode: 'nearest', intersect: false, axis: 'x' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => items[0] ? fmt(items[0].parsed.x) + '/h' : '',
            label: ctx => `相对密度: ${ctx.parsed.y.toFixed(3)}`,
          },
        },
      },
      scales: {
        x: {
          type: 'linear', min: 0,
          title: { display: true, text: 'tokens / hour' },
          ticks: { callback: v => fmt(v) },
        },
        y: {
          min: 0, max: 1.05,
          title: { display: true, text: '相对密度 (峰值=1)' },
          ticks: { callback: v => v.toFixed(2) },
        },
      },
      elements: { point: { radius: 0 } },
    },
  });
  return kdeChart;
}

function renderEffDistribution() {
  const sd = effState.data?.slot_distribution;
  if (!sd) return;
  const values = effState.mode === 'bill' ? sd.bill_rates_sorted : sd.raw_rates_sorted;
  const modeLabel = effState.mode === 'bill' ? 'billing-equiv' : 'raw';
  const cdfLbl = $('#eff-cdf-mode-label'); if (cdfLbl) cdfLbl.textContent = modeLabel;
  const kdeLbl = $('#eff-kde-mode-label'); if (kdeLbl) kdeLbl.textContent = modeLabel;

  const cdfPoints = buildCDF(values);
  const grid = makeGrid(values);
  const density = computeKDE(values, grid);
  const peak = density.reduce((m, v) => v > m ? v : m, 0) || 1;
  const kdePoints = grid.map((x, i) => ({ x, y: density[i] / peak }));

  const accent = chartAccent();
  const cdf = ensureCdfChart();
  cdf.data.datasets[0].data = cdfPoints;
  cdf.data.datasets[0].borderColor = accent;
  cdf.data.datasets[0].backgroundColor = withAlpha(accent, 0.15);
  cdf.update('none');

  const kde = ensureKdeChart();
  kde.data.datasets[0].data = kdePoints;
  kde.data.datasets[0].borderColor = accent;
  kde.data.datasets[0].backgroundColor = withAlpha(accent, 0.22);
  kde.update('none');
}

// Toggle: raw / billing-equiv
document.querySelectorAll('.eff-dist-toggle button').forEach(btn => {
  btn.addEventListener('click', () => {
    const mode = btn.dataset.effMode;
    if (mode === effState.mode) return;
    effState.mode = mode;
    document.querySelectorAll('.eff-dist-toggle button').forEach(b =>
      b.classList.toggle('active', b.dataset.effMode === mode));
    renderEffDistribution();
  });
});

function renderEffPerDay(rows) {
  const nonzero = rows.filter(r => r.active_min > 0);
  const maxRate = Math.max(0, ...nonzero.map(r => r.rate_per_hr));
  document.querySelector('#eff-perday tbody').innerHTML = nonzero.map(r => {
    const hrs = r.active_min / 60;
    const isPeak = maxRate > 0 && r.rate_per_hr === maxRate;
    return `<tr class="${isPeak ? 'peak' : ''}">
      <td>${escapeHtml(r.date)}</td>
      <td class="num">${hrs.toFixed(1)}h</td>
      <td class="num">${fmt(r.tokens)}</td>
      <td class="num">${fmt(r.billing_equiv)}</td>
      <td class="num">${fmt(r.rate_per_hr)}/h</td>
    </tr>`;
  }).join('');
}

function updateEfficiency(data) {
  effState.data = data;
  const s = data.summary;
  $('#eff-active-hours').textContent = s.active_hours.toFixed(1) + 'h';
  $('#eff-active-pct').textContent   = (s.active_pct * 100).toFixed(1) + '%';
  $('#eff-rate-raw').textContent     = fmt(s.avg_rate_raw_per_hr) + '/h';
  $('#eff-rate-bill').textContent    = fmt(s.avg_rate_bill_per_hr) + '/h';

  const sd = data.slot_distribution;
  $('#eff-slot-meta').innerHTML =
    `<b>${fmtInt(sd.active_slots)}</b> / ${fmtInt(sd.total_slots)} 个 30 分钟槽有活动`
    + ` (<b>${(sd.active_slot_pct * 100).toFixed(1)}%</b> 占用率) &nbsp;·&nbsp; `
    + `中位 <b>${fmt(sd.raw_per_hr.median)}/h</b> (raw) / <b>${fmt(sd.bill_per_hr.median)}/h</b> (bill)`;

  renderEffDistribution();
  renderEffPerDay(data.per_day);

  $('#efficiency-range-label').textContent = '— ' + (data.range.label || state.range);
}

async function refreshEfficiency({ force = false } = {}) {
  const range = state.range;
  try {
    const data = await fetchEfficiency(range, { force });
    if (range !== state.range) return;
    updateEfficiency(data);
  } catch (e) {
    console.error('efficiency fetch failed', e);
  }
}

// ---- detail table ----
const detailState = { rows: [], sortKey: 'total', sortDir: -1, groupBy: 'none', filter: '' };

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, c =>
  ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));

function updateDetail(rows) {
  detailState.rows = rows;
  drawDetail();
}

function _cmpRows(a, b) {
  const { sortKey, sortDir } = detailState;
  const av = a[sortKey], bv = b[sortKey];
  if (typeof av === 'string') return sortDir * av.localeCompare(bv);
  return sortDir * ((av ?? 0) - (bv ?? 0));
}

function _rowCells(r) {
  return `<td>${escapeHtml(r.model)}</td><td>${escapeHtml(r.project)}</td>
    <td>${fmtInt(r.count)}</td><td>${fmtInt(r.in)}</td>
    <td>${fmtInt(r.out)}</td><td>${fmtInt(r.cr)}</td>
    <td>${fmtInt(r.cc)}</td><td>${fmtInt(r.total)}</td>`;
}

function _aggregate(rows) {
  const a = { count: 0, in: 0, out: 0, cr: 0, cc: 0, total: 0 };
  for (const r of rows) {
    a.count += r.count; a.in += r.in; a.out += r.out;
    a.cr += r.cr; a.cc += r.cc; a.total += r.total;
  }
  return a;
}

function drawDetail() {
  const { rows, sortKey, groupBy, filter } = detailState;
  const f = filter.trim().toLowerCase();
  const filtered = f
    ? rows.filter(r => r.model.toLowerCase().includes(f) || r.project.toLowerCase().includes(f))
    : rows;

  $('#detail-count').textContent = f || groupBy !== 'none'
    ? `${filtered.length} / ${rows.length} rows`
    : `${rows.length} rows`;

  const tbody = document.querySelector('#detail tbody');

  if (groupBy === 'none') {
    const sorted = [...filtered].sort(_cmpRows);
    tbody.innerHTML = sorted.map(r => `<tr>${_rowCells(r)}</tr>`).join('');
  } else {
    const groups = new Map();
    for (const r of filtered) {
      const k = r[groupBy];
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(r);
    }
    const ordered = [...groups.entries()]
      .map(([name, members]) => ({ name, members, agg: _aggregate(members) }))
      .sort((a, b) => b.agg.total - a.agg.total);

    tbody.innerHTML = ordered.map(({ name, members, agg }) => {
      const isModel = groupBy === 'model';
      const modelCell   = isModel ? escapeHtml(name) : `<em>(${members.length} models)</em>`;
      const projectCell = isModel ? `<em>(${members.length} projects)</em>` : escapeHtml(name);
      const header = `<tr class="group-header">
        <td>${modelCell}</td><td>${projectCell}</td>
        <td>${fmtInt(agg.count)}</td><td>${fmtInt(agg.in)}</td>
        <td>${fmtInt(agg.out)}</td><td>${fmtInt(agg.cr)}</td>
        <td>${fmtInt(agg.cc)}</td><td>${fmtInt(agg.total)}</td>
      </tr>`;
      const sub = [...members].sort(_cmpRows)
        .map(r => `<tr class="grouped">${_rowCells(r)}</tr>`).join('');
      return header + sub;
    }).join('');
  }

  document.querySelectorAll('#detail th').forEach(th => {
    th.classList.toggle('sorted', th.dataset.sort === sortKey);
  });
}

document.querySelectorAll('#detail th').forEach(th => {
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (detailState.sortKey === key) detailState.sortDir *= -1;
    else {
      detailState.sortKey = key;
      detailState.sortDir = (key === 'model' || key === 'project') ? 1 : -1;
    }
    drawDetail();
  });
});

$('#detail-group').addEventListener('change', (e) => {
  detailState.groupBy = e.target.value;
  drawDetail();
});

$('#detail-filter').addEventListener('input', (e) => {
  detailState.filter = e.target.value;
  drawDetail();
});

// ---- orchestration ----
async function refreshAll({ force = false } = {}) {
  const range = state.range;
  // Kick off efficiency fetch in parallel — it renders independently and does
  // its own range-race check, so we don't need to await its result here.
  refreshEfficiency({ force });
  const data = await fetchDashboard(range, { force });
  // Guard against races: if the user switched range while this was in flight,
  // drop the result.
  if (range !== state.range) return;
  updateSummary(data.summary);
  updateDaily(data.by_day);
  updateModel(data.by_model);
  updateProject(data.by_project);
  updateDetail(data.detail);
  setUpdatedAt(data.generated_at);
}

// Window auto-fills the chart: pick as many buckets as fit at minPxPerBucket,
// so a wider browser window shows more history without the user picking a range.
function computeRealtimeWindowMinutes() {
  const canvas = document.getElementById('chart-realtime');
  const w = (canvas && canvas.clientWidth) || 800;
  const minPxPerBucket = 10;
  const buckets = Math.max(20, Math.floor(w / minPxPerBucket));
  return Math.max(1, Math.round(buckets * state.rtBucketSec / 60));
}

function fmtWindowMinutes(min) {
  if (min < 60) return `${min}m`;
  const h = min / 60;
  if (h < 24) return Number.isInteger(h) ? `${h}h` : `${h.toFixed(1)}h`;
  const d = h / 24;
  return Number.isInteger(d) ? `${d}d` : `${d.toFixed(1)}d`;
}

async function refreshRealtime() {
  const windowMin = computeRealtimeWindowMinutes();
  const url = `/api/realtime?bucket=${state.rtBucketSec}&window=${windowMin}m`;
  const res = await fetchJSON(url);
  updateRealtime(res.rows);
  const lbl = $('#realtime-range-label');
  if (lbl) lbl.textContent = `— last ${fmtWindowMinutes(windowMin)}, ${state.rtLabel} buckets (auto 10s)`;
}

function scheduleRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  if (state.intervalSec > 0) {
    refreshTimer = setInterval(() => refreshAll({ force: true }), state.intervalSec * 1000);
  }
}

// Realtime always refreshes every 10s regardless of the main interval.
realtimeTimer = setInterval(refreshRealtime, 10_000);

// ---- patterns panel ----
let cpChart;
const patternsCacheMap = new Map();   // qs -> { data, fetchedAt }
const PATTERNS_TTL_MS = 60_000;
let lastPatternsQS = '';              // remember the most recent successful query string

function patternsParamsFromUI() {
  const range = $('#patterns-range')?.value || '7d';
  const bucket = $('#patterns-bucket')?.value || '1800';
  const params = { bucket };
  if (range === 'custom') {
    const f = $('#patterns-from')?.value || '';
    const t = $('#patterns-to')?.value || '';
    if (f) params.from = f;
    if (t) params.to = t;
  } else {
    // value like "7d" → days=7
    const m = /^(\d+)d$/.exec(range);
    if (m) params.days = m[1];
  }
  return params;
}

function patternsValidateUI() {
  const range = $('#patterns-range')?.value;
  if (range !== 'custom') return null;
  const f = $('#patterns-from')?.value;
  const t = $('#patterns-to')?.value;
  if (!f) return '请选择起始日期';
  if (t && t < f) return '结束日期不能早于起始日期';
  const today = new Date().toISOString().slice(0, 10);
  if (f > today) return '起始日期不能在未来';
  if (t && t > today) return '结束日期不能在未来';
  return null;
}

function patternsLabelFromParams(p) {
  // Build the small subtitle text shown next to "Patterns"
  const bucketSec = Number(p.bucket_sec || 1800);
  const bucketLabel = bucketSec >= 86400
    ? `${bucketSec/86400} 天`
    : bucketSec >= 3600 ? `${bucketSec/3600} 小时` : `${bucketSec/60} 分钟`;
  if (p.from) {
    return `— ${p.from} → ${p.to}（${p.days} 天）· 桶 ${bucketLabel}`;
  }
  return `— 过去 ${p.days} 天 · 桶 ${bucketLabel}`;
}

async function fetchPatterns(force = false, params = null) {
  // Build query string deterministically (sorted keys) for stable cache.
  const qs = params
    ? '?' + Object.keys(params).sort().map(k => k + '=' + encodeURIComponent(params[k])).join('&')
    : '';
  const cached = patternsCacheMap.get(qs);
  if (!force && cached && (Date.now() - cached.fetchedAt) < PATTERNS_TTL_MS) {
    return cached.data;
  }
  const data = await fetchJSON('/api/patterns' + qs);
  patternsCacheMap.set(qs, { data, fetchedAt: Date.now() });
  lastPatternsQS = qs;
  return data;
}

async function refreshPatterns(params = null) {
  const panel = $('#patterns-panel');
  const applyBtn = $('#patterns-apply');
  panel?.classList.add('is-loading');
  let prevApplyText = null;
  if (applyBtn) {
    prevApplyText = applyBtn.textContent;
    applyBtn.disabled = true;
    applyBtn.textContent = '⏳ 分析中…';
  }
  try {
    const data = await fetchPatterns(true, params);
    renderPatterns(data);
    const lbl = $('#patterns-summary-label');
    if (lbl && data?.params) lbl.textContent = patternsLabelFromParams(data.params);
  } catch (e) {
    console.warn('patterns load failed:', e);
    showPatternsError(e?.message || String(e));
  } finally {
    panel?.classList.remove('is-loading');
    if (applyBtn) {
      applyBtn.disabled = false;
      applyBtn.textContent = prevApplyText || '应用';
    }
  }
}

function showPatternsError(msg) {
  const el = $('#patterns-error');
  if (!el) return;
  el.textContent = msg || '';
  el.hidden = !msg;
}

// Restore saved selections (range/bucket/from/to) from localStorage.
function patternsRestoreUI() {
  try {
    const r = localStorage.getItem('patterns-range');
    const b = localStorage.getItem('patterns-bucket');
    const f = localStorage.getItem('patterns-from');
    const t = localStorage.getItem('patterns-to');
    if (r && $('#patterns-range')) $('#patterns-range').value = r;
    if (b && $('#patterns-bucket')) $('#patterns-bucket').value = b;
    if (f && $('#patterns-from')) $('#patterns-from').value = f;
    if (t && $('#patterns-to')) $('#patterns-to').value = t;
    // Show/hide custom range row
    const cr = $('#patterns-custom-range');
    if (cr) cr.hidden = ($('#patterns-range')?.value !== 'custom');
  } catch {}
}

function patternsPersistUI() {
  try {
    const r = $('#patterns-range')?.value;
    const b = $('#patterns-bucket')?.value;
    const f = $('#patterns-from')?.value;
    const t = $('#patterns-to')?.value;
    if (r) localStorage.setItem('patterns-range', r);
    if (b) localStorage.setItem('patterns-bucket', b);
    if (f) localStorage.setItem('patterns-from', f); else localStorage.removeItem('patterns-from');
    if (t) localStorage.setItem('patterns-to', t); else localStorage.removeItem('patterns-to');
  } catch {}
}

// Toolbar wiring
$('#patterns-range')?.addEventListener('change', () => {
  const cr = $('#patterns-custom-range');
  if (cr) cr.hidden = ($('#patterns-range').value !== 'custom');
  showPatternsError(null);
  // Pre-fill from/to defaults the first time custom is selected
  if ($('#patterns-range').value === 'custom') {
    const f = $('#patterns-from'); const t = $('#patterns-to');
    if (f && !f.value) {
      const d = new Date(); d.setDate(d.getDate() - 30);
      f.value = d.toISOString().slice(0, 10);
    }
    if (t && !t.value) t.value = new Date().toISOString().slice(0, 10);
  }
});

$('#patterns-apply')?.addEventListener('click', () => {
  const err = patternsValidateUI();
  if (err) { showPatternsError(err); return; }
  showPatternsError(null);
  patternsPersistUI();
  refreshPatterns(patternsParamsFromUI());
});

function fmtPct(x, digits = 1) { return (x * 100).toFixed(digits) + '%'; }
function fmtSigned(x, digits = 2) {
  const s = Number(x).toFixed(digits);
  return (x >= 0 ? '+' : '') + s;
}
function fmtAcf(x) {
  // The ACF report uses 3-digit precision and a unicode minus for negatives.
  const s = Math.abs(x).toFixed(3);
  return (x < 0 ? '−' : '+') + s;
}
function fmtHours(h, digits = 1) {
  if (!Number.isFinite(h)) return '—';
  return h.toFixed(digits) + 'h';
}

function computeProfile(data) {
  const labels = [];
  const bullets = [];
  const hours = data.seasonal?.hour || [];
  const burst = data.features?.burstiness?.goh_barabasi_B ?? 0;
  const acf = data.features?.acf || [];
  const acfBy = new Map(acf.map(a => [a.lag_label, a.value]));
  const lag12 = acfBy.get('12h');
  const lag24 = acfBy.get('24h');
  const lag7d = acfBy.get('7d');
  const stick = data.markov_2?.stickiness ?? 0;
  const PHH = data.markov_3?.P?.H?.H ?? 0;
  const PLL = data.markov_3?.P?.L?.L ?? 0;

  // Peak hour
  let peakHour = null, peakMean = -Infinity;
  for (const h of hours) {
    if (h.mean > peakMean) { peakMean = h.mean; peakHour = h.hour; }
  }
  if (peakHour != null && peakHour >= 17 && peakHour <= 21) labels.push('晚高峰');
  else if (peakHour != null && peakHour >= 9 && peakHour <= 12) labels.push('早高峰');
  else if (peakHour != null && peakHour >= 13 && peakHour <= 16) labels.push('下午高峰');

  if (burst > 0.15) labels.push('突发型');
  else if (burst < -0.05) labels.push('均匀型');

  if ((lag12 != null && lag12 < -0.1) || (lag24 != null && lag24 > 0.1)) labels.push('强24h周期');
  if (lag7d != null && Math.abs(lag7d) < 0.1) labels.push('无周周期');
  else if (lag7d != null && lag7d > 0.1) labels.push('强周周期');

  if (stick > 0.3) labels.push('高黏性');
  if (PHH > PLL) labels.push('High沉浸');

  const summary = labels.length ? labels.join(' · ') : '数据不足以判断画像';

  // Bullets — pull a handful of headline numbers
  const runs = data.features?.runs;
  const conc = data.features?.concentration;
  if (runs?.active && runs?.idle) {
    bullets.push(`节律：活跃中位 <b>${fmtHours(runs.active.median_h)}</b> / 静默中位 <b>${fmtHours(runs.idle.median_h)}</b>`);
  }
  bullets.push(`突发指数 B = <b>${fmtSigned(burst)}</b>`);
  if (conc?.gini != null) {
    bullets.push(`Gini = <b>${conc.gini.toFixed(3)}</b>，top5% 占 <b>${conc.top5pct.toFixed(1)}%</b>`);
  }
  if (peakHour != null) {
    bullets.push(`峰值时段：<b>${String(peakHour).padStart(2,'0')}:00</b>（均值 ${fmt(peakMean)}）`);
  }
  if (data.markov_3?.dwell_h?.H != null) {
    bullets.push(`High 平均停留 <b>${fmtHours(data.markov_3.dwell_h.H)}</b>，自留率 <b>${fmtPct(PHH)}</b>`);
  }

  return { summary, bullets };
}

function renderProfile(data) {
  const { summary, bullets } = computeProfile(data);
  $('#profile-summary').textContent = summary;
  $('#profile-bullets').innerHTML = bullets.map(b => `<li>${b}</li>`).join('');
}

function renderKpis(data) {
  const gini = data.features?.concentration?.gini ?? 0;
  const burst = data.features?.burstiness?.goh_barabasi_B ?? 0;
  const top5 = data.features?.concentration?.top5pct ?? 0;
  const stick = data.markov_2?.stickiness ?? 0;

  const setKpi = (id, text, signed = false, value = 0) => {
    const el = $(id);
    el.textContent = text;
    el.classList.remove('pos', 'neg');
    if (signed) el.classList.add(value >= 0 ? 'pos' : 'neg');
  };
  setKpi('#kpi-gini', gini.toFixed(2));
  setKpi('#kpi-burst', fmtSigned(burst, 2), true, burst);
  setKpi('#kpi-top5', top5.toFixed(1) + '%');
  setKpi('#kpi-stick', fmtSigned(stick, 2), true, stick);
}

// Render the ACF horizontal bars. Bars are anchored to the center axis;
// negative values grow left, positive grow right.
function renderAcf(data) {
  const acf = data.features?.acf || [];
  const container = $('#chart-acf');
  if (!acf.length) { container.innerHTML = ''; return; }
  const max = Math.max(...acf.map(a => Math.abs(a.value)), 0.05);
  container.innerHTML = acf.map(a => {
    const v = a.value;
    const widthPct = (Math.abs(v) / max) * 50;          // half-width is 50% of the track
    const left = v >= 0 ? 50 : (50 - widthPct);
    const cls = v >= 0 ? '' : 'neg';
    const valCls = v >= 0 ? '' : 'neg';
    return `<div class="bar-row acf">
      <span class="label">lag ${escapeHtml(a.lag_label)}</span>
      <span class="val ${valCls}">${fmtAcf(v)}</span>
      <div class="bar-track">
        <div class="bar ${cls}" style="left:${left}%; width:${widthPct}%;"></div>
      </div>
    </div>`;
  }).join('');
}

function renderHour(data) {
  const hours = data.seasonal?.hour || [];
  const container = $('#chart-hour');
  if (!hours.length) { container.innerHTML = ''; return; }
  const max = Math.max(...hours.map(h => h.mean), 1);
  let peakHour = 0, peakMean = -Infinity;
  for (const h of hours) {
    if (h.mean > peakMean) { peakMean = h.mean; peakHour = h.hour; }
  }
  container.innerHTML = hours.map(h => {
    const widthPct = (h.mean / max) * 100;
    const isPeak = h.hour === peakHour && h.mean > 0;
    const isZero = h.mean === 0;
    const barCls = isPeak ? 'peak' : (isZero ? 'dim' : '');
    const valCls = isPeak ? 'peak' : '';
    return `<div class="bar-row">
      <span class="label">${String(h.hour).padStart(2,'0')}:00</span>
      <span class="val ${valCls}">${fmt(h.mean)}</span>
      <div class="bar-track">
        <div class="bar ${barCls}" style="width:${Math.max(widthPct, isZero ? 1 : 2)}%;"></div>
      </div>
    </div>`;
  }).join('');
}

function renderDow(data) {
  const dow = data.seasonal?.dow || [];
  const container = $('#chart-dow');
  if (!dow.length) { container.innerHTML = ''; return; }
  const cnNames = { Mon: '周一', Tue: '周二', Wed: '周三', Thu: '周四', Fri: '周五', Sat: '周六', Sun: '周日' };
  const max = Math.max(...dow.map(d => d.mean), 1);
  let peakIdx = 0, peakMean = -Infinity;
  dow.forEach((d, i) => { if (d.mean > peakMean) { peakMean = d.mean; peakIdx = i; } });
  container.innerHTML = dow.map((d, i) => {
    const widthPct = (d.mean / max) * 100;
    const isPeak = i === peakIdx && d.mean > 0;
    const cls = isPeak ? 'peak' : '';
    const valCls = isPeak ? 'peak' : '';
    return `<div class="bar-row">
      <span class="label">${cnNames[d.name] || d.name}</span>
      <span class="val ${valCls}">${fmt(d.mean)}</span>
      <div class="bar-track">
        <div class="bar ${cls}" style="width:${Math.max(widthPct, 2)}%;"></div>
      </div>
    </div>`;
  }).join('');
}

// Vertical bar chart of the 30-day daily totals with a vertical CP marker.
const cpMarkerPlugin = {
  id: 'cpMarker',
  afterDatasetsDraw(chart, _args, opts) {
    const dates = opts?.dates || [];
    if (!dates.length) return;
    const { ctx, scales, chartArea } = chart;
    const accent = chartAccent();
    ctx.save();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = accent;
    ctx.setLineDash([4, 3]);
    const cs = getComputedStyle(document.body);
    ctx.font = `600 10px ${cs.getPropertyValue('--font-mono').trim() || 'monospace'}`;
    ctx.fillStyle = accent;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    for (const d of dates) {
      const x = scales.x.getPixelForValue(d);
      if (x == null || Number.isNaN(x)) continue;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.fillText('CP ' + d.slice(5), x + 3, chartArea.top + 2);
    }
    ctx.restore();
  },
};

function ensureCpChart() {
  if (cpChart) return cpChart;
  const accent = chartAccent();
  cpChart = new Chart($('#chart-cp'), {
    type: 'bar',
    data: { labels: [], datasets: [{
      label: 'tokens/day',
      data: [],
      backgroundColor: withAlpha(accent, 0.55),
      borderWidth: 0,
      borderRadius: 1,
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false, axis: 'x' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => items[0]?.label || '',
            label: (ctx) => 'tokens: ' + fmt(ctx.parsed.y),
          },
        },
        cpMarker: { dates: [] },
      },
      scales: {
        x: { grid: { display: false }, ticks: { autoSkip: true, maxRotation: 0 } },
        y: { ticks: { callback: (v) => fmt(v) } },
      },
    },
    plugins: [cpMarkerPlugin],
  });
  return cpChart;
}

function renderCp(data) {
  const totals = data.changepoint?.daily_totals || [];
  const cps = (data.changepoint?.changepoints || []).map(cp => cp.date);
  const chart = ensureCpChart();
  const accent = chartAccent();
  chart.data.labels = totals.map(t => t.date);
  chart.data.datasets[0].data = totals.map(t => t.total);
  chart.data.datasets[0].backgroundColor = totals.map(t =>
    cps.includes(t.date) ? accent : withAlpha(accent, 0.45));
  chart.options.plugins.cpMarker.dates = cps;
  chart.update('none');

  const ann = $('#cp-annotations');
  if (!data.changepoint?.changepoints?.length) {
    ann.textContent = '未检测到显著变化点。';
  } else {
    ann.innerHTML = data.changepoint.changepoints.map(cp =>
      `变化点 <b>${cp.date}</b>：前段均值 ${fmt(cp.before_mean)}/天 → 后段 ${fmt(cp.after_mean)}/天 (<b>${fmtSigned(cp.pct_change, 0)}%</b>)`
    ).join('<br>');
  }
}

function renderMarkov(data) {
  const m = data.markov_3;
  if (!m) return;
  const order = ['I', 'L', 'H'];
  const tbody = document.querySelector('#markov-3 tbody');
  tbody.innerHTML = order.map(from => {
    const cells = order.map(to => {
      const v = m.P?.[from]?.[to] ?? 0;
      const isSelf = from === to;
      return `<td class="num${isSelf ? ' self' : ''}">${v.toFixed(2)}</td>`;
    }).join('');
    return `<tr><th>${from}</th>${cells}</tr>`;
  }).join('');

  const pi = m.stationary || {};
  const dw = m.dwell_h || {};
  const thr = m.threshold ?? 0;
  const help = (title, body) => `<span class="help"><span class="help-icon" tabindex="0">?</span><span class="help-tip"><strong>${title}</strong>${body}</span></span>`;
  const tipPi = help('稳态分布 π', '马尔可夫链长期稳定下来后，在每个状态的<b>时间占比</b>。<br><br>例如 π(H)=25% 意味着长期有 25% 的 30min 桶处于 High 状态。');
  const tipDw = help('平均停留时长', '进入某个状态后，期望连续待多久（以小时计）。<br><br>公式：<code>1 / (1 − P[s][s])</code>×bucket（几何分布的均值）。<br><br>P[H→H]=0.6 意味着平均连续 High 1.25 桶 = 0.6 h。');
  const tipThr = help('High / Low 阈值', '区分"低强度"和"高强度"的 token 数。<br><br>默认是<b>活跃桶 token 数的中位数</b>，自动根据你的数据调整。<br><br>桶 token 数 0 → I，&lt; 阈值 → L，≥ 阈值 → H。');
  $('#markov-meta').innerHTML = `
    <div>稳态 π${tipPi}：I=<b>${fmtPct(pi.I ?? 0)}</b> · L=<b>${fmtPct(pi.L ?? 0)}</b> · H=<b>${fmtPct(pi.H ?? 0)}</b></div>
    <div>平均停留${tipDw}：I=<b>${fmtHours(dw.I)}</b> · L=<b>${fmtHours(dw.L)}</b> · H=<b>${fmtHours(dw.H)}</b></div>
    <div>High 阈值${tipThr}：<b>${fmt(thr)}</b> tokens/桶</div>
  `;
}

function renderWorkflow(data) {
  const m = data.markov_3;
  if (!m) return;
  const P = m.P || {};
  const dw = m.dwell_h || {};
  const PIH = P.I?.H ?? 0;
  const PHI = P.H?.I ?? 0;
  const PHH = P.H?.H ?? 0;
  const PLL = P.L?.L ?? 0;
  const PLH = P.L?.H ?? 0;
  const PHL = P.H?.L ?? 0;
  const PII = P.I?.I ?? 0;

  // Pick a flow shape. If H mostly cools to L (PHL > PHI) and ramps from L (PIH small),
  // it's the warmup/cooldown loop; otherwise simpler I↔A.
  let shape;
  if (PHL > PHI && PIH < 0.1) {
    shape =
`           ramp-up         cool-down
   Idle ─────────► Low ◄─────────► High
      ▲             ▲                │
      │             │                │
      └─────────────┴────────────────┘
        （慢慢回落，几乎不直接收工）`;
  } else {
    shape =
`   Idle ◄─────► Low ◄─────► High
      （状态切换较自由）`;
  }
  $('#workflow-shape').textContent = shape;

  const sym = Math.abs(PLH - PHL);
  const items = [
    `<b>升档难</b>：P[I→H] = <b>${fmtPct(PIH)}</b>（启动很少猛干，绝大多数从 Low 起步）`,
    `<b>收工慢</b>：P[H→I] = <b>${fmtPct(PHI)}</b>（高强度极少直接收工，先冷却到 Low）`,
    `<b>沉浸度</b>：P[H→H] = <b>${fmtPct(PHH)}</b> ${PHH > PLL ? '>' : '≤'} P[L→L] = <b>${fmtPct(PLL)}</b>（${PHH > PLL ? 'High 比 Low 更沉浸' : 'Low 比 High 更黏'}）`,
    `<b>升降对称性</b>：|P[L→H] − P[H→L]| = <b>${sym.toFixed(2)}</b>（${sym < 0.05 ? '基本对称' : 'L↔H 升降不对称'}）`,
    `<b>Idle 黏性</b>：P[I→I] = <b>${fmtPct(PII)}</b>，平均停留 <b>${fmtHours(dw.I)}</b>`,
  ];
  $('#workflow-patterns').innerHTML = items.map(i => `<li>${i}</li>`).join('');
}

function renderPatterns(data) {
  if (!data) return;
  renderProfile(data);
  renderKpis(data);
  renderAcf(data);
  renderHour(data);
  renderDow(data);
  renderCp(data);
  renderMarkov(data);
  renderWorkflow(data);
}

// Export current patterns as JSON. HTML export is handled by analyze.py --html.
$('#export-patterns-html')?.addEventListener('click', async () => {
  try {
    const data = await fetchPatterns(false, patternsParamsFromUI());
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    a.href = url;
    a.download = `patterns-${stamp}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    console.warn('export failed:', e);
  }
});

// ---- AI interpret modal ----
function renderMarkdown(md) {
  // Tiny markdown renderer (no deps). Order matters.
  let html = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // Code fences ```lang ... ``` → <pre><code>
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_m, _lang, body) => `<pre><code>${body}</code></pre>`);
  // Headings (most specific first)
  html = html.replace(/^#### (.+)$/gm, '<h5>$1</h5>');
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
  // Bold then italic (bold uses **, italic uses single *)
  html = html.replace(/\*\*([^\n*][^*]*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/(^|[^*])\*([^\n*][^*]*?)\*(?!\*)/g, '$1<em>$2</em>');
  // Inline code
  html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  // Lists: collect consecutive "- " lines
  html = html.replace(/(^- .+(?:\n- .+)*)/gm, m => {
    const items = m.split('\n').map(l => '<li>' + l.replace(/^- /, '') + '</li>').join('');
    return '<ul>' + items + '</ul>';
  });
  html = html.replace(/(^\d+\. .+(?:\n\d+\. .+)*)/gm, m => {
    const items = m.split('\n').map(l => '<li>' + l.replace(/^\d+\. /, '') + '</li>').join('');
    return '<ol>' + items + '</ol>';
  });
  // Paragraphs from blank-line-separated chunks (skip if already a block element)
  html = html.split(/\n{2,}/).map(p => {
    const t = p.trim();
    if (!t) return '';
    if (/^<(h\d|ul|ol|pre|blockquote|hr)/.test(t)) return t;
    return '<p>' + t.replace(/\n/g, '<br>') + '</p>';
  }).filter(Boolean).join('\n');
  // Horizontal rule
  html = html.replace(/<p>---+<\/p>/g, '<hr>');
  return html;
}

let interpretInFlight = false;
let interpretLast = null;  // { interpretation, model, elapsed_ms }

function showInterpretSection() {
  const sec = $('#interpret-section');
  if (sec) sec.hidden = false;
}

function setInterpretActions({ copy, regen, dismiss }) {
  const set = (id, on) => { const el = $(id); if (el) el.hidden = !on; };
  set('#interpret-copy', !!copy);
  set('#interpret-regen', !!regen);
  set('#interpret-dismiss', !!dismiss);
}

async function runInterpret() {
  if (interpretInFlight) return;
  const btn = $('#patterns-interpret');
  const body = $('#interpret-body');
  const meta = $('#interpret-meta');
  const params = patternsParamsFromUI();
  const qs = '?' + Object.keys(params).sort().map(k => k + '=' + encodeURIComponent(params[k])).join('&');

  interpretInFlight = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 生成中…'; }
  showInterpretSection();
  setInterpretActions({ copy: false, regen: false, dismiss: true });
  if (body) body.innerHTML = '<p class="muted">⏳ 正在调用本地 claude CLI（通常 10-90 秒），生成完会渲染在此处…</p>';
  if (meta) meta.textContent = '';
  // Smooth-scroll to the section so the user sees something happening
  $('#interpret-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const res = await fetch('/api/interpret' + qs);
    const data = await res.json();
    if (!res.ok || data.error) {
      const msg = data.error || `HTTP ${res.status}`;
      const hint = data.hint ? `<br><small class="muted">${renderMarkdown(data.hint)}</small>` : '';
      if (body) body.innerHTML = `<p class="patterns-error">⚠️ ${msg}</p>${hint}`;
      if (meta) meta.textContent = '';
      setInterpretActions({ copy: false, regen: true, dismiss: true });
    } else {
      interpretLast = data;
      if (body) body.innerHTML = renderMarkdown(data.interpretation || '');
      const stamp = data.computed_at ? ` · 生成于 ${new Date(data.computed_at).toLocaleString()}` : '';
      if (meta) meta.textContent = `${data.model || 'claude'} · ${(data.elapsed_ms/1000).toFixed(1)}s${stamp}`;
      setInterpretActions({ copy: true, regen: true, dismiss: true });
    }
  } catch (e) {
    if (body) body.innerHTML = `<p class="patterns-error">⚠️ 网络错误：${e.message || e}</p>`;
    setInterpretActions({ copy: false, regen: true, dismiss: true });
  } finally {
    interpretInFlight = false;
    if (btn) { btn.disabled = false; btn.textContent = '🤖 AI 解读'; }
  }
}

$('#patterns-interpret')?.addEventListener('click', runInterpret);
$('#interpret-regen')?.addEventListener('click', runInterpret);

$('#interpret-dismiss')?.addEventListener('click', () => {
  const sec = $('#interpret-section');
  const body = $('#interpret-body');
  const meta = $('#interpret-meta');
  if (sec) sec.hidden = true;
  if (body) body.innerHTML = '';
  if (meta) meta.textContent = '';
  interpretLast = null;
});

$('#interpret-copy')?.addEventListener('click', async () => {
  if (!interpretLast?.interpretation) return;
  const btn = $('#interpret-copy');
  try {
    await navigator.clipboard.writeText(interpretLast.interpretation);
    const orig = btn.textContent;
    btn.textContent = '✓ 已复制';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  } catch (e) {
    alert('复制失败：' + e.message);
  }
});

// Bootstrap
patternsRestoreUI();
refreshAll();
refreshRealtime();
refreshPatterns(patternsParamsFromUI());
scheduleRefresh();
