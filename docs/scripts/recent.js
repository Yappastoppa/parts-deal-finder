import { $, el, notify } from './main.js';
const key = 'apf:recent-searches';
const fields = ['year', 'make', 'model', 'part'];
function read() {
  try {
    const data = JSON.parse(sessionStorage.getItem(key) || '[]');
    return Array.isArray(data) ? data.filter(item => item && fields.every(field => typeof item[field] === 'string' && item[field].length > 0 && item[field].length <= 120)).slice(0, 5) : [];
  } catch { return []; }
}
export function rememberSearch(params) {
  const item = Object.fromEntries(fields.map(field => [field, String(params[field] || '').trim().slice(0,120)]));
  if (fields.some(field => !item[field])) return;
  try { sessionStorage.setItem(key, JSON.stringify([item, ...read().filter(old => fields.some(field => old[field].toLowerCase() !== item[field].toLowerCase()))].slice(0,5))); } catch { /* Search works when browser storage is unavailable. */ }
}
export function renderRecent() {
  const host = $('.hero'); if (!host) return;
  const items = read(); if (!items.length) return;
  const panel = el('section', undefined, 'recent-searches'); panel.setAttribute('aria-label', 'Recent searches');
  const heading = el('div', undefined, 'recent-heading'); heading.append(el('strong', 'Recent searches'), el('span', 'This tab'));
  const clear = el('button', 'Clear recent searches', 'text-button');
  clear.addEventListener('click', () => {
    try { sessionStorage.removeItem(key); panel.remove(); $('#query').focus(); }
    catch { notify('Recent searches could not be cleared. Browser storage is unavailable.'); }
  });
  heading.append(clear); panel.append(heading);
  for (const item of items) { const link = el('a', fields.map(field => item[field]).join(' ')); link.href = './results.html?' + new URLSearchParams(item); panel.append(link); }
  host.append(panel);
}
