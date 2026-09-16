import { createResultTools, createComparison, openQuickView } from './discovery.js';
import { rememberSearch } from './recent.js';
import { liveMode, liveSearch, forgetSearch, fetchPhotos, cacheStatus } from './api.js';
import { $, el, readJSON, money, selectionButton, partImage, imagePath, notify } from './main.js';
// Demo remains static; live mode uses the isolated customer API.
export async function searchParts(searchParams, forceRefresh = false) {
  if (liveMode) return liveSearch(searchParams, message => { $('#result-count').textContent = message; }, forceRefresh);
  const data = await readJSON('./data/demo_results.json');
  const normalize = value => String(value || '').trim().toLowerCase();
  const matches = ['year','make','model'].every(k => normalize(searchParams[k]) === normalize(data.search[k]));
  const part = normalize(searchParams.part);
  return {status: 'ok', listings: matches && ['spindle','spindle knuckle','spindle/knuckle assembly, front'].includes(part) ? data.listings : []};
}
function timeAgo(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`;
  const h = Math.round(m / 60);
  return `${h} hour${h === 1 ? '' : 's'} ago`;
}
let galleryImages=[], imageIndex=0, galleryIsLive=false, galleryRequest=0;
const photoRequests = new Map();
function loadPhotos(item) {
  if (item.galleryChecked || !item.has_gallery) return Promise.resolve();
  if (!photoRequests.has(item.id)) {
    photoRequests.set(item.id, fetchPhotos(item.id).then(result => {
      if (result.listing_id !== item.id || result.gallery_status === 'error') throw new Error('Photos unavailable');
      item.images = result.images || []; item.galleryChecked = true;
    }).finally(() => photoRequests.delete(item.id)));
  }
  return photoRequests.get(item.id);
}
let photoQueue = Promise.resolve();
const visiblePhotos = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    visiblePhotos.unobserve(entry.target);
    const { item, photo } = entry.target.photoContext;
    photoQueue = photoQueue.then(async () => {
      if (!entry.target.isConnected) return;
      await loadPhotos(item);
      if (item.images?.length && entry.target.isConnected) {
        photo.querySelector('img').replaceWith(partImage(item));
        photo.querySelector('span').textContent = `${item.images.length} SUPPLIER PHOTOS`;
      }
    }).catch(() => {});
  }
});
function showImage() {
  $('#gallery-image').src = imagePath(galleryImages[imageIndex]);
  $('#gallery-image').alt = `${galleryIsLive ? 'Supplier photo' : 'Placeholder illustration'}, view ${imageIndex+1}`;
  $('#gallery-counter').textContent = `${imageIndex+1} / ${galleryImages.length}`;
}
async function openGallery(item) {
  const request = ++galleryRequest;
  if (item.mode === 'live' && item.has_gallery && !item.images?.length && !item.galleryChecked) {
    galleryIsLive = false; galleryImages = ['./assets/images/spindle.svg']; imageIndex = 0;
    $('#gallery .muted').textContent = 'Loading supplier photos…';
    $('#gallery-title').textContent = `${item.part} · ${item.stock}`; showImage(); $('#gallery').showModal();
    try {
      await loadPhotos(item);
    } catch { /* A later View Photos action can retry. */ }
    if (!$('#gallery').open || request !== galleryRequest) return;
  }
  galleryIsLive = item.mode === 'live' && Boolean(item.images?.length);
  $('#gallery .muted').textContent = galleryIsLive ? 'Supplier photos. Confirm the exact part and condition before purchase.'
    : item.mode === 'live' ? 'No supplier photo was provided for this listing. Confirm details before purchase.' : 'Original placeholder illustration. Not a photograph of inventory.';
  galleryImages = item.images?.length ? item.images : ['./assets/images/spindle.svg']; imageIndex=0;
  $('#gallery-title').textContent = `${item.part} · ${item.stock}`; showImage(); $('#gallery').showModal();
}
$('#gallery-image').addEventListener('error', () => { const img = $('#gallery-image'); const fallback = new URL('./assets/images/spindle.svg', location.href).href; if (img.src !== fallback) img.src = fallback; img.alt='Photo unavailable; placeholder illustration'; });
$('#gallery-close').addEventListener('click',()=>$('#gallery').close());
$('#gallery-prev').addEventListener('click',()=>{imageIndex=(imageIndex-1+galleryImages.length)%galleryImages.length;showImage();});
$('#gallery-next').addEventListener('click',()=>{imageIndex=(imageIndex+1)%galleryImages.length;showImage();});
$('#gallery').addEventListener('keydown',e=>{if(e.key==='ArrowLeft') $('#gallery-prev').click(); if(e.key==='ArrowRight') $('#gallery-next').click();});
let resultTools, comparison, generation = 0;
function renderCacheBanner(cache, params, request) {
  if (!cache) return;
  const banner = el('p', '', 'cache-status'); banner.id = 'cache-status';
  const age = () => timeAgo(Date.now() / 1000 - cache.last_refreshed_at);
  banner.textContent = cache.status === 'fresh' ? `Live search just now · ${cache.result_count} part${cache.result_count === 1 ? '' : 's'}`
    : cache.status === 'refreshing' ? `Recently checked ${age()} · Refreshing live inventory…`
    : `Previously found inventory · Last checked ${age()}`;
  $('#search-summary').after(banner);
  if (cache.status !== 'refreshing') return;
  (async () => {
    for (let attempt = 0; attempt < 12; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 2500));
      if (request !== generation) return;
      const status = await cacheStatus(params);
      if (!status?.cache || status.cache.refreshing) continue;
      const response = await searchParts(params);
      if (request !== generation) return;
      notify(`Inventory refreshed · ${response.listings?.length ?? 0} parts · Updated just now`);
      init(params.interchange || '');
      return;
    }
  })();
}
function card(item) {
  const article=el('article',undefined,'listing');
  const photoLabel = item.mode === 'live' ? (item.images?.length ? `${item.images.length} SUPPLIER PHOTOS` : item.has_gallery ? 'PHOTOS AVAILABLE' : 'NO SUPPLIER PHOTO') : '2 PLACEHOLDER VIEWS';
  const photo=el('div',undefined,'listing-photo'); photo.append(partImage(item),el('span', photoLabel)); article.append(photo);
  if (item.mode === 'live' && item.has_gallery) {
    article.photoContext = { item, photo }; visiblePhotos.observe(article);
  }
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
async function init(interchange, forceRefresh = false) {
  const request = ++generation;
  resultTools?.destroy(); comparison?.destroy(); resultTools = undefined; comparison = undefined;
  const query=new URLSearchParams(location.search); const params=Object.fromEntries(['year','make','model','part'].map(k=>[k,query.get(k)||'']));
  const edit = $('.back'); edit.href = `./index.html?${new URLSearchParams(params)}#vehicle-form`; edit.textContent = '← Edit search';
  interchange = interchange ?? query.get('interchange') ?? '';
  if (interchange) query.set('interchange', interchange);
  else query.delete('interchange');
  history.replaceState(null, '', `${location.pathname}?${query}`);
  $('#search-summary').replaceChildren(); $('#cache-status')?.remove();
  $('#vehicle-title').textContent=[params.year,params.make,params.model].join(' '); $('#part-title').textContent=params.part;
  for(const [k,v] of Object.entries(params)) $('#search-summary').append(el('span',`${k[0].toUpperCase()+k.slice(1)}: ${v || 'Not provided'}`));
  if(Object.values(params).some(v=>!v.trim())) { $('#result-count').textContent='Enter a vehicle and part to search.'; return; }
  rememberSearch(params);
  if (interchange) params.interchange = interchange;
  $('#results').replaceChildren(); $('#pagination').replaceChildren();
  $('#result-count').textContent = liveMode ? 'Connecting to supplier inventory…' : 'Loading listings…';
  try {
    const response=await searchParts(params, forceRefresh);
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
      refresh.addEventListener('click',()=>{ forgetSearch(); init(interchange, true); });
      $('#search-summary').append(refresh);
      renderCacheBanner(response.cache, params, request);
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
