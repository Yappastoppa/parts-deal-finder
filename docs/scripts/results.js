import { $, el, readJSON, money, addItem, partImage, imagePath } from './main.js';
// Backend seam: replace this implementation with an API request returning a listing array.
export async function searchParts(searchParams) {
  const data = await readJSON('./data/demo_results.json');
  const normalize = value => String(value || '').trim().toLowerCase();
  const matches = ['year','make','model'].every(k => normalize(searchParams[k]) === normalize(data.search[k]));
  const part = normalize(searchParams.part);
  return matches && ['spindle','spindle knuckle','spindle/knuckle assembly, front'].includes(part) ? data.listings : [];
}
let galleryImages=[], imageIndex=0;
function showImage() {
  $('#gallery-image').src = imagePath(galleryImages[imageIndex]);
  $('#gallery-image').alt = `Spindle placeholder illustration, view ${imageIndex+1}`;
  $('#gallery-counter').textContent = `${imageIndex+1} / ${galleryImages.length}`;
}
function openGallery(item) {
  galleryImages = item.images?.length ? item.images : ['./assets/images/spindle.svg']; imageIndex=0;
  $('#gallery-title').textContent = `${item.part} · ${item.stock}`; showImage(); $('#gallery').showModal();
}
$('#gallery-close').addEventListener('click',()=>$('#gallery').close());
$('#gallery-prev').addEventListener('click',()=>{imageIndex=(imageIndex-1+galleryImages.length)%galleryImages.length;showImage();});
$('#gallery-next').addEventListener('click',()=>{imageIndex=(imageIndex+1)%galleryImages.length;showImage();});
$('#gallery').addEventListener('keydown',e=>{if(e.key==='ArrowLeft') $('#gallery-prev').click(); if(e.key==='ArrowRight') $('#gallery-next').click();});
function card(item) {
  const article=el('article',undefined,'listing');
  const photo=el('div',undefined,'listing-photo'); photo.append(partImage(item),el('span','2 PLACEHOLDER VIEWS')); article.append(photo);
  const body=el('div',undefined,'listing-body'); body.append(el('div',item.location,'eyebrow'),el('h3',`${item.year} ${item.make} ${item.model} · ${item.part}`));
  const dl=el('dl');
  for (const [key,value] of [['Condition',item.condition],['Mileage',`${item.mileage.toLocaleString()} mi`],['Seller',item.seller],['Location',item.city],['Stock #',item.stock]]) { dl.append(el('dt',key),el('dd',value)); }
  body.append(dl,el('p',item.source,'muted')); article.append(body);
  const actions=el('div',undefined,'listing-actions'); actions.append(el('strong',money(item.price),'price'),el('small','Demo price · shipping not included'));
  for (const [label,action,style] of [['Choose This Part',()=>addItem(item),''],['View Photos',()=>openGallery(item),'secondary'],['Save Part',()=>addItem(item,'saved'),'text-button']]) { const button=el('button',label,style); button.addEventListener('click',action); actions.append(button); }
  article.append(actions); return article;
}
async function init() {
  const query=new URLSearchParams(location.search); const params=Object.fromEntries(['year','make','model','part'].map(k=>[k,query.get(k)||'']));
  $('#vehicle-title').textContent=[params.year,params.make,params.model].join(' '); $('#part-title').textContent=params.part;
  for(const [k,v] of Object.entries(params)) $('#search-summary').append(el('span',`${k[0].toUpperCase()+k.slice(1)}: ${v || 'Not provided'}`));
  if(Object.values(params).some(v=>!v.trim())) { $('#result-count').textContent='Enter a vehicle and part to search.'; return; }
  try {
    const items=await searchParts(params); let page=1;
    function render() {
      $('#results').replaceChildren(...items.slice((page-1)*5,page*5).map(card));
      $('#result-count').textContent=items.length ? `Showing ${(page-1)*5+1}–${Math.min(page*5,items.length)} of ${items.length} demo listings` : 'No demo listings for this search.';
      if(!items.length) { const empty=el('div',undefined,'empty-state'); empty.append(el('h2','Your search is ready for live inventory.'),el('p','This preview only includes 2021 BMW M4 Spindle. Other searches do not yet have listings.')); const link=el('a','Explore the BMW M4 demo','button'); link.href='./results.html?year=2021&make=BMW&model=M4&part=Spindle';empty.append(link);$('#results').append(empty); }
      $('#pagination').replaceChildren();
      for(let n=1;n<=Math.ceil(items.length/5);n++){const b=el('button',String(n),'secondary');b.setAttribute('aria-label',`Page ${n}`);if(n===page)b.setAttribute('aria-current','page'); b.addEventListener('click',()=>{page=n;render();$('#result-count').scrollIntoView();$('#pagination').querySelector('[aria-current]').focus({preventScroll:true});});$('#pagination').append(b);}
    }
    render();
  } catch { $('#result-count').textContent='Unable to load listings. Please refresh to try again.'; }
}
init();
