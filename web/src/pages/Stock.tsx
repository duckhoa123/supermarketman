import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type InventoryItem, type Supplier } from "../lib/api";

export default function Stock() {
  const { location } = useApp();
  const [lowOnly, setLowOnly] = useState(false);
  const [search, setSearch] = useState("");
  const query = `/locations/${location.id}/inventory?low_stock_only=${lowOnly}${search ? `&search=${encodeURIComponent(search)}` : ""}`;
  const { data, error, reload } = useApi<{ items: InventoryItem[]; items_with_stock_tracking: number }>(query);
  const suppliers = useApi<Supplier[]>(`/locations/${location.id}/suppliers`);

  async function patch(item: InventoryItem, body: Record<string, unknown>) {
    await api(`/locations/${location.id}/items/${item.id}`, { method: "PATCH", body });
    await reload();
  }

  async function setReorder(item: InventoryItem) {
    const value = window.prompt(`Order how many ${item.unit ?? ""} of ${item.name} when it runs low?`, String(item.reorder_qty ?? ""));
    if (value === null) return;
    const n = value.trim() === "" ? null : Number(value);
    if (n !== null && (Number.isNaN(n) || n < 0)) return;
    await patch(item, { reorder_qty: n });
  }

  async function setThreshold(item: InventoryItem) {
    const value = window.prompt(`Alert me when ${item.name} is at or below:`, String(item.low_stock_threshold ?? ""));
    if (value === null) return;
    const n = value.trim() === "" ? null : Number(value);
    if (n !== null && (Number.isNaN(n) || n < 0)) return;
    await api(`/locations/${location.id}/items/${item.id}`, { method: "PATCH", body: { low_stock_threshold: n } });
    await reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Stock</h1>
          <p>Levels come from your POS. Set an alert level, and a supplier to have the order drafted for you.</p>
        </div>
        <div className="row">
          <input
            className="input"
            style={{ width: 200 }}
            placeholder="Search items"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <label className="check">
            <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} />
            Low only
          </label>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {data && data.items_with_stock_tracking === 0 && (
        <p className="panel muted">
          None of your items have stock tracking turned on in your POS yet. Turn it on in Clover's Inventory app to see
          levels here.
        </p>
      )}
      {data && (
        <div className="panel table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Item</th>
                <th>Category</th>
                <th className="num">In stock</th>
                <th className="num">Alert at</th>
                <th>Supplier</th>
                <th className="num">Price</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((i) => (
                <tr key={i.id}>
                  <td>{i.name}</td>
                  <td className="muted">{i.category ?? "–"}</td>
                  <td className="num">
                    {i.stock_qty === null ? (
                      <span className="muted">not tracked</span>
                    ) : (
                      <span className={i.low ? "pill low" : ""}>
                        {i.stock_qty} {i.unit ?? ""}
                      </span>
                    )}
                  </td>
                  <td className="num">{i.low_stock_threshold ?? "–"}</td>
                  <td>
                    {i.stock_qty !== null && (
                      <select className="select" style={{ minWidth: 140 }} value={i.supplier_id ?? ""} aria-label={`Supplier for ${i.name}`}
                        onChange={(e) => void patch(i, { supplier_id: e.target.value || null })}>
                        <option value="">No supplier</option>
                        {suppliers.data?.map((s) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td className="num">{i.price}</td>
                  <td className="num">
                    <div className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                      <button className="btn small" onClick={() => void setThreshold(i)}>
                        Set alert
                      </button>
                      {i.supplier_id && (
                        <button className="btn small" onClick={() => void setReorder(i)}>
                          Reorder {i.reorder_qty ?? "auto"}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.items.length === 0 && <p className="empty">No items match.</p>}
        </div>
      )}
    </>
  );
}
