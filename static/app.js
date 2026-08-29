/* 闲鱼监控仪表盘 - 前端逻辑 */
'use strict';

/* 支持从 URL ?token= 传递并记忆仪表盘访问令牌：脚本/浏览器直接访问
   http://<host>:5000/?token=xxx 后写入 localStorage，之后所有 API 请求自动带上。 */
(function () {
  try {
    const t = new URLSearchParams(window.location.search).get('token');
    if (t) localStorage.setItem('dashboard_token', t);
  } catch (e) { /* 忽略（非浏览器/隐私模式等） */ }
})();

/* ───────── 工具函数 ───────── */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(url, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const token = localStorage.getItem('dashboard_token');
  if (token) headers['X-Auth-Token'] = token;
  const resp = await fetch(url, { ...opts, headers });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}

function fmtPrice(p) {
  if (p === null || p === undefined) return '--';
  const s = '¥' + Number(p).toFixed(2);
  return s.replace(/\.00$/, '');
}

// 坐标轴价格标签：万元级显示 "2.5万"，千元级显示原数
function fmtAxisPrice(v) {
  if (v == null || isNaN(v)) return '--';
  if (Math.abs(v) >= 10000) {
    const w = v / 10000;
    return (Math.round(w * 10) / 10) + '万';
  }
  return String(Math.round(v));
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}

/* ───────── 视图切换 ───────── */
const VIEW_TITLES = {
  overview: '总览', products: '监控商品', analysis: '市场分析',
  drops: '降价记录', notifications: '通知记录', settings: '设置', bark: 'Bark 推送',
};

function switchView(name) {
  $$('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.view === name));
  $$('.view').forEach((el) => el.classList.toggle('active', el.id === 'view-' + name));
  $('#page-title').textContent = VIEW_TITLES[name] || name;
  if (name === 'analysis') loadAnalysis();
  if (name === 'bark') loadBarkTargets();
}

$$('.nav-item').forEach((el) => el.addEventListener('click', () => switchView(el.dataset.view)));
document.querySelectorAll('[data-goto]').forEach((el) => {
  el.addEventListener('click', () => switchView(el.dataset.goto));
});

/* ───────── 状态轮询 ───────── */
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    const dot = $('#status-dot');
    const text = $('#status-text');
    const loginFailed = s.login_ok === false && ['running', 'checking', 'error'].includes(s.monitor_status);
    dot.className = 'status-dot ' + (loginFailed ? 'error' : s.monitor_status);
    text.textContent = loginFailed ? '未登录，请扫码登录' : ({
      running: '运行中', checking: '检查中…', starting: '启动中…',
      stopped: '已停止', error: '异常',
    }[s.monitor_status] || s.monitor_status);
    if (s.monitor_status === 'checking' && s.current_keyword) {
      text.textContent = `正在检查: ${s.current_keyword}`;
    }
    $('#status-sub').textContent = s.last_check_at
      ? `上次检查: ${s.last_check_at}`
      : '上次检查: --';
  } catch (e) { /* 忽略 */ }
}

/* ───────── 总览 ───────── */
async function loadOverview() {
  try {
    const st = await api('/api/stats');
    $('#stat-products').textContent = st.monitored_products;
    $('#stat-total-items').textContent = st.total_items;
    $('#stat-today').textContent = st.today_notified;
    $('#stat-total-notified').textContent = st.total_notified;
    $('#stat-drops').textContent = st.total_drops;
    $('#stat-checks').textContent = st.total_checks;

    // 最新通知
    const tbody = $('#overview-table tbody');
    if (!st.recent_notifications.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无数据，点击右上角"立即检查"开始监控</td></tr>';
      return;
    }
    tbody.innerHTML = st.recent_notifications.map((n) => `
      <tr>
        <td class="price">${fmtPrice(n.price)}</td>
        <td title="${esc(n.title)}">${esc(n.title.slice(0, 40))}${n.title.length > 40 ? '…' : ''}</td>
        <td>${esc(n.keyword)}</td>
        <td>--</td>
        <td>${esc(n.time)}</td>
        <td><a class="item-link" href="${esc(n.url)}" target="_blank">打开 →</a></td>
      </tr>`).join('');
  } catch (e) {
    toast('加载总览失败: ' + e.message, true);
  }
}

