import React from "react";

function StatusPill({ status }) {
  return <span className={`pill s-${status}`}>{status.replace(/_/g, " ")}</span>;
}

function mm(m) {
  return m ? `${m.value.toFixed(2)} ± ${m.uncertainty.toFixed(2)} ${m.unit}` : "-";
}

export default function ReportView({ report }) {
  const s = report.summary;
  const cal = report.calibration;

  return (
    <div className="card report">
      <h2>Report {report.ref_no || report.report_id.slice(0, 8)}</h2>

      <div className="banner">
        DECISION-SUPPORT - potential non-compliance flagged for officer verification.
        Not a final legal finding.
      </div>

      <div className="kpis">
        <div>Checked <b>{s.checked}</b></div>
        <div className="s-compliant">Compliant <b>{s.compliant}</b></div>
        <div className="s-potential_non_compliance">Potential NC <b>{s.potential_non_compliance}</b></div>
        <div className="s-not_detected">Not detected <b>{s.not_detected}</b></div>
        <div className="s-not_assessable">Not assessable <b>{s.not_assessable}</b></div>
        <div>Confidence <b>{Math.round(s.overall_confidence * 100)}%</b></div>
      </div>

      <h3>Calibration</h3>
      <p>
        <StatusPill status={cal.verdict === "calibrated" ? "compliant" : "not_assessable"} />{" "}
        {cal.verdict}
        {cal.mm_per_pixel ? ` · ${cal.mm_per_pixel.toFixed(5)} mm/px` : ""}
        {cal.reason ? ` · ${cal.reason}` : ""}
      </p>

      <h3>Declarations (Rule 6)</h3>
      <table>
        <thead><tr><th>Declaration</th><th>Clause</th><th>Extracted</th><th>Status</th></tr></thead>
        <tbody>
          {report.declarations.map((d) => (
            <tr key={d.id}>
              <td>{d.label}</td>
              <td>{d.clause_ref.clause}</td>
              <td>{d.extracted || "-"}</td>
              <td><StatusPill status={d.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Font & placement (Rule 7)</h3>
      {report.font_analysis.panel_area_cm2 && (
        <p>
          Panel area {mm(report.font_analysis.panel_area_cm2)} → band{" "}
          <code>{report.font_analysis.table_i_band.area_band}</code>, min{" "}
          {report.font_analysis.table_i_band.min_height_mm} mm
        </p>
      )}
      {report.font_analysis.items.length ? (
        <table>
          <thead><tr><th>Declaration</th><th>Height</th><th>Threshold</th><th>Status</th><th>Reason</th></tr></thead>
          <tbody>
            {report.font_analysis.items.map((i) => (
              <tr key={i.declaration_id}>
                <td>{i.declaration_id}</td>
                <td>{mm(i.height_mm)}</td>
                <td>{i.threshold_mm != null ? `${i.threshold_mm.toFixed(1)} mm` : "-"}</td>
                <td><StatusPill status={i.status} /></td>
                <td className="muted">{i.reason || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No font measurements (no glyph boxes or uncalibrated).</p>
      )}

      {s.required_actions.length > 0 && (
        <>
          <h3>Required officer actions</h3>
          <ul>{s.required_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </>
      )}

      <a className="dl" href={`/scans/${report.report_id}/report.docx`}>Download editable report (DOCX)</a>
    </div>
  );
}
