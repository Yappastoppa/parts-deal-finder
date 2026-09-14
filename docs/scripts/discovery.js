import { $, el, money, partImage, addItem, notify, selectionButton } from './main.js';

export function createResultTools(items, onChange) {
  const panel = el('section', undefined, 'result-tools');
  panel.setAttribute('aria-label', 'Filter and sort parts');
  function select(label, id, options) {
    const field = el('label', label); const control = el('select'); control.id = id;
    for (const [value, text] of options) { const option = el('option', text); option.value = value; control.append(option); }
    field.append(control); panel.append(field); control.addEventListener('change', onChange); return control;
  }
  const position = select('Part position', 'filter-position', [['', 'All positions'], ...[...new Set(items.map(item => item.location).filter(Boolean))].sort().map(value => [value, value])]);
  const budgetLabel = el('label', 'Max price (USD)'); const budget = el('input');
  budget.id = 'filter-price'; budget.type = 'number'; budget.min = '0'; budget.step = 'any'; budget.placeholder = 'Any price'; budget.inputMode = 'decimal';
  budgetLabel.append(budget); panel.append(budgetLabel); budget.addEventListener('input', onChange);
  const sort = select('Sort by', 'sort-parts', [['original', 'Original order'], ['price-low', 'Price: low to high'], ['price-high', 'Price: high to low'], ['mileage', 'Lowest mileage']]);
  const clear = el('button', 'Reset filters', 'secondary'); clear.type = 'button'; panel.append(clear);
  function reset() { position.value = ''; budget.value = ''; sort.value = 'original'; onChange(); }
  clear.addEventListener('click', reset);
  const priceHelp = 'Prices exclude shipping. Parts with an unconfirmed price are excluded when a price limit is set.';
  const note = el('p', priceHelp, 'muted'); note.id = 'filter-price-help'; budget.setAttribute('aria-describedby', note.id);
  panel.append(note);
  $('#results').previousElementSibling.before(panel);
  const initial = new URLSearchParams(location.search);
  if ([...position.options].some(option => option.value === initial.get('position'))) position.value = initial.get('position');
  const price = initial.get('maxPrice');
  if (price !== null && price.trim() && Number.isFinite(Number(price)) && Number(price) >= 0) budget.value = price;
  if ([...sort.options].some(option => option.value === initial.get('sort'))) sort.value = initial.get('sort');
  return {
    reset,
    state() { return {position: position.value, maxPrice: budget.validity.valid ? budget.value : '', sort: sort.value === 'original' ? '' : sort.value}; },
    destroy() { panel.remove(); },
    values() {
      budget.setAttribute('aria-invalid', String(!budget.validity.valid));
      note.textContent = budget.validity.valid ? priceHelp : 'Enter a price of zero or more.';
      const max = budget.value !== '' && budget.validity.valid ? Number(budget.value) : null;
      const filtered = items.filter(item => (!position.value || item.location === position.value) && (max === null || (Number.isFinite(item.price) && item.price <= max)));
      if (sort.value !== 'original') {
        const key = sort.value === 'mileage' ? 'mileage' : 'price';
        filtered.sort((a, b) => {
          const av = a[key], bv = b[key];
          if (!Number.isFinite(av)) return Number.isFinite(bv) ? 1 : 0;
          if (!Number.isFinite(bv)) return -1;
          return sort.value === 'price-high' ? bv - av : av - bv;
        });
      }
      return filtered;
    }
  };
}

