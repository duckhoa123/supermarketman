import { useState, type FormEvent } from "react";
import { useApi } from "../../hooks";
import { api, type NutritionDay } from "../../lib/api";

const NUTRIENTS = [
  { key: "calories", label: "Calories", unit: "" },
  { key: "protein_g", label: "Protein", unit: "g" },
  { key: "carbs_g", label: "Carbs", unit: "g" },
  { key: "fat_g", label: "Fat", unit: "g" },
] as const;

/** Today's food against the person's own goals. App orders are added automatically when they arrive. */
export default function Nutrition() {
  const { data, reload, error } = useApi<NutritionDay>("/customer/nutrition");
  const [form, setForm] = useState({ name: "", calories: "" });

  async function log(e: FormEvent) {
    e.preventDefault();
    await api("/customer/nutrition", {
      method: "POST",
      body: { name: form.name, calories: form.calories ? Number(form.calories) : undefined },
    });
    setForm({ name: "", calories: "" });
    await reload();
  }

  async function remove(id: string) {
    await api(`/customer/nutrition/${id}`, { method: "DELETE" });
    await reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Today's food</h1>
          <p>Nutrition comes from restaurants' menus and is an estimate. Set your goals under Profile.</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <div className="figures">
          {NUTRIENTS.map((n) => {
            const total = data.totals[n.key];
            const goal = data.goals[n.key];
            return (
              <div key={n.key} className="figure">
                <div className="label">{n.label}</div>
                <div className="value">{Math.round(total)}{n.unit}</div>
                <div className="small muted">{goal ? `of ${goal}${n.unit} goal` : "no goal set"}</div>
                {goal ? (
                  <div className="meter"><span style={{ width: `${Math.min(100, (total / goal) * 100)}%` }} /></div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
      <section className="panel">
        <h2>Logged</h2>
        <ul className="list">
          {data?.entries.map((e) => (
            <li key={e.id} className="spread">
              <span>
                {e.name} {e.source === "order" && <span className="pill">app order</span>}
              </span>
              <span className="row">
                <span className="muted">{e.calories != null ? `${Math.round(e.calories)} cal` : ""}</span>
                <button className="btn small" onClick={() => void remove(e.id)} aria-label={`Remove ${e.name}`}>×</button>
              </span>
            </li>
          ))}
        </ul>
        <form className="row" style={{ marginTop: 12 }} onSubmit={log}>
          <input className="input" style={{ flex: 2 }} required placeholder="Something else you ate" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" style={{ width: 120 }} type="number" min={0} placeholder="Calories" value={form.calories}
            onChange={(e) => setForm({ ...form, calories: e.target.value })} />
          <button className="btn">Add</button>
        </form>
      </section>
    </>
  );
}
