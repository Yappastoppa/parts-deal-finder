"""Minimal help UX and opt-in AI adapter checks, without real provider traffic."""
import json
import os
from playwright.sync_api import sync_playwright, expect
base=os.environ.get('APF_PREVIEW_URL','http://127.0.0.1:8000/')
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1000})
 errors=[];bad=[]
 # These checks target the static demo help experience, independent of whatever backend config.js points to in production.
 page.route('**/scripts/config.js', lambda route: route.fulfill(content_type='text/javascript', body='export const API_BASE_URL = ""; export const HELP_API_BASE_URL = "";'))
 page.on('pageerror',lambda error:errors.append(str(error)))
 page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
 page.on('response',lambda response:bad.append(response.status) if response.status>=400 else None)
 for path in ['index.html','results.html?year=2021&make=BMW&model=M4&part=Spindle','cart.html','cart.html?view=saved','about.html','quote.html']:
  page.goto(base+path)
  expect(page.locator('#help-launcher')).to_be_visible()
  expect(page.locator('#site-help')).to_be_hidden()
  page.locator('#help-launcher').click()
  expect(page.locator('#site-help')).to_be_visible()
  assert 'built-in' in page.locator('.help-mode').inner_text()
  page.get_by_role('button',name='Close help',exact=True).click()
  expect(page.locator('#site-help')).to_be_hidden()
 page.goto(base)
 page.locator('#help-launcher').click()
 page.locator('.help-topics').get_by_role('button',name='Compare your options',exact=True).click()
 assert 'two or three' in page.locator('.help-answer').inner_text()
 page.screenshot(path='/tmp/apf-help-desktop.png',animations='disabled')
 page.keyboard.press('Escape')
 expect(page.locator('#site-help')).to_be_hidden()
 assert page.locator('#help-launcher').evaluate('(n)=>n===document.activeElement')
 page.locator('#help-launcher').click()
 page.locator('#help-question').fill('How do I remove a part from my cart?')
 page.locator('.help-form button').click()
 assert 'Remove' in page.locator('.help-answer').inner_text()
 assert 'remove a part' not in page.evaluate('JSON.stringify([localStorage,sessionStorage])')
 page.get_by_role('button',name='← All topics',exact=True).click()
 page.locator('.help-topics').get_by_role('button',name='Find a part',exact=True).click()
 page.get_by_role('button',name='Open search',exact=True).click()
 expect(page.locator('#site-help')).to_be_hidden()
 expect(page.locator('.command-search')).to_be_visible()
 page.keyboard.press('Escape')
 page.locator('#help-launcher').click();page.locator('h1').click()
 expect(page.locator('#site-help')).to_be_hidden()
 for width in [320,375,768]:
  page.set_viewport_size({'width':width,'height':700})
  page.locator('#help-launcher').click()
  page.locator('.help-topics').get_by_role('button',name='Photos & quick view',exact=True).click()
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  box=page.locator('#site-help').bounding_box();assert box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width
  if width==375:page.screenshot(path='/tmp/apf-help-mobile.png',animations='disabled')
  page.get_by_role('button',name='Close help',exact=True).click()
 page.set_viewport_size({'width':375,'height':812})
 page.goto(base+'results.html?year=2021&make=BMW&model=M4&part=Spindle');page.wait_for_selector('.listing')
 page.get_by_role('checkbox',name='Compare DEMO-M4-001',exact=True).check()
 page.get_by_role('checkbox',name='Compare DEMO-M4-002',exact=True).check()
 button=page.locator('#help-launcher').bounding_box();tray=page.locator('.compare-tray').bounding_box()
 assert button['y']+button['height']<=tray['y']
 assert not errors,errors;assert not bad,bad
 # Simulate an enabled server, while keeping live inventory off.
 api='https://help.example.invalid'
 page.route('**/scripts/config.js',lambda route:route.fulfill(content_type='text/javascript',body=f'export const API_BASE_URL=""; export const HELP_API_BASE_URL={json.dumps(api)};'))
 page.route(api+'/api/help/status',lambda route:route.fulfill(json={'enabled':True},headers={'Access-Control-Allow-Origin':'*'}))
 calls=[]
 def answer(route):
  if route.request.method=='OPTIONS':
   route.fulfill(status=204,headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Methods':'POST'});return
  calls.append(route.request.post_data_json)
  route.fulfill(json={'answer':'Use Compare. <img src=x onerror=alert(1)>'},headers={'Access-Control-Allow-Origin':'*'})
 page.route(api+'/api/help',answer)
 page.goto(base);page.locator('#help-launcher').click()
 expect(page.locator('.help-mode')).to_contain_text('AI site help')
 page.locator('#help-question').fill('How to compare?');page.locator('.help-form button').click()
 expect(page.locator('.help-source')).to_have_text('AI answer')
 assert page.locator('.help-answer img').count()==0
 assert '<img' in page.locator('.help-answer').inner_text()
 assert calls==[{'message':'How to compare?','page':'home','mode':'demo'}]
 assert not errors,errors
 page.unroute(api+'/api/help',answer)
 page.route(api+'/api/help',lambda route:route.fulfill(json={'unexpected':'no answer'},headers={'Access-Control-Allow-Origin':'*'}))
 page.locator('#help-question').fill('Where is my cart?');page.locator('.help-form button').click()
 expect(page.locator('.help-fallback')).to_contain_text('unavailable')
 expect(page.locator('.help-source')).to_have_text('Quick guide')
 page.get_by_role('button',name='Close help',exact=True).click()
 expect(page.locator('#site-help')).to_be_hidden()
 # Closing remains responsive even when the AI response never arrives.
 page.locator('#help-launcher').click()
 page.evaluate('''() => {
   const original = window.fetch;
   window.fetch = (url, options = {}) => String(url).endsWith('/api/help')
     ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
         window.helpRequestAborted = true; reject(new DOMException('Aborted', 'AbortError'));
       })) : original(url, options);
 }''')
 page.locator('#help-question').fill('How can I search?');page.locator('.help-form button').click()
 expect(page.locator('.help-form button')).to_be_disabled()
 page.get_by_role('button',name='Close help',exact=True).click()
 page.wait_for_function('window.helpRequestAborted === true')
 expect(page.locator('#site-help')).to_be_hidden()
 page.locator('#help-launcher').click()
 expect(page.locator('.help-form button')).to_be_enabled()
 page.locator('.help-topics').get_by_role('button',name='Find a part',exact=True).click()
 expect(page.locator('.help-answer')).to_contain_text('Browse By Vehicle')
 assert not errors,errors
 browser.close()
 print('PASS: help on every page, no auto-open, guides, local answers, clear AI labeling, Escape/close/outside click, search action, responsive panel, comparison tray separation, no chat storage, AI request allowlist, text-only rendering and unavailable-AI fallback.')
