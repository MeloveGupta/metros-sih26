import React, { useState } from "react";
import { scan } from "./api.js";
import ReportView from "./ReportView.jsx";
import CameraCapture from "./CameraCapture.jsx";

function ScanForm({ onReport }) {
  const [shots, setShots] = useState([]); // [{file, url}]
  const [productName, setProductName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function addFiles(fileList) {
    const arr = Array.from(fileList || []).filter(Boolean);
    if (!arr.length) return;
    setErr("");
    setShots((prev) => [...prev, ...arr.map((f) => ({ file: f, url: URL.createObjectURL(f) }))]);
  }

  function removeShot(i) {
    setShots((prev) => {
      const next = [...prev];
      const [gone] = next.splice(i, 1);
      if (gone) URL.revokeObjectURL(gone.url);
      return next;
    });
  }

  async function submit(e) {
    e.preventDefault();
    if (!shots.length) return setErr("Add at least one product photo (front and back work best).");
    setBusy(true);
    setErr("");
    try {
      onReport(await scan({ files: shots.map((s) => s.file), productName }));
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
          Upload or shoot the front and back of the pack. More angles let the
          reader find every declaration. Include the printed ArUco card in a shot
          to also measure letter height in millimetres (Rule 7) - optional.
        </p>
      </div>

      <CameraCapture onCapture={(f) => addFiles([f])} />
      <label className="field file">
        <span>…or choose photos (front + back)</span>
        <input type="file" accept="image/*" capture="environment" multiple
          onChange={(e) => addFiles(e.target.files)} />
      </label>

      {shots.length > 0 && (
        <div className="shots">
          {shots.map((s, i) => (
            <figure className="shot" key={s.url}>
              <img src={s.url} alt={`Photo ${i + 1}`} />
              <button type="button" className="shot-x" onClick={() => removeShot(i)}
                aria-label={`Remove photo ${i + 1}`}>×</button>
            </figure>
          ))}
        </div>
      )}

      <label className="field">
        <span>Product name</span>
        <input value={productName} placeholder="optional"
          onChange={(e) => setProductName(e.target.value)} />
      </label>

      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Analysing…" : `Scan ${shots.length || ""} photo${shots.length === 1 ? "" : "s"}`.trim()}
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
