function formatNumber(value) {
  return Number(value || 0).toLocaleString('zh-CN');
}

function percent(part, total) {
  if (!total) return '0.0%';
  return ((part / total) * 100).toFixed(1) + '%';
}

function statusClass(status) {
  if (status === 'success') return 'status-success';
  if (status === 'failed') return 'status-failed';
  return 'status-interrupted';
}

function statusLabel(status) {
  if (status === 'success') return '成功';
  if (status === 'failed') return '失败';
  return '中断';
}

function formatDatetime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
    pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

/** Animated number counter */
function animateValue(el, target, duration) {
  const targetInt = Math.round(Number(target || 0));
  el.dataset.target = targetInt;
  const start = performance.now();
  const startVal = 0;

  function update(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    // ease-out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.round(startVal + (targetInt - startVal) * eased);
    el.textContent = formatNumber(current);
    if (progress < 1) {
      requestAnimationFrame(update);
    } else {
      el.textContent = formatNumber(targetInt);
    }
  }
  requestAnimationFrame(update);
}

let allProxyConfigs = [];
let currentPage = 1;
let pageSize = 20;
let totalRecords = 0;

async function fetchProxyConfigs() {
  try {
    const resp = await fetch('/api/v1/proxy/configs');
    allProxyConfigs = await resp.json();
  } catch {
    allProxyConfigs = [];
  }
}

function renderProxyFilter() {
  const sel = document.getElementById('filterProxy');
  sel.innerHTML = '<option value="">全部代理</option>';
  for (const cfg of allProxyConfigs) {
    const opt = document.createElement('option');
    opt.value = cfg.provider;
    opt.textContent = cfg.display_name || cfg.proxy_key;
    sel.appendChild(opt);
  }
}

function getFilters() {
  return {
    provider: document.getElementById('filterProxy').value,
    start_date: document.getElementById('filterStart').value,
    end_date: document.getElementById('filterEnd').value,
  };
}

async function loadSummary(filters) {
  const params = new URLSearchParams();
  if (filters.provider) params.set('provider', filters.provider);
  if (filters.start_date) params.set('start_date', filters.start_date);
  if (filters.end_date) params.set('end_date', filters.end_date);
  const qs = params.toString();
  const url = '/api/v1/stats/summary' + (qs ? '?' + qs : '');
  try {
    const resp = await fetch(url);
    return await resp.json();
  } catch {
    return null;
  }
}

function renderSummary(summary, filters) {
  const requestEl = document.getElementById('requestCount');
  const successEl = document.getElementById('successCount');
  const tokenEl = document.getElementById('tokenCount');
  const savedEl = document.getElementById('tokenSaved');

  if (!summary) {
    requestEl.textContent = 'ERR';
    successEl.textContent = 'ERR';
    tokenEl.textContent = 'ERR';
    savedEl.textContent = 'ERR';
    return;
  }

  const reqCount = Number(summary.request_count || 0);
  const sucCount = Number(summary.success_count || 0);
  const tokCount = Number(summary.total_tokens || 0);
  const inputSaved = Number(summary.total_input_tokens_saved || 0);
  const outputSaved = Number(summary.total_output_tokens_saved || 0);
  const totalSaved = inputSaved + outputSaved;

  const animate = true;
  if (animate) {
    animateValue(requestEl, reqCount, 800);
    animateValue(successEl, sucCount, 800);
    animateValue(tokenEl, tokCount, 1000);
    animateValue(savedEl, totalSaved, 1000);
  } else {
    requestEl.textContent = formatNumber(reqCount);
    successEl.textContent = formatNumber(sucCount);
    tokenEl.textContent = formatNumber(tokCount);
    savedEl.textContent = formatNumber(totalSaved);
  }

  document.getElementById('successRate').textContent = '成功率 ' + percent(sucCount, reqCount);
  document.getElementById('avgToken').textContent = '平均每次 ' + formatNumber(reqCount ? Math.round(tokCount / reqCount) : 0) + ' tokens';
  document.getElementById('savedRate').textContent =
    '节省率 ' + percent(totalSaved, totalSaved + tokCount) +
    ' (输入 ' + formatNumber(inputSaved) + ' / 输出 ' + formatNumber(outputSaved) + ')';

  const latestEl = document.getElementById('latestDate');
  if (filters.start_date || filters.end_date) {
    const parts = [];
    if (filters.start_date) parts.push('从 ' + filters.start_date);
    if (filters.end_date) parts.push('至 ' + filters.end_date);
    latestEl.textContent = parts.join(' ');
  } else {
    latestEl.textContent = '全部时间';
  }

  totalRecords = reqCount;
}

async function loadUsageList(filters, page, size) {
  const params = new URLSearchParams();
  params.set('limit', String(size));
  params.set('offset', String((page - 1) * size));
  if (filters.provider) params.set('provider', filters.provider);
  if (filters.start_date) params.set('start_date', filters.start_date);
  if (filters.end_date) params.set('end_date', filters.end_date);
  const qs = params.toString();
  const url = '/api/v1/usage/list' + (qs ? '?' + qs : '');
  try {
    const resp = await fetch(url);
    return await resp.json();
  } catch {
    return [];
  }
}

