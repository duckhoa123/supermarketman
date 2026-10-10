import { useState, type FormEvent } from "react";
import { api } from "../lib/api";

const ZONES = [
  "Australia/Sydney",
  "Australia/Melbourne",
  "Australia/Brisbane",
  "Australia/Adelaide",
  "Australia/Perth",
  "Australia/Hobart",
  "Pacific/Auckland",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
];

export default function Onboarding({ onDone }: { onDone: () => Promise<void> }) {
  const guess = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [business, setBusiness] = useState("");
  const [name, setName] = useState("");
  const [tz, setTz] = useState(ZONES.includes(guess) ? guess : "Australia/Sydney");
  const [currency, setCurrency] = useState(tz.startsWith("America") ? "USD" : "AUD");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/onboarding", {
        method: "POST",
        body: { business_name: business, location_name: name || business, timezone: tz, currency },
      });
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create the restaurant.");
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card-narrow" onSubmit={submit}>
        <h1>Set up your restaurant</h1>
        <p className="muted">Next you'll connect your POS so the assistant can see your sales and stock.</p>
        <label className="stack" style={{ gap: 6 }}>
          <span className="small">Business name</span>
          <input className="input" required value={business} onChange={(e) => setBusiness(e.target.value)} />
        </label>
        <label className="stack" style={{ gap: 6 }}>
          <span className="small">Venue name (if you have more than one)</span>
          <input className="input" value={name} placeholder={business} onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="row">
          <label className="stack" style={{ gap: 6, flex: 2 }}>
            <span className="small">Time zone</span>
            <select className="select" value={tz} onChange={(e) => setTz(e.target.value)}>
              {ZONES.map((z) => (
                <option key={z}>{z}</option>
              ))}
            </select>
          </label>
          <label className="stack" style={{ gap: 6, flex: 1 }}>
            <span className="small">Currency</span>
            <select className="select" value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {["AUD", "NZD", "USD", "CAD", "GBP", "EUR"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>
        <button className="btn primary" disabled={busy}>
          Create restaurant
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}
