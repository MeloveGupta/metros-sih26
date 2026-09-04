import React, { useState } from "react";
import { scan } from "./api.js";
import ReportView from "./ReportView.jsx";

function ImageSlot({ label, hint, shot, onPick, onClear }) {
  const inputId = `slot-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="slot">
      <input id={inputId} type="file" accept="image/*" capture="environment"
        hidden onChange={(e) => onPick(e.target.files[0] || null)} />
      {shot ? (
        <figure className="slot-fill">
          <img src={shot.url} alt={label} />
          <button type="button" className="shot-x" onClick={onClear}
            aria-label={`Remove ${label}`}>×</button>
        </figure>
      ) : (
        <label htmlFor={inputId} className="slot-empty">
          <span className="slot-plus">+</span>
          <span className="slot-label">{label}</span>
          <span className="slot-hint">{hint}</span>
        </label>
      )}
    </div>
  );
}

function ScanForm({ onReport }) {
  const [front, setFront] = useState(null); // {file,url}
  const [back, setBack] = useState(null);
  const [productName, setProductName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function setSlot(setter, prev, f) {
    setErr("");
    if (prev) URL.revokeObjectURL(prev.url);
    setter(f ? { file: f, url: URL.createObjectURL(f) } : null);
  }

  async function submit(e) {
    e.preventDefault();
    if (!front || !back) return setErr("Add both the front and back photos of the pack.");
    setBusy(true);
    setErr("");
    try {
      onReport(await scan({ files: [front.file, back.file], productName }));
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
          Add the front and back of the pack. The front gives the product name and
          country of origin; the back carries the mandatory declarations. Include
          the printed ArUco card in a shot to also measure letter height (Rule 7).
        </p>
      </div>

      <div className="slots">
        <ImageSlot label="Front of pack" hint="name, brand, origin"
          shot={front} onPick={(f) => setSlot(setFront, front, f)}
          onClear={() => setSlot(setFront, front, null)} />
        <ImageSlot label="Back of pack" hint="MRP, qty, mfg, care"
          shot={back} onPick={(f) => setSlot(setBack, back, f)}
          onClear={() => setSlot(setBack, back, null)} />
      </div>

      <label className="field">
        <span>Product name</span>
        <input value={productName} placeholder="optional"
          onChange={(e) => setProductName(e.target.value)} />
      </label>

      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Analysing…" : "Scan front & back"}
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
