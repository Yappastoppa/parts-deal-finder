import { liveMode, apiURL } from './api.js';
export const $ = (selector) => document.querySelector(selector);
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export async function readJSON(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error('Catalog data could not be loaded. Please try again.');
  return response.json();
}
export const money = value => value === null || value === undefined ? 'Price on request' : new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(value);
export function notify(message, action) {
  const host = $('#notice'); const text = el('span', message); host.replaceChildren(text);
  if (action) { const undo = el('button', action.label, 'notice-action'); undo.addEventListener('click', () => { host.replaceChildren(); action.run(); }); host.append(undo); }
  const close = el('button', '×', 'notice-close'); close.setAttribute('aria-label', 'Dismiss notification'); close.addEventListener('click', () => host.replaceChildren()); host.append(close);
}
export function readItems(collection = 'cart') {
  try {
    const items = JSON.parse(localStorage.getItem(`apf:${collection}`) || '[]');
    return Array.isArray(items) ? items.filter(x => x && typeof x.id === 'string' && (x.price === null || (typeof x.price === 'number' && Number.isFinite(x.price) && x.price >= 0))) : [];
  } catch { return []; }
}
export function writeItems(items, collection = 'cart') {
  try { localStorage.setItem(`apf:${collection}`, JSON.stringify(items)); updateCounts(); window.dispatchEvent(new Event('apf:collections')); return true; }
  catch { notify('Browser storage is unavailable or full. Your selection could not be saved.'); return false; }
}
export function addItem(item, collection = 'cart') {
  const items = readItems(collection);
  if (items.some(x => x.id === item.id)) { notify('This part is already in your ' + (collection === 'cart' ? 'cart.' : 'saved parts.')); return true; }
  if (!writeItems([...items, item], collection)) return false;
  notify(collection === 'cart' ? 'Part added to your cart.' : 'Part saved.'); return true;
}
function updateSelectionButton(button, active) {
  const saved = button.dataset.selection === 'saved';
  button.classList.toggle('is-selected', active);
  if (saved) { button.textContent = active ? 'Saved ✓' : 'Save Part'; button.setAttribute('aria-pressed', String(active)); button.setAttribute('aria-label', active ? 'Remove from saved parts' : 'Save Part'); }
  else { button.setAttribute('aria-label', 'Choose This Part'); button.dataset.inCart = String(active); button.title = active ? 'Already in your cart' : 'Add this part to your cart'; }
}
export function selectionButton(item, collection = 'cart', onDone) {
  const button = el('button', 'Choose This Part', collection === 'saved' ? 'text-button' : '');
  button.dataset.selection = collection; button.dataset.itemId = item.id;
  updateSelectionButton(button, readItems(collection).some(value => value.id === item.id));
  button.addEventListener('click', () => {
    const items = readItems(collection);
    if (collection === 'saved' && items.some(value => value.id === item.id)) {
      if (writeItems(items.filter(value => value.id !== item.id), collection)) notify('Part removed from Saved Parts.');
    } else addItem(item, collection);
    onDone?.();
  });
  return button;
}
export function updateCounts() {
  document.querySelectorAll('[data-cart-count]').forEach(n => n.textContent = readItems().length);
  document.querySelectorAll('[data-saved-count]').forEach(n => n.textContent = readItems('saved').length);
  for (const collection of ['cart', 'saved']) {
    const ids = new Set(readItems(collection).map(item => item.id));
    document.querySelectorAll(`[data-selection=${collection}]`).forEach(button => updateSelectionButton(button, ids.has(button.dataset.itemId)));
  }
}
// Accept only the explicitly public local illustration assets, including when reading browser state.
export function imagePath(path) {
  if (liveMode && typeof path === 'string') {
    try { const url = new URL(path, apiURL('/')); if (url.origin === new URL(apiURL('/')).origin && /^\/api\/media\/[a-f0-9]{32}$/.test(url.pathname) && !url.search && !url.hash && !url.username && !url.password) return url.href; } catch { /* Fall back to the local illustration. */ }
  }
  return ['./assets/images/spindle.svg', './assets/images/spindle-detail.svg'].includes(path) ? path : './assets/images/spindle.svg';
}
export function partImage(item) {
  const img = el('img'); img.src = imagePath(item.images?.[0]);
  img.alt = `${item.part || 'Part'} ${item.mode === 'live' && item.images?.length ? 'supplier photo' : 'placeholder illustration'}`;
  img.addEventListener('error', () => { const fallback = new URL('./assets/images/spindle.svg', location.href).href; if (img.src !== fallback) img.src = fallback; img.alt = 'Photo unavailable; placeholder illustration'; }); img.loading = 'lazy'; return img;
}
updateCounts();
window.addEventListener('storage', updateCounts);
const current = location.pathname.split('/').pop() || 'index.html';
document.querySelectorAll('.nav-inner a').forEach(a => {
  const url = new URL(a.href);
  const section = ['results.html', 'quote.html'].includes(current) ? (current === 'results.html' ? 'index.html' : 'cart.html') : current;
  const savedView = current === 'cart.html' && new URLSearchParams(location.search).get('view') === 'saved';
  if (url.pathname.endsWith(section) && !url.hash && (savedView ? url.search === '?view=saved' : !url.search)) a.setAttribute('aria-current', 'page');
});

if (liveMode) {
  document.querySelector('.featured-part')?.remove();
  document.querySelector('.hero')?.classList.add('hero-live');
  document.querySelectorAll('[data-live-text]').forEach(n => n.textContent = n.dataset.liveText);
  document.querySelectorAll('footer span').forEach(n => n.textContent = 'Search inventory · Request a quote · No online payment');
  document.querySelectorAll('.demo-banner').forEach(n => {
    if (!n.closest('dialog')) n.textContent = 'Request a quote to confirm fitment, availability, price and shipping. Selecting a part does not place an order.';
  });
  document.querySelector('.badge')?.replaceChildren(document.createTextNode('SUPPLIER INVENTORY'));
}
