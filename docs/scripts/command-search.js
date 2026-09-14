import { $, el } from './main.js';
import { parseSearch, loadCatalog, searchURL } from './search.js';
const launch = $('.search-launch');
if (launch) {
  launch.querySelector('kbd').textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K';
  const dialog = el('dialog', undefined, 'command-search');
  dialog.setAttribute('aria-labelledby', 'command-title');
  const heading = el('div', undefined, 'section-heading'); const title = el('h2', 'Find a part'); title.id = 'command-title';
  const close = el('button', 'Close search', 'text-button'); heading.append(title, close);
  const form = el('form'); const label = el('label', 'Year, make, model and part');
  const input = el('input'); input.type = 'search'; input.placeholder = '2021 BMW M4 Spindle'; input.required = true;
  const error = el('p', undefined, 'search-validation'); error.setAttribute('role', 'alert');
  const submit = el('button', 'Search parts'); submit.type = 'submit'; label.append(input); form.append(label, error, submit);
  const help = el('p', 'Search from anywhere. Press Escape to close.', 'muted');
  dialog.append(heading, form, help); document.body.append(dialog);
  function open() { if (!document.querySelector('dialog[open]')) { dialog.showModal(); input.focus(); input.select(); } }
  dialog.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); dialog.close(); } });
  launch.addEventListener('click', open); close.addEventListener('click', () => dialog.close());
  document.addEventListener('keydown', event => {
    const typing = event.target.closest('input,select,textarea,[contenteditable=true]');
    if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') || (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey)) {
      event.preventDefault(); open();
    }
  });
  let request = 0;
  dialog.addEventListener('close', () => { request++; submit.disabled = false; error.textContent = ''; });
  form.addEventListener('submit', async event => {
    const current = ++request;
    event.preventDefault(); error.textContent = ''; submit.disabled = true;
    const query = input.value;
    try { const catalog = await loadCatalog(); if (current === request && dialog.open) location.href = searchURL(parseSearch(query, catalog)); }
    catch (failure) { if (current === request && dialog.open) { error.textContent = failure.message; submit.disabled = false; input.focus(); } }
  });
}
