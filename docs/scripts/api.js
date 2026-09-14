import { API_BASE_URL } from './config.js';
export const liveMode = Boolean(API_BASE_URL);
export function apiURL(path) {
  const base = new URL(API_BASE_URL);
  if (base.username || base.password || base.search || base.hash || (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(base.hostname)))) throw new Error('The inventory connection is not configured correctly.');
  return new URL(path, base.origin).href;
}
export async function apiRequest(path, body) {
  let response;
  try {
    response = await fetch(apiURL(path), {
      method: body === undefined ? 'GET' : 'POST', mode: 'cors', credentials: 'omit',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(20000),
    });
  } catch {
    throw new Error('Connection interrupted. Check your connection and try again.');
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The service returned an unexpected response. Please try again.'); }
  if (!response.ok) {
    const error = new Error(data.message || 'The service is unavailable. Please try again.');
    error.status = response.status;
    throw error;
  }
  return data;
}

const searchStorageKey = 'apf:search:last';
function rememberSearch(value) {
  try {
    if (value) sessionStorage.setItem(searchStorageKey, JSON.stringify(value));
    else sessionStorage.removeItem(searchStorageKey);
  } catch { /* Search remains usable when browser storage is unavailable. */ }
}
export function forgetSearch() { rememberSearch(null); }
function previousSearch(key) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(searchStorageKey));
    if (saved?.key === key && /^[a-f0-9]{32}$/.test(saved.id) && Number.isFinite(saved.created)) return saved;
  } catch { /* Ignore invalid browser state. */ }
  return null;
}
export async function liveSearch(params, onProgress) {
  const key = JSON.stringify([apiURL('/'), ...['year', 'make', 'model', 'part', 'interchange'].map(k => params[k] || '')]);
  let search = previousSearch(key);
  if (!search) {
    const started = await apiRequest('/api/search', params);
    if (!/^[a-f0-9]{32}$/.test(started.search_id)) throw new Error('The service could not start this search. Please try again.');
    search = { key, id: started.search_id, created: Date.now() };
    rememberSearch(search);
  }
  // Resume the server job on refresh or return navigation, rather than starting another supplier search.
  while (true) {
    let result;
    try { result = await apiRequest(`/api/search/${search.id}`); }
    catch (error) {
      if (error.status === 404) rememberSearch(null);
      throw error;
    }
    if (result.status === 'failed') { rememberSearch(null); throw new Error(result.message); }
    if (result.status !== 'pending') return result;
    if (Date.now() > search.created + 270000) {
      rememberSearch(null);
      throw new Error('Search took too long. Please try again.');
    }
    onProgress?.('Searching supplier inventory and loading photos…');
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}
