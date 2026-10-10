import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api } from "../lib/api";

type Table = { id: string; name: string; seats: number; status: string };
const NEXT: Record<string, string> = { free: "occupied", occupied: "cleaning", cleaning: "free", reserved: "occupied" };
const LABEL: Record<string, string> = { free: "Free", occupied: "Seated", reserved: "Reserved", cleaning: "Needs reset" };

export default function Tables() {
  const { location } = useApp();
  const { data, error, reload } = useApi<{ summary: Record<string, number>; tables: Table[] }>(
    `/locations/${location.id}/tables`
  );
  const [name, setName] = useState("");

  async function cycle(t: Table) {
    await api(`/locations/${location.id}/tables/${t.id}`, { method: "PATCH", body: { status: NEXT[t.status] ?? "free" } });
    await reload();
  }

  async function reserve(t: Table) {
    await api(`/locations/${location.id}/tables/${t.id}`, { method: "PATCH", body: { status: "reserved" } });
    await reload();
  }

  async function add() {
    if (!name.trim()) return;
    await api(`/locations/${location.id}/tables`, { method: "POST", body: { name: name.trim(), seats: 4 } });
    setName("");
    await reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tables</h1>
          <p>
            {data
              ? Object.entries(data.summary)
                  .map(([k, v]) => `${v} ${LABEL[k]?.toLowerCase() ?? k}`)
                  .join(", ")
              : "Tap a table to move it to its next state."}
          </p>
        </div>
        <div className="row">
          <input className="input" style={{ width: 140 }} placeholder="New table, e.g. T13" value={name}
            onChange={(e) => setName(e.target.value)} />
          <button className="btn" onClick={() => void add()}>
            Add table
          </button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="floor">
        {data?.tables.map((t) => (
          <div key={t.id} className="stack" style={{ gap: 4 }}>
            <button className={`table-tile ${t.status}`} onClick={() => void cycle(t)}>
              <div className="name">{t.name}</div>
              <div className="small muted">{t.seats} seats</div>
              <div className="small">{LABEL[t.status] ?? t.status}</div>
            </button>
            {t.status === "free" && (
              <button className="btn small" onClick={() => void reserve(t)}>
                Reserve
              </button>
            )}
          </div>
        ))}
      </div>
      {data && data.tables.length === 0 && <p className="empty">Add your tables to track who's seated.</p>}
    </>
  );
}
