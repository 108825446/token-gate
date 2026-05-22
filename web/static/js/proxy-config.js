let modalMode = 'create';
let editingKey = null;
let currentConfigs = [];

function showStatus(message, isError = false) {
  const el = document.getElementById('globalStatus');
  el.textContent = message;
  el.className = isError ? 'status error' : 'status';
  el.style.display = 'block';
}

function prettyHeaders(headers) {
  return JSON.stringify(headers || {}, null, 2);
}

function defaultValuesForProvider(provider) {
  if (provider === 'anthropic') {
    return {
      auth_header: 'x-api-key',
      api_key_env: 'ANTHROPIC_API_KEY',
      api_key_prefix: '',
      static_headers: { 'anthropic-version': '2023-06-01' },
    };
  }
  return {
    auth_header: 'Authorization',
    api_key_env: 'OPENAI_API_KEY',
    api_key_prefix: 'Bearer',
    static_headers: {},
  };
}

function renderConfigRow(item) {
  const enabledClass = item.enabled ? 'badge' : 'badge off';
  const enabledText = item.enabled ? 'Enabled' : 'Disabled';
  const saverBadge = item.token_saver_enabled
    ? `<span class="badge saver">Token Saver: ${item.token_saver_input_level || 'full'}/${item.token_saver_output_level || 'full'}</span>`
    : '';
  return `
    <tr data-provider="${item.provider}" data-proxy-key="${item.proxy_key}">
      <td>
        <div class="proxy-name">${item.display_name || item.proxy_key}</div>
        <div class="proxy-meta">${item.provider} / ${item.proxy_key} ${saverBadge}</div>
      </td>
      <td><span class="${enabledClass}">${enabledText}</span></td>
      <td><div class="proxy-url">${item.proxy_base_url}</div></td>
      <td><div class="proxy-url">${item.base_url}</div></td>
      <td>
        <div class="row-actions">
          <button type="button" class="secondary" onclick="openEditModal('${item.provider}','${item.proxy_key}')">编辑</button>
          <button type="button" class="secondary" onclick="toggleConfig('${item.provider}','${item.proxy_key}')">${item.enabled ? '禁用' : '启用'}</button>
          <button type="button" class="danger" onclick="deleteConfig('${item.provider}','${item.proxy_key}')">删除</button>
        </div>
      </td>
    </tr>
  `;
}

function closeModal() {
  document.getElementById('configModal').classList.remove('open');
}

function fillForm(item, isCreate) {
  const form = document.getElementById('configForm');
  const defaults = defaultValuesForProvider(item.provider || 'openai');
  form.provider.value = item.provider || 'openai';
  form.proxy_key.value = item.proxy_key || 'default';
  form.display_name.value = item.display_name || 'Default Proxy';
  form.base_url.value = item.base_url || '';
  form.auth_header.value = item.auth_header ?? defaults.auth_header;
  form.api_key_env.value = item.api_key_env ?? defaults.api_key_env;
  form.api_key_prefix.value = item.api_key_prefix ?? defaults.api_key_prefix;
  form.timeout_seconds.value = item.timeout_seconds || 60;
  form.forward_user_auth.value = String(item.forward_user_auth ?? false);
  form.enabled.value = String(item.enabled ?? true);
  form.ssl_verify.value = String(item.ssl_verify ?? true);
  form.token_saver_enabled.value = String(item.token_saver_enabled ?? false);
  form.token_saver_input_level.value = item.token_saver_input_level || 'full';
  form.token_saver_output_level.value = item.token_saver_output_level || 'full';
  form.static_headers.value = prettyHeaders(item.static_headers ?? defaults.static_headers);
  form.provider.disabled = !isCreate;
  form.proxy_key.readOnly = !isCreate;
}

function openCreateModal() {
  modalMode = 'create';
  editingKey = null;
  document.getElementById('modalTitle').textContent = '新增代理';
  document.getElementById('modalSub').textContent = '创建新的代理实例并生成独立接入地址。';
  document.getElementById('modalHint').textContent = '保存后会生成独立代理地址。';
  fillForm({ provider: 'openai', proxy_key: 'default', display_name: 'Default Proxy' }, true);
  document.getElementById('configModal').classList.add('open');
}

function openEditModal(provider, proxyKey) {
  const item = currentConfigs.find(config => config.provider === provider && config.proxy_key === proxyKey);
  if (!item) return;
  modalMode = 'edit';
  editingKey = { provider, proxyKey };
  document.getElementById('modalTitle').textContent = '编辑代理';
  document.getElementById('modalSub').textContent = '修改现有代理实例的上游与启用状态。';
  document.getElementById('modalHint').textContent = `当前代理地址：${item.proxy_base_url}`;
  fillForm(item, false);
  document.getElementById('configModal').classList.add('open');
}

