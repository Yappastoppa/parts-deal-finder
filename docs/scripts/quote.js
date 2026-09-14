import { $, el, readItems, money } from './main.js';
import { liveMode, apiRequest, apiURL } from './api.js';
const items = readItems();
let requestKey = crypto.randomUUID();
let attemptedPayload = null;
const receiptKey = 'apf:quote:receipt';
const receiptScope = liveMode ? JSON.stringify([apiURL('/'), items.map(item => item.id).sort()]) : '';
function receipt() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(receiptKey));
    if (saved?.scope === receiptScope && /^APF-[A-F0-9]{12}$/.test(saved.reference)) return saved.reference;
  } catch { /* Contact information is never stored; invalid receipt metadata is ignored. */ }
  return null;
}
function showReceipt(reference) {
  $('#quote-form').reset(); $('#quote-form').hidden = true;
  $('#quote-feedback').replaceChildren(document.createTextNode(`Request received. Your reference is ${reference}. Keep this reference. No payment or order has been placed.`));
  const another = el('button', 'Start another quote', 'secondary');
  another.type = 'button';
  another.addEventListener('click', () => {
    try { sessionStorage.removeItem(receiptKey); } catch { /* In-memory reset still works. */ }
    requestKey = crypto.randomUUID(); attemptedPayload = null;
    $('#quote-feedback').replaceChildren(); $('#quote-form').hidden = false; $('#quote-submit').disabled = false;
    $('#quote-form').elements.name.focus();
  });
  $('#quote-feedback').append(el('br'), another);
}

const liveItems = items.length > 0 && items.length <= 10 && items.every(item => item.mode === 'live' && /^[a-f0-9]{32}$/.test(item.id));
$('#quote-mode').textContent = !liveMode ? 'This is a demo preview. Quote requests are not sent, and contact information is not collected.' : 'Your request goes to our private review queue. Availability and price are confirmed before any purchase.';
for (const item of items) {
  const row = el('article', undefined, 'quote-item');
  row.append(el('strong', `${item.year} ${item.make} ${item.model} · ${item.part}`), el('p', `${item.seller} · ${item.stock || 'Stock pending'} · ${money(item.price)}`));
  $('#quote-items').append(row);
}
if (!items.length || (liveMode && !liveItems)) {
  $('#quote-feedback').textContent = items.length ? 'Choose up to ten current supplier listings. Demo or expired selections need to be removed from your cart first.' : 'Your cart is empty. Find a part before requesting a quote.';
  const link=el('a','Find Your Part','button'); link.href='./index.html'; $('#quote-items').append(link);
} else if (liveMode) {
  const reference = receipt();
  if (reference) showReceipt(reference);
  else $('#quote-form').hidden=false;
}
$('#quote-form').addEventListener('submit', async event => {
  event.preventDefault();
  const values=Object.fromEntries(new FormData(event.currentTarget));
  values.consent=event.currentTarget.elements.consent.checked;
  values.listing_ids=items.map(item=>item.id);
  const snapshot=JSON.stringify(values);
  if (attemptedPayload !== null && attemptedPayload !== snapshot) requestKey=crypto.randomUUID();
  attemptedPayload=snapshot;
  values.request_key=requestKey;
  $('#quote-submit').disabled=true; $('#quote-feedback').textContent='Sending your request…';
  try {
    const result=await apiRequest('/api/quotes',values);
    if (!/^APF-[A-F0-9]{12}$/.test(result.reference)) throw new Error('Confirmation could not be read. Please retry this request.');
    try { sessionStorage.setItem(receiptKey, JSON.stringify({scope: receiptScope, reference: result.reference})); } catch { /* The displayed reference remains usable. */ }
    showReceipt(result.reference);
    $('#quote-feedback').tabIndex=-1; $('#quote-feedback').focus();
  } catch (error) {
    $('#quote-feedback').textContent=error.message || 'Could not send your request. Please try again.';
    $('#quote-submit').disabled=false;
  }
});
