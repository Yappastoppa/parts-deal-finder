"""Admin initialization regression using isolated API fixtures and intercepted browser requests."""
import sys,json,secrets,os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from playwright.sync_api import sync_playwright
from test_admin_api import AdminAPI
c=AdminAPI();c.setUp()
password=secrets.token_hex(24);os.environ['APF_ADMIN_PASSWORD']=password
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
  page=browser.new_page();visited=[]
  def route(r):
   path=r.request.url.split('http://apf.test',1)[1].split('?',1)[0]
   if path.startswith('/api/'):
    visited.append(path)
    status,data,_=c._auth_request(path,{'HTTP_AUTHORIZATION':r.request.headers.get('authorization','')},method=r.request.method,data=r.request.post_data_json)
    r.fulfill(status=status,content_type='application/json',body=json.dumps(data));return
   if path=='/scripts/config.js':r.fulfill(content_type='text/javascript',body="export const API_BASE_URL='http://apf.test';");return
   file=(ROOT / 'docs')/path.lstrip('/')
   import mimetypes
   r.fulfill(path=str(file),content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
  page.route('http://apf.test/**',route)
  page.goto('http://apf.test/admin.html')
  page.locator('#admin-password').fill(password);page.locator('#login-form button').click()
  page.wait_for_selector('#dashboard-stats .admin-stat')
  page.wait_for_function("document.querySelector('#audit-list').textContent.includes('Nothing here yet')")
  assert not page.locator('#notice').inner_text()
  assert all('/api/admin/'+name in visited for name in ['settings','pricing-rules','cache','orders','audit'])
  print('PASS: admin login, dashboard, settings, pricing, cache, orders and audit initialize without an error.')
  print('API paths visited:',visited)
  browser.close()
finally:c.tearDown();os.environ.pop('APF_ADMIN_PASSWORD',None)
