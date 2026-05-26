"""统计仪表盘 API + HTML 页面"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from src.router.key_pool import get_key_pool
from src.stats.tracker import get_stats_tracker

log = logging.getLogger("dashboard")
router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats/summary")
async def summary():
    tracker = get_stats_tracker()
    data = await tracker.get_summary()
    return JSONResponse(data)


@router.get("/stats/daily")
async def daily(days: int = 30):
    tracker = get_stats_tracker()
    data = await tracker.get_daily(days=days)
    return JSONResponse(data)


@router.get("/stats/models")
async def by_model():
    tracker = get_stats_tracker()
    data = await tracker.get_by_model()
    return JSONResponse(data)


@router.get("/keypool")
async def keypool():
    pool = get_key_pool()
    return JSONResponse(pool.get_stats())


DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DeepSeek Proxy 仪表盘</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.container{max-width:1400px;margin:0 auto;padding:16px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid #1e293b}
.header h1{font-size:1.25rem;font-weight:600;color:#f1f5f9}
.header-right{display:flex;align-items:center;gap:16px;font-size:.75rem;color:#64748b}
#last-update{font-family:monospace;font-size:.7rem}
.refresh-btn{background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:.75rem}
.refresh-btn:hover{background:#334155;color:#e2e8f0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.card{background:#1e293b;border-radius:10px;padding:16px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.card.req::before{background:#3b82f6}
.card.prompt::before{background:#8b5cf6}
.card.completion::before{background:#06b6d4}
.card.cost::before{background:#f87171}
.card.saved::before{background:#4ade80}
.card.cache::before{background:#f59e0b}
.card.proxy::before{background:#10b981}
.card.keypool::before{background:#6366f1}
.card .label{font-size:.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
.card .value{font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums}
.card .sub{font-size:.7rem;color:#64748b;margin-top:2px}
.saved{color:#4ade80}
.cost{color:#f87171}
.chart-row{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-bottom:16px}
@media(max-width:900px){.chart-row{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,1fr)}}
.chart-box{background:#1e293b;border-radius:10px;padding:16px}
.chart-box h3{font-size:.8rem;font-weight:600;margin-bottom:12px;color:#e2e8f0;display:flex;align-items:center;gap:8px}
.chart-box h3 .badge{font-size:.65rem;font-weight:400;color:#94a3b8;background:#0f172a;padding:2px 6px;border-radius:4px}
.table-wrap{background:#1e293b;border-radius:10px;padding:16px;overflow-x:auto;margin-bottom:16px}
.table-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.table-header h3{font-size:.8rem;font-weight:600;color:#e2e8f0}
.table-header .days-selector{display:flex;gap:4px}
.table-header .days-selector button{background:transparent;border:1px solid #334155;color:#64748b;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:.7rem}
.table-header .days-selector button.active{background:#334155;color:#e2e8f0;border-color:#475569}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{text-align:left;color:#94a3b8;font-weight:500;padding:8px 10px;border-bottom:1px solid #334155;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid #1e293b;white-space:nowrap}
tr:hover td{background:#334155}
.num{font-variant-numeric:tabular-nums;text-align:right;font-family:monospace}
.empty-state{text-align:center;color:#64748b;padding:32px;font-size:.85rem}
.empty-state p{margin-top:8px}
.empty-state .icon{font-size:2rem;opacity:.3}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.loading .value{animation:pulse 1.5s ease-in-out infinite;background:#334155;border-radius:4px;height:1.5rem;width:80%}
.loading .sub{animation:pulse 1.5s ease-in-out infinite;background:#1e293b;border-radius:4px;height:.7rem;width:60%;margin-top:4px}
</style>
</head>
<body>
<div class="container">
<div class="header">
  <h1>DeepSeek Proxy 仪表盘</h1>
  <div class="header-right">
    <span id="live-badge" style="display:none;background:#166534;color:#4ade80;padding:2px 8px;border-radius:4px;font-size:.7rem">LIVE</span>
    <span id="last-update">--</span>
    <button class="refresh-btn" onclick="loadData()">刷新</button>
  </div>
</div>

<div class="grid" id="summary-grid">
  <div class="card req"><div class="label">总请求</div><div class="value" id="total-req"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="req-sub"></div></div>
  <div class="card prompt"><div class="label">Prompt Tokens</div><div class="value" id="total-prompt"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="prompt-sub"></div></div>
  <div class="card completion"><div class="label">Completion Tokens</div><div class="value" id="total-completion"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="completion-sub"></div></div>
  <div class="card cache"><div class="label">DeepSeek 缓存命中率</div><div class="value" id="cache-rate"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="cache-sub"></div></div>
  <div class="card proxy"><div class="label">代理层缓存命中</div><div class="value" id="proxy-hits"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="proxy-sub"></div></div>
  <div class="card cost"><div class="label">实际费用</div><div class="value cost" id="total-cost"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="cost-sub"></div></div>
  <div class="card"><div class="label">无缓存理论费用</div><div class="value" style="color:#94a3b8" id="theoretical-cost"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="theoretical-sub"></div></div>
  <div class="card saved"><div class="label">节省金额</div><div class="value saved" id="total-saved"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="saved-sub"></div></div>
  <div class="card keypool"><div class="label">可用 Key / 总数</div><div class="value" id="keypool"><span class="loading" style="display:block"><div class="value"></div><div class="sub"></div></span></div><div class="sub" id="keypool-sub"></div></div>
</div>

<div class="chart-row">
  <div class="chart-box">
    <h3>每日 Token 消耗</h3>
    <canvas id="token-chart" height="200"></canvas>
    <div class="empty-state" id="token-empty" style="display:none">
      <div class="icon">&#9654;</div>
      <p>暂无数据 - 发送请求后会自动记录</p>
    </div>
  </div>
  <div class="chart-box">
    <h3>按模型费用分布</h3>
    <canvas id="model-chart" height="200"></canvas>
    <div class="empty-state" id="model-empty" style="display:none">
      <div class="icon">&#9679;</div>
      <p>暂无数据</p>
    </div>
  </div>
</div>

<div class="chart-row">
  <div class="chart-box">
    <h3>每日费用 <span class="badge">实际 vs 无缓存</span></h3>
    <canvas id="cost-chart" height="200"></canvas>
    <div class="empty-state" id="cost-empty" style="display:none">
      <div class="icon">&#9660;</div>
      <p>暂无数据</p>
    </div>
  </div>
  <div class="chart-box">
    <h3>每日缓存命中率 <span class="badge">DeepSeek 前缀缓存</span></h3>
    <canvas id="cache-chart" height="200"></canvas>
    <div class="empty-state" id="cache-empty" style="display:none">
      <div class="icon">&#9654;</div>
      <p>暂无数据</p>
    </div>
  </div>
</div>

<div class="table-wrap">
  <div class="table-header">
    <h3>每日明细</h3>
    <div class="days-selector">
      <button onclick="switchDays(7)">7天</button>
      <button class="active" onclick="switchDays(30)">30天</button>
      <button onclick="switchDays(90)">90天</button>
    </div>
  </div>
  <table id="daily-table">
    <thead>
      <tr>
        <th>日期</th>
        <th>请求</th>
        <th>Prompt Tokens</th>
        <th>Completion</th>
        <th>缓存命中</th>
        <th>缓存 Miss</th>
        <th>缓存率</th>
        <th>代理缓存</th>
        <th>实际费用</th>
        <th>理论费用</th>
        <th>节省</th>
      </tr>
    </thead>
    <tbody id="daily-tbody"></tbody>
  </table>
  <div class="empty-state" id="daily-empty" style="display:none">
    <p>暂无数据 - 开始使用代理后自动记录</p>
  </div>
</div>
</div>

<script>
let currentDays = 30;
let sse = null;

function fmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

function fmtMoney(n) {
  if (n === undefined || n === null || n === 0) return '$0';
  if (n < 0.001) return '$' + n.toFixed(6);
  if (n < 0.01) return '$' + n.toFixed(5);
  if (n < 0.1) return '$' + n.toFixed(4);
  return '$' + n.toFixed(4);
}

function switchDays(days) {
  currentDays = days;
  document.querySelectorAll('.days-selector button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.days-selector button').forEach(b => {
    if (b.textContent.includes(days + '天')) b.classList.add('active');
  });
  loadData();
}

function startSSE() {
  if (sse) sse.close();
  sse = new EventSource('/api/stats/sse');
  document.getElementById('live-badge').style.display = 'inline';

  sse.onmessage = function(e) {
    try {
      const data = JSON.parse(e.data);
      if (data.error) return;
      updateUI(data);
      document.getElementById('last-update').textContent = '实时 ' + new Date().toLocaleTimeString();
    } catch (err) {
      console.error('SSE parse error:', err);
    }
  };

  sse.onerror = function() {
    document.getElementById('live-badge').style.display = 'none';
    // 连接断开后 3s 重连
    setTimeout(startSSE, 3000);
  };
}

async function loadData() {
  document.getElementById('last-update').textContent = '加载中... ' + new Date().toLocaleTimeString();
  try {
    const [summary, daily, models, keypool] = await Promise.all([
      fetch('/api/stats/summary').then(r => r.json()).catch(() => null),
      fetch('/api/stats/daily?days=' + currentDays).then(r => r.json()).catch(() => []),
      fetch('/api/stats/models').then(r => r.json()).catch(() => []),
      fetch('/api/keypool').then(r => r.json()).catch(() => ({total:0,available:0})),
    ]);
    updateUI({summary, daily, models, keypool, ts: Date.now() / 1000});
    document.getElementById('last-update').textContent = '最后更新: ' + new Date().toLocaleTimeString();
  } catch (err) {
    console.error('加载失败:', err);
    document.getElementById('last-update').textContent = '加载失败: ' + err.message;
  }
}

function updateUI(data) {
  const summary = data.summary;
  const daily = data.daily;
  const models = data.models;
  const keypool = data.keypool;

  if (summary && summary.total_requests !== undefined) {
    setCard('total-req', summary.total_requests.toLocaleString());
    setCard('req-sub', '代理层缓存: ' + (summary.proxy_cache_hits || 0) + ' 次');
    setCard('total-prompt', fmt(summary.total_prompt_tokens));
    setCard('prompt-sub', '缓存命中: ' + fmt(summary.deepseek_cache_hit_tokens) + ' / Miss: ' + fmt(summary.deepseek_cache_miss_tokens));
    setCard('total-completion', fmt(summary.total_completion_tokens));
    setCard('completion-sub', '平均 ' + (summary.total_requests > 0 ? (summary.total_completion_tokens / summary.total_requests).toFixed(0) : 0) + ' /req');
    setCard('cache-rate', (summary.deepseek_cache_hit_rate || 0).toFixed(1) + '%');
    setCard('cache-sub', fmt(summary.deepseek_cache_hit_tokens) + ' / ' + fmt(summary.deepseek_cache_hit_tokens + summary.deepseek_cache_miss_tokens) + ' tokens');
    setCard('proxy-hits', (summary.proxy_cache_hits || 0).toLocaleString());
    setCard('proxy-sub', '占请求 ' + (summary.total_requests > 0 ? (summary.proxy_cache_hits / summary.total_requests * 100).toFixed(1) : 0) + '%');
    setCard('total-cost', fmtMoney(summary.total_cost_usd));
    setCard('cost-sub', '平均 ' + (summary.total_requests > 0 ? fmtMoney(summary.total_cost_usd / summary.total_requests) : '$0') + ' /req');
    setCard('theoretical-cost', fmtMoney(summary.total_theoretical_cost_usd));
    setCard('theoretical-sub', summary.total_requests > 0 ? fmtMoney(summary.total_theoretical_cost_usd / summary.total_requests) + ' /req' : '');
    setCard('total-saved', fmtMoney(summary.total_saved_usd));
    setCard('saved-sub', '节省 ' + (summary.total_theoretical_cost_usd > 0 ? (summary.total_saved_usd / summary.total_theoretical_cost_usd * 100).toFixed(1) : 0) + '%');
  }

  if (keypool) {
    const avail = keypool.available || 0;
    const total = keypool.total || 0;
    setCard('keypool', avail + ' / ' + total);
    setCard('keypool-sub', '退避中: ' + (keypool.backing_off || 0));
  }

  // 每日数据只在天数切换或页面首次加载时更新图表
  if (daily && daily.length > 0) {
    document.getElementById('token-empty').style.display = 'none';
    document.getElementById('cost-empty').style.display = 'none';
    document.getElementById('cache-empty').style.display = 'none';
    document.getElementById('daily-empty').style.display = 'none';
    renderTokenChart(daily);
    renderCostChart(daily);
    renderCacheChart(daily);
    renderDailyTable(daily);
  } else {
    document.getElementById('token-empty').style.display = 'block';
    document.getElementById('cost-empty').style.display = 'block';
    document.getElementById('cache-empty').style.display = 'block';
  }

  if (models && models.length > 0) {
    document.getElementById('model-empty').style.display = 'none';
    renderModelChart(models);
  } else {
    document.getElementById('model-empty').style.display = 'block';
  }
}

function setCard(id, text) {
  const el = document.getElementById(id);
  if (el) {
    // 移除 loading 占位
    const loading = el.querySelector('.loading');
    if (loading) loading.remove();
    el.textContent = text;
  }
}

// Chart instances for cleanup
let tokenChart, costChart, cacheChart, modelChart;

function renderTokenChart(daily) {
  if (tokenChart) tokenChart.destroy();
  tokenChart = new Chart(document.getElementById('token-chart'), {
    type: 'bar',
    data: {
      labels: daily.map(d => d.date),
      datasets: [
        { label: 'Prompt', data: daily.map(d => Math.round(d.prompt_tokens/1000)), backgroundColor: '#8b5cf6', borderRadius: 3 },
        { label: 'Completion', data: daily.map(d => Math.round(d.completion_tokens/1000)), backgroundColor: '#06b6d4', borderRadius: 3 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#94a3b8', boxWidth: 12, padding: 12 } } },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 10, font: { size: 10 } }, grid: { color: '#1e293b' } },
        y: { title: { display: true, text: 'K Tokens', color: '#64748b', font: { size: 10 } }, ticks: { color: '#64748b' }, grid: { color: '#334155' } }
      }
    }
  });
}

function renderCostChart(daily) {
  if (costChart) costChart.destroy();
  costChart = new Chart(document.getElementById('cost-chart'), {
    type: 'bar',
    data: {
      labels: daily.map(d => d.date),
      datasets: [
        { label: '实际费用', data: daily.map(d => d.cost_usd), backgroundColor: '#3b82f6', borderRadius: 3 },
        { label: '节省(缓存折扣)', data: daily.map(d => d.saved_usd), backgroundColor: '#4ade80', borderRadius: 3 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#94a3b8', boxWidth: 12, padding: 12 } } },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 10, font: { size: 10 } }, grid: { color: '#1e293b' } },
        y: { title: { display: true, text: 'USD', color: '#64748b', font: { size: 10 } }, ticks: { color: '#64748b', callback: v => '$' + v.toFixed(4) }, grid: { color: '#334155' } }
      }
    }
  });
}

function renderCacheChart(daily) {
  if (cacheChart) cacheChart.destroy();
  cacheChart = new Chart(document.getElementById('cache-chart'), {
    type: 'line',
    data: {
      labels: daily.map(d => d.date),
      datasets: [
        {
          label: '缓存命中率',
          data: daily.map(d => {
            const total = d.cache_hit_tokens + d.cache_miss_tokens;
            return total > 0 ? (d.cache_hit_tokens / total * 100) : 0;
          }),
          borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', fill: true,
          tension: 0.3, pointRadius: 3,
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#94a3b8', boxWidth: 12, padding: 12 } } },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 10, font: { size: 10 } }, grid: { color: '#1e293b' } },
        y: { max: 100, ticks: { color: '#64748b', callback: v => v + '%' }, grid: { color: '#334155' } }
      }
    }
  });
}

function renderModelChart(models) {
  if (modelChart) modelChart.destroy();
  const colors = ['#3b82f6','#8b5cf6','#06b6d4','#f59e0b','#ef4444','#10b981'];
  modelChart = new Chart(document.getElementById('model-chart'), {
    type: 'doughnut',
    data: {
      labels: models.map(m => m.model),
      datasets: [{
        data: models.map(m => Math.max(m.cost_usd, 0.001)),
        backgroundColor: colors.slice(0, models.length),
        borderColor: '#0f172a', borderWidth: 2,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#94a3b8', boxWidth: 12, padding: 12, font: { size: 10 } } },
        tooltip: {
          callbacks: {
            label: ctx => {
              const m = models[ctx.dataIndex];
              return ' $' + m.cost_usd.toFixed(4) + ' | ' + fmt(m.prompt_tokens) + ' / ' + fmt(m.completion_tokens) + ' tokens | 缓存率: ' + (m.prompt_tokens > 0 ? (m.cache_hit_tokens / (m.cache_hit_tokens + m.cache_miss_tokens) * 100).toFixed(1) : 0) + '%';
            }
          }
        }
      }
    }
  });
}

function renderDailyTable(daily) {
  const tbody = document.getElementById('daily-tbody');
  const empty = document.getElementById('daily-empty');
  if (!daily || daily.length === 0) {
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = '';
  daily.slice().reverse().forEach(d => {
    const total = d.cache_hit_tokens + d.cache_miss_tokens;
    const cachePct = total > 0 ? (d.cache_hit_tokens / total * 100).toFixed(1) : '0.0';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${d.date}</td>
      <td class="num">${d.requests.toLocaleString()}</td>
      <td class="num">${fmt(d.prompt_tokens)}</td>
      <td class="num">${fmt(d.completion_tokens)}</td>
      <td class="num" style="color:#f59e0b">${fmt(d.cache_hit_tokens)}</td>
      <td class="num" style="color:#94a3b8">${fmt(d.cache_miss_tokens)}</td>
      <td class="num" style="color:${cachePct > 50 ? '#4ade80' : '#f59e0b'}">${cachePct}%</td>
      <td class="num" style="color:#10b981">${d.proxy_cache_hits}</td>
      <td class="num" style="color:#f87171">${fmtMoney(d.cost_usd)}</td>
      <td class="num" style="color:#94a3b8">${fmtMoney(d.theoretical_cost_usd)}</td>
      <td class="num" style="color:#4ade80">${fmtMoney(d.saved_usd)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ========== 初始化: SSE 实时推送 + 首次加载 ==========
startSSE();
loadData();
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)