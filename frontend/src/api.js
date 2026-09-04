// Thin API client for the Metros backend. Prototype: no auth.

export async function scan({ files, productName }) {
  const form = new FormData();
  for (const f of files) form.append("images", f);
  if (productName) form.append("product_name", productName);

  const res = await fetch("/scan", { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Scan failed (${res.status})`);
  }
  return res.json();
}

export async function listScans() {
  const res = await fetch("/scans");
  if (!res.ok) throw new Error("Could not load scans");
  return res.json();
}

export function pdfUrl(reportId) {
  return `/scans/${reportId}/report.pdf`;
}

export async function finalize(reportId, { officerName, actions }) {
  const res = await fetch(`/scans/${reportId}/finalize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ officer_name: officerName, actions }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Finalize failed (${res.status})`);
  }
  return res.json();
}
