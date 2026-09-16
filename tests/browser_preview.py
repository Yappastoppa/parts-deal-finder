"""Static frontend regression checks. Requires Playwright and a running preview server."""
from playwright.sync_api import sync_playwright, expect
import os
base=os.environ.get('APF_PREVIEW_URL', 'http://127.0.0.1:8765/parts-deal-finder/')
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1000})
 # Demo checks are independent of whatever backend the committed config.js points to in production.
 page.route('**/scripts/config.js', lambda route: route.fulfill(content_type='text/javascript', body='export const API_BASE_URL = ""; export const HELP_API_BASE_URL = "";'))
 errors=[];bad=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
 page.on('response',lambda r:bad.append(r.url) if r.status>=400 else None)
 page.goto(base);page.wait_for_function("document.querySelector('#catalog-count').textContent.includes('MAKES')")
 assert page.locator('#make-catalog > details').count()==12
 assert page.locator('.model-list .model-button').count()==0
 assert page.get_by_role('button',name='Common makes',exact=True).get_attribute('aria-pressed')=='true'
 page.get_by_role('button',name='View all makes',exact=True).click()
 assert page.locator('#make-catalog > details').count()>20
 page.get_by_role('button',name='Common makes',exact=True).click()
 assert page.locator('#make-catalog > details').count()==12
 assert page.locator('.edition').count()==0
 page.screenshot(path='/tmp/apf-home.png',full_page=True)
 assert page.locator('.vehicle-sidebar').bounding_box()['x'] < page.locator('#catalog').bounding_box()['x']
 page.locator('#make-filter').fill('bmw')
 assert page.locator('#make-catalog > details').count()==1
 page.locator('#make-filter').fill('not-a-make')
 assert page.locator('#make-catalog > details').count()==0
 page.locator('#make-filter').fill('')
 assert page.locator('#make-catalog > details').count()==12
 page.keyboard.press('Control+k')
 assert page.locator('.command-search').is_visible()
 page.locator('.command-search input').fill('bad query')
 page.locator('.command-search form button').click()
 assert page.locator('.search-validation').inner_text()
 page.keyboard.press('Escape')
 assert not page.locator('.command-search').is_visible()
 page.locator('button[data-part=Engine]').click()
 assert page.locator('#part').input_value()=='Engine'
 page.get_by_role('button',name='B',exact=True).click()
 page.locator('#make-catalog summary').filter(has_text='BMW').click()
 page.get_by_role('button',name='M4',exact=True).click()
 assert page.locator('#make').input_value()=='BMW' and page.locator('#model').input_value()=='M4'
 page.locator('#query').fill('bad query');page.locator('#search-form button').click();assert page.locator('#search-error').inner_text()
 page.locator('#query').fill('2021 BMW M4 Spindle');page.locator('#search-form button').click();page.wait_for_url('**/results.html?*');page.wait_for_selector('.listing')
 assert 'year=2021&make=BMW&model=M4&part=Spindle' in page.url
 assert page.locator('.listing').count()==5
 page.get_by_role('button',name='Quick view ↗',exact=True).first.click()
 assert page.locator('.quick-view').is_visible()
 assert 'DEMO-M4-001' in page.locator('.quick-view').inner_text()
 page.keyboard.press('Escape')
 expect(page.locator('.quick-view')).to_have_count(0)
 page.get_by_role('button',name='Page 2',exact=True).click();assert page.locator('.listing').count()==1
 page.reload();page.wait_for_selector('.listing');assert page.locator('.listing').count()==1
 assert 'page=2' in page.url
 page.get_by_role('button',name='Page 1',exact=True).click()
 # Modern discovery controls reset pagination and preserve comparison across pages.
 page.locator('#sort-parts').select_option('price-low')
 assert '$229.00' in page.locator('.listing').first.inner_text()
 page.locator('#filter-position').select_option('Front right')
 assert page.locator('.listing').count()==3
 page.locator('#filter-price').fill('280')
 assert page.locator('.listing').count()==1 and '$275.00' in page.locator('.listing').first.inner_text()
 page.reload();page.wait_for_selector('.listing')
 assert page.locator('#filter-price').input_value()=='280'
 assert page.locator('#filter-position').input_value()=='Front right'
 assert page.locator('#sort-parts').input_value()=='price-low'
 assert page.locator('.listing').count()==1
 page.locator('#filter-price').fill('1')
 assert page.get_by_role('heading',name='Try a wider search').is_visible()
 page.get_by_role('button',name='Clear filters',exact=True).click()
 assert page.locator('.listing').count()==5
 page.get_by_role('checkbox',name='Compare DEMO-M4-001',exact=True).check()
 page.get_by_role('checkbox',name='Compare DEMO-M4-002',exact=True).check()
 page.get_by_role('button',name='Page 2',exact=True).click()
 page.get_by_role('checkbox',name='Compare DEMO-M4-006',exact=True).check()
 page.get_by_role('button',name='Page 1',exact=True).click()
 page.reload();page.wait_for_selector('.listing')
 assert page.get_by_role('checkbox',name='Compare DEMO-M4-001',exact=True).is_checked()
 assert '3 of 3' in page.locator('.compare-tray').inner_text()
 page.get_by_role('checkbox',name='Compare DEMO-M4-003',exact=True).click()
 assert not page.get_by_role('checkbox',name='Compare DEMO-M4-003',exact=True).is_checked()
 page.get_by_role('button',name='Compare parts',exact=True).click()
 assert page.locator('#comparison').is_visible()
 assert page.locator('.compare-table thead th').count()==4
 assert 'DEMO-M4-006' in page.locator('.compare-table').inner_text()
 page.screenshot(path='/tmp/apf-compare.png')
 page.get_by_role('button',name='Remove from comparison',exact=True).last.click()
 assert page.locator('.compare-table thead th').count()==3
 page.keyboard.press('Escape')
 assert not page.locator('#comparison').is_visible()
 page.get_by_role('button',name='Compare parts',exact=True).click()
 page.locator('#comparison').get_by_role('button',name='Choose This Part',exact=True).first.click()
 assert not page.locator('#comparison').is_visible()
 assert page.locator('[data-cart-count]').inner_text()=='1'
 page.get_by_role('button',name='Clear selection',exact=True).click()
 assert not page.locator('.compare-tray').is_visible()
 page.get_by_role('button',name='View Photos').first.click();assert page.locator('#gallery').evaluate('(d)=>d.open')
 initial=page.locator('#gallery-image').get_attribute('src');page.locator('#gallery-next').click();assert initial!=page.locator('#gallery-image').get_attribute('src')
 page.locator('#gallery-prev').click();assert initial==page.locator('#gallery-image').get_attribute('src')
 page.keyboard.press('Escape');assert not page.locator('#gallery').evaluate('(d)=>d.open')
 page.get_by_role('button',name='View Photos').first.click();page.locator('#gallery-close').click();assert not page.locator('#gallery').evaluate('(d)=>d.open')
 page.get_by_role('button',name='Choose This Part').first.click();page.get_by_role('button',name='Choose This Part').first.click();assert page.locator('[data-cart-count]').inner_text()=='1'
 page.get_by_role('button',name='Save Part',exact=True).first.click()
 assert page.get_by_role('button',name='Remove from saved parts',exact=True).count()==1
 page.get_by_role('button',name='Remove from saved parts',exact=True).click()
 assert page.locator('[data-saved-count]').inner_text()=='0'
 page.get_by_role('button',name='Save Part',exact=True).first.click()
 page.screenshot(path='/tmp/apf-results.png',full_page=True)
 page.get_by_role('link',name='Cart 1',exact=True).click();page.wait_for_selector('.cart-listing');assert page.locator('.cart-listing').count()==1
 page.reload();page.wait_for_selector('.cart-listing');assert page.locator('.cart-listing').count()==1
 assert page.get_by_role('link',name='Request a Quote →',exact=True).count()==0
 assert 'demo selections' in page.locator('#quote-action').inner_text()
 page.screenshot(path='/tmp/apf-cart.png',full_page=True)
 page.locator('.cart-listing').get_by_role('button',name='Remove Spindle DEMO-M4-001',exact=True).click();assert page.locator('.cart-listing').count()==0
 page.get_by_role('button',name='Undo',exact=True).click();assert page.locator('.cart-listing').count()==1
 page.locator('.cart-listing').get_by_role('button',name='Remove Spindle DEMO-M4-001',exact=True).click()
 page.get_by_role('button',name='Dismiss notification',exact=True).click();assert page.locator('#notice').inner_text()==''
 page.get_by_role('link',name='Saved Parts 1').click();page.wait_for_selector('.cart-listing');assert page.locator('.cart-listing').count()==1
 page.get_by_role('button',name='Choose This Part').click();assert page.locator('[data-cart-count]').inner_text()=='1'
 page.goto(base)
 assert page.locator('.recent-searches a').count()==1
 page.locator('.recent-searches a').first.click();page.wait_for_selector('.listing')
 page.goto(base);page.get_by_role('button',name='Clear recent searches',exact=True).click()
 assert page.locator('.recent-searches').count()==0
 page.reload();assert page.locator('.recent-searches').count()==0
 page.goto(base);page.locator('#year').select_option('2020');page.locator('#make').select_option('HONDA');page.locator('#model').fill('Accord');page.locator('#part').fill('Engine');page.get_by_role('button',name='Find Parts',exact=True).click();page.wait_for_selector('.empty-state');assert page.locator('.listing').count()==0
 page.goto(base+'about.html')
 page.locator('.search-launch').click()
 page.locator('.command-search input').fill('2021 BMW M4 Spindle')
 page.locator('.command-search form button').click()
 page.wait_for_selector('.listing')
 page.get_by_role('link',name='← Edit search',exact=True).click()
 expect(page.locator('#year')).to_have_value('2021')
 assert page.locator('#make').input_value()=='BMW' and page.locator('#model').input_value()=='M4'
 assert page.locator('#part').input_value()=='Spindle'
 assert page.locator('.nav-inner a[aria-current=page]').inner_text()=='Part Search'
 page.goto(base+'about.html');assert page.get_by_role('heading',name='Parts first. Project next.').is_visible()
 for width in [320,375,768]:
  page.set_viewport_size({'width':width,'height':812})
  for path in ['index.html','results.html?year=2021&make=BMW&model=M4&part=Spindle','cart.html','about.html']:
   page.goto(base+path);page.wait_for_timeout(200)
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(width,path)
   assert page.locator('.nav-cart').is_visible()
   if path=='index.html' and width==375:
    page.screenshot(path='/tmp/apf-home-mobile.png',full_page=True)
    assert page.locator('.nav-cart').bounding_box()['height']>=40
    assert page.locator('#year').bounding_box()['height']>=44
    page.get_by_role('button',name='View all makes',exact=True).click()
    assert page.locator('#make-catalog > details').count()>20
    page.get_by_role('button',name='B',exact=True).click()
    page.locator('#make-catalog summary').filter(has_text='BMW').click()
    expect(page.get_by_role('button',name='M4',exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   if width==375 and path.startswith('results'):
    page.get_by_role('button',name='View Photos').first.click();assert page.locator('#gallery').is_visible();page.locator('#gallery-close').click();page.screenshot(path='/tmp/apf-mobile.png',full_page=True)
 page.set_viewport_size({'width':375,'height':812})
 page.goto(base+'results.html?year=2021&make=BMW&model=M4&part=Spindle');page.wait_for_selector('.listing')
 page.get_by_role('checkbox',name='Compare DEMO-M4-001',exact=True).check()
 page.get_by_role('checkbox',name='Compare DEMO-M4-002',exact=True).check()
 page.get_by_role('button',name='Compare parts',exact=True).click()
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
 assert page.locator('.compare-scroll').evaluate('(n)=>n.scrollWidth > n.clientWidth')
 page.screenshot(path='/tmp/apf-compare-mobile.png')
 page.get_by_role('button',name='Close comparison',exact=True).click()
 page.get_by_role('button',name='Clear selection',exact=True).click()
 page.get_by_role('button',name='Quick view ↗',exact=True).first.click()
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
 page.screenshot(path='/tmp/apf-quick-mobile.png', animations='disabled')
 page.locator('.quick-view').get_by_role('button',name='Choose This Part',exact=True).click()
 expect(page.locator('.quick-view')).to_have_count(0)
 # Corrupt storage recovers, blocked writes explain failure.
 page.evaluate("localStorage.setItem('apf:cart','not json')");page.goto(base+'cart.html');assert page.locator('.empty-state').is_visible()
 page.goto(base+'results.html?year=2021&make=BMW&model=M4&part=Spindle');page.wait_for_selector('.listing');page.evaluate("() => { Storage.prototype.setItem = () => {throw new Error('blocked')}; }");page.get_by_role('button',name='Choose This Part').first.click();assert 'could not be saved' in page.locator('#notice').inner_text()
 assert not errors,errors
 assert not bad,bad
 # Unknown prices are not displayed as zero-dollar totals.
 page.goto(base)  # Reset the deliberate storage prototype override.
 page.evaluate("localStorage.setItem('apf:cart', JSON.stringify([{id:'unknown', price:null, part:'Engine', year:'2021', make:'BMW', model:'M4', stock:'TEST'}]))")
 page.goto(base+'cart.html');assert '1 price is not confirmed' in page.locator('#cart-total').inner_text()
 assert '$0.00' not in page.locator('#cart-total').inner_text()
 # Deliberate fetch failure and empty make fixtures.
 page.route('**/data/car_data.json',lambda route:route.fulfill(status=503,body='Unavailable'))
 page.goto(base);page.get_by_role('button',name='Retry catalog',exact=True).wait_for()
 page.unroute('**/data/car_data.json')
 page.get_by_role('button',name='Retry catalog',exact=True).click()
 expect(page.locator('#make-catalog > details')).to_have_count(12)
 assert page.get_by_role('button',name='Common makes',exact=True).is_enabled()
 page.route('**/data/car_data.json',lambda route:route.fulfill(json={'years':['2021'],'makes':{'EMPTY':{}},'parts':['Spindle']}))
 page.goto(base);page.wait_for_selector('.empty-make');assert page.locator('#make-catalog details').count()==0
 page.unroute('**/data/car_data.json');page.route('**/data/demo_results.json',lambda route:route.abort())
 page.goto(base+'results.html?year=2021&make=BMW&model=M4&part=Spindle');page.wait_for_function("document.querySelector('#result-count').textContent.includes('Unable')")
 print('PASS: global keyboard search, make filtering, quick-view drawer/cart selection, price/position filters, sorting, cross-page comparison/limit/removal/cart choice, mobile comparison, recent searches/clear, subpath assets, styles, catalog/model selection, parser, dropdown search, five-per-page pagination, gallery next/previous/close/Escape, cart deduplication/persistence/removal, saved parts, empty searches, all pages at 320/375/768px, common/all makes, usable mobile controls, malformed and blocked storage, empty makes, fetch failure. No console errors or HTTP failures in normal flow.')
 browser.close()
