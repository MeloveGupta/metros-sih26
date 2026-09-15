import React, { useState } from "react";
import { login } from "./api.js";

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const session = await login(email, password);
      onSignedIn(session);
    } catch (e2) {
      setErr(String(e2.message || e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-head">
        <h2>Sign in</h2>
        <p className="lede">Officer, auditor, or admin credentials.</p>
      </div>
      <label className="field">
        <span>Email</span>
        <input type="email" required autoComplete="username" value={email}
          onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label className="field">
        <span>Password</span>
        <input type="password" required autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} />
      </label>
      <button className="cta" type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      {err && <p className="err" role="alert">{err}</p>}
    </form>
  );
}
