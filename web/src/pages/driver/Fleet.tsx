import { useEffect, useState, type FormEvent } from "react";
import { useApi } from "../../hooks";
import { api } from "../../lib/api";

type Overview = {
  drivers: { id: string; name: string; email: string; status: string; vehicle: string; joined: boolean; role: string;
    today: { hours: number; deliveries: number; deliveries_per_hour: number | null; on_time_pct: number | null } }[];
  on_shift: number;
  on_call: number;
  deliveries: { waiting: number; assigned: number; picked_up: number };
  today: { deliveries: number; driver_hours: number; deliveries_per_driver_hour: number | null; break_even_per_hour: number };
  busy_areas: { restaurant: string; open_deliveries: number }[];
  batching_suggestions: { deliveries: string[]; why: string }[];
};

/** Dispatcher view: who is on shift, open deliveries, deliveries per driver-hour vs break-even, invites. */
export default function Fleet() {
  const { data, reload, error } = useApi<Overview>("/fleet/overview");
  const [form, setForm] = useState({ email: "", name: "", phone: "", vehicle: "e_bike", wage: "20.00", role: "driver" });
  const [message, setMessage] = useState("");

  useEffect(() => {
    const t = window.setInterval(() => void reload(), 20_000);
    return () => window.clearInterval(t);
  }, [reload]);

  async function invite(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    try {
      await api("/fleet/drivers", {
        method: "POST",
        body: { email: form.email, name: form.name, phone: form.phone || undefined, vehicle: form.vehicle, role: form.role,
          hourly_wage_cents: Math.round(Number(form.wage) * 100) },
      });
      setMessage(`Invited ${form.email}. They get the Driver Assistant when they sign in with that email.`);
      setForm({ ...form, email: "", name: "", phone: "" });
      await reload();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Couldn't invite.");
    }
  }

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const perHour = data.today.deliveries_per_driver_hour;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fleet</h1>
          <p>{data.on_shift} on shift · {data.on_call} on call</p>
        </div>
        <button className="btn" onClick={() => void api("/fleet/sweep", { method: "POST", body: {} }).then(reload)}>Assign waiting orders</button>
      </div>
      <div className="figures">
        <div className="figure"><div className="label">Waiting</div><div className="value">{data.deliveries.waiting}</div></div>
        <div className="figure"><div className="label">With drivers</div><div className="value">{data.deliveries.assigned + data.deliveries.picked_up}</div></div>
        <div className="figure"><div className="label">Deliveries per driver-hour</div>
          <div className={`value ${perHour != null && perHour < data.today.break_even_per_hour ? "down" : ""}`}>{perHour ?? "–"}</div>
          <div className="small muted">break-even about {data.today.break_even_per_hour}</div></div>
      </div>
      <div className="grid two">
        <section className="panel">
          <h2>Drivers</h2>
          <table className="data">
            <thead><tr><th>Name</th><th>Status</th><th className="num">Today</th></tr></thead>
            <tbody>
              {data.drivers.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}<div className="small muted">{d.vehicle.replace("_", "-")}{d.role === "dispatcher" ? " · dispatcher" : ""}{!d.joined ? " · invite pending" : ""}</div></td>
                  <td><span className={`pill ${d.status === "on_shift" ? "good" : d.status === "on_call" ? "warn" : ""}`}>{d.status.replace("_", " ")}</span></td>
                  <td className="num">{d.today.deliveries} in {d.today.hours} h</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="panel">
          <h2>Busy now</h2>
          {data.busy_areas.length === 0 && <p className="muted">Nothing waiting.</p>}
          <ul className="list">
            {data.busy_areas.map((a) => <li key={a.restaurant}>{a.restaurant}: {a.open_deliveries} open</li>)}
            {data.batching_suggestions.map((b, i) => <li key={i} className="small muted">Could combine {b.deliveries.length} orders: {b.why}</li>)}
          </ul>
        </section>
      </div>
      <section className="panel" style={{ marginTop: 16 }}>
        <h2>Invite a driver</h2>
        <form className="row" onSubmit={invite}>
          <input className="input" style={{ flex: 2 }} required type="email" placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input className="input" style={{ flex: 2 }} required placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" style={{ flex: 1 }} placeholder="+1604…" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <select className="select" style={{ width: "auto" }} value={form.vehicle} onChange={(e) => setForm({ ...form, vehicle: e.target.value })}>
            <option value="e_bike">E-bike</option><option value="bike">Bike</option><option value="car">Car</option>
          </select>
          <select className="select" style={{ width: "auto" }} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="driver">Driver</option><option value="dispatcher">Dispatcher</option>
          </select>
          <label className="row small">$/h <input className="input" style={{ width: 90 }} type="number" step="0.25" min={18.25} value={form.wage} onChange={(e) => setForm({ ...form, wage: e.target.value })} /></label>
          <button className="btn primary">Invite</button>
        </form>
        <p className="small muted" style={{ marginTop: 8 }}>BC minimum wage is $18.25/hour from June 2026.</p>
        {message && <p className="small">{message}</p>}
      </section>
    </>
  );
}
