"""Static frontend regression checks. Requires Playwright and a running preview server."""
from playwright.sync_api import sync_playwright
import os
base=os.environ.get('APF_PREVIEW_URL', 'http://127.0.0.1:8765/parts-deal-finder/')
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1000})
 errors=[];bad=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
 page.on('response',lambda r:bad.append(r.url) if r.status>=400 else None)
 page.goto(base);page.wait_for_function("document.querySelector('#catalog-count').textContent.includes('MAKES')")
 assert page.locator('#make-catalog details').count()>20
 assert page.evaluate("getComputedStyle(document.querySelector('header')).backgroundColor")=='rgb(23, 39, 53)'
 page.screenshot(path='/tmp/apf-home.png',full_page=True)
 page.get_by_role('button',name='B',exact=True).click()
 page.locator('#make-catalog summary').filter(has_text='BMW').click()
 page.get_by_role('button',name='M4',exact=True).click()
 assert page.locator('#make').input_value()=='BMW' and page.locator('#model').input_value()=='M4'
 page.locator('#query').fill('bad query');page.locator('#search-form button').click();assert page.locator('#search-error').inner_text()
 page.locator('#query').fill('2021 BMW M4 Spindle');page.locator('#search-form button').click();page.wait_for_url('**/results.html?*');page.wait_for_selector('.listing')
 assert 'year=2021&make=BMW&model=M4&part=Spindle' in page.url
 assert page.locator('.listing').count()==5
 page.get_by_role('button',name='Page 2',exact=True).click();assert page.locator('.listing').count()==1
 page.get_by_role('button',name='Page 1',exact=True).click()
 page.get_by_role('button',name='View Photos').first.click();assert page.locator('#gallery').evaluate('(d)=>d.open')
 initial=page.locator('#gallery-image').get_attribute('src');page.locator('#gallery-next').click();assert initial!=page.locator('#gallery-image').get_attribute('src')
 page.locator('#gallery-prev').click();assert initial==page.locator('#gallery-image').get_attribute('src')
 page.keyboard.press('Escape');assert not page.locator('#gallery').evaluate('(d)=>d.open')
 page.get_by_role('button',name='View Photos').first.click();page.locator('#gallery-close').click();assert not page.locator('#gallery').evaluate('(d)=>d.open')
 page.get_by_role('button',name='Choose This Part').first.click();page.get_by_role('button',name='Choose This Part').first.click();assert page.locator('[data-cart-count]').inner_text()=='1'
 page.get_by_role('button',name='Save Part',exact=True).first.click()
 page.screenshot(path='/tmp/apf-results.png',full_page=True)
 page.get_by_role('link',name='Cart 1',exact=True).click();assert page.locator('.cart-listing').count()==1
 page.reload();assert page.locator('.cart-listing').count()==1
 page.get_by_role('button',name='Remove',exact=False).click();assert page.locator('.cart-listing').count()==0
 page.get_by_role('link',name='Saved Parts 1').click();assert page.locator('.cart-listing').count()==1
 page.get_by_role('button',name='Choose This Part').click();assert page.locator('[data-cart-count]').inner_text()=='1'
 page.goto(base);page.locator('#year').select_option('2020');page.locator('#make').select_option('HONDA');page.locator('#model').fill('Accord');page.locator('#part').fill('Engine');page.get_by_role('button',name='Find Parts',exact=True).click();page.wait_for_selector('.empty-state');assert page.locator('.listing').count()==0
 page.goto(base+'about.html');assert page.get_by_role('heading',name='Parts first. Project next.').is_visible()
 for width in [375,768]:
  page.set_viewport_size({'width':width,'height':812})
  for path in ['index.html','results.html?year=2021&make=BMW&model=M4&part=Spindle','cart.html','about.html']:
   page.goto(base+path);page.wait_for_timeout(200)
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(width,path)
   if width==375 and path.startswith('results'):
    page.get_by_role('button',name='View Photos').first.click();assert page.locator('#gallery').is_visible();page.locator('#gallery-close').click();page.screenshot(path='/tmp/apf-mobile.png',full_page=True)
 # Corrupt storage recovers, blocked writes explain failure.
 page.evaluate("localStorage.setItem('apf:cart','not json')");page.goto(base+'cart.html');assert page.locator('.empty-state').is_visible()
 page.goto(base+'results.html?year=2021&make=BMW&model=M4&part=Spindle');page.wait_for_selector('.listing');page.evaluate("() => { Storage.prototype.setItem = () => {throw new Error('blocked')}; }");page.get_by_role('button',name='Choose This Part').first.click();assert 'could not be saved' in page.locator('#notice').inner_text()
 assert not errors,errors
 assert not bad,bad
 # Deliberate fetch failure and empty make fixtures.
 page.route('**/data/car_data.json',lambda route:route.fulfill(json={'years':['2021'],'makes':{'EMPTY':{}},'parts':['Spindle']}))
 page.goto(base);page.wait_for_selector('.empty-make');assert page.locator('#make-catalog details').count()==0
 page.unroute('**/data/car_data.json');page.route('**/data/demo_results.json',lambda route:route.abort())
 page.goto(base+'results.html?year=2021&make=BMW&model=M4&part=Spindle');page.wait_for_function("document.querySelector('#result-count').textContent.includes('Unable')")
 print('PASS: subpath assets, styles, catalog/model selection, parser, dropdown search, five-per-page pagination, gallery next/previous/close/Escape, cart deduplication/persistence/removal, saved parts, empty searches, all pages at 375/768px, malformed and blocked storage, empty makes, fetch failure. No console errors or HTTP failures in normal flow.')
 browser.close()
