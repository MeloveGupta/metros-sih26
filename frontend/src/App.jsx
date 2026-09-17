import React, { useState } from "react";
import { logout, scan } from "./api.js";
import Dashboard from "./Dashboard.jsx";
import History from "./History.jsx";
import Login from "./Login.jsx";
import ReportView from "./ReportView.jsx";

// Mirrors api.js's token persistence: keeps { name, role } around across a
// reload (see api.js for why sessionStorage, not localStorage) so a reload
// restores straight into the signed-in view instead of forcing Login again.
const SESSION_KEY = "metros_session";

function loadStoredSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function storeSession(session) {
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // ignore -- same fallback as api.js: just won't survive a reload
  }
}

function ScanForm({ onReport }) {
  const [shots, setShots] = useState([]); // [{file,url}]
  const [source, setSource] = useState("retail_pack"); // retail_pack | ecommerce_listing
  const [labelText, setLabelText] = useState("");
  const [productName, setProductName] = useState("");
  const [category, setCategory] = useState("unknown");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const isListing = source === "ecommerce_listing";

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
    if (isListing) {
      if (shots.length === 0 && !labelText.trim())
        return setErr("Add a screenshot or paste the listing text.");
    } else if (shots.length < 2) {
      return setErr("Add at least two photos - front and back of the pack.");
    }
    setBusy(true);
    setErr("");
    try {
      onReport(await scan({
        files: shots.map((s) => s.file), productName, category, source, labelText,
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
          {isListing
            ? "Add a screenshot of the online listing and/or paste its text. There is " +
              "no letter-height or panel-placement check for a listing (Rule 7/8 need a " +
              "physical pack); month/year of manufacture is not required either (Rule 6(10))."
            : "Add the front and back of the pack, plus any close-ups of the label. More " +
              "photos means the reader finds more declarations. Include the printed Metros " +
              "card in a shot to also measure letter height (Rule 7) — lay the card flat " +
              "on the same face as the label, touching the text you want measured. Without " +
              "the card in frame, letter height still gets measured but can't be checked " +
              "against the right size threshold."}
        </p>
      </div>

      <label className="field">
        <span>Scan type</span>
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="retail_pack">Physical retail pack (photos)</option>
          <option value="ecommerce_listing">E-commerce listing (screenshot / pasted text)</option>
        </select>
      </label>

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

      {isListing && (
        <label className="field">
          <span>Listing text {shots.length ? "(optional)" : ""}</span>
          <textarea rows={5} value={labelText} placeholder="Paste the product listing text here…"
            onChange={(e) => setLabelText(e.target.value)} />
        </label>
      )}

      <label className="field">
        <span>Product name</span>
        <input value={productName} placeholder="e.g. Tasty Masala Chips"
          onChange={(e) => setProductName(e.target.value)} />
      </label>

      <label className="field">
        <span>Product category</span>
        <select required value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="unknown">Not sure</option>
          <option value="food">Food</option>
          <option value="cosmetic">Cosmetic</option>
          <option value="other_non_food">Other</option>
        </select>
      </label>

      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Analysing…" : "Scan product"}
      </button>
      {err && <p className="err" role="alert">{err}</p>}
    </form>
  );
}

export default function App() {
  const [session, setSessionState] = useState(loadStoredSession); // { name, role, ... }
  const [report, setReport] = useState(null);
  const [tab, setTab] = useState("scan"); // scan | history | dashboard

  function setSession(next) {
    storeSession(next);
    setSessionState(next);
  }

  function signOut() {
    logout();
    setSession(null);
    setReport(null);
  }

  function openReportFromHistory(r) {
    setReport(r);
    setTab("scan");
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
            <nav className="tabs">
              <button type="button" className={`tab${tab === "scan" ? " on" : ""}`}
                onClick={() => setTab("scan")}>Scan</button>
              <button type="button" className={`tab${tab === "history" ? " on" : ""}`}
                onClick={() => setTab("history")}>History</button>
              <button type="button" className={`tab${tab === "dashboard" ? " on" : ""}`}
                onClick={() => setTab("dashboard")}>Dashboard</button>
            </nav>

            {tab === "scan" && (
              <>
                <ScanForm onReport={setReport} />
                {report && <ReportView report={report} onUpdate={setReport} />}
              </>
            )}
            {tab === "history" && <History onOpenReport={openReportFromHistory} />}
            {tab === "dashboard" && <Dashboard />}
          </>
        )}
      </main>
    </div>
  );
}
