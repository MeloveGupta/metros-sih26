import React, { useEffect, useState } from "react";
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

// A real phone camera photo is typically 3000-4000px and several MB.
// Shrinking here helps two separate things: upload size/memory pressure
// (the backend already downsizes to 1600px before sending to Gemini
// anyway -- see backend/extract/gemini_reader.py), and -- the bigger cost
// in practice -- ArUco calibration's own CPU time, which scales with image
// area (backend/vision/scale.py runs up to six detection passes per
// image). Capped well below Gemini's own 1600px target so it never
// upsizes what we send. Falls back to the original file untouched on any
// resize failure (an unsupported format, a very old browser) -- never
// worse than before.
const MAX_PHOTO_DIMENSION = 1280;

async function resizeImageFile(file, maxDim = MAX_PHOTO_DIMENSION, quality = 0.85) {
  if (!file.type || !file.type.startsWith("image/")) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
    if (scale >= 1) {
      bitmap.close?.();
      return file; // already small enough
    }
    const w = Math.round(bitmap.width * scale);
    const h = Math.round(bitmap.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close?.();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
    if (!blob) return file;
    const name = file.name.replace(/\.\w+$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch {
    return file;
  }
}

// Persists in-progress photo picks across an unexpected reload -- same idea
// as api.js/App.jsx's session persistence, but for the shots the officer
// has already picked before submitting. Android will often "discard" a
// backgrounded tab (freeing its memory while the native camera app is in
// the foreground) and silently reload it on return: sessionStorage
// survives that, plain React state does not, so without this an officer
// who's already added several photos loses them the moment they take one
// more via the in-page camera button.
const SHOTS_KEY = "metros_pending_shots";

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function dataUrlToFile(dataUrl, name) {
  const blob = await (await fetch(dataUrl)).blob();
  return new File([blob], name, { type: blob.type });
}

function loadStoredShotsMeta() {
  try {
    const raw = sessionStorage.getItem(SHOTS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

async function persistShots(shots) {
  try {
    const encoded = await Promise.all(
      shots.map(async (s) => ({ name: s.file.name, dataUrl: await fileToDataUrl(s.file) }))
    );
    sessionStorage.setItem(SHOTS_KEY, JSON.stringify(encoded));
  } catch {
    // Quota exceeded or storage unavailable -- degrade to the old
    // behaviour (photos just won't survive an unexpected reload) rather
    // than breaking the scan flow over a persistence nicety.
    try { sessionStorage.removeItem(SHOTS_KEY); } catch { /* ignore */ }
  }
}

function clearStoredShots() {
  try { sessionStorage.removeItem(SHOTS_KEY); } catch { /* ignore */ }
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

  // Restore any photos left over from a reload like the one described above.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const meta = loadStoredShotsMeta();
      if (!meta.length) return;
      const restored = [];
      for (const m of meta) {
        try {
          const file = await dataUrlToFile(m.dataUrl, m.name);
          restored.push({ file, url: URL.createObjectURL(file) });
        } catch {
          // one corrupted entry shouldn't lose the rest
        }
      }
      if (!cancelled && restored.length) setShots(restored);
    })();
    return () => { cancelled = true; };
  }, []);

  async function addFiles(fileList) {
    const arr = Array.from(fileList || []).filter(Boolean);
    if (!arr.length) return;
    setErr("");
    const resized = await Promise.all(arr.map((f) => resizeImageFile(f)));
    setShots((prev) => {
      const next = [...prev, ...resized.map((f) => ({ file: f, url: URL.createObjectURL(f) }))];
      persistShots(next);
      return next;
    });
  }

  function removeShot(i) {
    setShots((prev) => {
      const next = [...prev];
      const [gone] = next.splice(i, 1);
      if (gone) URL.revokeObjectURL(gone.url);
      persistShots(next);
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
      const result = await scan({
        files: shots.map((s) => s.file), productName, category, source, labelText,
      });
      clearStoredShots();
      onReport(result);
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

      {/* Gallery-only on purpose: the in-page camera capture (input capture=
          "environment") backgrounds the browser to hand off to the native
          camera app, and on a low-RAM phone Android will often discard
          (unload) the backgrounded tab to free memory and silently reload
          it on return -- losing whatever wasn't picked yet. Taking photos
          with the phone's own camera app first, then picking them here,
          avoids that handoff entirely. */}
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
        <label htmlFor="galimg" className="slot-empty">
          <span className="slot-plus">+</span>
          <span className="slot-label">{shots.length ? "Add photo" : "Add photos"}</span>
          <span className="slot-hint">{shots.length ? `${shots.length} added` : "from gallery"}</span>
        </label>
      </div>

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
