import React, { useState } from "react";
import { getToken, login, logout, scan } from "./api.js";
import ReportView from "./ReportView.jsx";
import CameraCapture from "./CameraCapture.jsx";

function Login({ onLogin }) {
  const [email, setEmail] = useState("officer@x.gov");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");

  async function submit(e) {
    e.preventDefault();
    setErr("");
    try {
      await login(email, password);
      onLogin();
    } catch {
      setErr("Login failed - check email and password.");
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Officer sign-in</h2>
      <label>Email<input value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <label>Password<input type="password" value={password}
        onChange={(e) => setPassword(e.target.value)} /></label>
      <button type="submit">Sign in</button>
      {err && <p className="err">{err}</p>}
    </form>
  );
}

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
    if (!file) return setErr("Capture or choose a product image first.");
    setBusy(true);
    setErr("");
    try {
      const report = await scan({ file, markerMm, productName });
      onReport(report);
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Scan a packaged product</h2>
      <p className="hint">
        Frame the product with the printed ArUco calibration card in the same plane
        as the label, so letter height can be measured in millimetres.
      </p>

      <CameraCapture onCapture={pick} />
      <label>…or choose / take a photo
        <input type="file" accept="image/*" capture="environment"
          onChange={(e) => pick(e.target.files[0] || null)} /></label>

      {previewUrl && (
        <div className="preview">
          <img src={previewUrl} alt="captured product" />
          <span className="muted">{file?.name}</span>
        </div>
      )}

      <label>Marker size (mm)
        <input value={markerMm} onChange={(e) => setMarkerMm(e.target.value)} /></label>
      <label>Product name (optional)
        <input value={productName} onChange={(e) => setProductName(e.target.value)} /></label>
      <button type="submit" disabled={busy}>{busy ? "Scanning…" : "Scan"}</button>
      {err && <p className="err">{err}</p>}
    </form>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const [report, setReport] = useState(null);

  return (
    <div className="wrap">
      <header>
        <h1>Metros</h1>
        <span className="tag">Legal Metrology (Packaged Commodities) Rules, 2011 - decision-support</span>
        {authed && (
          <button className="linkish" onClick={() => { logout(); setAuthed(false); setReport(null); }}>
            Sign out
          </button>
        )}
      </header>

      {!authed ? (
        <Login onLogin={() => setAuthed(true)} />
      ) : (
        <>
          <ScanForm onReport={setReport} />
          {report && <ReportView report={report} />}
        </>
      )}

      <footer>
        Reports flag <b>potential</b> non-compliance for officer verification - not a
        final legal finding. Physical verification required for enforcement.
      </footer>
    </div>
  );
}
