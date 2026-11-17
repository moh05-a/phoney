// REST client and token storage. The panel is served by the agent, so every URL is relative.

const TOKEN_KEY = 'pbx.token';
const BASE = '/api/v1';

export const store = {
  get(key, fallback = null) {
    try { const raw = localStorage.getItem(key); return raw == null ? fallback : JSON.parse(raw); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: keep working without persistence */ }
  },
  remove(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

export const session = {
  get token() { return store.get(TOKEN_KEY); },
  set token(value) { value ? store.set(TOKEN_KEY, value) : store.remove(TOKEN_KEY); },
};

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    Object.assign(this, { status, code, details: details || {} });
  }
}

export const authEvents = new EventTarget(); // fires 'unauthorized' when the agent rejects our token

export async function api(method, path, body) {
  const headers = {};
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'network', 'Cannot reach the PC.');
  }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { /* non-JSON error page */ }
  if (response.ok) return data;
  const err = data?.error || {};
  if (response.status === 401 && session.token) {
    session.token = null;
    authEvents.dispatchEvent(new Event('unauthorized'));
  }
  throw new ApiError(response.status, err.code || 'error', err.message || `Request failed (${response.status}).`, err.details);
}

/** Fetches a binary resource (cover art) with the device token. Returns a Blob, or null if there is none. */
export async function getBlob(path) {
  try {
    const response = await fetch(BASE + path, { headers: session.token ? { Authorization: `Bearer ${session.token}` } : {} });
    return response.ok ? await response.blob() : null;
  } catch { return null; }
}

export const get = (path) => api('GET', path);
export const post = (path, body = {}) => api('POST', path, body);
export const patch = (path, body) => api('PATCH', path, body);
export const put = (path, body) => api('PUT', path, body);
export const del = (path) => api('DELETE', path);

/** Runs an allowlisted action. Dangerous ones come back as 409; `confirm` asks the user, then we retry. */
export async function runAction(actionId, confirm, params) {
  const path = `/actions/${encodeURIComponent(actionId)}/run`;
  const body = params ? { params } : {};
  try {
    return await post(path, body);
  } catch (error) {
    if (error.code !== 'confirmation_required') throw error;
    if (!(await confirm(error.message))) return null;
    return post(path, { ...body, confirm_token: error.details.confirm_token });
  }
}