async function loadConfigs() {
  const resp = await fetch('/api/v1/proxy/configs');
  const data = await resp.json();
  currentConfigs = data;
  document.getElementById('configRows').innerHTML = data.map(renderConfigRow).join('');
}

async function submitModal(event) {
  event.preventDefault();
  const form = document.getElementById('configForm');
  const provider = form.provider.value;
  const proxyKey = form.proxy_key.value.trim();
  let staticHeaders = {};
  try {
    staticHeaders = JSON.parse(form.static_headers.value || '{}');
  } catch (error) {
    showStatus(`${provider}/${proxyKey} 的 static_headers 不是合法 JSON`, true);
    return;
  }
  const payload = {
    provider,
    proxy_key: proxyKey,
    display_name: form.display_name.value.trim() || proxyKey,
    base_url: form.base_url.value.trim(),
    auth_header: form.auth_header.value.trim(),
    api_key_env: form.api_key_env.value.trim() || null,
    api_key_prefix: form.api_key_prefix.value,
    forward_user_auth: form.forward_user_auth.value === 'true',
    timeout_seconds: Number(form.timeout_seconds.value || 60),
    enabled: form.enabled.value === 'true',
    ssl_verify: form.ssl_verify.value === 'true',
    static_headers: staticHeaders,
    token_saver_enabled: form.token_saver_enabled.value === 'true',
    token_saver_input_level: form.token_saver_input_level.value,
    token_saver_output_level: form.token_saver_output_level.value,
  };
  const isCreate = modalMode === 'create';
  const url = isCreate ? '/api/v1/proxy/configs' : `/api/v1/proxy/configs/${editingKey.provider}/${editingKey.proxyKey}`;
  const method = isCreate ? 'POST' : 'PUT';
  const resp = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await resp.json();
  if (!resp.ok) {
    showStatus(body.detail || `${provider}/${proxyKey} 保存失败`, true);
    return;
  }
  showStatus(`${provider}/${proxyKey} 配置已保存，代理地址是 ${body.proxy_base_url}`);
  closeModal();
  await loadConfigs();
}

async function toggleConfig(provider, proxyKey) {
  const item = currentConfigs.find(config => config.provider === provider && config.proxy_key === proxyKey);
  if (!item) return;
  // 显式构建 payload，只包含 ProxyConfigUpdateRequest 中定义的字段
  const payload = {
    provider: item.provider,
    proxy_key: item.proxy_key,
    display_name: item.display_name,
    base_url: item.base_url,
    auth_header: item.auth_header,
    api_key_env: item.api_key_env,
    api_key_prefix: item.api_key_prefix,
    forward_user_auth: item.forward_user_auth,
    timeout_seconds: item.timeout_seconds,
    enabled: !item.enabled,
    ssl_verify: item.ssl_verify,
    static_headers: item.static_headers,
    token_saver_enabled: item.token_saver_enabled,
    token_saver_input_level: item.token_saver_input_level,
    token_saver_output_level: item.token_saver_output_level,
  };
  const resp = await fetch(`/api/v1/proxy/configs/${provider}/${proxyKey}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await resp.json();
  if (!resp.ok) {
    showStatus(body.detail || `${provider}/${proxyKey} 状态更新失败`, true);
    return;
  }
  showStatus(`${provider}/${proxyKey} 已${body.enabled ? '启用' : '禁用'}`);
  await loadConfigs();
}

async function deleteConfig(provider, proxyKey) {
  const resp = await fetch(`/api/v1/proxy/configs/${provider}/${proxyKey}`, { method: 'DELETE' });
  const body = await resp.json();
  if (!resp.ok) {
    showStatus(body.detail || `${provider}/${proxyKey} 删除失败`, true);
    return;
  }
  showStatus(`已删除代理 ${provider}/${proxyKey}`);
  await loadConfigs();
}

async function reloadConfigs() {
  await fetch('/api/v1/proxy/configs/reload', { method: 'POST' });
  await loadConfigs();
  showStatus('代理配置已从磁盘重新加载');
}

document.getElementById('configForm').addEventListener('submit', submitModal);
document.getElementById('configForm').provider.addEventListener('change', (event) => {
  if (modalMode !== 'create') return;
  const defaults = defaultValuesForProvider(event.target.value);
  const form = document.getElementById('configForm');
  form.auth_header.value = defaults.auth_header;
  form.api_key_env.value = defaults.api_key_env;
  form.api_key_prefix.value = defaults.api_key_prefix;
  form.static_headers.value = prettyHeaders(defaults.static_headers);
});

loadConfigs();