function renderTable(rows) {
  const tbody = document.getElementById('usageTableBody');
  const empty = document.getElementById('tableEmpty');
  const loading = document.getElementById('tableLoading');
  const count = document.getElementById('recordCount');

  loading.style.display = 'none';

  if (!rows || rows.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'grid';
    count.textContent = '';
    return;
  }

  empty.style.display = 'none';

  tbody.innerHTML = rows.map((row, i) => {
    const proxyName = (allProxyConfigs.find(c => c.provider === row.provider) || {}).display_name || row.provider;
    const cacheCreate = Number(row.cache_creation_tokens || 0);
    const cacheRead = Number(row.cache_read_tokens || 0);
    const cacheTotal = cacheCreate + cacheRead;
    const cacheLabel = cacheTotal > 0
      ? (cacheCreate > 0 ? ('创 ' + formatNumber(cacheCreate)) : '') +
        (cacheCreate > 0 && cacheRead > 0 ? ' / ' : '') +
        (cacheRead > 0 ? ('读 ' + formatNumber(cacheRead)) : '')
      : '-';
    const inputSaved = Number(row.input_tokens_saved || 0);
    const outputSaved = Number(row.output_tokens_saved || 0);
    const totalSaved = inputSaved + outputSaved;
    const savedLabel = totalSaved > 0
      ? (inputSaved > 0 ? ('入-' + formatNumber(inputSaved)) : '') +
        (inputSaved > 0 && outputSaved > 0 ? ' ' : '') +
        (outputSaved > 0 ? ('出-' + formatNumber(outputSaved)) : '')
      : '-';
    const seq = (currentPage - 1) * pageSize + i + 1;
    return '<tr>' +
      '<td class="num">' + seq + '</td>' +
      '<td>' + escapeHtml(proxyName) + '</td>' +
      '<td>' + escapeHtml(row.model) + '</td>' +
      '<td>' + formatDatetime(row.created_at) + '</td>' +
      '<td class="num">' + formatNumber(row.input_tokens || 0) + '</td>' +
      '<td class="num">' + formatNumber(row.output_tokens || 0) + '</td>' +
      '<td class="num">' + cacheLabel + '</td>' +
      '<td class="num">' + formatNumber(row.total_tokens || 0) + '</td>' +
      '<td class="num saved-cell">' + (totalSaved > 0 ? '<span class="saved-badge">' + formatNumber(totalSaved) + '</span>' : '-') + '</td>' +
      '<td><span class="status-badge ' + statusClass(row.status) + '">' + statusLabel(row.status) + '</span></td>' +
      '<td class="num">' + formatNumber(row.latency_ms) + '</td>' +
      '</tr>';
  }).join('');
}

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function updatePagination() {
  const totalPages = Math.max(1, Math.ceil(totalRecords / pageSize));
  if (currentPage > totalPages) currentPage = totalPages;
  if (currentPage < 1) currentPage = 1;

  document.getElementById('pageInfo').textContent =
    '第 ' + currentPage + '/' + totalPages + ' 页';
  document.getElementById('prevPageBtn').disabled = currentPage <= 1;
  document.getElementById('nextPageBtn').disabled = currentPage >= totalPages;

  const count = document.getElementById('recordCount');
  const start = (currentPage - 1) * pageSize + 1;
  const end = Math.min(currentPage * pageSize, totalRecords);
  if (totalRecords > 0) {
    count.textContent = '第 ' + start + '-' + end + ' 条，共 ' + formatNumber(totalRecords) + ' 条';
  } else {
    count.textContent = '';
  }
}

let refreshTimeout = null;

async function refresh(resetPage) {
  if (resetPage) currentPage = 1;

  const filters = getFilters();
  const loading = document.getElementById('tableLoading');
  const empty = document.getElementById('tableEmpty');
  loading.style.display = 'grid';
  empty.style.display = 'none';

  const [summary, rows] = await Promise.all([
    loadSummary(filters),
    loadUsageList(filters, currentPage, pageSize),
  ]);

  renderSummary(summary, filters);
  renderTable(rows);
  updatePagination();
}

async function init() {
  await fetchProxyConfigs();
  renderProxyFilter();

  document.getElementById('filterBtn').addEventListener('click', function () {
    refresh(true);
  });

  document.getElementById('pageSizeSelect').addEventListener('change', function () {
    pageSize = parseInt(this.value, 10);
    currentPage = 1;
    refresh(false);
  });

  document.getElementById('prevPageBtn').addEventListener('click', function () {
    if (currentPage > 1) {
      currentPage--;
      refresh(false);
    }
  });

  document.getElementById('nextPageBtn').addEventListener('click', function () {
    const totalPages = Math.ceil(totalRecords / pageSize);
    if (currentPage < totalPages) {
      currentPage++;
      refresh(false);
    }
  });

  // Keyboard shortcut for search
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT' && e.target.tagName !== 'TEXTAREA') {
      refresh(true);
    }
  });

  await refresh(true);
}

init();
