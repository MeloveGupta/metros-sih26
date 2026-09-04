import React, { useState } from "react";
import { scan } from "./api.js";
import ReportView from "./ReportView.jsx";
import CameraCapture from "./CameraCapture.jsx";

function ScanForm({ onReport }) {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [markerMm, setMarkerMm] = useState("40");
  const [productName, setProductName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function pick(f) {
    setFile(f);
    setErr("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(f ? URL.createObjectURL(f) : "");
  }

  async function submit(e) {
    e.preventDefault();
    if (!file) return setErr("Capture or choose a product photo first.");
    setBusy(true);
    setErr("");
    try {
      onReport(await scan({ file, markerMm, productName }));
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-head">
        <span className="eyebrow">Capture</span>
        <h2>Scan a packaged product</h2>
        <p className="lede">
          Keep the printed ArUco card flat, in the same plane as the label. It sets
          the millimetre scale - no card in frame, no letter-height verdict.
        </p>
      </div>

      <CameraCapture onCapture={pick} />
      <label className="field file">
        <span>…or choose / take a photo</span>
        <input type="file" accept="image/*" capture="environment"
          onChange={(e) => pick(e.target.files[0] || null)} />
      </label>

      {previewUrl && (
        <figure className="preview">
          <img src={previewUrl} alt="Captured product" />
          <figcaption>{file?.name}</figcaption>
        </figure>
      )}

      <div className="grid2">
        <label className="field">
          <span>Marker side (mm)</span>
          <input className="mono" value={markerMm}
            onChange={(e) => setMarkerMm(e.target.value)} inputMode="decimal" />
        </label>
        <label className="field">
          <span>Product name</span>
          <input value={productName} placeholder="optional"
            onChange={(e) => setProductName(e.target.value)} />
        </label>
      </div>

      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Measuring…" : "Scan & measure"}
      </button>
      {err && <p className="err" role="alert">{err}</p>}
    </form>
  );
}

export default function App() {
  const [report, setReport] = useState(null);

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <span className="wordmark">METROS</span>
          <span className="tick-strip" aria-hidden="true" />
        </div>
        <p className="tagline">
          Millimetre-grade compliance for packaged commodities · Legal Metrology
          (Packaged Commodities) Rules, 2011
        </p>
      </header>

      <main>
        <ScanForm onReport={setReport} />
        {report && <ReportView report={report} />}
      </main>

      <footer className="foot">
        Decision-support. Metros flags <b>potential</b> non-compliance for officer
        verification - it is not a final legal finding. Physical verification is
        required for enforcement.
      </footer>
    </div>
  );
}
