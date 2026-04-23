const MEMBER = document.getElementById('dashboardRoot').dataset.member;

// ── Chart.js global defaults ──────────────────────────────────────────
Chart.defaults.color = getComputedStyle(document.documentElement)
  .getPropertyValue('--text-muted').trim() || '#64748b';
Chart.defaults.borderColor = getComputedStyle(document.documentElement)
  .getPropertyValue('--chart-grid').trim() || 'rgba(148,163,184,0.08)';
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 12;

// ── Custom datalabels plugin for div bar chart ────────────────────────
const divBarLabelPlugin = {
  id: 'divBarLabels',
  afterDraw(chart) {
    if (chart.canvas.id !== 'divBarChart') return;
    const { ctx, data } = chart;
    const dataset = data.datasets[0];
    if (!dataset || !dataset._labels) return;
    const meta = chart.getDatasetMeta(0);
    ctx.save();
    ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    meta.data.forEach((bar, i) => {
      const lbl = dataset._labels[i];
      if (!lbl) return;
      const x = bar.x;
      const y = bar.y - 4;
      ctx.strokeStyle = 'rgba(0,0,0,0.5)';
      ctx.lineWidth = 3;
      ctx.lineJoin = 'round';
      ctx.strokeText(lbl, x, y);
      ctx.fillStyle = '#fff';
      ctx.fillText(lbl, x, y);
    });
    ctx.restore();
  }
};
Chart.register(divBarLabelPlugin);

// ── Global state ─────────────────────────────────────────────────────
let _data = null;
let _charts = {};
let _globalDivision = null;
let _tsView = 'match';
let _hfView = 'match';
let _tsTrend = 0;
let _hfTrend = 0;

// ── Constants ─────────────────────────────────────────────────────────
const ALL_DIVISIONS = [
  'Open', 'Limited', 'Limited 10', 'Production',
  'Revolver', 'Single Stack', 'Carry Optics', 'PCC', 'Limited Optics'
];

// ── Helpers ───────────────────────────────────────────────────────────
function classToColor(cls) {
  const map = { GM: '#eab308', M: '#ef4444', A: '#a855f7', B: '#3b82f6', C: '#22c55e', D: '#6b7280', U: '#94a3b8' };
  return map[(cls || '').toUpperCase()] || '#94a3b8';
}
function classBadge(cls) {
  const c = (cls || '').toUpperCase();
  return `<span class="badge badge-${c.toLowerCase()}">${c || '?'}</span>`;
}
function fmtPct(v) { return v != null ? v.toFixed(2) + '%' : '—'; }
function fmtHF(v)  { return v != null ? v.toFixed(4) : '—'; }
function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); }
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const PALETTE = ['#6366f1','#22c55e','#f59e0b','#ef4444','#a855f7','#06b6d4','#f97316','#84cc16'];

function themeColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    grid: style.getPropertyValue('--chart-grid').trim(),
    text: style.getPropertyValue('--text-muted').trim(),
    bg:   style.getPropertyValue('--bg-card').trim(),
  };
}

function destroyChart(id) {
  if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
}

// ── Trend line: least-squares regression on last N points ─────────────
// Returns full-length array projecting the regression line across all x positions.
function computeTrendLine(yValues, windowSize) {
  if (!windowSize || windowSize <= 0 || yValues.length < 2) return null;
  const n = Math.min(windowSize, yValues.length);
  const startIdx = yValues.length - n;
  const pts = [];
  for (let i = 0; i < n; i++) {
    const v = yValues[startIdx + i];
    if (v != null) pts.push({ xi: i, y: v });
  }
  if (pts.length < 2) return null;
  const N = pts.length;
  const sx  = pts.reduce((a, p) => a + p.xi, 0);
  const sy  = pts.reduce((a, p) => a + p.y, 0);
  const sxy = pts.reduce((a, p) => a + p.xi * p.y, 0);
  const sx2 = pts.reduce((a, p) => a + p.xi * p.xi, 0);
  const denom = N * sx2 - sx * sx;
  if (Math.abs(denom) < 1e-10) return null;
  const m = (N * sxy - sx * sy) / denom;
  const b = (sy - m * sx) / N;
  // Project line across all x positions (local index = global_i - startIdx)
  return yValues.map((_, i) => m * (i - startIdx) + b);
}

