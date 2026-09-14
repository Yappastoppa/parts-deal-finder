import { $, el } from './main.js';
import { liveMode } from './api.js';
import * as config from './config.js';

const pageName = location.pathname.split('/').pop() || 'index.html';
const page = pageName === 'cart.html' && new URLSearchParams(location.search).get('view') === 'saved' ? 'saved' : ({'index.html':'home','results.html':'results','cart.html':'cart','quote.html':'quote','about.html':'about'}[pageName] || 'home');
const topics = {
  search: {title:'Find a part', text:'Enter a year, make, model and part, such as “2021 BMW M4 Spindle”. Or use Browse By Vehicle, fill the four fields, and select Find Parts. The Search parts button also works from any page.', action:'Open search', target:'search'},
  compare: {title:'Compare your options', text:'On the results page, check Compare on two or three listings. Then select Compare parts in the bottom bar. You can compare price, condition, mileage and seller. Your selection survives refresh in this tab for the same search. Use Close comparison or Escape to return.', action:'Browse parts', target:'browse'},
  cart: {title:'Cart & saved parts', text:'Choose This Part adds a listing to your cart. Save Part keeps it in Saved Parts for later. Both stay in this browser after a refresh. Select Saved again to unsave a part. Open Cart and use Remove to delete a selection; Undo restores it. Clearing browser data removes saved selections.', action:'Open cart', target:'cart'},
  saved: {title:'Saved parts', text:'Select Save Part on a listing to keep it for later. Open Saved Parts in the navigation to see your list or add a saved part to your cart. Saved parts stay in this browser, not an account.', action:'Open saved parts', target:'saved'},
  photos: {title:'Photos & quick view', text:'Select View Photos to open the gallery; use its arrows to switch images. Quick view opens part details beside your results. Close either view with its Close button or Escape.', action:'Browse parts', target:'browse'},
  filters: {title:'Narrow your results', text:'Use Part position and Max price above the listings. Sort by price or mileage. Filters and sort stay set after refresh. Reset filters returns to the original list. Parts marked Price on request are excluded when you set a price limit.', action:'Browse parts', target:'browse'},
  quote: {title:'Quotes and availability', text:liveMode ? 'Choose current supplier parts, open Cart, and select Request a Quote. Complete your contact details and consent. A reference confirms your request was received; it does not place an order.' : 'This preview uses sample listings. Quotes, payment and real inventory are not connected. You can try the cart and comparison without placing an order.', action:'Read about the site', target:'about'},
  fitment: {title:'Confirm the fit', text:'Search results and catalog years do not guarantee compatibility. Confirm the exact part, vehicle configuration and fitment with the seller before buying.', action:'Read about the site', target:'about'},
};
function localAnswer(message) {
  const text = message.toLowerCase();
  if (/fit|compatib|repair|install|safe|brake/.test(text)) return topics.fitment;
  if (/compar/.test(text)) return topics.compare;
  if (/sav|favorit|favourit/.test(text)) return topics.saved;
  if (/cart|remov|basket|refresh/.test(text)) return topics.cart;
  if (/photo|picture|image|quick view/.test(text)) return topics.photos;
  if (/filter|sort|price|mile/.test(text)) return topics.filters;
  if (/quote|order|pay|stock|inventor|real/.test(text)) return topics.quote;
  if (/search|find|make|model|vehicle|catalog|start/.test(text)) return topics.search;
  return {title:'I can help you get around', text:'Try asking how to search, compare parts, view photos, or use your cart. For more about what this site can do, open the site guide.', action:'Open site guide', target:'about'};
}

