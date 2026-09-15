// Thin API client for the Metros backend. Prototype: no auth.

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