/* ───────── 监控商品 ───────── */
async function loadProducts() {
  try {
    const list = await api('/api/products');
    const tbody = $('#products-table tbody');
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">还没有监控商品，用上面的表单添加</td></tr>';
      return;
    }
    tbody.innerHTML = list.map((p) => `
      <tr>
        <td><span class="badge ${p.enabled ? 'badge-yes' : 'badge-off'}">${p.enabled ? '监控中' : '已停用'}</span></td>
        <td><strong>${esc(p.keyword)}</strong></td>
        <td class="price">${fmtPrice(p.min_price)} ~ ${fmtPrice(p.max_price)}</td>
        <td>${esc(p.exclude_keywords || '—')}</td>
        <td>${esc(p.must_include || '—')}</td>
        <td>${esc(p.created_at)}</td>
        <td>
          <button class="btn btn-sm" onclick="editProduct(${p.id})">编辑</button>
          <button class="btn btn-sm" onclick="toggleProduct(${p.id})">${p.enabled ? '停用' : '启用'}</button>
          <button class="btn btn-sm btn-danger" onclick="deleteProduct(${p.id})">删除</button>
        </td>
      </tr>`).join('');

    // 同步到分析页下拉框
    const sel = $('#analysis-keyword');
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部关键词</option>' + list.map((p) =>
      `<option value="${esc(p.keyword)}">${esc(p.keyword)}</option>`).join('');
    if (cur) sel.value = cur;
  } catch (e) {
    toast('加载商品失败: ' + e.message, true);
  }
}

$('#product-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const r = await api('/api/products', {
      method: 'POST',
      body: JSON.stringify({
        keyword: $('#pf-keyword').value.trim(),
        max_price: $('#pf-max').value,
        min_price: $('#pf-min').value || 0,
        exclude_keywords: $('#pf-exclude').value.trim(),
        must_include: $('#pf-must').value.trim(),
      }),
    });
    if (r.ok) {
      toast('已添加监控商品');
      $('#product-form').reset();
      loadProducts();
    } else {
      toast(r.error || '添加失败', true);
    }
  } catch (err) {
    toast('添加失败: ' + err.message, true);
  }
});

let editingId = null;

function openModal(title) {
  $('#modal-title').textContent = title;
  $('#modal').hidden = false;
}

function closeModal() {
  $('#modal').hidden = true;
  editingId = null;
}

async function editProduct(id) {
  const list = await api('/api/products');
  const p = list.find((x) => x.id === id);
  if (!p) return;
  editingId = id;
  $('#me-keyword').value = p.keyword;
  $('#me-max').value = p.max_price;
  $('#me-min').value = p.min_price;
  $('#me-exclude').value = p.exclude_keywords;
  $('#me-must').value = p.must_include || '';
  openModal('编辑商品');
}

async function openAddModal() {
  editingId = null;
  $('#me-keyword').value = '';
  $('#me-max').value = '';
  $('#me-min').value = '';
  $('#me-exclude').value = '';
  $('#me-must').value = '';
  openModal('添加商品');
}

// 取消按钮 / 点击遮罩 / Esc 键 都能关闭弹窗
$('#modal-cancel').addEventListener('click', closeModal);
$('#modal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#modal').hidden) closeModal();
});

$('#modal-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    keyword: $('#me-keyword').value.trim(),
    max_price: $('#me-max').value,
    min_price: $('#me-min').value || 0,
    exclude_keywords: $('#me-exclude').value.trim(),
    must_include: $('#me-must').value.trim(),
  };
  try {
    if (editingId) {
      await api('/api/products/' + editingId, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      toast('已保存');
    } else {
      const r = await api('/api/products', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(r.error || '添加失败');
      toast('已新增监控商品');
    }
    closeModal();
    loadProducts();
  } catch (err) {
    toast('保存失败: ' + err.message, true);
  }
});