// ── Match grouping: group classifier_breakdown by date+match ──────────
function groupByMatch(division) {
  const clsf = (_data.classifier_breakdown || []).filter(c => c.division === division);
  const groups = {};
  clsf.forEach(c => {
    const key = `${c.match_date}||${c.match_name}`;
    if (!groups[key]) {
      groups[key] = { match_name: c.match_name, match_date: c.match_date, pcts: [], hfs: [] };
    }
    if (c.percentage != null) groups[key].pcts.push(c.percentage);
    if (c.hit_factor != null) groups[key].hfs.push(c.hit_factor);
  });
  const avg = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  return Object.values(groups)
    .sort((a, b) => a.match_date.localeCompare(b.match_date))
    .map(g => ({ ...g, avgPct: avg(g.pcts), avgHF: avg(g.hfs), count: g.pcts.length }));
}

// ── Default division: most classifier scores ──────────────────────────
function getDefaultDivision() {
  const clsf = _data.classifier_breakdown || [];
  const divCount = {};
  clsf.forEach(c => { divCount[c.division] = (divCount[c.division] || 0) + 1; });
  const top = Object.entries(divCount).sort((a, b) => b[1] - a[1])[0];
  return top ? top[0] : (Object.keys(_data.time_series || {})[0] || ALL_DIVISIONS[0]);
}

