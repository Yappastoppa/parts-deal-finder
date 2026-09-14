import { renderRecent } from './recent.js';
import { $, readJSON } from './main.js';
let catalogPromise;
export const loadCatalog = () => catalogPromise ||= readJSON('./data/car_data.json').then(data => {
  if (!data || !Array.isArray(data.years) || !Array.isArray(data.parts) || !data.makes || typeof data.makes !== 'object' || Array.isArray(data.makes)) throw new Error('Catalog data could not be loaded. Please try again.');
  return data;
}).catch(error => { catalogPromise = undefined; throw error; });
export function searchURL(params) { return './results.html?' + new URLSearchParams(params).toString(); }
export function parseSearch(query, catalog) {
  if (query.length > 400) throw new Error('Use a shorter vehicle and part search.');
  const normalized = query.trim().replace(/\s+/g, ' ');
  const match = normalized.match(/^(\d{4})\s+(.+)$/);
  if (!match) throw new Error('Enter a year, make, model and part, such as 2021 BMW M4 Spindle.');
  const [, year, rest] = match;
  if (!/^(19|20)\d{2}$/.test(year)) throw new Error('Enter a vehicle year from 1900 to 2099.');
  const make = Object.keys(catalog.makes).sort((a,b) => b.length-a.length).find(m => rest.toLowerCase().startsWith(m.toLowerCase() + ' '));
  if (!make) throw new Error('Make not recognized. Choose a make in Browse By Vehicle.');
  const remaining = rest.slice(make.length).trim();
  const known = Object.keys(catalog.makes[make] || {}).sort((a,b) => b.length-a.length).find(m => remaining.toLowerCase().startsWith(m.toLowerCase() + ' '));
  if (!known) throw new Error('Model not recognized. Use Browse By Vehicle to enter the model and part separately.');
  const part = remaining.slice(known.length).trim();
  if (part.length > 120) throw new Error('Use a shorter part name.');
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

renderRecent();
