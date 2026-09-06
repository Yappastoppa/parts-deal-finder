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
export const money = value => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(value);
export function notify(message) { $('#notice').textContent = message; }
export function readItems(collection = 'cart') {
  try {
    const items = JSON.parse(localStorage.getItem(`apf:${collection}`) || '[]');
    return Array.isArray(items) ? items.filter(x => x && typeof x.id === 'string' && typeof x.price === 'number' && Number.isFinite(x.price)) : [];
  } catch { return []; }
}
export function writeItems(items, collection = 'cart') {
  try { localStorage.setItem(`apf:${collection}`, JSON.stringify(items)); updateCounts(); return true; }
  catch { notify('Browser storage is unavailable or full. Your selection could not be saved.'); return false; }
}
export function addItem(item, collection = 'cart') {
  const items = readItems(collection);
  if (items.some(x => x.id === item.id)) { notify('This part is already in your ' + (collection === 'cart' ? 'cart.' : 'saved parts.')); return; }
  if (writeItems([...items, item], collection)) notify(collection === 'cart' ? 'Part added to your cart.' : 'Part saved.');
}
export function updateCounts() {
  document.querySelectorAll('[data-cart-count]').forEach(n => n.textContent = readItems().length);
  document.querySelectorAll('[data-saved-count]').forEach(n => n.textContent = readItems('saved').length);
}
// Accept only the explicitly public local illustration assets, including when reading browser state.
export function imagePath(path) {
  return ['./assets/images/spindle.svg', './assets/images/spindle-detail.svg'].includes(path) ? path : './assets/images/spindle.svg';
}
export function partImage(item) {
  const img = el('img'); img.src = imagePath(item.images?.[0]);
  img.alt = `${item.part || 'Part'} placeholder illustration`; img.loading = 'lazy'; return img;
}
updateCounts();
window.addEventListener('storage', updateCounts);
const current = location.pathname.split('/').pop() || 'index.html';
document.querySelectorAll('.nav-inner a').forEach(a => {
  const url = new URL(a.href);
  if (url.pathname.endsWith(current) && url.search === location.search && !url.hash) a.setAttribute('aria-current', 'page');
});
