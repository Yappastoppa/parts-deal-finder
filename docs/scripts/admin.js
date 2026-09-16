import { API_BASE_URL } from './config.js';

const TOKEN_KEY = 'apf:admin:token';
const $ = (selector) => document.querySelector(selector);
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function money(value) {
  return value === null || value === undefined ? 'Price on request' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}
function notify(message) {
  const host = $('#notice'); host.replaceChildren(el('span', message));
  const close = el('button', '×', 'notice-close'); close.setAttribute('aria-label', 'Dismiss'); close.addEventListener('click', () => host.replaceChildren()); host.append(close);
}

if (!API_BASE_URL) {
  $('#login-error').textContent = 'No API_BASE_URL is configured in config.js. Admin requires a connected backend.';
}

function apiURL(path) { return new URL(path, new URL(API_BASE_URL).origin).href; }

async function apiRequest(path, { method, body } = {}) {
  const token = sessionStorage.getItem(TOKEN_KEY);
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const response = await fetch(apiURL(path), {
    method: method || (body !== undefined ? 'POST' : 'GET'), mode: 'cors', credentials: 'omit',
    headers, body: body !== undefined ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(20000),
  });
  let data = {};
  try { data = await response.json(); } catch { /* Some responses have no body. */ }
  if (response.status === 401) { signOut(); throw new Error('Session expired. Please sign in again.'); }
  if (!response.ok) throw new Error(data.message || 'The request failed.');
  return data;
}

function signOut() {
  sessionStorage.removeItem(TOKEN_KEY);
  $('#admin-app').hidden = true; $('#login-panel').hidden = false; $('#logout-button').hidden = true;
}

$('#logout-button').addEventListener('click', async () => {
  try { await apiRequest('/api/admin/logout', { method: 'POST', body: {} }); } catch { /* Token is discarded locally regardless. */ }
  signOut();
});

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('#login-error').textContent = '';
  try {
    const response = await fetch(apiURL('/api/admin/login'), {
      method: 'POST', mode: 'cors', credentials: 'omit', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: $('#admin-password').value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || 'Sign in failed.');
    sessionStorage.setItem(TOKEN_KEY, data.token);
    $('#admin-password').value = '';
    enterApp();
  } catch (error) {
    $('#login-error').textContent = error.message || 'Sign in failed. Please try again.';
  }
});

function table(rows, columns) {
  const host = el('div');
  if (!rows.length) { host.append(el('p', 'Nothing here yet.', 'muted')); return host; }
  for (const row of rows) {
    const card = el('article', undefined, 'listing admin-row');
    const body = el('div');
    for (const [label, render] of columns) {
      const line = el('p'); line.append(el('strong', label + ': '), document.createTextNode(render(row)));
      body.append(line);
    }
    card.append(body);
    host.append(card);
  }
  return host;
}

async function loadDashboard() {
  const stats = await apiRequest('/api/admin/dashboard');
  const host = $('#dashboard-stats'); host.replaceChildren();
  const entries = [
    ['Searches today', stats.searches_today], ['Cached searches', stats.cached_searches],
    ['Live listings today', stats.live_listings_today], ['Orders today', stats.orders_today],
    ['Recent failed searches', stats.recent_failed_searches],
  ];
  for (const [label, value] of entries) {
    const card = el('div', undefined, 'admin-stat'); card.append(el('strong', String(value)), el('span', label)); host.append(card);
  }
  const byStatus = el('p', 'Orders by status: ' + (Object.entries(stats.orders_by_status || {}).map(([k, v]) => `${k} (${v})`).join(', ') || 'none'), 'muted');
  host.append(byStatus);
}

async function loadSettings() {
  const { settings } = await apiRequest('/api/admin/settings');
  $('#maintenance-mode').checked = Boolean(settings.maintenance_mode);
  $('#announcement').value = settings.announcement || '';
}