async function toggleProduct(id) {
  await api('/api/products/' + id + '/toggle', { method: 'POST' });
  loadProducts();
}

async function deleteProduct(id) {
  if (!confirm('确定删除该监控商品？')) return;
  await api('/api/products/' + id, { method: 'DELETE' });
  toast('已删除');
  loadProducts();
}

window.editProduct = editProduct;
window.toggleProduct = toggleProduct;
window.deleteProduct = deleteProduct;
window.openAddModal = openAddModal;

/* ───────── 市场分析 ───────── */
let analysisData = null;
let distChart = null;
let trendChart = null;
let activeFilter = null;
let priceSort = null; // null=默认 | 'asc' | 'desc'

$('#analysis-keyword').addEventListener('change', () => {
  activeFilter = null;
  loadAnalysis();
});
$('#btn-analysis-refresh').addEventListener('click', loadAnalysis);
$('#btn-apply-price-filter').addEventListener('click', () => {
  activeFilter = null;
  loadAnalysis();
});
$('#btn-clear-price-filter').addEventListener('click', () => {
  $('#filter-price-min').value = '';
  $('#filter-price-max').value = '';
  activeFilter = null;
  $('#item-filter-badge').hidden = true;
  $('#btn-clear-price-filter').hidden = true;
  loadAnalysis();
});
// 价格排序：默认 → 升序 → 降序 循环
$('#btn-sort-price').addEventListener('click', () => {
  priceSort = priceSort === null ? 'asc' : priceSort === 'asc' ? 'desc' : null;
  $('#btn-sort-price').textContent =
    priceSort === null ? '价格: 默认' : priceSort === 'asc' ? '价格: 升序 ↑' : '价格: 降序 ↓';
  if (analysisData) renderAnalysisTable(analysisData.items, activeFilter);
});
// 回车同样触发筛选
['filter-price-min', 'filter-price-max'].forEach((id) => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { activeFilter = null; loadAnalysis(); }
  });
});

async function loadAnalysis() {
  try {
    const kw = $('#analysis-keyword').value;
    const pmin = $('#filter-price-min').value.trim();
    const pmax = $('#filter-price-max').value.trim();
    let url = '/api/analysis?keyword=' + encodeURIComponent(kw || 'all');
    if (pmin) url += '&price_min=' + encodeURIComponent(pmin);
    if (pmax) url += '&price_max=' + encodeURIComponent(pmax);
    const data = await api(url);
    if (!data.ok) {
      toast(data.error || '暂无数据', true);
      return;
    }
    analysisData = data;
    $('#items-count').textContent = `共 ${data.items.length} 条商品`;
    // 显示当前价格筛选状态
    if (pmin || pmax) {
      const lo = pmin ? `¥${fmtPrice(Number(pmin))}` : '';
      const hi = pmax ? `¥${fmtPrice(Number(pmax))}` : '';
      $('#item-filter-badge').textContent = lo && hi ? `${lo} -- ${hi}` : (lo || hi);
      $('#item-filter-badge').hidden = false;
      $('#btn-clear-price-filter').hidden = false;
    }
    renderDistribution(data);
    renderTrend(data);
    renderAnalysisTable(data.items, null);
  } catch (e) {
    toast('加载分析失败: ' + e.message, true);
  }
}

