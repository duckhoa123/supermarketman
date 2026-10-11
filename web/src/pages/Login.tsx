import { useState, type FormEvent } from "react";
import { devMode, googleSignIn, signInWithEmail, signInWithGoogle } from "../lib/auth";

export default function Login({ onSignedIn }: { onSignedIn: () => Promise<void> }) {
  const [email, setEmail] = useState(devMode ? "owner@example.com" : "");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const msg = await signInWithEmail(email);
      if (msg) setNotice(msg);
      else await onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card-narrow" onSubmit={submit}>
        <div className="brand" style={{ padding: 0 }}>
          <img src="/icon.svg" alt="" />
          Pass
        </div>
        <h1>Your restaurant, one question away</h1>
        <p className="muted">Ask about sales, stock and reviews from your POS, and get told when something needs you.</p>
        <label className="stack" style={{ gap: 6 }}>
          <span className="small">Work email</span>
          <input
            className="input"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <button className="btn primary" disabled={busy}>
          {devMode ? "Sign in (development)" : "Email me a sign-in link"}
        </button>
        {!devMode && googleSignIn && (
          <button type="button" className="btn" onClick={() => void signInWithGoogle()}>
            Continue with Google
          </button>
        )}
        {devMode && (
          <p className="small muted">
            Development sign-in: no password needed. Run the seed script for this email to get a demo restaurant.
          </p>
        )}
        {notice && <p className="up">{notice}</p>}
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}
