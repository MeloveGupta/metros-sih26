import React, { useMemo, useState } from "react";
import { finalize, pdfUrl } from "./api.js";

function Pill({ status }) {
  return <span className={`pill s-${status}`}>{status.replace(/_/g, " ")}</span>;
}

function mm(m) {
  return m ? `${m.value.toFixed(2)} ± ${m.uncertainty.toFixed(2)} ${m.unit}` : "-";
}

// A tolerance gauge: threshold line + measured value with its uncertainty band.
function Gauge({ item }) {
  if (!item.height_mm || item.threshold_mm == null) return null;
  const t = item.threshold_mm;
  const v = item.height_mm.value;
  const u = item.height_mm.uncertainty;
  const full = Math.max(t, v + u) * 1.25 || 1;
  const pct = (x) => `${Math.min(100, Math.max(0, (x / full) * 100))}%`;
  return (
    <div className={`gauge s-${item.status}`}>
      <div className="gauge-track">
        <span className="gauge-band" style={{ left: pct(v - u), width: pct(2 * u) }} />
        <span className="gauge-val" style={{ left: pct(v) }} />
        <span className="gauge-thresh" style={{ left: pct(t) }} title={`min ${t} mm`} />
      </div>
      <div className="gauge-legend mono">
        <span>{v.toFixed(2)} mm</span>
        <span className="muted">min {t.toFixed(1)} mm</span>
      </div>
    </div>
  );
}

// Items needing officer attention: only the flagged (potential non-compliance) ones.
function itemsNeedingReview(report) {
  return report.declarations
    .filter((d) => d.status === "potential_non_compliance")
    .map((d) => ({ id: d.id, label: d.label, status: d.status }));
}