function renderDistribution(data) {
  const el = $('#chart-distribution');
  if (!distChart) distChart = echarts.init(el);
  const bins = data.distribution;
  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const b = bins[params[0].dataIndex];
        return `${fmtPrice(b.from)} ~ ${fmtPrice(b.to)}<br>${b.count} 件商品`;
      },
    },
    grid: { left: 50, right: 16, top: 24, bottom: 40 },
    xAxis: {
      type: 'category',
      data: bins.map((b) => Math.round(b.from)),
      axisLabel: {
        color: '#8b93a7', fontSize: 11,
        formatter: (v) => fmtAxisPrice(Number(v)),
      },
      axisLine: { lineStyle: { color: '#2c3242' } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#8b93a7', fontSize: 11 },
      splitLine: { lineStyle: { color: '#222736' } },
    },
    series: [{
      type: 'bar',
      data: bins.map((b) => b.count),
      itemStyle: { color: '#4f8cff', borderRadius: [4, 4, 0, 0] },
      emphasis: { itemStyle: { color: '#34d399' } },
      barMaxWidth: 36,
    }],
  };
  distChart.setOption(option, true);
  distChart.off('click');
  distChart.on('click', (params) => {
    const b = bins[params.dataIndex];
    activeFilter = { from: b.from, to: b.to };
    $('#item-filter-badge').textContent = `${fmtPrice(b.from)} ~ ${fmtPrice(b.to)}`;
    $('#item-filter-badge').hidden = false;
    renderAnalysisTable(data.items, activeFilter);
  });
}

function renderTrend(data) {
  const el = $('#chart-trend');
  if (!trendChart) trendChart = echarts.init(el);
  const points = data.trend;
  if (!points.length) {
    trendChart.clear();
    trendChart.setOption({
      title: { text: '暂无历史数据，等待监控运行', left: 'center', top: 'middle', textStyle: { color: '#8b93a7', fontSize: 13, fontWeight: 'normal' } },
    });
    return;
  }
  const times = points.map((p) => p.time.slice(5, 16));
  const hasFiltered = points.some((p) => p.filtered_avg != null);
  const legendData = ['过滤后均价', '中位数', '均值'];
  if (hasFiltered && points.some((p) => p.max)) legendData.push('最低');
  if (points.some((p) => p.max)) legendData.push('最高');
  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '—' : fmtPrice(v)),
    },
    legend: { data: legendData, textStyle: { color: '#8b93a7', fontSize: 11 }, top: 0 },
    grid: { left: 56, right: 16, top: 32, bottom: 40 },
    xAxis: {
      type: 'category',
      data: times,
      axisLabel: { color: '#8b93a7', fontSize: 11 },
      axisLine: { lineStyle: { color: '#2c3242' } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: {
        color: '#8b93a7', fontSize: 11,
        formatter: (v) => fmtAxisPrice(v),
      },
      splitLine: { lineStyle: { color: '#222736' } },
    },
    series: (() => {
      const series = [];
      // 过滤后均价（剔除配件/无关配置后的主流参考价），重点标注
      if (hasFiltered) {
        series.push({
          name: '过滤后均价', type: 'line', data: points.map((p) => p.filtered_avg),
          smooth: true, symbol: 'circle', symbolSize: 6, lineStyle: { width: 3 }, itemStyle: { color: '#a78bfa' },
        });
      }
      series.push(
        { name: '中位数', type: 'line', data: points.map((p) => p.median), smooth: true, symbol: 'circle', symbolSize: 5, lineStyle: { width: 2 }, itemStyle: { color: '#4f8cff' } },
        { name: '均值', type: 'line', data: points.map((p) => p.avg), smooth: true, symbol: 'circle', symbolSize: 4, itemStyle: { color: '#34d399' } },
      );
      if (points.some((p) => p.max)) {
        series.push(
          { name: '最低', type: 'line', data: points.map((p) => p.min), smooth: true, symbol: 'none', lineStyle: { opacity: .5 }, itemStyle: { color: '#fbbf24' } },
          { name: '最高', type: 'line', data: points.map((p) => p.max), smooth: true, symbol: 'none', lineStyle: { opacity: .5 }, itemStyle: { color: '#f87171' } },
        );
      }
      return series;
    })(),
  };
  trendChart.setOption(option, true);
}