$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await apiRequest('/api/admin/settings', { method: 'POST', body: { maintenance_mode: $('#maintenance-mode').checked, announcement: $('#announcement').value } });
    notify('Settings saved.');
  } catch (error) { notify(error.message); }
});

async function loadPricingRules() {
  const { rules } = await apiRequest('/api/admin/pricing-rules');
  const host = $('#pricing-rules'); host.replaceChildren();
  host.append(table(rules, [
    ['Scope', r => r.scope_type + (r.scope_value ? ` (${r.scope_value})` : '')],
    ['Markup', r => r.markup_type === 'percent' ? `${r.markup_value}%` : money(r.markup_value)],
    ['Minimum margin', r => r.min_margin === null || r.min_margin === undefined ? 'None' : money(r.min_margin)],
    ['Updated', r => new Date(r.updated_at * 1000).toLocaleString()],
  ]));
  // Add per-rule delete buttons.
  [...host.querySelectorAll('.admin-row')].forEach((row, index) => {
    const remove = el('button', 'Delete rule', 'secondary');
    remove.addEventListener('click', async () => {
      try { await apiRequest(`/api/admin/pricing-rules/${rules[index].id}`, { method: 'DELETE' }); notify('Rule deleted.'); loadPricingRules(); }
      catch (error) { notify(error.message); }
    });
    row.append(remove);
  });
}

$('#pricing-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await apiRequest('/api/admin/pricing-rules', { method: 'POST', body: {
      scope_type: $('#rule-scope').value, scope_value: $('#rule-scope-value').value,
      markup_type: $('#rule-markup-type').value, markup_value: Number($('#rule-markup-value').value),
      min_margin: $('#rule-min-margin').value ? Number($('#rule-min-margin').value) : null,
    } });
    event.target.reset();
    notify('Pricing rule saved.');
    loadPricingRules();
  } catch (error) { notify(error.message); }
});

async function loadCache() {
  const { cache } = await apiRequest('/api/admin/cache');
  const host = $('#cache-list'); host.replaceChildren();
  host.append(table(cache, [
    ['Vehicle / part', c => `${c.year} ${c.make} ${c.model} · ${c.part}${c.interchange ? ' · ' + c.interchange : ''}`],
    ['Results', c => `${c.result_count} (${c.status})`],
    ['Last refreshed', c => new Date(c.last_refreshed_at * 1000).toLocaleString()],
    ['Refreshing now', c => c.refreshing ? 'Yes' : 'No'],
  ]));
  [...host.querySelectorAll('.admin-row')].forEach((row, index) => {
    const entry = cache[index];
    const refresh = el('button', 'Refresh inventory now', 'secondary');
    refresh.addEventListener('click', async () => {
      try { await apiRequest(`/api/admin/cache/${encodeURIComponent(entry.cache_key)}/refresh`, { method: 'POST', body: {} }); notify('Inventory refresh started.'); await loadCache(); }
      catch (error) { notify(error.message); }
    });
    const remove = el('button', 'Delete', 'secondary');
    remove.addEventListener('click', async () => { await apiRequest(`/api/admin/cache/${encodeURIComponent(entry.cache_key)}`, { method: 'DELETE' }); notify('Cache entry deleted.'); loadCache(); });
    row.append(refresh, remove);
  });
}

