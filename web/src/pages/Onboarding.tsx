import { useState, type FormEvent } from "react";
import { api } from "../lib/api";

const ZONES = [
  "America/Toronto",
  "America/Vancouver",
  "America/Edmonton",
  "America/Winnipeg",
  "America/Regina",
  "America/Halifax",
  "America/St_Johns",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Australia/Sydney",
  "Australia/Melbourne",
  "Australia/Brisbane",
  "Australia/Adelaide",
  "Australia/Perth",
  "Australia/Hobart",
  "Pacific/Auckland",
  "Europe/London",
];

const CANADA = ZONES.slice(0, 7);

export default function Onboarding({
  onDone,
  demoAvailable,
}: {
  onDone: () => Promise<void>;
  demoAvailable: boolean;
}) {
  const guess = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [business, setBusiness] = useState("");
  const [name, setName] = useState("");
  const [tz, setTz] = useState(ZONES.includes(guess) ? guess : "America/Toronto");
  const [currency, setCurrency] = useState(CANADA.includes(tz) ? "CAD" : tz.startsWith("America") ? "USD" : "AUD");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadDemo() {
    setBusy(true);
    setError("");
    try {
      await api("/demo", { method: "POST" });
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load the demo restaurant.");
      setBusy(false);
    }
  }

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
              {["CAD", "USD", "AUD", "NZD", "GBP", "EUR"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>
        <button className="btn primary" disabled={busy}>
          Create restaurant
        </button>
        {demoAvailable && (
          <>
            <p className="small muted" style={{ textAlign: "center" }}>
              or
            </p>
            <button type="button" className="btn" disabled={busy} onClick={() => void loadDemo()}>
              {busy ? "Setting up…" : "Explore a demo restaurant first"}
            </button>
            <p className="small muted">
              Adds "Demo Bistro" with six weeks of sample sales, stock, tables and reviews. Takes up to a minute.
            </p>
          </>
        )}
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}