// ── Load dashboard data ───────────────────────────────────────────────
async function loadDashboard() {
  show('pageLoading'); hide('pageError'); hide('dashboardContent');
  try {
    const resp = await fetch(`/api/member/${MEMBER}/dashboard`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    _data = await resp.json();
    _globalDivision = getDefaultDivision();
    renderAll();
    hide('pageLoading');
    show('dashboardContent');
  } catch (err) {
    hide('pageLoading');
    document.getElementById('pageErrorMsg').textContent = err.message || 'Failed to load dashboard data.';
    show('pageError');
  }
}

async function refreshData() {
  try { await fetch(`/api/analyze/${MEMBER}`, { method: 'POST' }); } catch (_) {}
  _data = null;
  loadDashboard();
}

function show(id) { document.getElementById(id).style.display = ''; }
function hide(id) { document.getElementById(id).style.display = 'none'; }

// ── Render all sections ───────────────────────────────────────────────
function renderAll() {
  renderDivBarChart();
  populateGlobalDivFilter();
  renderOverview();
  updateTimeSeriesChart();
  updateHFChart();
  renderClassifierBreakdown();
  renderStats();
}

// ── 1. Avg % per Division bar chart (top) ─────────────────────────────
function renderDivBarChart() {
  const divStats = _data.division_stats || {};
  const labels = ALL_DIVISIONS;
  const pcts   = labels.map(d => {
    const info = divStats[d];
    return info && info.percentage != null ? info.percentage : 0;
  });
  const colors = labels.map(d => {
    const info = divStats[d];
    return classToColor(info ? info.class : 'U');
  });
  const dataLabels = labels.map(d => {
    const info = divStats[d];
    const pct = info && info.percentage != null ? info.percentage : 0;
    const cls = info ? (info.class || 'U').toUpperCase() : 'U';
    return `${pct.toFixed(2)}%(${cls})`;
  });

  const tc = themeColors();
  destroyChart('divBar');

  const ds = {
    label: 'Avg %',
    data: pcts,
    backgroundColor: colors,
    _labels: dataLabels,
  };

  _charts['divBar'] = new Chart(
    document.getElementById('divBarChart').getContext('2d'),
    {
      type: 'bar',
      data: { labels, datasets: [ds] },
      options: {
        responsive: true,
        layout: { padding: { top: 24 } },
        plugins: { legend: { display: false } },
        scales: {
          y: { min: 0, max: 110, grid: { color: tc.grid }, ticks: { callback: v => v + '%' } },
          x: { grid: { display: false } }
        }
      }
    }
  );
}

// ── 2. Global division filter ─────────────────────────────────────────
function populateGlobalDivFilter() {
  const divisions = Object.keys(_data.time_series || {});
  // Include any divisions from classifier_breakdown not in time_series
  const clsfDivs = [...new Set((_data.classifier_breakdown || []).map(c => c.division))];
  const allDivs  = [...new Set([...divisions, ...clsfDivs])];

  const sel = document.getElementById('globalDivisionFilter');
  sel.innerHTML = allDivs.map(d =>
    `<option value="${escapeHtml(d)}"${d === _globalDivision ? ' selected' : ''}>${escapeHtml(d)}</option>`
  ).join('');
}

function onDivisionChange() {
  _globalDivision = document.getElementById('globalDivisionFilter').value;
  updateTimeSeriesChart();
  updateHFChart();
  renderClassifierBreakdown();
  renderStats();
}

// ── 3. Overview ───────────────────────────────────────────────────────
function renderOverview() {
  const ov   = _data.overview;
  const clsf = _data.classifier_breakdown || [];

  const divCount = {};
  clsf.forEach(c => { divCount[c.division] = (divCount[c.division] || 0) + 1; });
  const mostActive = Object.entries(divCount).sort((a, b) => b[1] - a[1])[0];

  const totalMatches = (_data.match_stats || []).length;
  const pcts = clsf.map(c => c.percentage).filter(v => v != null);
  const avgPct = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : null;

  const cards = [
    { label: 'Member',           value: ov.member_number },
    { label: 'Total Classifiers',value: clsf.length },
    { label: 'Total Matches',    value: totalMatches },
    { label: 'Avg % (All)',      value: fmtPct(avgPct) },
    { label: 'Most Active Div',  value: mostActive ? mostActive[0] : '—' },
  ];

  document.getElementById('overviewStats').innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(c.label)}</div>
      <div class="stat-value" style="font-size:1.25rem;">${escapeHtml(c.value)}</div>
    </div>
  `).join('');

  if (ov.last_scraped_at) {
    document.getElementById('lastScraped').textContent =
      'Last scraped: ' + new Date(ov.last_scraped_at).toLocaleString();
  } else {
    document.getElementById('lastScraped').textContent = '';
  }
}

// ── 4. Time Series Chart ─────────────────────────────────────────────
function updateTimeSeriesChart() {
  const dateFrom = document.getElementById('dateFrom').value;
  const dateTo   = document.getElementById('dateTo').value;
  const tc = themeColors();
  destroyChart('timeSeries');

  if (_tsView === 'match') {
    // Match View: group by date+match, avg % per match
    let matches = groupByMatch(_globalDivision)
      .filter(m => m.avgPct != null);
    if (dateFrom) matches = matches.filter(m => m.match_date >= dateFrom);
    if (dateTo)   matches = matches.filter(m => m.match_date <= dateTo);

    const labels = matches.map(m => truncate(m.match_name, 22));
    const yVals  = matches.map(m => m.avgPct);
    const trend  = computeTrendLine(yVals, _tsTrend);

    const datasets = [{
      label: 'Avg % per Match',
      data: yVals,
      borderColor: PALETTE[0],
      backgroundColor: 'rgba(99,102,241,0.08)',
      pointRadius: 4,
      tension: 0.3,
      fill: true,
    }];
    if (trend) {
      datasets.push({
        label: `Trend (last ${_tsTrend})`,
        data: trend,
        borderColor: '#f59e0b',
        borderWidth: 2,
        borderDash: [6, 3],
        pointRadius: 0,
        tension: 0,
      });
    }

    _charts['timeSeries'] = new Chart(
      document.getElementById('timeSeriesChart').getContext('2d'),
      {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: {
            legend: { display: !!trend },
            tooltip: {
              callbacks: {
                title: (items) => {
                  const m = matches[items[0].dataIndex];
                  return m ? m.match_name : items[0].label;
                },
                afterTitle: (items) => {
                  const m = matches[items[0].dataIndex];
                  return m ? m.match_date : '';
                },
                label: (item) => {
                  if (item.datasetIndex === 1) return `Trend: ${item.parsed.y.toFixed(2)}%`;
                  const m = matches[item.dataIndex];
                  return m
                    ? [`Avg %: ${m.avgPct.toFixed(2)}%`, `Classifiers: ${m.count}`]
                    : `${item.parsed.y.toFixed(2)}%`;
                }
              }
            }
          },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 35, font: { size: 10 } } },
            y: {
              min: 0, max: 110,
              grid: { color: tc.grid },
              ticks: { callback: v => v + '%' }
            }
          }
        }
      }
    );
  } else {
    // Classifier View: individual scores sorted by date, x = classifier_number
    let rawData = ((_data.time_series || {})[_globalDivision] || [])
      .filter(d => d.date && d.percentage != null)
      .sort((a, b) => a.date.localeCompare(b.date));
    if (dateFrom) rawData = rawData.filter(d => d.date >= dateFrom);
    if (dateTo)   rawData = rawData.filter(d => d.date <= dateTo);

    const labels = rawData.map(d => d.classifier_number || d.date);
    const yVals  = rawData.map(d => d.percentage);
    const trend  = computeTrendLine(yVals, _tsTrend);

    const datasets = [{
      label: 'Classifier %',
      data: yVals,
      borderColor: PALETTE[0],
      backgroundColor: 'rgba(99,102,241,0.08)',
      pointRadius: 3,
      tension: 0.3,
      fill: true,
    }];
    if (trend) {
      datasets.push({
        label: `Trend (last ${_tsTrend})`,
        data: trend,
        borderColor: '#f59e0b',
        borderWidth: 2,
        borderDash: [6, 3],
        pointRadius: 0,
        tension: 0,
      });
    }

    _charts['timeSeries'] = new Chart(
      document.getElementById('timeSeriesChart').getContext('2d'),
      {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: {
            legend: { display: !!trend },
            tooltip: {
              callbacks: {
                label: (item) => {
                  if (item.datasetIndex === 1) return `Trend: ${item.parsed.y.toFixed(2)}%`;
                  const d = rawData[item.dataIndex];
                  return d ? [`${item.parsed.y.toFixed(2)}%`, `Date: ${d.date}`] : `${item.parsed.y.toFixed(2)}%`;
                }
              }
            }
          },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 35, font: { size: 10 } } },
            y: {
              min: 0, max: 110,
              grid: { color: tc.grid },
              ticks: { callback: v => v + '%' }
            }
          }
        }
      }
    );
  }
}

function setTsView(btn, mode) {
  _tsView = mode;
  btn.closest('.toggle-group').querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  updateTimeSeriesChart();
}

function setTsTrend(btn, n) {
  _tsTrend = n;
  btn.closest('.toggle-group').querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  updateTimeSeriesChart();
}

// ── 5. Hit Factor Chart ───────────────────────────────────────────────
function updateHFChart() {
  const tc = themeColors();
  destroyChart('hf');

  if (_hfView === 'match') {
    // Match View: group by date+match, avg HF per match
    const matches = groupByMatch(_globalDivision).filter(m => m.avgHF != null);
    const labels = matches.map(m => truncate(m.match_name, 22));
    const yVals  = matches.map(m => m.avgHF);
    const maxHF  = Math.max(...yVals.filter(v => v != null), 0);
    const trend  = computeTrendLine(yVals, _hfTrend);

    const datasets = [{
      label: 'Avg HF per Match',
      data: yVals,
      borderColor: PALETTE[1],
      pointBackgroundColor: yVals.map(v => v === maxHF ? '#f59e0b' : PALETTE[1]),
      pointRadius: 4,
      tension: 0.3,
    }];
    if (trend) {
      datasets.push({
        label: `Trend (last ${_hfTrend})`,
        data: trend,
        borderColor: '#f59e0b',
        borderWidth: 2,
        borderDash: [6, 3],
        pointRadius: 0,
        tension: 0,
      });
    }

    _charts['hf'] = new Chart(
      document.getElementById('hfChart').getContext('2d'),
      {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: {
            legend: { display: !!trend },
            tooltip: {
              callbacks: {
                title: (items) => {
                  const m = matches[items[0].dataIndex];
                  return m ? m.match_name : items[0].label;
                },
                afterTitle: (items) => {
                  const m = matches[items[0].dataIndex];
                  return m ? m.match_date : '';
                },
                label: (item) => {
                  if (item.datasetIndex === 1) return `Trend: ${item.parsed.y.toFixed(4)}`;
                  const m = matches[item.dataIndex];
                  return m
                    ? [`Avg HF: ${m.avgHF.toFixed(4)}`, `Classifiers: ${m.count}`]
                    : item.parsed.y.toFixed(4);
                }
              }
            }
          },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 35, font: { size: 10 } } },
            y: { grid: { color: tc.grid } }
          }
        }
      }
    );
  } else {
    // Classifier View: individual scores sorted by date, x = classifier_number
    const rawData = ((_data.time_series || {})[_globalDivision] || [])
      .filter(d => d.date && d.hit_factor != null)
      .sort((a, b) => a.date.localeCompare(b.date));

    const labels = rawData.map(d => d.classifier_number || d.date);
    const yVals  = rawData.map(d => d.hit_factor);
    const maxHF  = Math.max(...yVals.filter(v => v != null), 0);
    const trend  = computeTrendLine(yVals, _hfTrend);

    const datasets = [{
      label: 'Hit Factor',
      data: yVals,
      borderColor: PALETTE[1],
      pointBackgroundColor: yVals.map(v => v === maxHF ? '#f59e0b' : PALETTE[1]),
      pointRadius: 4,
      tension: 0.3,
    }];
    if (trend) {
      datasets.push({
        label: `Trend (last ${_hfTrend})`,
        data: trend,
        borderColor: '#f59e0b',
        borderWidth: 2,
        borderDash: [6, 3],
        pointRadius: 0,
        tension: 0,
      });
    }

    _charts['hf'] = new Chart(
      document.getElementById('hfChart').getContext('2d'),
      {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: {
            legend: { display: !!trend },
            tooltip: {
              callbacks: {
                label: (item) => {
                  if (item.datasetIndex === 1) return `Trend: ${item.parsed.y.toFixed(4)}`;
                  const d = rawData[item.dataIndex];
                  return d ? [`HF: ${item.parsed.y.toFixed(4)}`, `Date: ${d.date}`] : item.parsed.y.toFixed(4);
                }
              }
            }
          },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 35, font: { size: 10 } } },
            y: { grid: { color: tc.grid } }
          }
        }
      }
    );
  }
}

function setHfView(btn, mode) {
  _hfView = mode;
  btn.closest('.toggle-group').querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  updateHFChart();
}

function setHfTrend(btn, n) {
  _hfTrend = n;
  btn.closest('.toggle-group').querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  updateHFChart();
}

// ── 6. Classifier Breakdown ───────────────────────────────────────────
function renderClassifierBreakdown() {
  const clsf = (_data.classifier_breakdown || [])
    .filter(c => c.percentage != null && c.division === _globalDivision);
  const sorted = [...clsf].sort((a, b) => b.percentage - a.percentage);
  const top10  = sorted.slice(0, 10);
  const bot10  = sorted.slice(-10).reverse();

  function row(c, i, isTop) {
    const pctClass = isTop ? 'pct-up' : 'pct-down';
    return `<tr>
      <td class="num-rank">${i+1}</td>
      <td>${escapeHtml(c.classifier_number)}${c.classifier_name ? '<br><span class="text-muted" style="font-size:0.7rem;">' + escapeHtml(c.classifier_name) + '</span>' : ''}</td>
      <td>${escapeHtml(c.division)}</td>
      <td class="${pctClass}">${fmtPct(c.percentage)}</td>
    </tr>`;
  }

  document.getElementById('top10Body').innerHTML =
    top10.map((c, i) => row(c, i, true)).join('') ||
    '<tr><td colspan="4" class="text-muted">No data</td></tr>';
  document.getElementById('bottom10Body').innerHTML =
    bot10.map((c, i) => row(c, i, false)).join('') ||
    '<tr><td colspan="4" class="text-muted">No data</td></tr>';

  // Frequency chart — top 15 most-shot classifiers for selected division
  const freq = {};
  (_data.classifier_breakdown || [])
    .filter(c => c.division === _globalDivision)
    .forEach(c => { freq[c.classifier_number] = (freq[c.classifier_number] || 0) + 1; });
  const freqSorted = Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 15);

  const tc = themeColors();
  destroyChart('freq');
  _charts['freq'] = new Chart(
    document.getElementById('freqChart').getContext('2d'),
    {
      type: 'bar',
      data: {
        labels: freqSorted.map(([k]) => k),
        datasets: [{ label: 'Times Shot', data: freqSorted.map(([, v]) => v), backgroundColor: PALETTE[3] }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: tc.grid }, ticks: { stepSize: 1 } },
          y: { grid: { display: false } }
        }
      }
    }
  );
}

// ── 7. Statistical Summary ────────────────────────────────────────────
function renderStats() {
  const clsf = (_data.classifier_breakdown || [])
    .filter(c => c.percentage != null && c.division === _globalDivision)
    .map(c => c.percentage);

  if (!clsf.length) {
    document.getElementById('statsGrid').innerHTML = '<p class="text-muted">No classifier data available.</p>';
    return;
  }

  clsf.sort((a, b) => a - b);
  const mean   = clsf.reduce((a, b) => a + b, 0) / clsf.length;
  const median = clsf.length % 2 === 0
    ? (clsf[clsf.length / 2 - 1] + clsf[clsf.length / 2]) / 2
    : clsf[Math.floor(clsf.length / 2)];
  const std = Math.sqrt(clsf.map(v => (v - mean) ** 2).reduce((a, b) => a + b, 0) / clsf.length);
  const volatility = mean ? (std / mean * 100) : 0;

  let slope = null;
  const n = clsf.length;
  if (n >= 2) {
    const sx = n * (n - 1) / 2, sx2 = (n - 1) * n * (2 * n - 1) / 6;
    const sy = clsf.reduce((a, b) => a + b, 0);
    const sxy = clsf.reduce((a, v, i) => a + i * v, 0);
    slope = (n * sxy - sx * sy) / (n * sx2 - sx * sx);
  }

  const half = Math.floor(clsf.length / 2);
  let yoy = null;
  if (half > 0) {
    const recent = clsf.slice(-half);
    const prior  = clsf.slice(0, half);
    yoy = recent.reduce((a, b) => a + b, 0) / recent.length - prior.reduce((a, b) => a + b, 0) / prior.length;
  }

  const stats = [
    { label: 'Mean %',      value: fmtPct(mean),                        sub: 'Average across all classifiers' },
    { label: 'Median %',    value: fmtPct(median),                      sub: '50th percentile' },
    { label: 'Std Dev',     value: std.toFixed(2),                      sub: 'Consistency measure' },
    { label: 'Volatility',  value: volatility.toFixed(1) + '%',         sub: '(std/mean × 100)' },
    { label: 'Trend Slope', value: slope != null ? slope.toFixed(4) : '—', sub: 'Per-shot improvement rate' },
    { label: 'YoY Est.',    value: yoy != null ? (yoy >= 0 ? '+' : '') + yoy.toFixed(2) + '%' : '—', sub: 'Recent vs prior half' },
  ];

  document.getElementById('statsGrid').innerHTML = stats.map(s => `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(s.label)}</div>
      <div class="stat-value" style="font-size:1.5rem;">${escapeHtml(s.value)}</div>
      <div class="stat-sub">${escapeHtml(s.sub)}</div>
    </div>
  `).join('');
}

// ── Theme change listener — re-render charts ──────────────────────────
document.getElementById('themeToggle').addEventListener('click', () => {
  setTimeout(() => {
    if (!_data) return;
    renderDivBarChart();
    updateTimeSeriesChart();
    updateHFChart();
    renderClassifierBreakdown();
    if (_mrData) renderMatchResultsCharts();
  }, 50);
});

// ── Init ──────────────────────────────────────────────────────────────
loadDashboard();

// ═══════════════════════════════════════════════════════════════════════
// TAB SYSTEM
// ═══════════════════════════════════════════════════════════════════════

let _activeTab = 'classifier';

function switchTab(tabId) {
  _activeTab = tabId;
  document.querySelectorAll('.tab-nav-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
  document.getElementById('tab-' + tabId).classList.add('active');
  // Find the button by its onclick attribute pattern
  document.querySelectorAll('.tab-nav-btn').forEach(btn => {
    if (btn.getAttribute('onclick') && btn.getAttribute('onclick').includes(tabId)) {
      btn.classList.add('active');
    }
  });

  if (tabId === 'matchresults' && !_mrData && !_mrLoading) {
    loadPractiscoreData();
  }
  // Resize charts when tab becomes visible
  if (tabId === 'classifier' && _data) {
    setTimeout(() => {
      Object.values(_charts).forEach(c => c && c.resize && c.resize());
    }, 50);
  }
  if (tabId === 'matchresults' && _mrData) {
    setTimeout(() => {
      Object.values(_mrCharts).forEach(c => c && c.resize && c.resize());
    }, 50);
  }
}

// ═══════════════════════════════════════════════════════════════════════
// MATCH RESULTS TAB
// ═══════════════════════════════════════════════════════════════════════

let _mrData = null;
let _mrCharts = {};
let _mrLoading = false;
let _mrPollTimer = null;
let _mrSortCol = 'match_date';
let _mrSortDir = 'asc';

function mrShow(id) { document.getElementById(id).style.display = ''; }
function mrHide(id) { document.getElementById(id).style.display = 'none'; }

async function loadPractiscoreData() {
  if (_mrLoading) return;
  mrHide('mrError');
  mrHide('mrContent');

  const cached = await fetchPractiscoreResults();
  if (cached) {
    _mrData = cached;
    mrHide('mrLoading');
    mrShow('mrContent');
    renderMatchResultsAll();
    updateMrBanner('loaded');
    return;
  }
  // No data yet — show banner only
  updateMrBanner('none');
}

async function fetchPractiscoreResults() {
  try {
    const resp = await fetch(`/api/member/${MEMBER}/practiscore`);
    if (resp.status === 404) return null;
    if (!resp.ok) return null;
    return await resp.json();
  } catch (_) {
    return null;
  }
}

async function triggerPractiscoreScrape() {
  _mrLoading = true;
  updateMrBanner('scraping');
  mrShow('mrLoading');
  mrHide('mrError');
  mrHide('mrContent');

  try {
    const resp = await fetch(`/api/analyze/${MEMBER}/practiscore`, { method: 'POST' });
    const body = await resp.json();

    if (resp.status === 200 && body.status === 'complete') {
      // Cached result returned immediately
      mrHide('mrLoading');
      _mrLoading = false;
      const data = await fetchPractiscoreResults();
      if (data) {
        _mrData = data;
        mrShow('mrContent');
        renderMatchResultsAll();
        updateMrBanner('loaded');
      }
      return;
    }

    // Poll for job completion
    const jobId = body.job_id;
    if (!jobId) {
      throw new Error('No job_id returned');
    }
    _pollPractiscoreJob(jobId);
  } catch (err) {
    _mrLoading = false;
    mrHide('mrLoading');
    document.getElementById('mrErrorMsg').textContent = err.message || 'Failed to start scrape.';
    mrShow('mrError');
    updateMrBanner('error');
  }
}

async function _pollPractiscoreJob(jobId) {
  const poll = async () => {
    try {
      // We poll the generic status endpoint — it covers all job types
      const resp = await fetch(`/api/member/${MEMBER}/status`);
      if (!resp.ok) throw new Error(`Status check failed: ${resp.status}`);
      const body = await resp.json();

      if (body.status === 'complete' || body.status === 'not_started') {
        clearInterval(_mrPollTimer);
        _mrPollTimer = null;
        _mrLoading = false;
        mrHide('mrLoading');
        const data = await fetchPractiscoreResults();
        if (data) {
          _mrData = data;
          mrShow('mrContent');
          renderMatchResultsAll();
          updateMrBanner('loaded');
        } else {
          updateMrBanner('none');
        }
        return;
      }
      if (body.status === 'error') {
        clearInterval(_mrPollTimer);
        _mrPollTimer = null;
        _mrLoading = false;
        mrHide('mrLoading');
        document.getElementById('mrErrorMsg').textContent = body.error || 'Scrape failed.';
        mrShow('mrError');
        updateMrBanner('error');
        return;
      }
    } catch (_) {}
  };

  _mrPollTimer = setInterval(poll, 3000);
}

function updateMrBanner(state) {
  const banner = document.getElementById('mrScrapeBanner');
  const msg    = document.getElementById('mrBannerMsg');
  const btn    = document.getElementById('mrScrapeBtn');

  if (state === 'loaded') {
    banner.style.display = 'none';
    return;
  }
  banner.style.display = '';
  if (state === 'none') {
    msg.textContent = 'No PractiScore data yet for this member.';
    btn.textContent = 'Fetch Match Results';
    btn.disabled = false;
  } else if (state === 'scraping') {
    msg.textContent = 'Scraping PractiScore… this can take 1-2 minutes.';
    btn.textContent = 'Scraping…';
    btn.disabled = true;
  } else if (state === 'error') {
    msg.textContent = 'Scrape encountered an error.';
    btn.textContent = 'Retry';
    btn.disabled = false;
  }
}

// ── Render all Match Results sections ─────────────────────────────────
function renderMatchResultsAll() {
  if (!_mrData) return;
  renderMrStatCards();
  renderMatchResultsCharts();
  renderMrTable();
}

function renderMrStatCards() {
  const s = _mrData.stats || {};
  const cards = [
    { label: 'Total Matches',    value: s.total_matches ?? '—' },
    { label: 'Avg Finish %',     value: s.avg_percent_of_winner != null ? s.avg_percent_of_winner.toFixed(1) + '%' : '—' },
    { label: 'Best Placement',   value: s.best_placement ?? '—' },
    { label: 'Avg Percentile',   value: s.avg_placement_percentile != null ? s.avg_placement_percentile.toFixed(1) + '%' : '—' },
  ];
  document.getElementById('mrStatCards').innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(c.label)}</div>
      <div class="stat-value" style="font-size:1.4rem;">${escapeHtml(c.value)}</div>
    </div>
  `).join('');
}

