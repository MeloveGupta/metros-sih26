import React from "react";
import { docxUrl } from "./api.js";

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

export default function ReportView({ report }) {
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
      <div className="tblwrap">
        <table className="tbl">
          <thead><tr><th>Declaration</th><th>Clause</th><th>Extracted</th><th>Status</th></tr></thead>
          <tbody>
            {report.declarations.map((d) => (
              <tr key={d.id}>
                <td>{d.label}</td>
                <td className="mono nowrap">{d.clause_ref.clause}</td>
                <td className="muted">{d.extracted || "-"}</td>
                <td><Pill status={d.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

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

      {s.required_actions.length > 0 && (
        <>
          <h3 className="sec"><span className="sec-no mono">03</span> Officer actions</h3>
          <ul className="actions">{s.required_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </>
      )}

      <a className="dl" href={docxUrl(report.report_id)}>Download editable report (DOCX)</a>
    </section>
  );
}
