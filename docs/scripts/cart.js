import { liveMode } from './api.js';
import { $, el, readItems, writeItems, money, partImage, selectionButton, notify, updateCounts } from './main.js';
const saved = new URLSearchParams(location.search).get('view') === 'saved';
const collection = saved ? 'saved' : 'cart';
if (saved) { $('#collection-title').textContent = 'Saved Parts'; $('#collection-description').textContent = 'A shortlist for your next repair, stored in this browser.'; }
function restore(item, index) {
  const current = readItems(collection);
  if (!current.some(value => value.id === item.id)) current.splice(Math.min(index, current.length), 0, item);
  if (writeItems(current, collection)) notify('Part restored.');
}
function render() {
  const focused = document.activeElement?.dataset;
  const focusSelection = focused?.selection; const focusId = focused?.itemId;
  const items = readItems(collection); $('#cart-items').replaceChildren();
  if (!items.length) {
    const empty = el('div', undefined, 'empty-state'); empty.append(el('h2', saved ? 'No saved parts yet.' : 'Your cart is empty.'), el('p', 'Search for a part to start comparing your options.'));
    const link = el('a', 'Find Your Part →', 'button'); link.href = './index.html'; empty.append(link); $('#cart-items').append(empty);
  }
  for (const [index, item] of items.entries()) {
    const row = el('article', undefined, 'listing cart-listing'); row.append(partImage(item));
    const body = el('div'); body.append(el('h2', item.part), el('p', `${item.year} ${item.make} ${item.model} · ${item.location || 'Position not provided'}`), el('p', `${item.seller || 'Seller not provided'} · ${item.city || 'Location not provided'}`)); row.append(body);
    const actions = el('div', undefined, 'listing-actions'); actions.append(el('strong', money(item.price), 'price'));
    actions.append(selectionButton(item, saved ? 'cart' : 'saved'));
    const remove = el('button', 'Remove', 'secondary'); remove.setAttribute('aria-label', `Remove ${item.part} ${item.stock}`);
    remove.addEventListener('click', () => {
      if (writeItems(readItems(collection).filter(value => value.id !== item.id), collection)) {
        notify('Part removed.', {label:'Undo', run:() => restore(item, index)});
        $('#collection-title').tabIndex = -1; $('#collection-title').focus();
      }
    });
    actions.append(remove); row.append(actions); $('#cart-items').append(row);
  }
  $('#quote-action').replaceChildren(); $('#cart-total').textContent = '';
  if (!saved && items.length) {
    const quoteReady = liveMode && items.every(item => item.mode === 'live');
    if (quoteReady) { const link = el('a', 'Request a Quote →', 'button'); link.href = './quote.html'; $('#quote-action').append(link); }
    else {
      $('#quote-action').append(el('p', liveMode ? 'Your cart includes demo parts. Remove those parts before requesting a supplier quote.' : 'These are demo selections. Checkout and quote submission are not available in this preview.', 'muted'));
      const link = el('a', 'Continue searching', 'button'); link.href = './index.html'; $('#quote-action').append(link);
    }
    if (liveMode) $('#cart-total').textContent = 'Final price, shipping and availability are confirmed in your quote.';
    else {
      const priced = items.filter(item => Number.isFinite(item.price));
      const pending = items.length - priced.length;
      $('#cart-total').textContent = priced.length ? `Demo subtotal${pending ? ' (confirmed prices only)' : ''}: ${money(priced.reduce((sum, item) => sum + item.price, 0))}. ` : '';
      $('#cart-total').textContent += pending ? `${pending} ${pending === 1 ? 'price is' : 'prices are'} not confirmed. ` : '';
      $('#cart-total').textContent += 'Shipping and tax not calculated.';
    }
  }
  updateCounts();
  if (focusSelection && focusId) [...document.querySelectorAll('[data-selection]')].find(button => button.dataset.selection === focusSelection && button.dataset.itemId === focusId)?.focus({preventScroll:true});
}
window.addEventListener('storage', render); window.addEventListener('apf:collections', render); render();
