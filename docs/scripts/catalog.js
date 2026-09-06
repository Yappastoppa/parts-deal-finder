import { $, el } from './main.js';
import { loadCatalog, searchURL } from './search.js';
function option(value) { const node = el('option', value); node.value = value; return node; }
function modelsOf(value) { return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; }
async function init() {
  try {
    const data = await loadCatalog();
    const makes = Object.keys(data.makes).sort();
    $('#catalog-count').textContent = `${makes.length} MAKES / A–Z`;
    data.years.forEach(y => $('#year').append(option(y)));
    makes.forEach(m => $('#make').append(option(m)));
    data.parts.forEach(p => $('#part-options').append(option(p)));
    function updateModels() {
      $('#model-options').replaceChildren(...Object.keys(modelsOf(data.makes[$('#make').value])).map(option));
      $('#model').value = '';
    }
    $('#make').addEventListener('change', updateModels);
    $('#vehicle-form').addEventListener('submit', event => {
      event.preventDefault();
      const params = Object.fromEntries(['year','make','model','part'].map(k => [k, $(`#${k}`).value.trim()]));
      if (!params.model || !params.part) return;
      location.href = searchURL(params);
    });
    function choose(make, model, year) {
      $('#make').value = make; updateModels(); $('#model').value = model;
      if (year) $('#year').value = year;
      $('#vehicle-form').scrollIntoView({block:'center'});
      ($('#year').value ? $('#part') : $('#year')).focus();
    }
    function render(letter) {
      const host = $('#make-catalog'); host.replaceChildren();
      for (const make of makes.filter(m => !letter || m.startsWith(letter))) {
        const models = modelsOf(data.makes[make]);
        if (!Object.keys(models).length) { const row = el('div', make, 'empty-make'); row.append(el('small', 'Model data unavailable')); host.append(row); continue; }
        const group = el('details'); const summary = el('summary', make); summary.append(el('small', `${Object.keys(models).length} models`)); group.append(summary);
        const list = el('div', undefined, 'model-list');
        for (const [model, years] of Object.entries(models)) {
          if (Array.isArray(years) && years.length) {
            const detail = el('details'); detail.append(el('summary',model));
            years.forEach(year => { const button=el('button',year,'model-button'); button.addEventListener('click',()=>choose(make,model,year)); detail.append(button); }); list.append(detail);
          } else {
            const button = el('button', model, 'model-button'); button.type='button'; button.addEventListener('click', () => choose(make,model)); list.append(button);
          }
        }
        list.append(el('p','Model-year coverage unavailable. Choose a search year above.','muted')); group.append(list); host.append(group);
      }
    }
    for (const letter of ['All', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ']) {
      const button = el('button',letter,'letter'); button.type='button'; button.disabled = letter !== 'All' && !makes.some(m => m.startsWith(letter));
      button.setAttribute('aria-pressed',String(letter === 'All'));
      button.addEventListener('click', () => { $('#alphabet').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed',String(b===button))); render(letter === 'All' ? '' : letter); });
      $('#alphabet').append(button);
    }
    render('');
  } catch { $('#make-catalog').textContent = 'Unable to load the catalog. Please refresh to try again.'; $('#catalog-count').textContent = 'CATALOG UNAVAILABLE'; }
}
init();
