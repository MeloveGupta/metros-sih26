// Thin API client for the MetroScan backend.

const TOKEN_KEY = "metroscan_token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function setToken(t) {
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* storage unavailable; token stays in memory only */
  }
}

export function logout() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export async function login(email, password) {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch("/auth/token", { method: "POST", body });
  if (!res.ok) throw new Error("login failed");
  const data = await res.json();
  setToken(data.access_token);
  return data;
}

export async function scan({ file, labelText, markerMm, productName }) {
  const form = new FormData();
  form.append("image", file);
  if (labelText) form.append("label_text", labelText);
  if (markerMm) form.append("marker_mm", markerMm);
  if (productName) form.append("product_name", productName);

  const res = await fetch("/scan", {
    method: "POST",
    headers: { Authorization: `Bearer ${getToken()}` },
    body: form,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `scan failed (${res.status})`);
  }
  return res.json();
}

export async function listScans() {
  const res = await fetch("/scans", {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new Error("could not list scans");
  return res.json();
}