function renderMatchResultsCharts() {
  const tc = themeColors();
  const matches = (_mrData.matches || []).sort((a, b) =>
    (a.match_date || '').localeCompare(b.match_date || '')
  );

  // ── Finish % Over Time ────────────────────────────────────────────
  Object.keys(_mrCharts).forEach(k => { if (_mrCharts[k]) { _mrCharts[k].destroy(); delete _mrCharts[k]; } });

  const pctLabels = matches.map(m => truncate(m.match_name, 20));
  const pctVals   = matches.map(m => m.member_percent);

  _mrCharts['mrPct'] = new Chart(
    document.getElementById('mrPctChart').getContext('2d'),
    {
      type: 'line',
      data: {
        labels: pctLabels,
        datasets: [{
          label: 'Finish %',
          data: pctVals,
          borderColor: PALETTE[0],
          backgroundColor: 'rgba(99,102,241,0.08)',
          pointRadius: 4,
          tension: 0.3,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => matches[items[0].dataIndex]?.match_name || items[0].label,
              afterTitle: (items) => matches[items[0].dataIndex]?.match_date || '',
              label: (item) => item.parsed.y != null ? `Finish: ${item.parsed.y.toFixed(2)}%` : '—',
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxRotation: 35, font: { size: 10 } } },
          y: { min: 0, max: 105, grid: { color: tc.grid }, ticks: { callback: v => v + '%' } },
        },
      },
    }
  );

  // ── Placement Rank Over Time ──────────────────────────────────────
  const rankLabels = matches.map(m => truncate(m.match_name, 20));
  const rankVals   = matches.map(m => m.member_placement);

  _mrCharts['mrRank'] = new Chart(
    document.getElementById('mrRankChart').getContext('2d'),
    {
      type: 'line',
      data: {
        labels: rankLabels,
        datasets: [{
          label: 'Placement',
          data: rankVals,
          borderColor: PALETTE[2],
          backgroundColor: 'rgba(245,158,11,0.08)',
          pointRadius: 4,
          tension: 0.3,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => matches[items[0].dataIndex]?.match_name || items[0].label,
              afterTitle: (items) => {
                const m = matches[items[0].dataIndex];
                return m ? `${m.match_date}  (${m.total_competitors ?? '?'} competitors)` : '';
              },
              label: (item) => item.parsed.y != null ? `Place: ${item.parsed.y}` : '—',
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxRotation: 35, font: { size: 10 } } },
          y: {
            grid: { color: tc.grid },
            reverse: true,
            ticks: { stepSize: 1 },
            title: { display: true, text: 'Place (1 = best)' },
          },
        },
      },
    }
  );

  // ── Match Level Distribution ──────────────────────────────────────
  const levelMap = {};
  matches.forEach(m => {
    const lvl = m.match_level != null ? `Level ${m.match_level}` : 'Unknown';
    if (!levelMap[lvl]) levelMap[lvl] = [];
    if (m.member_percent != null) levelMap[lvl].push(m.member_percent);
  });
  const levelLabels = Object.keys(levelMap).sort();
  const levelVals   = levelLabels.map(l => {
    const arr = levelMap[l];
    return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  });
  const levelColors = [PALETTE[0], PALETTE[1], PALETTE[2], PALETTE[3], PALETTE[5]];

  _mrCharts['mrLevel'] = new Chart(
    document.getElementById('mrLevelChart').getContext('2d'),
    {
      type: 'bar',
      data: {
        labels: levelLabels,
        datasets: [{
          label: 'Avg Finish %',
          data: levelVals,
          backgroundColor: levelLabels.map((_, i) => levelColors[i % levelColors.length]),
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (item) => item.parsed.y != null ? `Avg: ${item.parsed.y.toFixed(2)}%` : '—' } },
        },
        scales: {
          x: { grid: { display: false } },
          y: { min: 0, max: 105, grid: { color: tc.grid }, ticks: { callback: v => v + '%' } },
        },
      },
    }
  );
}

