import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Dialog } from "../../components/PosDialogs";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api, type Supplier } from "../../lib/api";
import {
  areaIcon, money, normUnit, qty, STATUS_LABEL, STATUS_PILL, unitCost, type Area, type OnHand, type StockItem,
} from "../../lib/inventory";

const NO_AREA = "__none__";
const FLAGGED = ["approaching", "at_par", "below_par", "out", "negative"];

function dollarsToCents(text: string): number | null {
  const n = Number(text.replace(/[$,\s]/g, ""));
  return text.trim() === "" || Number.isNaN(n) || n < 0 ? null : n * 100;
}
const num = (text: string): number | null => {
  const n = Number(text.trim());
  return text.trim() === "" || Number.isNaN(n) ? null : n;
};
const ago = (iso: string) => {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
};

export default function OnHandPanel({ onCount }: { onCount: () => void }) {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const { data, error, reload } = useApi<OnHand>(`${base}/inventory/on-hand`);
  const suppliers = useApi<Supplier[]>(`${base}/suppliers`);
  const [, setParams] = useSearchParams();
  const [area, setArea] = useState<string>("");
  const [search, setSearch] = useState("");
  const [onlyAlerts, setOnlyAlerts] = useState(false);
  const [editing, setEditing] = useState<StockItem | "new" | null>(null);
  const [areasOpen, setAreasOpen] = useState(false);
  const [notice, setNotice] = useState("");

  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.items ?? []).filter((i) =>
      (!area || (area === NO_AREA ? !i.storage_area_id : i.storage_area_id === area))
      && (!q || i.name.toLowerCase().includes(q) || i.barcode === search.trim())
      && (!onlyAlerts || FLAGGED.includes(i.status)));
  }, [data, area, search, onlyAlerts]);

  async function setItemArea(item: StockItem, id: string) {
    await api(`${base}/inventory/items/${item.id}`, { method: "PUT", body: { storage_area_id: id || null } });
    await reload();
  }

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const t = data.totals;
  const manage = data.can_manage;
  const short = t.below_par + t.out;
  return (
    <div className="stack">
      <div className="figures">
        {manage && (
          <div className="figure"><div className="label">Inventory value</div><div className="value">{money(t.value_cents, 0)}</div>
            <div className="small muted">at {data.cost_method === "last" ? "latest price" : "average cost"}</div></div>
        )}
        <div className="figure"><div className="label">Items counted</div><div className="value">{t.items}</div>
          <div className="small muted">{data.untracked} more not counted</div></div>
        <div className="figure"><div className="label">Below par or out</div>
          <div className={`value ${short ? "error" : ""}`}>{short}</div>
          <div className="small muted">{t.at_par} at par · {t.approaching} near par</div></div>
        <div className="figure"><div className="label">Below zero</div>
          <div className={`value ${t.negative ? "error" : ""}`}>{t.negative}</div>
          <div className="small muted">sold before the delivery was entered</div></div>
      </div>

      {manage && data.suggested_areas.length > 0 && (
        <div className="notice">
          <strong>Set up your storage areas</strong>
          <span className="small">Count sheets go one area at a time, in the order you walk them. Start with {data.suggested_areas.join(", ")}, and change them any time.</span>
          <div className="row">
            <button className="btn primary small" onClick={() => void api(`${base}/storage-areas/defaults`, { method: "POST" }).then(reload)}>Add these areas</button>
            <button className="btn small" onClick={() => setAreasOpen(true)}>Make my own</button>
          </div>
        </div>
      )}
      {manage && t.no_cost > 0 && (
        <p className="small warn-text">⚠ {t.no_cost} item{t.no_cost === 1 ? " has" : "s have"} no cost yet, so {t.no_cost === 1 ? "it isn't" : "they aren't"} in the value. Scan a delivery invoice, or set the cost with Edit.</p>
      )}
      {notice && <p className="notice small">{notice}</p>}

      <div className="spread inv-toolbar">
        <div className="chips" role="group" aria-label="Storage area">
          <button className={!area ? "chip on" : "chip"} onClick={() => setArea("")}>All <b>{data.items.length}</b></button>
          {data.areas.map((a) => (
            <button key={a.id} className={area === a.id ? "chip on" : "chip"} onClick={() => setArea(a.id)}><span aria-hidden>{areaIcon(a.name)}</span> {a.name} <b>{a.items}</b></button>
          ))}
          {data.areas.length > 0 && data.unassigned > 0 && (
            <button className={area === NO_AREA ? "chip on" : "chip"} onClick={() => setArea(NO_AREA)}>No area <b>{data.unassigned}</b></button>
          )}
        </div>
        <div className="row">
          <input className="input" style={{ width: 170 }} placeholder="Search or scan" value={search} onChange={(e) => setSearch(e.target.value)} />
          <label className="check small"><input type="checkbox" checked={onlyAlerts} onChange={(e) => setOnlyAlerts(e.target.checked)} />Needs ordering</label>
          {manage && <button className="btn" onClick={() => setAreasOpen(true)}>Areas</button>}
          <button className="btn" onClick={onCount}>Start a count</button>
          {manage && <button className="btn primary" onClick={() => setEditing("new")}>+ Add item</button>}
        </div>
      </div>

      <div className="panel table-wrap inv-table-wrap">
        <table className="data inv-table">
          <thead>
            <tr>
              <th>Item</th><th>Area</th><th className="num">On hand</th><th className="num">Par · reorder at</th>
              <th>Last count</th><th className="num">Cover</th>
              {manage && <><th className="num">Cost</th><th className="num">Value</th></>}
              {manage && <th />}
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className={`st-${i.status}`}>
                <td data-label="Item">
                  <strong>{i.name}</strong>{i.is_prep && <span className="pill" style={{ marginLeft: 6 }}>Prep</span>}
                  <div className="small muted">
                    {[i.category, i.supplier, i.used_in ? `in ${i.used_in} recipe${i.used_in === 1 ? "" : "s"}` : null,
                      i.provider === "clover" ? "Clover" : null].filter(Boolean).join(" · ") || " "}
                  </div>
                </td>
                <td data-label="Area">
                  {manage ? (
                    <select className="select inv-area" aria-label={`Area for ${i.name}`} value={i.storage_area_id ?? ""}
                      onChange={(e) => void setItemArea(i, e.target.value)}>
                      <option value="">No area</option>
                      {data.areas.map((a) => <option key={a.id} value={a.id}>{areaIcon(a.name)} {a.name}</option>)}
                    </select>
                  ) : (() => { const a = data.areas.find((x) => x.id === i.storage_area_id); return <span className="muted">{a ? `${areaIcon(a.name)} ${a.name}` : "–"}</span>; })()}
                </td>
                <td data-label="On hand" className="num">
                  {i.stock_qty == null ? <span className="muted">{i.is_prep ? "made to order" : "not counted"}</span> : (
                    <>
                      <strong>{qty(i.stock_qty)}</strong> <span className="muted">{i.unit ?? ""}</span>
                      {i.status !== "ok" && <span className={`pill ${STATUS_PILL[i.status]}`} style={{ marginLeft: 6 }}>{STATUS_LABEL[i.status]}</span>}
                    </>
                  )}
                </td>
                <td data-label="Par · reorder at" className="num">
                  {i.par_level == null && i.reorder_point == null ? <span className="muted">–</span>
                    : <>{qty(i.par_level)} <span className="muted">·</span> {qty(i.reorder_point)}</>}
                </td>
                <td data-label="Last count">
                  {i.last_count ? <span title={new Date(i.last_count.at).toLocaleString()}>{qty(i.last_count.qty)} <span className="muted small">{ago(i.last_count.at)}</span></span>
                    : <span className="muted">never</span>}
                </td>
                <td data-label="Cover" className="num nowrap">{i.days_of_cover == null ? <span className="muted">–</span> : `${i.days_of_cover} d`}</td>
                {manage && (
                  <>
                    <td data-label="Cost" className="num">{i.avg_cost == null ? <span className="muted">–</span> : <>{unitCost(i.avg_cost)}<span className="muted small">/{i.unit ?? "each"}</span></>}</td>
                    <td data-label="Value" className="num">{i.avg_cost == null ? "–" : money(i.value_cents)}</td>
                    <td className="num inv-actions">
                      <button className="btn small" onClick={() => setEditing(i)}>Edit</button>
                      <button className="btn small" onClick={() => setParams({ tab: "ledger", item: i.id })}>History</button>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && (
          <p className="empty">
            {data.items.length === 0
              ? "Nothing is being counted yet. Add your ingredients with + Add item, or scan a delivery invoice under Supplies."
              : "No items match."}
          </p>
        )}
      </div>
      <p className="small muted">
        Par is what you want on hand after a delivery; the reorder point is when to order again. Cover is how many days
        the stock lasts at the last four weeks' usage.
      </p>

      {editing && (
        <StockItemDialog base={base} item={editing === "new" ? null : editing} data={data}
          suppliers={suppliers.data ?? []} defaultArea={area && area !== NO_AREA ? area : null}
          onClose={() => setEditing(null)}
          onDone={(m) => { setEditing(null); setNotice(m); void reload(); }} />
      )}
      {areasOpen && <AreasDialog base={base} areas={data.areas} onClose={() => { setAreasOpen(false); void reload(); }} />}
    </div>
  );
}

