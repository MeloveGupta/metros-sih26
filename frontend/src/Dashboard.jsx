import React, { useEffect, useState } from "react";
import { getStats } from "./api.js";

const DISP_LABELS = {
  compliant: "Compliant",
  potential_non_compliance: "Potential NC",
  needs_officer_review: "Needs review",
};

function BarChart({ title, bars, valueFmt = (v) => v }) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  return (
    <div className="chart">
      <h4 className="chart-title">{title}</h4>
      {bars.length === 0 && <p className="muted small">No data yet.</p>}
      {bars.map((b) => (
        <div className="chart-row" key={b.label}>
          <span className="chart-label mono">{b.label}</span>
          <div className="chart-track">
            <span className="chart-fill" style={{ width: `${(b.value / max) * 100}%` }} />
          </div>
          <span className="chart-val mono">{valueFmt(b.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    getStats()
      .then((s) => !cancelled && setStats(s))
      .catch((e) => !cancelled && setErr(String(e.message || e)));
    return () => { cancelled = true; };
  }, []);

  if (err) return <section className="panel"><p className="err" role="alert">{err}</p></section>;
  if (!stats) return <section className="panel"><p className="muted">Loading dashboard…</p></section>;

  const kpis = [
    ["Total scans", stats.total, ""],
    ["% calibrated", `${stats.pct_calibrated}%`, ""],
    ["% finalized", `${stats.pct_finalized}%`, ""],
  ];

  const dispBars = Object.entries(stats.by_disposition).map(([k, v]) => ({
    label: DISP_LABELS[k] || k.replace(/_/g, " "), value: v,
  }));
  const dailyBars = stats.scans_per_day.map((d) => ({ label: d.date.slice(5), value: d.count }));
  const flaggedBars = stats.most_flagged_declarations.map((d) => ({ label: d.id, value: d.count }));

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Dashboard</h2>
        <p className="lede">Officer KPIs across every recorded scan.</p>
      </div>

      <div className="kpis">
        {kpis.map(([label, n]) => (
          <div className="kpi" key={label}>
            <span className="kpi-n mono">{n}</span>
            <span className="kpi-l">{label}</span>
          </div>
        ))}
      </div>

      <BarChart title="Scans by disposition" bars={dispBars} />
      <BarChart title="Scans per day (last 30 days)" bars={dailyBars} />
      <BarChart title="Most-flagged declarations" bars={flaggedBars} />
    </section>
  );
}