const launcher = el('button', undefined, 'help-launcher'); launcher.id='help-launcher'; launcher.type='button';
const icon=el('span','?'); icon.setAttribute('aria-hidden','true'); launcher.append(icon,el('span','Help'));
launcher.setAttribute('aria-haspopup','dialog'); launcher.setAttribute('aria-expanded','false'); launcher.setAttribute('aria-controls','site-help');
const panel=el('section',undefined,'help-panel'); panel.id='site-help'; panel.hidden=true; panel.setAttribute('role','dialog'); panel.setAttribute('aria-modal','false'); panel.setAttribute('aria-labelledby','help-title');
const header=el('div',undefined,'help-header'); const heading=el('h2','A little help?'); heading.id='help-title'; const close=el('button','×','help-close'); close.type='button';close.setAttribute('aria-label','Close help');header.append(heading,close);
const mode=el('p','Quick help · built-in guidance','help-mode');
const intro=el('p','Find your way around in a few clicks.','help-intro');
const choices=el('div',undefined,'help-topics');
const back=el('button','← All topics','help-back');back.type='button';back.hidden=true;
const answer=el('div',undefined,'help-answer');answer.setAttribute('aria-live','polite');answer.setAttribute('aria-atomic','true');
const form=el('form',undefined,'help-form');const label=el('label','Ask about using the site');const input=el('input'); input.id='help-question';input.type='text';input.maxLength=600;input.required=true;input.placeholder='How do I compare parts?';input.autocomplete='off';label.htmlFor=input.id;
const send=el('button','Ask');send.type='submit';const line=el('div',undefined,'help-input-line');line.append(input,send);form.append(label,line);
const footer=el('p','Answers here are built-in tips. AI chat is not connected.','help-footnote');
panel.append(header,mode,intro,choices,back,answer,form,footer);document.body.append(launcher,panel);document.body.classList.add('has-site-help');
let aiReady=false, statusChecked=false, requestController;
let apiBase='';
try {
  if (config.HELP_API_BASE_URL) {
    const url=new URL(config.HELP_API_BASE_URL);
    if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || (url.protocol!=='https:' && !(url.protocol==='http:' && ['localhost','127.0.0.1'].includes(url.hostname)))) throw new Error('Invalid origin');
    apiBase=url.origin;
  }
} catch { /* Built-in help remains available if public configuration is invalid. */ }
function setMode() {
  mode.textContent=aiReady ? 'AI site help · quick guides included' : 'Quick help · built-in guidance';
  footer.textContent=aiReady ? 'Questions are sent to AI. Answers may be wrong; don’t share private details.' : 'Answers here are built-in tips. AI chat is not connected.';
}
async function checkAI() {
  if(statusChecked || !apiBase)return; statusChecked=true;
  try {
    const response=await fetch(apiBase+'/api/help/status',{credentials:'omit',signal:AbortSignal.timeout(5000)});
    if(response.ok){const data=await response.json();aiReady=data.enabled===true;setMode();}
  } catch { /* No network connection is needed for the quick guides. */ }
}
function answerMode(active) { intro.hidden=active;choices.hidden=active;back.hidden=!active; }
back.addEventListener('click',()=>{ if(requestController){requestController.abort();requestController=null;}send.disabled=false;input.disabled=false;answer.replaceChildren();answerMode(false); });
function dismiss(restore=true) {
  panel.hidden=true;launcher.setAttribute('aria-expanded','false');
  if(requestController) { requestController.abort(); requestController=null;answer.replaceChildren();send.disabled=false;input.disabled=false; }
  answer.replaceChildren();answerMode(false);input.value='';
  if(restore)launcher.focus({preventScroll:true});
}
function show() {
  if(document.querySelector('dialog[open]'))return;
  panel.hidden=false;launcher.setAttribute('aria-expanded','true');close.focus({preventScroll:true});checkAI();
}
launcher.addEventListener('click',()=>panel.hidden?show():dismiss());close.addEventListener('click',()=>dismiss());
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden&&!document.querySelector('dialog[open]')){event.preventDefault();dismiss();}});
document.addEventListener('click',event=>{if(!panel.hidden&&!panel.contains(event.target)&&!launcher.contains(event.target))dismiss(false);});
function navigate(target) {
  dismiss(false);
  if(target==='search'){ $('.search-launch')?.click();return; }
  const routes={cart:'./cart.html',saved:'./cart.html?view=saved',about:'./about.html',browse:liveMode?'./index.html':'./results.html?year=2021&make=BMW&model=M4&part=Spindle'};
  location.href=routes[target]||'./index.html';
}
function showGuide(topic) {
  answerMode(true);
  if(requestController){requestController.abort();requestController=null;send.disabled=false;input.disabled=false;}
  const title=el('h3',topic.title); const text=el('p',topic.text);const action=el('button',topic.action,'help-action');action.type='button';action.addEventListener('click',()=>navigate(topic.target));
  answer.replaceChildren(el('span','Quick guide','help-source'),title,text,action);
}
for(const key of ['search','compare','cart','photos']){const button=el('button',topics[key].title,'help-topic');button.type='button';button.addEventListener('click',()=>showGuide(topics[key]));choices.append(button);}
form.addEventListener('submit',async event=>{
  event.preventDefault();const message=input.value.trim();if(!message)return;
  if(!aiReady){showGuide(localAnswer(message));return;}
  const controller=new AbortController();requestController=controller;const timer=setTimeout(()=>controller.abort(),20000);
  answerMode(true);send.disabled=true;input.disabled=true;answer.replaceChildren(el('p','Finding a helpful answer…'));
  try {
    const response=await fetch(apiBase+'/api/help',{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,page,mode:liveMode?'live':'demo'}),signal:controller.signal});
    const data=await response.json();if(!response.ok||typeof data.answer!=='string'||!data.answer.trim()||data.answer.length>3000)throw new Error('No answer');
    if(requestController!==controller)return;
    answer.replaceChildren(el('span','AI answer','help-source'),el('p',data.answer));
  } catch {
    if(requestController!==controller)return;
    showGuide(localAnswer(message));answer.prepend(el('p','AI help is unavailable right now. Here’s a quick guide instead.','help-fallback'));
  } finally {
    clearTimeout(timer);if(requestController===controller){requestController=null;send.disabled=false;input.disabled=false;}
  }
});
