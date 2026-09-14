import { $, el } from './main.js';
import { loadCatalog, searchURL } from './search.js';
function option(value) { const node = el('option', value); node.value = value; return node; }
function modelsOf(value) { return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; }
async function init() {
  try {
    const data = await loadCatalog();
    const makes = Object.keys(data.makes).sort();
    $('#common-makes').disabled = false; $('#all-makes').disabled = false;
    $('#catalog-count').textContent = `${makes.length} MAKES / A–Z`;
    data.years.forEach(y => $('#year').append(option(y)));
    makes.forEach(m => $('#make').append(option(m)));
    data.parts.forEach(p => $('#part-options').append(option(p)));
    function updateModels() {
      $('#model-options').replaceChildren(...Object.keys(modelsOf(data.makes[$('#make').value])).map(option));
      $('#model').value = '';
    }
    $('#make').addEventListener('change', updateModels);
    const edit = new URLSearchParams(location.search);
    const editMake = makes.find(make => make.toLowerCase() === (edit.get('make') || '').toLowerCase());
    if (editMake) { $('#make').value = editMake; updateModels(); }
    if (data.years.some(year => String(year) === edit.get('year'))) $('#year').value = edit.get('year');
    for (const key of ['model', 'part']) if (edit.get(key)) $(`#${key}`).value = edit.get(key).slice(0, 120);
    if (edit.has('part')) $('#query').value = ['year','make','model','part'].map(key => $(`#${key}`).value).filter(Boolean).join(' ');
    document.querySelectorAll('[data-part]').forEach(button => button.addEventListener('click', () => {
      $('#part').value = button.dataset.part;
      $('#vehicle-form').scrollIntoView({block:'center'});
      ($('#year').value ? $('#model') : $('#year')).focus();
    }));
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
    const common = ['ACURA', 'AUDI', 'BMW', 'CHEVROLET', 'DODGE', 'FORD', 'HONDA', 'HYUNDAI', 'INFINITI', 'NISSAN', 'TOYOTA', 'VOLKSWAGEN'];
    let currentView = 'common';
    function render(view = currentView) {
      currentView = view;
      const host = $('#make-catalog'); host.replaceChildren();
      const term = $('#make-filter').value.trim().toUpperCase();
      let visible = term ? makes.filter(make => make.includes(term)) : view === 'common' ? makes.filter(m => common.includes(m)) : view === 'all' ? makes : makes.filter(m => m.startsWith(view));
      if (!term && view === 'common' && !visible.length) visible = makes;
      $('#catalog-count').textContent = `${visible.length} OF ${makes.length} MAKES`;
      if (!visible.length) host.append(el('p', 'No makes found. Try another name or clear your filter.', 'catalog-empty muted'));
      $('#common-makes').setAttribute('aria-pressed', String(view === 'common'));
      $('#all-makes').setAttribute('aria-pressed', String(view === 'all'));
      $('#alphabet').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.textContent === view)));
      for (const make of visible) {
        const models = modelsOf(data.makes[make]);
        if (!Object.keys(models).length) { const row = el('div', make, 'empty-make'); row.append(el('small', 'Model data unavailable')); host.append(row); continue; }
        const group = el('details'); const summary = el('summary', make); const initial = el('span', make.slice(0, 2), 'make-initial'); initial.setAttribute('aria-hidden','true'); summary.prepend(initial); summary.append(el('small', `${Object.keys(models).length} models`)); group.append(summary);
        const list = el('div', undefined, 'model-list');
        let populated = false;
        group.addEventListener('toggle', () => {
          if (!group.open || populated) return;
          populated = true;
        for (const [model, years] of Object.entries(models)) {
          if (Array.isArray(years) && years.length) {
            const detail = el('details'); detail.append(el('summary',model));
            years.forEach(year => { const button=el('button',year,'model-button'); button.addEventListener('click',()=>choose(make,model,year)); detail.append(button); }); list.append(detail);
          } else {
            const button = el('button', model, 'model-button'); button.type='button'; button.addEventListener('click', () => choose(make,model)); list.append(button);
          }
        }
        list.append(el('p','Choose a search year above to check available listings.','muted'));
        });
        group.append(list); host.append(group);
      }
    }
    $('#make-filter').addEventListener('input', () => render());
    $('#common-makes').addEventListener('click', () => { $('#make-filter').value = ''; render('common'); });
    $('#all-makes').addEventListener('click', () => { $('#make-filter').value = ''; render('all'); });
    for (const letter of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') {
      const button = el('button',letter,'letter'); button.type='button'; button.disabled = !makes.some(m => m.startsWith(letter));
      button.setAttribute('aria-pressed','false');
      button.addEventListener('click', () => { $('#make-filter').value = ''; render(letter); });
      $('#alphabet').append(button);
    }
    render('common');
  } catch {
    $('#common-makes').disabled = true; $('#all-makes').disabled = true;
    $('#make-catalog').textContent = 'Unable to load the catalog. Check your connection and try again.';
    const retry = el('button', 'Retry catalog', 'secondary'); retry.addEventListener('click', () => { retry.disabled = true; init(); }); $('#make-catalog').append(retry);
    $('#catalog-count').textContent = 'CATALOG UNAVAILABLE';
  }
}
init();
