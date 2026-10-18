import { useEffect, useState } from "react";
import { useApi } from "../../hooks";
import { api, type CustomerProfile } from "../../lib/api";

const DIETS = ["vegetarian", "vegan", "pescatarian", "gluten_free", "halal", "nut_free", "dairy_free"];

/** Delivery address, diet and daily nutrition goals. */
export default function Profile() {
  const { data, reload } = useApi<CustomerProfile>("/customer/profile");
  const [form, setForm] = useState<CustomerProfile | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => setForm(data), [data]);
  if (!form) return <p className="muted">Loading…</p>;

  function locate() {
    navigator.geolocation?.getCurrentPosition(
      (p) => setForm((f) => f && { ...f, home_lat: p.coords.latitude, home_lng: p.coords.longitude }),
      () => setMessage("Couldn't get your location; enter it below.")
    );
  }

  async function save() {
    if (!form) return;
    setMessage("");
    try {
      await api("/customer/profile", {
        method: "PUT",
        body: {
          home_address: form.home_address, home_lat: form.home_lat, home_lng: form.home_lng, diet_tags: form.diet_tags,
          calories_goal: form.goals.calories, protein_goal_g: form.goals.protein_g,
          carbs_goal_g: form.goals.carbs_g, fat_goal_g: form.goals.fat_g,
        },
      });
      setMessage("Saved.");
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't save.");
    }
  }

  const goal = (key: keyof CustomerProfile["goals"], value: string) =>
    setForm({ ...form, goals: { ...form.goals, [key]: value === "" ? null : Number(value) } });

  return (
    <>
      <div className="page-head">
        <h1>Profile</h1>
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <section className="panel stack">
          <h2>Delivery address</h2>
          <input className="input" placeholder="Street address and unit" value={form.home_address ?? ""}
            onChange={(e) => setForm({ ...form, home_address: e.target.value })} />
          <div className="row">
            <button className="btn small" onClick={locate}>Use my current location for the map pin</button>
            <span className="small muted">
              {form.home_lat != null ? `Pin set (${form.home_lat.toFixed(4)}, ${form.home_lng?.toFixed(4)})` : "No map pin yet"}
            </span>
          </div>
          <p className="small muted">Delivery is by our own drivers within about 20 km of central Vancouver.</p>
        </section>
        <section className="panel stack">
          <h2>Diet</h2>
          <div className="row">
            {DIETS.map((d) => (
              <label key={d} className="check small">
                <input type="checkbox" checked={form.diet_tags.includes(d)}
                  onChange={(e) => setForm({ ...form, diet_tags: e.target.checked ? [...form.diet_tags, d] : form.diet_tags.filter((x) => x !== d) })} />
                {d.replace("_", " ")}
              </label>
            ))}
          </div>
        </section>
        <section className="panel stack">
          <h2>Daily goals</h2>
          <div className="row">
            {([["calories", "Calories"], ["protein_g", "Protein (g)"], ["carbs_g", "Carbs (g)"], ["fat_g", "Fat (g)"]] as const).map(([k, label]) => (
              <label key={k} className="stack" style={{ gap: 4, flex: 1 }}>
                <span className="small">{label}</span>
                <input className="input" type="number" min={0} value={form.goals[k] ?? ""} onChange={(e) => goal(k, e.target.value)} />
              </label>
            ))}
          </div>
          <p className="small muted">Your own targets. For medical or diet advice, talk to a doctor or dietitian.</p>
        </section>
        <div className="row">
          <button className="btn primary" onClick={() => void save()}>Save</button>
          {message && <span className="small">{message}</span>}
        </div>
      </div>
    </>
  );
}
