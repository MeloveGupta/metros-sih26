import React, { useEffect, useState } from "react";
import { getScan, listScans } from "./api.js";

const DISPOSITIONS = [
  "", "compliant", "potential_non_compliance", "needs_officer_review",
];

function emptyFilters() {
  return {
    disposition: "", product_name: "", brand: "", category: "",
    finalized: "", has_rule7_flag: "", date_from: "", date_to: "",
  };
}

export default function History({ onOpenReport }) {
  const [filters, setFilters] = useState(emptyFilters());
  const [offset, setOffset] = useState(0);
  const limit = 20;
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [openingId, setOpeningId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setErr("");
    listScans({ ...filters, limit, offset })
      .then((body) => {
        if (cancelled) return;
        setRows(body.results);
        setTotal(body.total);
      })
      .catch((e) => !cancelled && setErr(String(e.message || e)))
      .finally(() => !cancelled && setBusy(false));
    return () => { cancelled = true; };
  }, [filters, offset]);

  function setFilter(patch) {
    setOffset(0);
    setFilters((f) => ({ ...f, ...patch }));
  }

  async function open(id) {
    setOpeningId(id);
    setErr("");
    try {
      onOpenReport(await getScan(id));
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setOpeningId(null);
    }
  }

  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Scan history</h2>
        <p className="lede">Search and reopen past scans.</p>
      </div>

      <div className="grid2">
        <label className="field">
          <span>Product name</span>
          <input value={filters.product_name}
            onChange={(e) => setFilter({ product_name: e.target.value })} placeholder="contains…" />
        </label>
        <label className="field">
          <span>Brand</span>
          <input value={filters.brand}
            onChange={(e) => setFilter({ brand: e.target.value })} placeholder="contains…" />
        </label>
      </div>
      <div className="grid2">
        <label className="field">
          <span>Disposition</span>
          <select value={filters.disposition} onChange={(e) => setFilter({ disposition: e.target.value })}>
            {DISPOSITIONS.map((d) => (
              <option key={d} value={d}>{d ? d.replace(/_/g, " ") : "any"}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Category</span>
          <select value={filters.category} onChange={(e) => setFilter({ category: e.target.value })}>
            <option value="">any</option>
            <option value="food">Food</option>
            <option value="cosmetic">Cosmetic</option>
            <option value="other_non_food">Other (non-food)</option>
            <option value="unknown">Unknown</option>
          </select>
        </label>
      </div>
      <div className="grid2">
        <label className="field">
          <span>Finalized</span>
          <select value={filters.finalized} onChange={(e) => setFilter({ finalized: e.target.value })}>
            <option value="">any</option>
            <option value="true">Finalized</option>
            <option value="false">Not finalized</option>
          </select>
        </label>
        <label className="field">
          <span>Rule 7 flagged</span>
          <select value={filters.has_rule7_flag} onChange={(e) => setFilter({ has_rule7_flag: e.target.value })}>
            <option value="">any</option>
            <option value="true">Flagged</option>
            <option value="false">Not flagged</option>
          </select>
        </label>
      </div>
      <div className="grid2">
        <label className="field">
          <span>From date</span>
          <input type="date" value={filters.date_from}
            onChange={(e) => setFilter({ date_from: e.target.value })} />
        </label>
        <label className="field">
          <span>To date</span>
          <input type="date" value={filters.date_to}
            onChange={(e) => setFilter({ date_to: e.target.value })} />
        </label>
      </div>

      {err && <p className="err" role="alert">{err}</p>}

      <div className="declist" style={{ marginTop: 18 }}>
        {busy && <p className="muted small" style={{ padding: "12px 14px" }}>Loading…</p>}
        {!busy && rows.length === 0 && (
          <p className="muted small" style={{ padding: "12px 14px" }}>No scans match these filters.</p>
        )}
        {rows.map((r) => (
          <button type="button" key={r.id} className="decl histrow"
            onClick={() => open(r.id)} disabled={openingId === r.id}>
            <div className="decl-main">
              <span className="decl-label">{r.product_name || "(unnamed product)"}</span>
              {r.brand && <span className="decl-clause mono">{r.brand}</span>}
            </div>
            <div className="decl-value">
              <span className="decl-text muted">
                {new Date(r.created_at).toLocaleString()}
                {r.finalized ? " · finalized" : ""}
                {r.has_rule7_flag ? " · Rule 7 flagged" : ""}
              </span>
            </div>
            <span className={`pill s-${r.disposition}`}>{r.disposition.replace(/_/g, " ")}</span>
          </button>
        ))}
      </div>

      <div className="pager">
        <button type="button" className="ghost" disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - limit))}>Prev</button>
        <span className="mono muted small">page {page} of {pages} · {total} total</span>
        <button type="button" className="ghost" disabled={offset + limit >= total}
          onClick={() => setOffset(offset + limit)}>Next</button>
      </div>
    </section>
  );
}