// ── Match History Table ────────────────────────────────────────────────
function renderMrTable() {
  const matches = [...(_mrData.matches || [])];

  // Sort
  matches.sort((a, b) => {
    let av = a[_mrSortCol], bv = b[_mrSortCol];
    if (av == null) av = _mrSortDir === 'asc' ? Infinity : -Infinity;
    if (bv == null) bv = _mrSortDir === 'asc' ? Infinity : -Infinity;
    if (typeof av === 'string') return _mrSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return _mrSortDir === 'asc' ? av - bv : bv - av;
  });

  document.getElementById('mrTableBody').innerHTML = matches.length
    ? matches.map(m => `<tr>
        <td>${escapeHtml(m.match_date) || '—'}</td>
        <td>${escapeHtml(m.match_name) || '—'}</td>
        <td>${escapeHtml(m.division) || '—'}</td>
        <td>${m.match_level != null ? 'L' + m.match_level : '—'}</td>
        <td>${m.member_placement ?? '—'}</td>
        <td>${m.total_competitors ?? '—'}</td>
        <td class="${(m.member_percent ?? 0) >= 70 ? 'pct-up' : 'pct-down'}">${m.member_percent != null ? m.member_percent.toFixed(2) + '%' : '—'}</td>
        <td>${m.placement_percentile != null ? m.placement_percentile.toFixed(1) + '%' : '—'}</td>
      </tr>`).join('')
    : '<tr><td colspan="8" class="text-muted">No match data available.</td></tr>';

  // Update sort indicators on headers
  document.querySelectorAll('#mrTable th[data-sort]').forEach(th => {
    th.classList.remove('asc', 'desc');
    if (th.dataset.sort === _mrSortCol) th.classList.add(_mrSortDir);
  });
}

// ── Table sort ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('#mrTable th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.sort;
      if (_mrSortCol === col) {
        _mrSortDir = _mrSortDir === 'asc' ? 'desc' : 'asc';
      } else {
        _mrSortCol = col;
        _mrSortDir = col === 'match_date' || col === 'match_name' ? 'asc' : 'asc';
      }
      if (_mrData) renderMrTable();
    });
  });
});
