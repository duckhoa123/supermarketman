import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { ItemDialog } from "../components/PosDialogs";
import { api, type InventoryItem, type Supplier } from "../lib/api";

/** Every item, as it is in the POS: add, edit and delete menu items (in Clover too, when it's connected). */
export default function Stock({ embedded = false }: { embedded?: boolean }) {
  const { location } = useApp();
  const [lowOnly, setLowOnly] = useState(false);
  const [search, setSearch] = useState("");
  const query = `/locations/${location.id}/inventory?low_stock_only=${lowOnly}${search ? `&search=${encodeURIComponent(search)}` : ""}`;
  const { data, error, reload } = useApi<{ items: InventoryItem[]; items_with_stock_tracking: number }>(query);
  const suppliers = useApi<Supplier[]>(`/locations/${location.id}/suppliers`);
  const pos = useApi<{ pos: string | null }>(`/locations/${location.id}/pos-write`);
  const [editing, setEditing] = useState<InventoryItem | "new" | null>(null);
  const [notice, setNotice] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const categories = Array.from(new Set((data?.items ?? []).map((i) => i.category).filter(Boolean) as string[])).sort();

  async function remove(list: InventoryItem[]) {
    if (!list.length) return;
    const fromPos = pos.data?.pos ? list.filter((i) => i.provider === pos.data?.pos).length : 0;
    const what = list.length === 1 ? `“${list[0].name}”` : `${list.length} items`;
    const where = fromPos ? ` They're deleted in ${pos.data!.pos![0].toUpperCase()}${pos.data!.pos!.slice(1)} too.` : "";
    if (!window.confirm(`Delete ${what}?${where} Past sales and deliveries keep their history. This can't be undone.`)) return;
    setDeleting(true);
    try {
      const r = await api<{ deleted: number; failed: { name: string; error: string }[] }>(
        `/locations/${location.id}/items/delete`, { method: "POST", body: { ids: list.map((i) => i.id) } });
      setNotice(`Deleted ${r.deleted} item${r.deleted === 1 ? "" : "s"}.`
        + (r.failed.length ? ` Not deleted: ${r.failed.map((f) => f.name).join(", ")} (${r.failed[0].error})` : ""));
      setPicked(new Set());
      setEditing(null);
      await reload();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Couldn't delete.");
    } finally {
      setDeleting(false);
    }
  }

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
      <div className={embedded ? "spread inv-toolbar" : "page-head"}>
        {embedded ? (
          <p className="small muted">All your items as they are in your POS. Add, edit or delete menu items here.</p>
        ) : (
          <div>
            <h1>Stock</h1>
            <p>Levels come from your POS. Set an alert level, and a supplier to have the order drafted for you.</p>
          </div>
        )}
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
          {picked.size > 0 && (
            <button className="btn danger" disabled={deleting}
              onClick={() => void remove((data?.items ?? []).filter((i) => picked.has(i.id)))}>
              {deleting ? "Deleting…" : `Delete ${picked.size} selected`}
            </button>
          )}
          <button className="btn primary" onClick={() => setEditing("new")}>+ Add item</button>
        </div>
      </div>
      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}</p>}
      {editing && (
        <ItemDialog locationId={location.id} item={editing === "new" ? null : editing} categories={categories}
          pos={pos.data?.pos ?? null} onClose={() => setEditing(null)} onDelete={(i) => void remove([i])}
          onDone={(m) => { setEditing(null); setNotice(m); void reload(); }} />
      )}
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
                <th style={{ width: 32 }}>
                  <input type="checkbox" aria-label="Select all items"
                    checked={!!data.items.length && data.items.every((i) => picked.has(i.id))}
                    onChange={(e) => setPicked(e.target.checked ? new Set(data.items.map((i) => i.id)) : new Set())} />
                </th>
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
                <tr key={i.id} className={picked.has(i.id) ? "picked" : ""}>
                  <td>
                    <input type="checkbox" aria-label={`Select ${i.name}`} checked={picked.has(i.id)}
                      onChange={(e) => setPicked((p) => { const n = new Set(p); if (e.target.checked) n.add(i.id); else n.delete(i.id); return n; })} />
                  </td>
                  <td>{i.name}{i.provider === "clover" && <span className="pill" style={{ marginLeft: 6 }}>Clover</span>}</td>
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
                      <button className="btn small" onClick={() => setEditing(i)}>Edit</button>
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