/** Add an ingredient, supply or prep item, or change one. Grouped: details, par, yield, supplier. */
function StockItemDialog({ base, item, data, suppliers, defaultArea, onClose, onDone }: {
  base: string; item: StockItem | null; data: OnHand; suppliers: Supplier[]; defaultArea: string | null;
  onClose: () => void; onDone: (message: string) => void;
}) {
  const [f, setF] = useState({
    name: item?.name ?? "", unit: normUnit(item?.unit) || "", category: item?.category ?? "",
    areaId: item?.storage_area_id ?? defaultArea ?? "",
    cost: item?.avg_cost != null && !item.is_prep ? (item.avg_cost / 100).toFixed(item.avg_cost < 100 ? 4 : 2) : "",
    quantity: "", par: item?.par_level != null ? String(item.par_level) : "", reorderAt: item?.reorder_point != null ? String(item.reorder_point) : "",
    orderQty: item?.reorder_qty != null ? String(item.reorder_qty) : "", supplierId: item?.supplier_id ?? "", barcode: item?.barcode ?? "",
    yieldPct: item?.default_yield_pct != null ? String(item.default_yield_pct) : "",
    prep: item?.is_prep ?? false, batch: item?.batch_qty != null ? String(item.batch_qty) : "",
  });
  const [yieldTest, setYieldTest] = useState<{ ap: string; ep: string; cooked: string } | null>(null);
  const [adjust, setAdjust] = useState({ mode: "set" as "set" | "add", amount: "", reason: "correction", note: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }));
  const unitShown = f.unit || "unit";
  const units = Array.from(new Set([...(f.unit ? [f.unit] : []), ...data.units]));
  const inArea = !!defaultArea && f.areaId === defaultArea;  // already filtered to an area: no need to ask

  const ap = num(yieldTest?.ap ?? ""), ep = num(yieldTest?.ep ?? ""), cooked = num(yieldTest?.cooked ?? "");
  const tested = ap && ap > 0 ? Math.round((((cooked ?? ep) ?? 0) / ap) * 1000) / 10 : null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const costCents = dollarsToCents(f.cost);
    if (f.cost.trim() && costCents == null) return setError("Enter the cost like 12.00.");
    const parN = num(f.par), rpN = num(f.reorderAt), orderN = num(f.orderQty), qtyN = num(f.quantity);
    const yieldN = num(f.yieldPct), batchN = num(f.batch);
    if ([[f.par, parN], [f.reorderAt, rpN], [f.orderQty, orderN]].some(([t, n]) => (t as string).trim() && (n == null || (n as number) < 0))) {
      return setError("Par, reorder point and order amount must be numbers.");
    }
    if (f.yieldPct.trim() && (yieldN == null || yieldN < 1 || yieldN > 100)) return setError("Yield is a % between 1 and 100.");
    if (f.prep && (batchN == null || batchN <= 0)) return setError("Say how much one batch makes.");
    const common = {
      unit: f.unit || null, category: f.category.trim() || null, storage_area_id: f.areaId || null,
      par_level: parN, reorder_point: rpN, reorder_qty: orderN, supplier_id: f.supplierId || null,
      barcode: f.barcode.trim() || null, default_yield_pct: yieldN,
    };
    setBusy(true);
    try {
      if (!item) {
        if (!f.name.trim()) throw new Error("Give the item a name.");
        if (f.quantity.trim() && (qtyN == null || qtyN < 0)) throw new Error("On hand must be a number.");
        await api(`${base}/inventory/items`, { method: "POST", body: {
          ...common, name: f.name.trim(), unit_cost: f.prep ? null : costCents, qty: qtyN, batch_qty: f.prep ? batchN : null } });
        onDone(`${f.name.trim()} added to inventory.${f.prep ? " Add its recipe on the Recipes tab." : ""}`);
        return;
      }
      await api(`${base}/inventory/items/${item.id}`, { method: "PUT", body: {
        ...common, ...(item.editable_name && f.name.trim() !== item.name ? { name: f.name.trim() } : {}),
        ...(costCents != null && !f.prep ? { unit_cost: costCents } : {}), batch_qty: f.prep ? batchN : 0 } });
      const amount = num(adjust.amount);
      if (amount != null && adjust.amount.trim()) {
        await api(`${base}/stock-adjustments`, { method: "POST", body: adjust.mode === "add"
          ? { item_id: item.id, qty_delta: amount, reason: adjust.reason, note: adjust.note || null }
          : { item_id: item.id, set_to: amount, reason: adjust.reason, note: adjust.note || null } });
      }
      onDone(`${item.name} saved.`);
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't save.");
      setBusy(false);
    }
  }

  async function stopCounting() {
    if (!item || !window.confirm(`Stop counting ${item.name}? Its past changes stay in History.`)) return;
    await api(`${base}/inventory/items/${item.id}`, { method: "PUT", body: { track: false } });
    onDone(`${item.name} is no longer counted.`);
  }

  // A field's explanation shows as a tooltip on its name (hover, or tap on a phone), not under the field.
  const field = (label: string, input: React.ReactNode, hint?: string) => (
    <label className="stack" style={{ gap: 4, flex: 1, minWidth: 0 }}>
      {hint ? <span className="small tip" tabIndex={0} data-tip={hint}>{label}</span> : <span className="small">{label}</span>}
      {input}
    </label>
  );
  const unitSelect = (
    <select className="select inv-unit" aria-label="Counted in" value={f.unit} onChange={(e) => set({ unit: e.target.value })}>
      <option value="">unit…</option>
      {units.map((u) => <option key={u} value={u}>{u}</option>)}
    </select>
  );
  return (
    <Dialog title={item ? `Edit ${item.name}` : "Add an inventory item"} onClose={onClose} width="min(560px, 100%)"
      footer={<div className="row" style={{ width: "100%" }}>
        {item && item.stock_qty != null && <button type="button" className="btn danger" onClick={() => void stopCounting()}>Stop counting</button>}
        <span style={{ flex: 1 }} />
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="stock-item-form" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>}>
      <form id="stock-item-form" className="stack" onSubmit={save}>
        <section className="inv-group">
          <h3>Details</h3>
          {field("Name",
            <input className="input" value={f.name} maxLength={300} autoFocus={!item} disabled={!!item && !item.editable_name}
              onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Salmon fillet" />,
            item && !item.editable_name ? "From your POS: rename it on the Menu items tab." : undefined)}
          <div className="inv-form-row">
            {field("Category", <><input className="input" list="inv-cats" value={f.category} onChange={(e) => set({ category: e.target.value })} placeholder="Produce, Protein…" />
              <datalist id="inv-cats">{data.categories.map((c) => <option key={c} value={c} />)}</datalist></>)}
            {!inArea && field("Kept in", <select className="select" value={f.areaId} onChange={(e) => set({ areaId: e.target.value })}>
              <option value="">No area</option>
              {data.areas.map((a) => <option key={a.id} value={a.id}>{areaIcon(a.name)} {a.name}</option>)}
            </select>)}
          </div>
          <label className="check small">
            <input type="checkbox" checked={f.prep} onChange={(e) => set({ prep: e.target.checked })} />
            A prep item made in the kitchen (sauce, dough, stock)
          </label>
          {f.prep ? field("One batch makes",
            <div className="inv-amount">
              <input className="input" inputMode="decimal" value={f.batch} onChange={(e) => set({ batch: e.target.value })} placeholder="e.g. 64" />
              {unitSelect}
            </div>, "Its cost comes from its recipe, so it follows ingredient prices by itself.")
            : (
              <>
                {field("Cost", <div className="inv-amount">
                  <span className="inv-cur">$</span>
                  <input className="input" inputMode="decimal" value={f.cost} onChange={(e) => set({ cost: e.target.value })} placeholder="12.00" />
                  <span className="muted">/</span>
                  {unitSelect}
                </div>, item ? `Updates itself from each delivery (${data.cost_method === "last" ? "latest price" : "weighted average"}). Change it only to correct it.` : undefined)}
                {!item && field("On hand", <div className="inv-amount">
                  <input className="input" inputMode="decimal" value={f.quantity} onChange={(e) => set({ quantity: e.target.value })} placeholder="0" />
                  <span className="muted inv-unit-label">{unitShown}</span>
                </div>)}
              </>
            )}
          {field("Barcode", <input className="input" value={f.barcode} onChange={(e) => set({ barcode: e.target.value })} placeholder="Scan or type (optional)" />)}
        </section>

        <section className="inv-group">
          <h3>Par</h3>
          <div className="inv-form-row">
            {field(`Par level (${unitShown})`, <input className="input" inputMode="decimal" value={f.par} onChange={(e) => set({ par: e.target.value })} placeholder="e.g. 40" />, "Wanted on hand after a delivery")}
            {field("Reorder point", <input className="input" inputMode="decimal" value={f.reorderAt} onChange={(e) => set({ reorderAt: e.target.value })} placeholder="e.g. 15" />, "Alert and order when it gets here")}
            {field("Order amount", <input className="input" inputMode="decimal" value={f.orderQty} onChange={(e) => set({ orderQty: e.target.value })} placeholder="Par − on hand" />)}
          </div>
        </section>

        <section className="inv-group">
          <div className="spread"><h3>Yield</h3>
            <button type="button" className="btn small" onClick={() => setYieldTest(yieldTest ? null : { ap: "", ep: "", cooked: "" })}>
              {yieldTest ? "Close" : "Work it out"}
            </button>
          </div>
          {field("Usual yield %", <input className="input" inputMode="decimal" value={f.yieldPct} onChange={(e) => set({ yieldPct: e.target.value })} placeholder="100" />,
            "The share that's usable after trimming and cooking. Recipes start from it.")}
          {yieldTest && (
            <div className="inv-yield">
              <div className="inv-form-row">
                {field("As bought", <input className="input" inputMode="decimal" value={yieldTest.ap} onChange={(e) => setYieldTest({ ...yieldTest, ap: e.target.value })} placeholder="10" />)}
                {field("After trimming", <input className="input" inputMode="decimal" value={yieldTest.ep} onChange={(e) => setYieldTest({ ...yieldTest, ep: e.target.value })} placeholder="8.5" />)}
                {field("After cooking (optional)", <input className="input" inputMode="decimal" value={yieldTest.cooked} onChange={(e) => setYieldTest({ ...yieldTest, cooked: e.target.value })} placeholder="–" />)}
              </div>
              <div className="spread small">
                <span>Weigh it the same way each time. {tested != null ? <strong>Yield {tested}%</strong> : null}</span>
                <button type="button" className="btn small primary" disabled={tested == null || tested <= 0 || tested > 100}
                  onClick={() => { set({ yieldPct: String(tested) }); setYieldTest(null); }}>Use {tested ?? "–"}%</button>
              </div>
            </div>
          )}
        </section>

        <section className="inv-group">
          <h3>Supplier</h3>
          <select className="select" aria-label="Supplier" value={f.supplierId} onChange={(e) => set({ supplierId: e.target.value })}>
            <option value="">No supplier</option>
            {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </section>

        {item && !item.is_prep && (
          <section className="inv-group">
            <h3><span className="tip" tabIndex={0} data-tip="Saved in History with your name. To log something thrown out, use the Waste tab.">Correct the quantity</span> <span className="muted small">(now {qty(item.stock_qty)} {item.unit ?? ""})</span></h3>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <select className="select" style={{ width: "auto" }} value={adjust.mode} aria-label="How"
                onChange={(e) => setAdjust({ ...adjust, mode: e.target.value as "set" | "add" })}>
                <option value="set">Set to</option>
                <option value="add">Add (− to take away)</option>
              </select>
              <input className="input" inputMode="decimal" aria-label="Amount" value={adjust.amount} placeholder={adjust.mode === "set" ? qty(item.stock_qty) : "e.g. -2"}
                onChange={(e) => setAdjust({ ...adjust, amount: e.target.value })} />
              <select className="select" style={{ width: "auto" }} aria-label="Reason" value={adjust.reason}
                onChange={(e) => setAdjust({ ...adjust, reason: e.target.value })}>
                <option value="correction">Correction</option>
                <option value="found">Found more</option>
                <option value="returned">Returned to supplier</option>
                <option value="transfer">Moved elsewhere</option>
                <option value="missing">Missing</option>
              </select>
            </div>
            <input className="input" maxLength={500} placeholder="Note (optional)" value={adjust.note} onChange={(e) => setAdjust({ ...adjust, note: e.target.value })} />
          </section>
        )}
        {error && <p className="small error">{error}</p>}
      </form>
    </Dialog>
  );
}

