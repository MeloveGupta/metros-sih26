// Thin API client for the Metros backend.
//
// The JWT lives in sessionStorage: it survives a page reload (e.g. a
// low-memory mobile browser killing and reloading the tab mid-scan, which
// otherwise dumped an officer straight back to Login) but is cleared the
// moment the tab is actually closed -- not localStorage, which would
// outlive the tab/browser session entirely and is a materially bigger
// blast radius if the page were ever compromised via XSS.
//
// API_BASE is empty by default (same-origin: the Vite dev proxy locally,
// or nginx/FastAPI serving both from one origin in Docker) -- set
// VITE_API_URL when the frontend and backend are on different origins
// (e.g. a Vercel frontend calling a Render/Railway backend).
const API_BASE = import.meta.env.VITE_API_URL || "";

function _url(path) {
  return `${API_BASE}${path}`;
}

const TOKEN_KEY = "metros_token";

export function setAuthToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // sessionStorage unavailable (private browsing, blocked site data) --
    // requests within this page load still work, just won't survive a reload.
  }
}

export function getAuthToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function _authHeaders() {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function _asJson(res, failMessage) {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${failMessage} (${res.status})`);
  }
  return res.json();
}

export async function login(email, password) {
  const res = await fetch(_url("/auth/token"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await _asJson(res, "Sign in failed");
  setAuthToken(body.access_token);
  return body; // { access_token, role, name }
}

export function logout() {
  setAuthToken(null);
}

export async function scan({ files, productName, category, source, labelText }) {
  const form = new FormData();
  for (const f of files) form.append("images", f);
  if (productName) form.append("product_name", productName);
  if (category) form.append("category", category);
  if (source) form.append("source", source);
  if (labelText) form.append("label_text", labelText);

  const res = await fetch(_url("/scan"), { method: "POST", body: form, headers: _authHeaders() });
  return _asJson(res, "Scan failed");
}

export async function getReviewItems(reportId) {
  const res = await fetch(_url(`/scans/${reportId}/review-items`), { headers: _authHeaders() });
  const body = await _asJson(res, "Could not load review items");
  return body.items;
}

export async function listScans(filters = {}) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== "" && v != null) params.set(k, v);
  }
  const qs = params.toString();
  const res = await fetch(_url(`/scans${qs ? `?${qs}` : ""}`), { headers: _authHeaders() });
  return _asJson(res, "Could not load scans");
}

export async function getScan(scanId) {
  const res = await fetch(_url(`/scans/${scanId}`), { headers: _authHeaders() });
  return _asJson(res, "Could not load scan");
}

export async function getStats() {
  const res = await fetch(_url("/stats"), { headers: _authHeaders() });
  return _asJson(res, "Could not load stats");
}

async function _downloadFile(path, filename) {
  const res = await fetch(_url(path), { headers: _authHeaders() });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Download failed (${res.status})`);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function fetchImageBlobUrl(path) {
  const res = await fetch(_url(path), { headers: _authHeaders() });
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function downloadPdf(reportId) {
  return _downloadFile(`/scans/${reportId}/report.pdf`, `metros-${reportId}.pdf`);
}

export function downloadDocx(reportId) {
  return _downloadFile(`/scans/${reportId}/report.docx`, `metros-${reportId}.docx`);
}

export async function finalize(reportId, { officerName, actions }) {
  const res = await fetch(_url(`/scans/${reportId}/finalize`), {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ officer_name: officerName, actions }),
  });
  return _asJson(res, "Finalize failed");
}
