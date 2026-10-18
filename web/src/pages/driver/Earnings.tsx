import { useState, type FormEvent } from "react";
import { useApi } from "../../hooks";
import { api, type DriverMe, type DriverStats } from "../../lib/api";

const PERIODS = [
  { value: "today", label: "Today" },
  { value: "this_week", label: "This week" },
  { value: "last_week", label: "Last week" },
  { value: "this_month", label: "This month" },
];

/** Hours, deliveries per hour, on-time rate, wages and tips, and the driver's own weekly goal. */
export default function Earnings() {
  const [period, setPeriod] = useState("this_week");
  const stats = useApi<DriverStats & { period: string }>(`/driver/stats?period=${period}`);
  const me = useApi<DriverMe>("/driver/me");
  const [goal, setGoal] = useState("");

  async function saveGoal(e: FormEvent) {
    e.preventDefault();
    await api("/driver/goal", { method: "PUT", body: { weekly_goal_cents: goal ? Math.round(Number(goal) * 100) : null } });
    setGoal("");
    await me.reload();
  }

  const s = stats.data;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Earnings</h1>
          <p>Paid {me.data?.driver.hourly_wage ?? ""}/hour for every hour on shift, plus 100% of tips.</p>
        </div>
        <div className="seg">
          {PERIODS.map((p) => (
            <button key={p.value} className={p.value === period ? "on" : ""} onClick={() => setPeriod(p.value)}>{p.label}</button>
          ))}
        </div>
      </div>
      {s && (
        <div className="figures">
          <div className="figure"><div className="label">Earnings</div><div className="value">{s.earnings}</div>
            <div className="small muted">{s.wages} wages + {s.tips} tips</div></div>
          <div className="figure"><div className="label">Hours</div><div className="value">{s.hours}</div></div>
          <div className="figure"><div className="label">Deliveries</div><div className="value">{s.deliveries}</div>
            <div className="small muted">{s.deliveries_per_hour ?? "–"} per hour</div></div>
          <div className="figure"><div className="label">On time</div><div className="value">{s.on_time_pct != null ? `${s.on_time_pct}%` : "–"}</div>
            <div className="small muted">{s.km} km</div></div>
        </div>
      )}
      <section className="panel stack">
        <h2>Weekly goal</h2>
        {me.data?.driver.weekly_goal_cents ? (
          <>
            <div className="meter"><span style={{ width: `${Math.min(100, me.data.goal_progress_pct ?? 0)}%` }} /></div>
            <p className="small">{me.data.goal_progress_pct}% of ${(me.data.driver.weekly_goal_cents / 100).toFixed(0)} this week</p>
          </>
        ) : (
          <p className="muted small">Set a goal to track your week.</p>
        )}
        <form className="row" onSubmit={saveGoal}>
          <input className="input" style={{ width: 160 }} type="number" min={0} placeholder="Goal in $" value={goal} onChange={(e) => setGoal(e.target.value)} />
          <button className="btn">Save goal</button>
        </form>
      </section>
    </>
  );
}
