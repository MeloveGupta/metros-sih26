import React, { useState } from "react";
import { scan } from "./api.js";
import ReportView from "./ReportView.jsx";

function ScanForm({ onReport }) {
  const [shots, setShots] = useState([]); // [{file,url}]
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
    if (shots.length < 2)
      return setErr("Add at least two photos - front and back of the pack.");
    setBusy(true);
    setErr("");
    try {
      onReport(await scan({ files: shots.map((s) => s.file) }));
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
          Add the front and back of the pack, plus any close-ups of the label. More
          photos means the reader finds more declarations. Include the printed Metros
          card in a shot to also measure letter height (Rule 7).
        </p>
      </div>

      {/* Camera input: single + capture=environment => opens the REAR camera.
          (A `multiple` input makes browsers ignore `capture`, defaulting to the
          front camera / chooser - so the camera path stays single-shot.) */}
      <input id="camimg" type="file" accept="image/*" capture="environment"
        hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
      {/* Gallery input: multiple, no capture => bulk-pick from photos. */}
      <input id="galimg" type="file" accept="image/*" multiple
        hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />

      <div className={`slots${shots.length ? "" : " slots-empty"}`}>
        {shots.map((s, i) => (
          <figure className="slot-fill" key={s.url}>
            <img src={s.url} alt={`Photo ${i + 1}`} />
            <button type="button" className="shot-x" onClick={() => removeShot(i)}
              aria-label={`Remove photo ${i + 1}`}>×</button>
          </figure>
        ))}
        <label htmlFor="camimg" className="slot-empty">
          <span className="slot-plus">+</span>
          <span className="slot-label">{shots.length ? "Take photo" : "Take photos"}</span>
          <span className="slot-hint">{shots.length ? `${shots.length} added` : "rear camera"}</span>
        </label>
      </div>
      <label htmlFor="galimg" className="gallery-link">or choose from gallery</label>

      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Analysing…" : "Scan product"}
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
        </div>
      </header>

      <main>
        <ScanForm onReport={setReport} />
        {report && <ReportView report={report} />}
      </main>
    </div>
  );
}