function renderAnalysisTable(items, filter) {
  const tbody = $('#analysis-table tbody');
  let rows = items;
  if (filter) {
    rows = items.filter((it) => it.price >= filter.from && it.price < filter.to);
  }
  if (priceSort) {
    rows = [...rows].sort((a, b) => priceSort === 'asc' ? a.price - b.price : b.price - a.price);
  }
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无商品数据</td></tr>';
    return;
  }
  rows = rows.slice(0, 100);
  tbody.innerHTML = rows.map((it) => `
    <tr>
      <td class="price">${fmtPrice(it.price)}</td>
      <td title="${esc(it.title)}">${esc(it.title.slice(0, 45))}${it.title.length > 45 ? '…' : ''}</td>
      <td>${esc(it.location || '—')}</td>
      <td>${it.seller_credit ? esc(it.seller_credit) : '—'}</td>
      <td>${esc(it.status || '—')}</td>
      <td>${esc(it.first_seen || '')}</td>
      <td><span class="badge ${it.notified ? 'badge-yes' : 'badge-no'}">${it.notified ? '已推送' : '未推送'}</span></td>
      <td><a class="item-link" href="${esc(it.url)}" target="_blank">打开商品 →</a></td>
    </tr>`).join('');
}

/* ───────── 降价记录 ───────── */
async function loadDrops() {
  try {
    const list = await api('/api/price-changes');
    const tbody = $('#drops-table tbody');
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无降价记录</td></tr>';
      return;
    }
    tbody.innerHTML = list.map((c) => `
      <tr>
        <td>${esc(c.time)}</td>
        <td class="price"><s>${fmtPrice(c.old_price)}</s> → ${fmtPrice(c.new_price)}</td>
        <td title="${esc(c.title)}">${esc(c.title.slice(0, 40))}</td>
        <td>${esc(c.keyword)}</td>
        <td>${c.item_id ? `<a class="item-link" href="https://www.goofish.com/item?id=${esc(c.item_id)}" target="_blank">打开 →</a>` : ''}</td>
      </tr>`).join('');
  } catch (e) {
    toast('加载降价记录失败: ' + e.message, true);
  }
}

/* ───────── 通知记录 ───────── */
async function loadNotifications() {
  try {
    const list = await api('/api/notifications');
    const tbody = $('#notifications-table tbody');
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无通知记录</td></tr>';
      return;
    }
    tbody.innerHTML = list.map((n) => `
      <tr>
        <td>${esc(n.time)}</td>
        <td class="price">${fmtPrice(n.price)}</td>
        <td title="${esc(n.title)}">${esc(n.title.slice(0, 40))}</td>
        <td>${esc(n.keyword)}</td>
        <td>${esc(n.channel)}</td>
        <td>${n.url ? `<a class="item-link" href="${esc(n.url)}" target="_blank">打开 →</a>` : ''}</td>
      </tr>`).join('');
  } catch (e) {
    toast('加载通知失败: ' + e.message, true);
  }
}

$('#btn-test-notify').addEventListener('click', async () => {
  try {
    const r = await api('/api/test-notify', { method: 'POST' });
    if (r.ok) toast('测试通知已发送，请检查手机');
    else toast('通知渠道未启用，请在 config.py 中配置', true);
  } catch (e) {
    toast('发送失败: ' + e.message, true);
  }
});

/* ───────── 设置 ───────── */
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    $('#set-interval').value = s.interval_minutes;
    $('#set-headless').value = s.headless === 'true' ? 'true' : 'false';

    const channels = s.channels;
    const names = { bark: 'Bark (iOS)', pushplus: 'PushPlus (微信)', smtp: '邮件 SMTP', telegram: 'Telegram' };
    $('#channel-list').innerHTML = Object.keys(names).map((k) => `
      <div class="channel-item">
        <span class="channel-name">${names[k]}</span>
        <span class="badge ${channels[k] ? 'badge-yes' : 'badge-off'}">${channels[k] ? '已启用' : '未启用'}</span>
      </div>`).join('');

    $('#info-list').innerHTML = `
      <div class="info-item"><span>数据库</span><span>${esc(s.data_dir)}</span></div>
      <div class="info-item"><span>浏览器配置</span><span>${esc(s.user_data_dir)}</span></div>
      <div class="info-item"><span>数据文件</span><span>monitor.db</span></div>`;
  } catch (e) {
    toast('加载设置失败: ' + e.message, true);
  }
}

