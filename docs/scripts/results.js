import { createResultTools, createComparison, openQuickView } from './discovery.js';
import { rememberSearch } from './recent.js';
import { liveMode, liveSearch, forgetSearch } from './api.js';
import { $, el, readJSON, money, selectionButton, partImage, imagePath } from './main.js';
// Demo remains static; live mode uses the isolated customer API.
export async function searchParts(searchParams) {
  if (liveMode) return liveSearch(searchParams, message => { $('#result-count').textContent = message; });
  const data = await readJSON('./data/demo_results.json');
  const normalize = value => String(value || '').trim().toLowerCase();
  const matches = ['year','make','model'].every(k => normalize(searchParams[k]) === normalize(data.search[k]));
  const part = normalize(searchParams.part);
  return {status: 'ok', listings: matches && ['spindle','spindle knuckle','spindle/knuckle assembly, front'].includes(part) ? data.listings : []};
}
let galleryImages=[], imageIndex=0, galleryIsLive=false;
function showImage() {
  $('#gallery-image').src = imagePath(galleryImages[imageIndex]);
  $('#gallery-image').alt = `${galleryIsLive ? 'Supplier photo' : 'Placeholder illustration'}, view ${imageIndex+1}`;
  $('#gallery-counter').textContent = `${imageIndex+1} / ${galleryImages.length}`;
}
function openGallery(item) {
  galleryIsLive = item.mode === 'live' && Boolean(item.images?.length);
  $('#gallery .muted').textContent = galleryIsLive ? 'Supplier photos. Confirm the exact part and condition before purchase.' : 'Original placeholder illustration. Not a photograph of inventory.';
  galleryImages = item.images?.length ? item.images : ['./assets/images/spindle.svg']; imageIndex=0;
  $('#gallery-title').textContent = `${item.part} · ${item.stock}`; showImage(); $('#gallery').showModal();
}
$('#gallery-image').addEventListener('error', () => { const img = $('#gallery-image'); const fallback = new URL('./assets/images/spindle.svg', location.href).href; if (img.src !== fallback) img.src = fallback; img.alt='Photo unavailable; placeholder illustration'; });
$('#gallery-close').addEventListener('click',()=>$('#gallery').close());
$('#gallery-prev').addEventListener('click',()=>{imageIndex=(imageIndex-1+galleryImages.length)%galleryImages.length;showImage();});
$('#gallery-next').addEventListener('click',()=>{imageIndex=(imageIndex+1)%galleryImages.length;showImage();});
$('#gallery').addEventListener('keydown',e=>{if(e.key==='ArrowLeft') $('#gallery-prev').click(); if(e.key==='ArrowRight') $('#gallery-next').click();});
let resultTools, comparison, generation = 0;
function card(item) {
  const article=el('article',undefined,'listing');
  const photo=el('div',undefined,'listing-photo'); photo.append(partImage(item),el('span', item.mode === 'live' ? (item.images?.length ? `${item.images.length} SUPPLIER PHOTOS` : 'PHOTO UNAVAILABLE') : '2 PLACEHOLDER VIEWS')); article.append(photo);
  const quick = el('button', 'Quick view ↗', 'quick-view-trigger'); quick.addEventListener('click', () => openQuickView(item)); photo.append(quick);
  const body=el('div',undefined,'listing-body'); body.append(el('div',item.location,'eyebrow'),el('h3',`${item.year} ${item.make} ${item.model} · ${item.part}`));
  const dl=el('dl');
  for (const [key,value] of [['Condition',item.condition],['Mileage',item.mileage === null || item.mileage === undefined ? 'Not provided' : `${item.mileage.toLocaleString()} mi`],['Seller',item.seller],['Location',item.city],['Stock #',item.stock]]) { dl.append(el('dt',key),el('dd',value)); }
  body.append(dl,el('p',item.source,'muted')); article.append(body);
  const actions=el('div',undefined,'listing-actions'); actions.append(el('strong',money(item.price),'price'),el('small', item.mode === 'live' ? 'Price and shipping confirmed by quote' : 'Demo price · shipping not included'));
  const photos = el('button', 'View Photos', 'secondary'); photos.addEventListener('click', () => openGallery(item));
  actions.append(selectionButton(item), photos, selectionButton(item, 'saved'));
  if (comparison) actions.append(comparison.control(item));
  article.append(actions); return article;
}
async function init(interchange) {
  const request = ++generation;
  resultTools?.destroy(); comparison?.destroy(); resultTools = undefined; comparison = undefined;
  const query=new URLSearchParams(location.search); const params=Object.fromEntries(['year','make','model','part'].map(k=>[k,query.get(k)||'']));
  const edit = $('.back'); edit.href = `./index.html?${new URLSearchParams(params)}#vehicle-form`; edit.textContent = '← Edit search';
  interchange = interchange ?? query.get('interchange') ?? '';
  if (interchange) query.set('interchange', interchange);
  else query.delete('interchange');
  history.replaceState(null, '', `${location.pathname}?${query}`);
  $('#search-summary').replaceChildren();
  $('#vehicle-title').textContent=[params.year,params.make,params.model].join(' '); $('#part-title').textContent=params.part;
  for(const [k,v] of Object.entries(params)) $('#search-summary').append(el('span',`${k[0].toUpperCase()+k.slice(1)}: ${v || 'Not provided'}`));
  if(Object.values(params).some(v=>!v.trim())) { $('#result-count').textContent='Enter a vehicle and part to search.'; return; }
  rememberSearch(params);
  if (interchange) params.interchange = interchange;
  $('#results').replaceChildren(); $('#pagination').replaceChildren();
  $('#result-count').textContent = liveMode ? 'Connecting to supplier inventory…' : 'Loading listings…';
  try {
    const response=await searchParts(params);
    if (request !== generation) return;
    if (response.status === 'needs_interchange_choice') {
      $('#result-count').textContent = 'Choose the configuration that matches your vehicle.';
      const panel = el('div', undefined, 'empty-state');
      panel.append(el('h2', 'Confirm your part configuration'));
      for (const choice of response.choices) {
        const button = el('button', choice, 'secondary'); button.addEventListener('click', () => init(choice)); panel.append(button);
      }
      if (!response.choices.length) panel.append(el('p', 'No configuration options were returned. Try a more specific part search.'));
      $('#results').append(panel); return;
    }
    const items=response.listings; const requestedPage = Number(query.get('page')); let page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
    if (liveMode) {
      const refresh=el('button','Refresh inventory','secondary');
      refresh.addEventListener('click',()=>{ forgetSearch(); init(interchange); });
      $('#search-summary').append(refresh);
    }
    if (items.length) {
      comparison = createComparison(items, JSON.stringify([liveMode, params]));
      resultTools = createResultTools(items, () => { page = 1; render(); });
    }
    function render() {
      const visible = resultTools ? resultTools.values() : items;
      page = Math.min(page, Math.max(1, Math.ceil(visible.length / 5)));
      for (const [key, value] of Object.entries(resultTools?.state() || {})) { if (value) query.set(key, value); else query.delete(key); }
      if (page > 1) query.set('page', String(page)); else query.delete('page');
      history.replaceState(null, '', `${location.pathname}?${query}`);
      $('#results').replaceChildren(...visible.slice((page-1)*5,page*5).map(card));
      $('#result-count').textContent=visible.length ? `Showing ${(page-1)*5+1}–${Math.min(page*5,visible.length)} of ${visible.length} ${liveMode ? 'supplier' : 'demo'} listings${response.partial ? ' · Partial results' : ''}` : liveMode ? 'No supplier listings for this search.' : 'No demo listings for this search.';
      if (items.length && !visible.length) {
        $('#result-count').textContent = 'No parts match your filters.';
        const empty = el('div', undefined, 'empty-state'); empty.append(el('h2', 'Try a wider search'), el('p', 'Raise the price limit or choose another part position.'));
        const reset = el('button', 'Clear filters'); reset.addEventListener('click', () => { resultTools.reset(); $('#filter-position').focus(); }); empty.append(reset); $('#results').append(empty);
      }
      if(!items.length) { const empty=el('div',undefined,'empty-state'); empty.append(el('h2',liveMode ? 'No matching parts were returned.' : 'Your search is ready for live inventory.'),el('p',liveMode ? 'Try a different part name or check the vehicle and configuration.' : 'This preview only includes 2021 BMW M4 Spindle. Other searches do not yet have listings.')); const link=el('a',liveMode ? 'Search again' : 'Explore the BMW M4 demo','button'); link.href=liveMode ? './index.html' : './results.html?year=2021&make=BMW&model=M4&part=Spindle';empty.append(link);$('#results').append(empty); }
      $('#pagination').replaceChildren();
      for(let n=1;n<=Math.ceil(visible.length/5);n++){const b=el('button',String(n),'secondary');b.setAttribute('aria-label',`Page ${n}`);if(n===page)b.setAttribute('aria-current','page'); b.addEventListener('click',()=>{page=n;render();$('#result-count').scrollIntoView();$('#pagination').querySelector('[aria-current]').focus({preventScroll:true});});$('#pagination').append(b);}
    }
    render();
  } catch (error) { if (request !== generation) return; $('#result-count').textContent=liveMode ? error.message : 'Unable to load listings. Please refresh to try again.'; const retry=el('button','Try again','secondary'); retry.addEventListener('click',()=>init(interchange)); $('#results').append(retry); }
}
init();