function Verification({ report, onFinalized }) {
  const items = useMemo(() => itemsNeedingReview(report), [report]);
  const [officerName, setOfficerName] = useState("");
  const [state, setState] = useState(() =>
    Object.fromEntries(items.map((i) => [i.id, { verdict: "", note: "" }])));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (report.finalized_by) {
    return (
      <div className="verified-done">
        ✓ Verified &amp; finalized by <b>{report.finalized_by}</b>. The PDF now
        carries your findings.
      </div>
    );
  }
  if (!items.length) return null;

  function set(id, patch) {
    setState((s) => ({ ...s, [id]: { ...s[id], ...patch } }));
    setErr("");
  }

  const allDecided = items.every((i) => state[i.id]?.verdict);
  const needNote = items.some(
    (i) => state[i.id]?.verdict === "confirmed_issue" && !state[i.id]?.note.trim());

  async function submit() {
    if (!allDecided) return setErr("Give a decision on every item.");
    if (needNote) return setErr("A confirmed non-compliance needs a note.");
    setBusy(true);
    setErr("");
    try {
      const actions = items.map((i) => ({
        declaration_id: i.id, label: i.label,
        verdict: state[i.id].verdict, note: state[i.id].note,
      }));
      const updated = await finalize(report.report_id, { officerName, actions });
      onFinalized(updated);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3 className="sec"><span className="sec-no mono">03</span> Officer verification</h3>
      <p className="muted small">Check the physical pack for each flagged item, record your finding, then confirm.</p>
      <div className="verify">
        {items.map((i) => {
          const v = state[i.id] || {};
          return (
            <div className="vrow" key={i.id}>
              <div className="vrow-top">
                <span className="vrow-label">{i.label}</span>
                <span className={`pill s-${i.status}`}>{i.status.replace(/_/g, " ")}</span>
              </div>
              <div className="vbtns">
                <button type="button"
                  className={`vbtn ${v.verdict === "verified_compliant" ? "on-ok" : ""}`}
                  onClick={() => set(i.id, { verdict: "verified_compliant" })}>
                  Verified compliant
                </button>
                <button type="button"
                  className={`vbtn ${v.verdict === "confirmed_issue" ? "on-bad" : ""}`}
                  onClick={() => set(i.id, { verdict: "confirmed_issue" })}>
                  Issue confirmed
                </button>
              </div>
              <input className="vnote" value={v.note || ""}
                onChange={(e) => set(i.id, { note: e.target.value })}
                placeholder={v.verdict === "confirmed_issue"
                  ? "note (required) - what is wrong / your finding"
                  : "note (optional) - e.g. the value you read on the pack"} />
            </div>
          );
        })}
        <label className="field">
          <span>Officer name</span>
          <input value={officerName} placeholder="optional"
            onChange={(e) => setOfficerName(e.target.value)} />
        </label>
        <button className="cta" type="button" disabled={busy} onClick={submit}>
          {busy ? "Finalizing…" : "Confirm & finalize report"}
        </button>
        {err && <p className="err" role="alert">{err}</p>}
      </div>
    </>
  );
}

export default function ReportView({ report, onUpdate }) {
  const s = report.summary;
  const cal = report.calibration;
  const fa = report.font_analysis;
  const kpis = [
    ["Checked", s.checked, ""],
    ["Compliant", s.compliant, "s-compliant"],
    ["Potential NC", s.potential_non_compliance, "s-potential_non_compliance"],
    ["Not detected", s.not_detected, "s-not_detected"],
    ["Not assessable", s.not_assessable, "s-not_assessable"],
  ];

  return (
    <section className="panel report">
      <div className="panel-head report-head">
        <div>
          <span className="eyebrow">Report</span>
          <h2 className="mono">{report.ref_no || report.report_id.slice(0, 8)}</h2>
        </div>
        <div className={`disp disp-${report.disposition}`}>{report.disposition.replace(/_/g, " ")}</div>
      </div>

      <p className="notice">
        Decision-support - potential non-compliance flagged for officer verification.
        Not a final legal finding.
      </p>

      <div className="kpis">
        {kpis.map(([label, n, cls]) => (
          <div className="kpi" key={label}>
            <span className={`kpi-n mono ${cls}`}>{n}</span>
            <span className="kpi-l">{label}</span>
          </div>
        ))}
        <div className="kpi">
          <span className="kpi-n mono">{Math.round(s.overall_confidence * 100)}%</span>
          <span className="kpi-l">Confidence</span>
        </div>
      </div>

      <div className="calbar">
        <span className={`dot ${cal.verdict === "calibrated" ? "on" : "off"}`} />
        <b>{cal.verdict.replace(/_/g, " ")}</b>
        {cal.mm_per_pixel && <span className="mono muted">{cal.mm_per_pixel.toFixed(5)} mm/px</span>}
        {cal.reason && <span className="muted">{cal.reason}</span>}
      </div>

      <h3 className="sec"><span className="sec-no mono">01</span> Declarations · Rule 6</h3>
      <ul className="declist">
        {report.declarations.map((d) => (
          <li className="decl" key={d.id}>
            <div className="decl-main">
              <span className="decl-label">{d.label}</span>
              <span className="decl-clause mono">{d.clause_ref.clause}</span>
            </div>
            <div className="decl-value">
              {d.extracted
                ? <span className="decl-text">{d.extracted}</span>
                : <span className="decl-text muted">not found on pack</span>}
            </div>
            <Pill status={d.status} />
          </li>
        ))}
      </ul>

      <h3 className="sec"><span className="sec-no mono">02</span> Letter height · Rule 7</h3>
      {fa.panel_area_cm2 && fa.table_i_band && (
        <p className="bandline">
          Panel area <b className="mono">{mm(fa.panel_area_cm2)}</b> → band{" "}
          <code className="mono">{fa.table_i_band.area_band}</code>, minimum{" "}
          <b className="mono">{fa.table_i_band.min_height_mm} mm</b>
        </p>
      )}
      {fa.items.length ? (
        <div className="fontlist">
          {fa.items.map((i) => (
            <div className="fontrow" key={i.declaration_id}>
              <div className="fontrow-top">
                <span className="fontrow-id">{i.declaration_id}</span>
                <Pill status={i.status} />
              </div>
              <Gauge item={i} />
              {i.reason && <p className="muted small">{i.reason}</p>}
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">No letter-height measurements (no glyph boxes, or uncalibrated).</p>
      )}

      <Verification report={report} onFinalized={onUpdate} />

      {/* Download only after the officer finalizes (or when nothing needs review). */}
      {(report.finalized_by || itemsNeedingReview(report).length === 0) && (
        <a className="dl" href={pdfUrl(report.report_id)} target="_blank" rel="noreferrer">
          Download PDF report
        </a>
      )}
    </section>
  );
}
