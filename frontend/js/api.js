'use strict';
/* Thin client for the Flask API: same-origin cookies plus the X-CSRF-Token header on every state-changing call. */
const API = {
  token: null,
  async csrf() {
    const r = await fetch('/api/auth/csrf', { credentials: 'same-origin' });
    if (!r.ok) throw new Error('Unable to verify your session. Refresh the page and try again.');
    const data = await r.json();
    if (!data.csrf_token) throw new Error('The server did not provide a session token.');
    API.token = data.csrf_token;
  },
  async req(method, url, body) {
    if (method !== 'GET' && !API.token) await API.csrf();
    const opts = { method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') opts.headers['X-CSRF-Token'] = API.token;
    if (body instanceof FormData) opts.body = body;
    else if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const r = await fetch(url, opts);
    const isJson = (r.headers.get('content-type') || '').includes('json');
    let data;
    if (isJson) {
      try { data = await r.json(); } catch { data = null; }
    } else if (r.ok) data = await r.blob();
    if (!r.ok) throw Object.assign(new Error(data?.message || data?.error || r.statusText || 'Request failed.'), { status: r.status });
    return data;
  },
  get: (u) => API.req('GET', u),
  post: (u, b) => API.req('POST', u, b),
  put: (u, b) => API.req('PUT', u, b),
};
