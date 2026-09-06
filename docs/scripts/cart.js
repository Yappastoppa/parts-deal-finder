import { $, el, readItems, writeItems, money, partImage, addItem } from './main.js';
const saved=new URLSearchParams(location.search).get('view')==='saved';
const collection=saved?'saved':'cart';
if(saved){$('#collection-title').textContent='Saved Parts';$('#collection-description').textContent='A shortlist for your next repair, stored in this browser.';}
function render(){
  const items=readItems(collection); $('#cart-items').replaceChildren();
  if(!items.length){const empty=el('div',undefined,'empty-state');empty.append(el('h2',saved?'No saved parts yet.':'Your cart is empty.'),el('p','Search for a part to start comparing your options.'));const a=el('a','Find Your Part →','button');a.href='./index.html';empty.append(a);$('#cart-items').append(empty);}
  for(const item of items){
    const row=el('article',undefined,'listing cart-listing');row.append(partImage(item));const body=el('div');body.append(el('h2',item.part),el('p',`${item.year} ${item.make} ${item.model} · ${item.location}`),el('p',`${item.seller} · ${item.city}`));row.append(body);
    const actions=el('div',undefined,'listing-actions');actions.append(el('strong',money(item.price),'price'));
    if(saved){const add=el('button','Choose This Part');add.addEventListener('click',()=>addItem(item));actions.append(add);}
    const remove=el('button','Remove','secondary');remove.setAttribute('aria-label',`Remove ${item.part} ${item.stock}`);remove.addEventListener('click',()=>{if(writeItems(readItems(collection).filter(x=>x.id!==item.id),collection)){render();$('#collection-title').tabIndex=-1;$('#collection-title').focus();}});actions.append(remove);row.append(actions);$('#cart-items').append(row);
  }
  $('#cart-total').textContent=!saved&&items.length?`Demo subtotal: ${money(items.reduce((total,item)=>total+item.price,0))} · Shipping and tax not calculated. No checkout available.`:'';
}
window.addEventListener('storage',render);render();