$('#btn-save-interval').addEventListener('click', async () => {
  try {
    await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({ interval_minutes: parseInt($('#set-interval').value, 10) }),
    });
    toast('间隔已保存，下一轮生效');
  } catch (e) {
    toast('保存失败: ' + e.message, true);
  }
});

$('#btn-save-headless').addEventListener('click', async () => {
  try {
    await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({ headless: $('#set-headless').value === 'true' }),
    });
    toast('已保存，重启程序后生效');
  } catch (e) {
    toast('保存失败: ' + e.message, true);
  }
});

$('#btn-export').addEventListener('click', () => {
  window.open('/api/export', '_blank');
});

// 保留策略
async function loadRetention() {
  try {
    const r = await api('/api/retention');
    $('#ret-items').value = r.items_days;
    $('#ret-history').value = r.history_days;
    $('#ret-checks').value = r.checks_keep;
    $('#ret-notifs').value = r.notifications_keep;
  } catch (e) { /* ignore */ }
}
$('#btn-save-retention').addEventListener('click', async () => {
  try {
    await api('/api/retention', {
      method: 'POST',
      body: JSON.stringify({
        items_days: parseInt($('#ret-items').value, 10),
        history_days: parseInt($('#ret-history').value, 10),
        checks_keep: parseInt($('#ret-checks').value, 10),
        notifications_keep: parseInt($('#ret-notifs').value, 10),
      }),
    });
    toast('保留策略已保存');
  } catch (e) { toast('保存失败: ' + e.message, true); }
});
$('#btn-cleanup').addEventListener('click', async () => {
  try { const r = await api('/api/cleanup', { method: 'POST', body: JSON.stringify({}) }); toast(`已清理：商品 ${r.stats.items_deleted} 条 / 历史 ${r.stats.history_deleted} 条`); } catch (e) { toast('清理失败: ' + e.message, true); }
});
$('#btn-cleanup-vacuum').addEventListener('click', async () => {
  const btn = $('#btn-cleanup-vacuum'); btn.disabled = true; btn.textContent = '回收中…';
  try { const r = await api('/api/cleanup', { method: 'POST', body: JSON.stringify({ vacuum: true }) }); toast(`已清理并回收`); } catch (e) { toast('回收失败: ' + e.message, true); } finally { btn.disabled = false; btn.textContent = '清理并回收空间'; }
});

$('#btn-clear-items').addEventListener('click', async () => {
  if (!confirm('确定清空全部商品数据？此操作不可恢复。')) return;
  try {
    await api('/api/clear-items', { method: 'POST' });
    toast('已清空');
    loadSettings();
  } catch (e) {
    toast('清空失败: ' + e.message, true);
  }
});

/* ───────── 顶部操作按钮 ───────── */
$('#btn-check-now').addEventListener('click', async () => {
  const btn = $('#btn-check-now');
  btn.disabled = true;
  btn.textContent = '已触发，等待浏览器…';
  try {
    await api('/api/control', {
      method: 'POST',
      body: JSON.stringify({ action: 'check_now' }),
    });
    toast('已触发立即检查');
  } catch (e) {
    toast('触发失败: ' + e.message, true);
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = '🔄 立即检查'; }, 2000);
  }
});

$('#btn-refresh').addEventListener('click', () => {
  loadOverview();
  loadProducts();
  loadNotifications();
  loadDrops();
  toast('数据已刷新');
});