export function createComparison(items = [], searchKey = '') {
  const selected = new Map();
  try {
    const stored = JSON.parse(sessionStorage.getItem('apf:comparison') || 'null');
    if (stored?.searchKey === searchKey && Array.isArray(stored.ids)) {
      for (const id of stored.ids.slice(0, 3)) { const item = items.find(value => value.id === id); if (item) selected.set(id, item); }
    }
  } catch { /* Comparison also works when browser storage is unavailable. */ }
  const tray = el('aside', undefined, 'compare-tray'); tray.hidden = true; tray.setAttribute('aria-label', 'Selected parts for comparison');
  const count = el('span'); count.setAttribute('role', 'status');
  const open = el('button', 'Compare parts'); const clear = el('button', 'Clear selection', 'secondary');
  tray.append(count, open, clear); document.body.append(tray);
  const dialog = el('dialog', undefined, 'compare-dialog'); dialog.id = 'comparison'; dialog.setAttribute('aria-labelledby', 'compare-title');
  const heading = el('div', undefined, 'section-heading'); const title = el('h2', 'Compare parts'); title.id = 'compare-title';
  const close = el('button', 'Close comparison', 'secondary'); heading.append(title, close);
  const hint = el('p', 'Compare details before choosing. Fitment and availability still need confirmation.', 'muted');
  const scroll = el('div', undefined, 'compare-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Part comparison table; scroll horizontally on small screens');
  const scrollHint = el('p', 'Scroll sideways to see every part.', 'comparison-hint muted');
  dialog.append(heading, hint, scrollHint, scroll); document.body.append(dialog);
  function sync() {
    try { sessionStorage.setItem('apf:comparison', JSON.stringify({searchKey, ids: [...selected.keys()]})); } catch { /* Keep this selection in memory. */ }
    tray.hidden = selected.size === 0; document.body.classList.toggle('has-comparison', selected.size > 0);
    count.textContent = `${selected.size} of 3 selected`; open.disabled = selected.size < 2;
    document.querySelectorAll('[data-compare-id]').forEach(input => { input.checked = selected.has(input.dataset.compareId); });
  }
  function render() {
    const table = el('table'); table.className = 'compare-table';
    const caption = el('caption', 'Selected part details'); caption.className = 'visually-hidden'; table.append(caption);
    const head = el('thead'); const row = el('tr'); const detail = el('th', 'Details'); detail.scope = 'col'; row.append(detail);
    for (const item of selected.values()) {
      const cell = el('th'); cell.scope = 'col'; cell.append(partImage(item), el('strong', `${item.year} ${item.make} ${item.model} · ${item.part}`), el('small', item.stock));
      row.append(cell);
    }
    head.append(row); table.append(head); const body = el('tbody');
    for (const [label, value] of [['Price', item => money(item.price)], ['Position', item => item.location], ['Condition', item => item.condition], ['Mileage', item => Number.isFinite(item.mileage) ? `${item.mileage.toLocaleString()} mi` : 'Not provided'], ['Seller', item => item.seller], ['Location', item => item.city]]) {
      const row = el('tr'); const labelCell = el('th', label); labelCell.scope = 'row'; row.append(labelCell);
      selected.forEach(item => row.append(el('td', value(item) || 'Not provided'))); body.append(row);
    }
    const actions = el('tr'); const actionLabel = el('th', 'Select a part'); actionLabel.scope = 'row'; actions.append(actionLabel);
    selected.forEach(item => {
      const cell = el('td'); const choose = el('button', 'Choose This Part'); choose.addEventListener('click', () => { addItem(item); dialog.close(); });
      const remove = el('button', 'Remove from comparison', 'text-button');
      remove.addEventListener('click', () => { selected.delete(item.id); sync(); if (selected.size < 2) dialog.close(); else { render(); close.focus(); } });
      cell.append(choose, remove); actions.append(cell);
    });
    body.append(actions); table.append(body); scroll.replaceChildren(table);
  }
  close.addEventListener('click', () => dialog.close());
  open.addEventListener('click', () => { render(); dialog.showModal(); close.focus(); });
  clear.addEventListener('click', () => { selected.clear(); sync(); $('#result-count').tabIndex = -1; $('#result-count').focus(); });
  sync();
  return {
    control(item) {
      const label = el('label', undefined, 'compare-check'); const input = el('input'); input.type = 'checkbox'; input.dataset.compareId = item.id;
      input.checked = selected.has(item.id); input.setAttribute('aria-label', `Compare ${item.stock}`);
      input.addEventListener('change', () => {
        if (input.checked && selected.size >= 3) { input.checked = false; notify('Compare up to three parts. Remove one to add another.'); return; }
        if (input.checked) selected.set(item.id, item); else selected.delete(item.id); sync();
      });
      label.append(input, el('span', 'Compare')); return label;
    },
    destroy() { tray.remove(); dialog.remove(); document.body.classList.remove('has-comparison'); }
  };
}

export function openQuickView(item) {
  const dialog = el('dialog', undefined, 'quick-view'); dialog.setAttribute('aria-labelledby', 'quick-title');
  const header = el('div', undefined, 'quick-header'); header.append(el('span', 'PART DETAILS', 'eyebrow'));
  const close = el('button', 'Close quick view', 'secondary'); close.addEventListener('click', () => dialog.close()); header.append(close);
  const photo = partImage(item); photo.loading = 'eager';
  const title = el('h2', `${item.year} ${item.make} ${item.model} · ${item.part}`); title.id = 'quick-title';
  const price = el('strong', money(item.price), 'price');
  const dl = el('dl', undefined, 'quick-details');
  for (const [label, value] of [['Position',item.location], ['Condition',item.condition], ['Mileage',Number.isFinite(item.mileage) ? `${item.mileage.toLocaleString()} mi` : 'Not provided'], ['Seller',item.seller], ['Location',item.city], ['Stock number',item.stock]]) dl.append(el('dt',label),el('dd',value || 'Not provided'));
  const note = el('p', item.mode === 'live' ? 'Confirm fitment, availability and final price before purchase.' : 'Sample listing and illustration. Live inventory is not connected.', 'demo-banner');
  const actions = el('div', undefined, 'quick-actions');
  for (const collection of ['cart', 'saved']) actions.append(selectionButton(item, collection, () => dialog.close()));
  dialog.append(header,photo,title,price,dl,note,actions); document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove(),{once:true}); dialog.showModal(); close.focus();
}