function AreasDialog({ base, areas, onClose }: { base: string; areas: Area[]; onClose: () => void }) {
  const [names, setNames] = useState<Record<string, string>>(Object.fromEntries(areas.map((a) => [a.id, a.name])));
  const [list, setList] = useState(areas);
  const [added, setAdded] = useState("");
  const [error, setError] = useState("");

  async function rename(a: Area) {
    const n = (names[a.id] ?? "").trim();
    if (!n || n === a.name) return;
    await api(`${base}/storage-areas/${a.id}`, { method: "PUT", body: { name: n } });
  }
  async function remove(a: Area) {
    if (!window.confirm(`Remove ${a.name}? Its ${a.items} item${a.items === 1 ? "" : "s"} stay in inventory, without an area.`)) return;
    await api(`${base}/storage-areas/${a.id}`, { method: "DELETE" });
    setList(list.filter((x) => x.id !== a.id));
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!added.trim()) return;
    try {
      const r = await api<{ id: string; name: string }>(`${base}/storage-areas`, { method: "POST", body: { name: added.trim() } });
      setList([...list, { id: r.id, name: r.name, sort: list.length, items: 0, value_cents: 0 }]);
      setNames({ ...names, [r.id]: r.name });
      setAdded("");
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't add it.");
    }
  }
  async function move(i: number, by: number) {
    const next = [...list];
    const [a] = next.splice(i, 1);
    next.splice(i + by, 0, a);
    setList(next);
    await Promise.all(next.map((x, n) => api(`${base}/storage-areas/${x.id}`, { method: "PUT", body: { name: names[x.id] || x.name, sort: n } })));
  }

  return (
    <Dialog title="Storage areas" onClose={onClose} narrow footer={<button className="btn primary" style={{ marginLeft: "auto" }} onClick={onClose}>Done</button>}>
      <p className="small muted">In the order you walk them during a count. Put items in shelf order on the Count tab.</p>
      <ul className="inv-areas">
        {list.map((a, i) => (
          <li key={a.id}>
            <span className="inv-area-icon" aria-hidden>{areaIcon(names[a.id] || a.name)}</span>
            <span className="inv-area-move">
              <button className="sb-icon" aria-label={`Move ${a.name} up`} disabled={i === 0} onClick={() => void move(i, -1)}>↑</button>
              <button className="sb-icon" aria-label={`Move ${a.name} down`} disabled={i === list.length - 1} onClick={() => void move(i, 1)}>↓</button>
            </span>
            <input className="input" value={names[a.id] ?? ""} aria-label="Area name" onChange={(e) => setNames({ ...names, [a.id]: e.target.value })}
              onBlur={() => void rename(a)} />
            <button className="sb-icon" aria-label={`Remove ${a.name}`} onClick={() => void remove(a)}>✕</button>
          </li>
        ))}
      </ul>
      <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={add}>
        <input className="input" placeholder="e.g. Line fridge" value={added} maxLength={100} onChange={(e) => setAdded(e.target.value)} />
        <button className="btn">Add</button>
      </form>
      {error && <p className="small error">{error}</p>}
    </Dialog>
  );
}
