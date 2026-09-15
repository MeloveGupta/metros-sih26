import React, { useState } from "react";
import { logout, scan } from "./api.js";
import Login from "./Login.jsx";
import ReportView from "./ReportView.jsx";

function PanelDimensions({ shape, setShape, dims, setDims }) {
  return (
    <div className="panel-dims">
      <label className="field">
        <span>Principal display panel shape</span>
        <select value={shape} onChange={(e) => setShape(e.target.value)}>
          <option value="">not specified</option>
          <option value="rectangular">Rectangular</option>
          <option value="cylindrical">Cylindrical</option>
          <option value="other">Other</option>
        </select>
      </label>
      {shape === "rectangular" && (
        <div className="grid2">
          <label className="field">
            <span>Height (cm)</span>
            <input type="number" step="0.1" value={dims.heightCm}
              onChange={(e) => setDims((d) => ({ ...d, heightCm: e.target.value }))} />
          </label>
          <label className="field">
            <span>Width (cm)</span>
            <input type="number" step="0.1" value={dims.widthCm}
              onChange={(e) => setDims((d) => ({ ...d, widthCm: e.target.value }))} />
          </label>
        </div>
      )}
      {shape === "cylindrical" && (
        <div className="grid2">
          <label className="field">
            <span>Height (cm)</span>
            <input type="number" step="0.1" value={dims.heightCm}
              onChange={(e) => setDims((d) => ({ ...d, heightCm: e.target.value }))} />
          </label>
          <label className="field">
            <span>Circumference (cm)</span>
            <input type="number" step="0.1" value={dims.circumferenceCm}
              onChange={(e) => setDims((d) => ({ ...d, circumferenceCm: e.target.value }))} />
          </label>
        </div>
      )}
      {shape === "other" && (
        <label className="field">
          <span>Panel area (cm²)</span>
          <input type="number" step="0.1" value={dims.areaCm2Other}
            onChange={(e) => setDims((d) => ({ ...d, areaCm2Other: e.target.value }))} />
        </label>
      )}
    </div>
  );
}

function ScanForm({ onReport }) {
  const [shots, setShots] = useState([]); // [{file,url}]
  const [commonName, setCommonName] = useState("");
  const [category, setCategory] = useState("unknown");
  const [panelShape, setPanelShape] = useState("");
  const [panelDims, setPanelDims] = useState({
    heightCm: "", widthCm: "", circumferenceCm: "", areaCm2Other: "",
  });
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
      onReport(await scan({
        files: shots.map((s) => s.file), commonName, category,
        panel: { shape: panelShape, ...panelDims },
      }));
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-head">
        <h2>Scan a packaged product</h2>
        <p className="lede">
          Add the front and back of the pack, plus any close-ups of the label. More
          photos means the reader finds more declarations. Include the printed Metros
          card in a shot to also measure letter height (Rule 7) — lay the card flat
          on the same face as the label, touching the text you want measured.
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

      <label className="field">
        <span>Generic name of the product</span>
        <input value={commonName} placeholder="e.g. tomato ketchup"
          onChange={(e) => setCommonName(e.target.value)} />
      </label>

      <label className="field">
        <span>Product category</span>
        <select required value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="unknown">Unknown / not sure</option>
          <option value="food">Food</option>
          <option value="cosmetic">Cosmetic</option>
          <option value="other_non_food">Other (non-food)</option>
        </select>
      </label>

      <PanelDimensions shape={panelShape} setShape={setPanelShape}
        dims={panelDims} setDims={setPanelDims} />

      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Analysing…" : "Scan product"}
      </button>
      {err && <p className="err" role="alert">{err}</p>}
    </form>
  );
}

export default function App() {
  const [session, setSession] = useState(null); // { name, role }
  const [report, setReport] = useState(null);

  function signOut() {
    logout();
    setSession(null);
    setReport(null);
  }

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <span className="wordmark">METROS</span>
        </div>
        {session && (
          <div className="session">
            <span className="session-who">{session.name || session.role}</span>
            <button type="button" className="ghost" onClick={signOut}>Log out</button>
          </div>
        )}
      </header>

      <main>
        {!session ? (
          <Login onSignedIn={setSession} />
        ) : (
          <>
            <ScanForm onReport={setReport} />
            {report && <ReportView report={report} onUpdate={setReport} />}
          </>
        )}
      </main>
    </div>
  );
}
