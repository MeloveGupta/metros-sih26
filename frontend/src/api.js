// Thin API client for the Metros backend.
//
// The JWT is kept in memory only (never localStorage/sessionStorage) -- a
// page reload requires signing in again, which is the point.

let _token = null;

export function setAuthToken(token) {
  _token = token;
}

export function getAuthToken() {
  return _token;
}

function _authHeaders() {
  return _token ? { Authorization: `Bearer ${_token}` } : {};
}

async function _asJson(res, failMessage) {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${failMessage} (${res.status})`);
  }
  return res.json();
}

export async function login(email, password) {
  const res = await fetch("/auth/token", {
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

export async function scan({ files, productName, commonName, category, panel }) {
  const form = new FormData();
  for (const f of files) form.append("images", f);
  if (productName) form.append("product_name", productName);
  if (commonName) form.append("common_name", commonName);
  if (category) form.append("category", category);
  if (panel && panel.shape) {
    form.append("panel_shape", panel.shape);
    if (panel.heightCm) form.append("panel_height_cm", panel.heightCm);
    if (panel.widthCm) form.append("panel_width_cm", panel.widthCm);
    if (panel.circumferenceCm) form.append("panel_circumference_cm", panel.circumferenceCm);
    if (panel.areaCm2Other) form.append("panel_area_cm2_other", panel.areaCm2Other);
  }

  const res = await fetch("/scan", { method: "POST", body: form, headers: _authHeaders() });
  return _asJson(res, "Scan failed");
}

export async function listScans() {
  const res = await fetch("/scans", { headers: _authHeaders() });
  return _asJson(res, "Could not load scans");
}

async function _downloadFile(url, filename) {
  const res = await fetch(url, { headers: _authHeaders() });
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

export function downloadPdf(reportId) {
  return _downloadFile(`/scans/${reportId}/report.pdf`, `metros-${reportId}.pdf`);
}

export function downloadDocx(reportId) {
  return _downloadFile(`/scans/${reportId}/report.docx`, `metros-${reportId}.docx`);
}

export async function finalize(reportId, { officerName, actions }) {
  const res = await fetch(`/scans/${reportId}/finalize`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ officer_name: officerName, actions }),
  });
  return _asJson(res, "Finalize failed");
}