/* ───────── Bark 推送管理 ───────── */
async function loadBarkTargets() {
  try {
    const list = await api('/api/bark-targets');
    const tbody = $('#bark-table tbody');
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">还没有 Bark 推送地址，在上方添加一个吧</td></tr>';
      return;
    }
    tbody.innerHTML = list.map((t) => `
      <tr>
        <td><span class="badge ${t.enabled ? 'badge-yes' : 'badge-off'}">${t.enabled ? '启用' : '停用'}</span></td>
        <td><strong>${esc(t.label || '—')}</strong></td>
        <td style="max-width:220px; overflow:hidden; text-overflow:ellipsis">${esc(t.server)}</td>
        <td><code style="font-size:12px">${esc(t.bark_key_masked)}</code></td>
        <td>${esc(t.created_at)}</td>
        <td style="display:flex; gap:4px; flex-wrap:wrap">
          <button class="btn btn-sm" onclick="editBarkTarget(${t.id})">编辑</button>
          <button class="btn btn-sm" onclick="toggleBarkTarget(${t.id})">${t.enabled ? '停用' : '启用'}</button>
          <button class="btn btn-sm" onclick="testBarkTarget(${t.id})">测试</button>
          <button class="btn btn-sm btn-danger" onclick="deleteBarkTarget(${t.id})">删除</button>
        </td>
      </tr>`).join('');
  } catch (e) {
    toast('加载 Bark 列表失败: ' + e.message, true);
  }
}

let editingBarkId = null;

$('#bark-add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const label = $('#bark-label').value.trim();
  const server = $('#bark-server').value.trim() || 'https://api.day.app';
  const bark_key = $('#bark-key').value.trim();
  if (!bark_key) { toast('Bark Key 不能为空', true); return; }
  try {
    const r = await api('/api/bark-targets', {
      method: 'POST',
      body: JSON.stringify({ label, server, bark_key }),
    });
    if (!r.ok) throw new Error(r.error || '添加失败');
    toast('已添加 Bark 推送地址');
    $('#bark-add-form').reset();
    $('#bark-server').value = 'https://api.day.app';
    loadBarkTargets();
  } catch (err) {
    toast('添加失败: ' + err.message, true);
  }
});

$('#btn-bark-refresh').addEventListener('click', loadBarkTargets);

async function editBarkTarget(id) {
  const list = await api('/api/bark-targets');
  const t = list.find((x) => x.id === id);
  if (!t) return;
  editingBarkId = id;
  $('#bark-edit-label').value = t.label || '';
  $('#bark-edit-server').value = t.server || 'https://api.day.app';
  // Key 只返回脱敏值；留空表示编辑时保持原 Key 不变。
  $('#bark-edit-key').value = '';
  $('#bark-edit-modal').hidden = false;
}

async function toggleBarkTarget(id) {
  await api('/api/bark-targets/' + id + '/toggle', { method: 'POST' });
  loadBarkTargets();
}

async function deleteBarkTarget(id) {
  if (!confirm('确定删除该 Bark 推送地址？')) return;
  await api('/api/bark-targets/' + id, { method: 'DELETE' });
  toast('已删除');
  loadBarkTargets();
}

async function testBarkTarget(id) {
  try {
    const r = await api('/api/bark-targets/' + id + '/test', { method: 'POST' });
    if (r.ok) toast('测试推送已发送，请检查手机');
    else toast('测试失败，请检查 Key 与服务器', true);
  } catch (e) {
    toast('测试失败: ' + e.message, true);
  }
}

$('#bark-edit-cancel').addEventListener('click', () => { $('#bark-edit-modal').hidden = true; editingBarkId = null; });
$('#bark-edit-modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) { $('#bark-edit-modal').hidden = true; editingBarkId = null; } });

$('#bark-edit-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    label: $('#bark-edit-label').value.trim(),
    server: $('#bark-edit-server').value.trim() || 'https://api.day.app',
    bark_key: $('#bark-edit-key').value.trim(),
  };
  try {
    await api('/api/bark-targets/' + editingBarkId, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    toast('已保存');
    $('#bark-edit-modal').hidden = true;
    editingBarkId = null;
    loadBarkTargets();
  } catch (err) {
    toast('保存失败: ' + err.message, true);
  }
});

