import { useEffect, useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api } from "../lib/api";

type Profile = {
  address: string | null;
  lat: number | null;
  lng: number | null;
  phone: string | null;
  cuisine: string | null;
  listed: boolean;
  accepts_orders: boolean;
  accepts_delivery: boolean;
  accepts_bookings: boolean;
};

const TOGGLES: { key: keyof Profile; label: string }[] = [
  { key: "listed", label: "Show the restaurant in the customer app" },
  { key: "accepts_orders", label: "Take pickup orders" },
  { key: "accepts_delivery", label: "Offer delivery by our drivers (within the service area)" },
  { key: "accepts_bookings", label: "Take table booking requests" },
];

/** What customers see, and what delivery needs: address, map position and which services are on. */
export default function RestaurantProfile() {
  const { location } = useApp();
  const { data, reload } = useApi<Profile>(`/locations/${location.id}/profile`);
  const [form, setForm] = useState<Profile | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => setForm(data), [data]);
  if (!form) return null;

  async function save() {
    setMessage("");
    try {
      await api(`/locations/${location.id}/profile`, { method: "PUT", body: form });
      setMessage("Saved.");
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't save.");
    }
  }

  const set = (patch: Partial<Profile>) => setForm({ ...form, ...patch });
  return (
    <section className="panel stack">
      <h2>Customer app and delivery</h2>
      <div className="row">
        <input className="input" style={{ flex: 3 }} placeholder="Street address" value={form.address ?? ""}
          onChange={(e) => set({ address: e.target.value })} />
        <input className="input" style={{ flex: 1 }} placeholder="Cuisine" value={form.cuisine ?? ""}
          onChange={(e) => set({ cuisine: e.target.value })} />
        <input className="input" style={{ flex: 1 }} placeholder="Phone" value={form.phone ?? ""}
          onChange={(e) => set({ phone: e.target.value })} />
      </div>
      <div className="row">
        <input className="input" style={{ flex: 1 }} type="number" step="0.0001" placeholder="Latitude, e.g. 49.2634"
          value={form.lat ?? ""} onChange={(e) => set({ lat: e.target.value === "" ? null : Number(e.target.value) })} />
        <input className="input" style={{ flex: 1 }} type="number" step="0.0001" placeholder="Longitude, e.g. -123.1007"
          value={form.lng ?? ""} onChange={(e) => set({ lng: e.target.value === "" ? null : Number(e.target.value) })} />
      </div>
      <p className="small muted">The map position is used for delivery distance, customer search and the weather forecast.</p>
      {TOGGLES.map((t) => (
        <label key={t.key} className="check">
          <input type="checkbox" checked={Boolean(form[t.key])} onChange={(e) => set({ [t.key]: e.target.checked })} />
          {t.label}
        </label>
      ))}
      <div className="row">
        <button className="btn primary" onClick={() => void save()}>
          Save
        </button>
        {message && <span className="small">{message}</span>}
      </div>
    </section>
  );
}
