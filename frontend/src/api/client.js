/**
 * One HTTP client for the whole app.
 *
 * Responsibilities:
 *   - attach the access token to every request
 *   - refresh it once, transparently, when the server says it has expired
 *   - turn FastAPI's error shapes into a plain readable `Error.message`
 */

/**
 * Where the API lives, read at build time from VITE_API_URL.
 *
 * It is set by hand at deployment time, so it may arrive as a bare
 * `trace-api.onrender.com`. A bare host is treated as https (and localhost as
 * http) rather than producing a relative path and a stream of confusing 404s.
 *
 * It must be the API's PUBLIC hostname. An internal Render hostname — the bare
 * service name with no domain — fails here with ERR_NAME_NOT_RESOLVED, because
 * this code runs in the visitor's browser, not inside Render's network.
 */
function resolveApiBase(raw) {
  const value = (raw || 'http://localhost:8000').trim().replace(/\/$/, '');
  if (!value || value.includes('://')) return value;
  const local = value.startsWith('localhost') || value.startsWith('127.0.0.1');
  return `${local ? 'http' : 'https'}://${value}`;
}

const API_BASE = resolveApiBase(import.meta.env.VITE_API_URL);

const STORAGE = {
  access: 'trace.access_token',
  refresh: 'trace.refresh_token',
  user: 'trace.user',
  device: 'trace.device_id',
};

/* ── Token storage ───────────────────────────────────────────────────────── */
export const tokens = {
  get access() {
    return localStorage.getItem(STORAGE.access);
  },
  get refresh() {
    return localStorage.getItem(STORAGE.refresh);
  },
  save(access, refresh) {
    if (access) localStorage.setItem(STORAGE.access, access);
    if (refresh) localStorage.setItem(STORAGE.refresh, refresh);
  },
  clear() {
    localStorage.removeItem(STORAGE.access);
    localStorage.removeItem(STORAGE.refresh);
    localStorage.removeItem(STORAGE.user);
  },
};

export const storedUser = {
  get() {
    try {
      const raw = localStorage.getItem(STORAGE.user);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  },
  set(user) {
    if (user) localStorage.setItem(STORAGE.user, JSON.stringify(user));
    else localStorage.removeItem(STORAGE.user);
  },
};

/**
 * A stable identifier for this browser. The server trusts a device for 90 days
 * after a successful 2FA challenge, so this is what keeps people from being
 * asked for a code on every single sign-in.
 */
export function deviceId() {
  let id = localStorage.getItem(STORAGE.device);
  if (!id) {
    id =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `dev-${Math.random().toString(36).slice(2)}-${Date.now()}`;
    localStorage.setItem(STORAGE.device, id);
  }
  return id;
}

/* ── Error handling ──────────────────────────────────────────────────────── */
export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function readDetail(payload, fallback) {
  if (!payload) return fallback;
  const { detail } = payload;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        const field = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : '';
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .join(' · ');
  }
  if (typeof payload.message === 'string') return payload.message;
  return fallback;
}

/* ── Core request ────────────────────────────────────────────────────────── */
let refreshing = null;

async function refreshAccessToken() {
  if (!tokens.refresh) return false;
  // Collapse parallel 401s into a single refresh call.
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/auth/refresh`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${tokens.refresh}` },
        });
        if (!res.ok) return false;
        const data = await res.json();
        tokens.save(data.access_token, data.refresh_token);
        if (data.user) storedUser.set(data.user);
        return true;
      } catch {
        return false;
      } finally {
        // Let the next 401 start a fresh attempt.
        setTimeout(() => {
          refreshing = null;
        }, 0);
      }
    })();
  }
  return refreshing;
}

async function request(path, { method = 'GET', body, params, auth = true, raw = false } = {}) {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value);
      }
    });
  }

  const send = async () => {
    const headers = {};
    if (auth && tokens.access) headers.Authorization = `Bearer ${tokens.access}`;

    let payload = body;
    if (body !== undefined && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }

    return fetch(url.toString(), { method, headers, body: payload });
  };

  let response;
  try {
    response = await send();
  } catch {
    throw new ApiError(
      'Cannot reach the server. Check that the API is running and try again.',
      0,
      null,
    );
  }

  // One transparent retry after refreshing an expired access token.
  if (response.status === 401 && auth && tokens.refresh && !path.startsWith('/api/auth/')) {
    const ok = await refreshAccessToken();
    if (ok) {
      response = await send();
    } else {
      tokens.clear();
      window.dispatchEvent(new CustomEvent('trace:session-expired'));
    }
  }

  if (response.status === 204) return null;

  let data = null;
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) {
    data = await response.json().catch(() => null);
  } else if (!raw) {
    data = await response.text().catch(() => null);
  }

  if (!response.ok) {
    throw new ApiError(
      readDetail(data, `Request failed (${response.status}).`),
      response.status,
      data,
    );
  }
  return data;
}

export const api = {
  // `options` carries { auth: false } for the two public calls — the school
  // directory and school registration, both of which happen before anyone has
  // an account to authenticate with.
  get: (path, params, options) => request(path, { params, ...options }),
  post: (path, body, options) => request(path, { method: 'POST', body, ...options }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
  base: API_BASE,
};

/** Turn a server-relative upload path into an absolute URL. */
export function assetUrl(path) {
  if (!path) return null;
  return path.startsWith('http') ? path : `${API_BASE}${path}`;
}
