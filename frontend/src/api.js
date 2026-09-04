// Thin API client for the Metros backend. Prototype: no auth.

export async function scan({ file, markerMm, productName }) {
  const form = new FormData();
  form.append("image", file);
  if (markerMm) form.append("marker_mm", markerMm);
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

export function docxUrl(reportId) {
  return `/scans/${reportId}/report.docx`;
}
