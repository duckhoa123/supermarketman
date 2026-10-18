import { useState, type FormEvent } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type BookingRow, type GuestRow } from "../lib/api";

const NEXT: Record<string, { status: string; label: string }[]> = {
  requested: [
    { status: "confirmed", label: "Confirm" },
    { status: "cancelled", label: "Decline" },
  ],
  confirmed: [
    { status: "seated", label: "Seated" },
    { status: "no_show", label: "No-show" },
  ],
  seated: [{ status: "completed", label: "Done" }],
};

function localDate(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString("en-CA"); // YYYY-MM-DD
}

/** Bookings from staff, phone and the customer app, and the guest list (people who agreed to share details). */
export default function Bookings() {
  const { location } = useApp();
  const [day, setDay] = useState(localDate());
  const bookings = useApi<{ bookings: BookingRow[] }>(`/locations/${location.id}/bookings?day=${day}`);
  const [sort, setSort] = useState("recent");
  const guests = useApi<{ total: number; guests: GuestRow[] }>(`/locations/${location.id}/guests?sort=${sort}`);
  const [form, setForm] = useState({ name: "", phone: "", party: 2, time: "19:00" });
  const [error, setError] = useState("");

  async function setStatus(id: string, status: string) {
    setError("");
    try {
      await api(`/locations/${location.id}/bookings/${id}`, { method: "PATCH", body: { status } });
      await bookings.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't update the booking.");
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      // The browser's offset is used; staff use the app in the restaurant's own time zone.
      const starts = new Date(`${day}T${form.time}:00`);
      await api(`/locations/${location.id}/bookings`, {
        method: "POST",
        body: { name: form.name, phone: form.phone || undefined, party_size: form.party, starts_at: starts.toISOString(), source: "phone" },
      });
      setForm({ ...form, name: "", phone: "" });
      await bookings.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add the booking.");
    }
  }

  const rows = bookings.data?.bookings ?? [];
  const covers = rows.filter((b) => !["cancelled", "no_show"].includes(b.status)).reduce((n, b) => n + b.party_size, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Bookings</h1>
          <p>
            {rows.length} bookings, {covers} covers
          </p>
        </div>
        <div className="seg" role="group" aria-label="Day">
          {[0, 1, 2].map((o) => (
            <button key={o} className={day === localDate(o) ? "on" : ""} onClick={() => setDay(localDate(o))}>
              {["Today", "Tomorrow", "In 2 days"][o]}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      <section className="panel">
        {rows.length === 0 && <p className="muted">No bookings for this day.</p>}
        <ul className="list">
          {rows.map((b) => (
            <li key={b.id} className="spread">
              <div>
                <strong>
                  {b.local_time.split(" ").pop()} · {b.party_size} · {b.name}
                </strong>
                <div className="small muted">
                  {b.source.replace("_", " ")}
                  {b.phone && ` · ${b.phone}`}
                  {b.notes && ` · ${b.notes}`}
                </div>
              </div>
              <div className="row">
                <span className={`pill ${b.status === "requested" ? "warn" : b.status === "confirmed" ? "good" : ""}`}>
                  {b.status.replace("_", " ")}
                </span>
                {NEXT[b.status]?.map((n) => (
                  <button key={n.status} className="btn small" onClick={() => void setStatus(b.id, n.status)}>
                    {n.label}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
        <form className="row" style={{ marginTop: 12 }} onSubmit={add}>
          <input className="input" style={{ flex: 2 }} required placeholder="Name" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" style={{ flex: 1 }} placeholder="Phone" value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <input className="input" style={{ width: 80 }} type="number" min={1} max={50} value={form.party}
            onChange={(e) => setForm({ ...form, party: Number(e.target.value) })} aria-label="Party size" />
          <input className="input" style={{ width: 120 }} type="time" value={form.time}
            onChange={(e) => setForm({ ...form, time: e.target.value })} aria-label="Time" />
          <button className="btn">Add booking</button>
        </form>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="spread">
          <h2>Guests ({guests.data?.total ?? 0})</h2>
          <select className="select" style={{ width: "auto" }} value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="recent">Most recent</option>
            <option value="visits">Most visits</option>
            <option value="spend">Highest spend</option>
          </select>
        </div>
        <p className="small muted" style={{ margin: "6px 0 10px" }}>
          Only people who agreed to share their details are listed. Promotions go only to those with marketing consent.
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Name</th>
                <th className="num">Visits</th>
                <th className="num">Spend</th>
                <th>Last visit</th>
                <th>Marketing</th>
              </tr>
            </thead>
            <tbody>
              {guests.data?.guests.map((g) => (
                <tr key={g.id}>
                  <td>
                    {g.name}
                    <div className="small muted">{g.email}</div>
                  </td>
                  <td className="num">{g.visits}</td>
                  <td className="num">{g.total_spend}</td>
                  <td>{g.last_visit ?? "–"}</td>
                  <td>{g.marketing_consent ? <span className="pill good">Yes</span> : <span className="pill">No</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
