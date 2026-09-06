import { $, readJSON } from './main.js';
let catalogPromise;
export const loadCatalog = () => catalogPromise ||= readJSON('./data/car_data.json');
export function searchURL(params) { return './results.html?' + new URLSearchParams(params).toString(); }
export function parseSearch(query, catalog) {
  const normalized = query.trim().replace(/\s+/g, ' ');
  const match = normalized.match(/^(\d{4})\s+(.+)$/);
  if (!match) throw new Error('Enter a year, make, model and part, such as 2021 BMW M4 Spindle.');
  const [, year, rest] = match;
  const make = Object.keys(catalog.makes).sort((a,b) => b.length-a.length).find(m => rest.toLowerCase().startsWith(m.toLowerCase() + ' '));
  if (!make) throw new Error('Make not recognized. Choose a make in Browse By Vehicle.');
  const remaining = rest.slice(make.length).trim();
  const known = Object.keys(catalog.makes[make] || {}).sort((a,b) => b.length-a.length).find(m => remaining.toLowerCase().startsWith(m.toLowerCase() + ' '));
  if (!known) throw new Error('Model not recognized. Use Browse By Vehicle to enter the model and part separately.');
  const part = remaining.slice(known.length).trim();
  if (!part) throw new Error('Add the part you are looking for.');
  return { year, make, model: known, part };
}
const form = $('#search-form');
if (form) {
  form.addEventListener('submit', async event => {
    event.preventDefault(); $('#search-error').textContent = '';
    try { location.href = searchURL(parseSearch($('#query').value, await loadCatalog())); }
    catch (error) { $('#search-error').textContent = error.message; }
  });
  document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => {
    $('#query').value = button.dataset.example; form.requestSubmit();
  }));
}