window.editBarkTarget = editBarkTarget;
window.toggleBarkTarget = toggleBarkTarget;
window.deleteBarkTarget = deleteBarkTarget;
window.testBarkTarget = testBarkTarget;

/* ───────── 新用户引导（防呆）───────── */
async function checkOnboarding() {
  let status;
  try {
    status = await api('/api/onboarding/status');
  } catch (e) {
    return; // 后端不可用时跳过引导
  }
  if (!status.need_notify && !status.need_product) return;
  $('#onboarding').hidden = false;
  // 只需要配商品时，直接进入第 2 步
  if (!status.need_notify && status.need_product) {
    $('#ob-step-notify').hidden = true;
    $('#ob-step-product').hidden = false;
  }
}

function closeOnboarding() {
  $('#onboarding').hidden = true;
}

async function obSaveNotify() {
  const barkKey = $('#ob-bark-key').value.trim();
  const smtpHost = $('#ob-smtp-host').value.trim();
  if (!barkKey && !smtpHost) {
    toast('请填写 Bark Key 或邮件 SMTP 配置（或点"跳过"）', true);
    return;
  }
  try {
    if (barkKey) {
      const r = await api('/api/bark-targets', {
        method: 'POST',
        body: JSON.stringify({
          label: '默认',
          server: $('#ob-bark-server').value.trim() || 'https://api.day.app',
          bark_key: barkKey,
        }),
      });
      if (!r.ok) throw new Error(r.error || 'Bark 添加失败');
    }
    if (smtpHost) {
      const r = await api('/api/channel-config', {
        method: 'POST',
        body: JSON.stringify({
          smtp: {
            host: smtpHost,
            port: $('#ob-smtp-port').value || 465,
            user: $('#ob-smtp-user').value.trim(),
            password: $('#ob-smtp-pass').value.trim(),
            to: $('#ob-smtp-to').value.trim(),
          },
        }),
      });
      if (!r.ok) throw new Error(r.error || 'SMTP 保存失败');
    }
    toast('通知渠道已配置 ✅');
    // 若产品也已就绪，直接完成引导；否则进入步骤 2
    try {
      const s = await api('/api/onboarding/status');
      if (!s.need_product) { closeOnboarding(); loadSettings(); return; }
    } catch (e) { /* 忽略，继续显示步骤 2 */ }
    $('#ob-step-notify').hidden = true;
    $('#ob-step-product').hidden = false;
  } catch (e) {
    toast('保存失败: ' + e.message, true);
  }
}

async function obSaveProduct() {
  const keyword = $('#ob-prod-keyword').value.trim();
  const maxPrice = $('#ob-prod-max').value;
  if (!keyword || !maxPrice) {
    toast('请填写关键词和最高价格（或点"跳过"）', true);
    return;
  }
  try {
    const r = await api('/api/products', {
      method: 'POST',
      body: JSON.stringify({
        keyword,
        max_price: maxPrice,
        min_price: $('#ob-prod-min').value || 0,
        exclude_keywords: $('#ob-prod-exclude').value.trim(),
        must_include: '',
      }),
    });
    if (!r.ok) throw new Error(r.error || '添加失败');
    toast('监控商品已添加 🎉');
  } catch (e) {
    toast('添加失败: ' + e.message, true);
    return;
  }
  closeOnboarding();
  loadProducts();
  loadSettings();
}

$('#ob-notify-save').addEventListener('click', obSaveNotify);
$('#ob-notify-skip').addEventListener('click', () => {
  $('#ob-step-notify').hidden = true;
  $('#ob-step-product').hidden = false;
});
$('#ob-prod-save').addEventListener('click', obSaveProduct);
$('#ob-prod-skip').addEventListener('click', () => {
  closeOnboarding();
  loadProducts();
});

/* ───────── 初始化 ───────── */
refreshStatus();
loadOverview();
loadProducts();
loadNotifications();
loadDrops();
loadSettings();
loadRetention();
checkOnboarding();

setInterval(refreshStatus, 5000);