$('#listing-form').addEventListener('submit', async event => {
  event.preventDefault();
  const id = $('#listing-id').value.trim();
  const host = $('#listing-detail'); host.replaceChildren();
  try {
    const data = await apiRequest(`/api/admin/listings/${id}`);
    const panel = el('div', undefined, 'listing admin-row');
    panel.append(el('p', `${data.listing.year} ${data.listing.make} ${data.listing.model} · ${data.listing.part} (${data.listing.stock})`));
    panel.append(el('p', `Supplier price: ${money(data.supplier_price)} · Seller: ${data.seller || 'Unknown'}`));
    panel.append(el('p', `Customer price: ${money(data.customer_price)}`));
    const form = el('form', undefined, 'admin-inline-form');
    const hiddenLabel = el('label'); const hidden = el('input'); hidden.type = 'checkbox'; hidden.checked = data.override.hidden; hiddenLabel.append(hidden, document.createTextNode(' Hidden from storefront'));
    const priceLabel = el('label', 'Manual customer price'); const price = el('input'); price.type = 'number'; price.step = 'any'; if (data.override.manual_price !== null) price.value = data.override.manual_price; priceLabel.append(price);
    const notesLabel = el('label', 'Admin notes'); const notes = el('input'); notes.value = data.override.admin_notes || ''; notesLabel.append(notes);
    const save = el('button', 'Save listing'); save.type = 'submit';
    form.append(hiddenLabel, priceLabel, notesLabel, save);
    form.addEventListener('submit', async submitEvent => {
      submitEvent.preventDefault();
      try {
        await apiRequest(`/api/admin/listings/${id}`, { method: 'POST', body: {
          hidden: hidden.checked, manual_price: price.value === '' ? null : Number(price.value),
          clear_manual_price: price.value === '', admin_notes: notes.value,
        } });
        notify('Listing updated.');
      } catch (error) { notify(error.message); }
    });
    panel.append(form); host.append(panel);
  } catch (error) { host.append(el('p', error.message, 'muted')); }
});

async function loadOrders() {
  const { orders } = await apiRequest('/api/admin/orders');
  const host = $('#orders-list'); host.replaceChildren();
  host.append(table(orders, [
    ['Reference', o => o.reference], ['Status', o => o.status],
    ['Created', o => new Date(o.created * 1000).toLocaleString()],
    ['Items', o => JSON.parse(o.items).length],
  ]));
  [...host.querySelectorAll('.admin-row')].forEach((row, index) => {
    const order = orders[index];
    const select = el('select');
    for (const status of ['submitted', 'needs_review', 'quoted', 'customer_confirmed', 'ready_for_supplier_order', 'cancelled', 'fulfilled']) {
      const option = el('option', status); option.value = status; if (status === order.status) option.selected = true; select.append(option);
    }
    const save = el('button', 'Update status', 'secondary');
    save.addEventListener('click', async () => {
      try { await apiRequest(`/api/admin/orders/${order.reference}/status`, { method: 'POST', body: { status: select.value } }); notify('Order status updated.'); loadOrders(); }
      catch (error) { notify(error.message); }
    });
    row.append(select, save);
    const prepare = el('button', 'Prepare supplier handoff', 'secondary');
    prepare.disabled = order.status !== 'ready_for_supplier_order';
    prepare.addEventListener('click', async () => {
      try {
        await apiRequest(`/api/admin/orders/${order.reference}/prepare-supplier-order`, { method: 'POST', body: {} });
        notify('Private source references are ready for operator review. Supplier availability must be revalidated. No supplier order was submitted.');
      } catch (error) { notify(error.message); }
    });
    row.append(prepare);
  });
}

async function loadAudit() {
  const { audit } = await apiRequest('/api/admin/audit');
  const host = $('#audit-list'); host.replaceChildren();
  host.append(table(audit, [
    ['When', a => new Date(a.at * 1000).toLocaleString()], ['Action', a => a.action], ['Target', a => a.target],
  ]));
}

const loaders = { dashboard: loadDashboard, settings: loadSettings, pricing: loadPricingRules, cache: loadCache, orders: loadOrders, audit: loadAudit };
document.querySelectorAll('[data-refresh]').forEach(button => button.addEventListener('click', () => loaders[button.dataset.refresh]?.().catch(error => notify(error.message))));

async function enterApp() {
  $('#login-panel').hidden = true; $('#admin-app').hidden = false; $('#logout-button').hidden = false;
  for (const load of Object.values(loaders)) {
    try { await load(); } catch (error) { notify(error.message); break; }
  }
}

if (sessionStorage.getItem(TOKEN_KEY)) enterApp().catch(() => signOut());
