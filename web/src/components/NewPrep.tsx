import { useState } from "react";
import { api } from "../lib/api";
import type { StockItem } from "../lib/inventory";

const UNITS = ["l", "ml", "kg", "g", "each", "portion"];

/** Create a prep item (broth, sauce, dough…): what it's called and how much one batch makes. Its own recipe is
 * added next. Returns a StockItem-shaped object so it can be used in a recipe straight away. */
export default function NewPrep({ base, onCreated, onCancel, initialName = "" }: {
  base: string; onCreated: (item: StockItem) => void; onCancel: () => void; initialName?: string;
}) {
  const [name, setName] = useState(initialName);
  const [batch, setBatch] = useState("");
  const [unit, setUnit] = useState("l");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    const n = Number(batch);
    if (!name.trim()) return setError("Give the prep item a name.");
    if (!(n > 0)) return setError("Say how much one batch makes.");
    setBusy(true);
    setError("");
    try {
      const r = await api<{ id: string }>(`${base}/inventory/items`, { method: "POST", body: {
        name: name.trim(), unit, batch_qty: n, category: "Prep" } });
      onCreated({
        id: r.id, name: name.trim(), category: "Prep", unit, provider: "pass", stock_qty: null, avg_cost: null,
        value_cents: null, status: "untracked", par_level: null, reorder_point: null, reorder_qty: null,
        storage_area_id: null, supplier_id: null, supplier: null, barcode: null, shelf_order: null,
        default_yield_pct: null, conversions: [], is_prep: true, batch_qty: n, batch_unit: unit, has_recipe: false,
        price_cents: 0, used_in: 0, editable_name: true, last_count: null, daily_usage: null, days_of_cover: null,
        ready_days: null, safety_days: null, restock: null,
      } as StockItem);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create it.");
      setBusy(false);
    }
  }

  return (
    <div className="new-prep" role="group" aria-label="New prep item">
      <div className="new-prep-row">
        <label><span className="small muted">Prep item</span>
          <input className="input" autoFocus value={name} placeholder="e.g. Pho broth" onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void create()} /></label>
        <label><span className="small muted">One batch makes</span>
          <span className="row" style={{ gap: 6 }}>
            <input className="input" inputMode="decimal" style={{ width: 90 }} value={batch} placeholder="20" onChange={(e) => setBatch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void create()} />
            <select className="select" style={{ width: "auto" }} value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Unit">
              {UNITS.map((u) => <option key={u} value={u}>{u === "l" ? "L" : u}</option>)}
            </select>
          </span></label>
      </div>
      {error && <p className="small error" style={{ margin: 0 }}>{error}</p>}
      <div className="row" style={{ gap: 6 }}>
        <button className="btn primary small" disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : "Create prep item"}</button>
        <button className="btn small" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
