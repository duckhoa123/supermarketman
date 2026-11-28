import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import FilterBar, { applyFilters, type Condition, type FilterField } from "../../components/FilterBar";
import { Dialog } from "../../components/PosDialogs";
import { MakeDialog, RecipeEditor } from "../../components/RecipeEditor";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api, type Supplier } from "../../lib/api";
import {
  money, normUnit, qty, STATUS_LABEL, STATUS_PILL, unitCost, type OnHand, type StockItem,
} from "../../lib/inventory";


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

export default function OnHandPanel() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const { data, error, reload } = useApi<OnHand>(`${base}/inventory/on-hand`);
  const suppliers = useApi<Supplier[]>(`${base}/suppliers`);
  const [, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const filterKey = `pass.inventory.filters.${location.id}`;
  const [conditions, setConditionsState] = useState<Condition[]>(() => {
    try { return JSON.parse(localStorage.getItem(filterKey) || "[]") as Condition[]; } catch { return []; }
  });
  function setConditions(c: Condition[]) {
    setConditionsState(c);
    try { localStorage.setItem(filterKey, JSON.stringify(c)); } catch { /* storage blocked */ }
  }
  const [editing, setEditing] = useState<StockItem | "new" | null>(null);
  const [recipeFor, setRecipeFor] = useState<string | null>(null);
  const [making, setMaking] = useState<StockItem | null>(null);
  const [notice, setNotice] = useState("");

  // Every column can be filtered on, with conditions that fit its type.
  const fields = useMemo<FilterField<StockItem>[]>(() => {
    const list: FilterField<StockItem>[] = [
      { key: "name", label: "Name", type: "text", get: (i) => i.name },
      { key: "category", label: "Category", type: "text", get: (i) => i.category },
      { key: "status", label: "Status", type: "choice", get: (i) => STATUS_LABEL[i.status], options: Object.values(STATUS_LABEL) },
      { key: "on_hand", label: "On hand", type: "number", get: (i) => i.stock_qty },
      { key: "unit", label: "Unit", type: "text", get: (i) => i.unit },
      { key: "reorder", label: "Reorder point", type: "number", get: (i) => i.reorder_point },
      { key: "last_count", label: "Last counted", type: "date", get: (i) => i.last_count ? new Date(i.last_count.at).toLocaleDateString("en-CA") : null },
      { key: "supplier", label: "Supplier", type: "choice", get: (i) => i.supplier, options: Array.from(new Set((data?.items ?? []).map((i) => i.supplier).filter(Boolean) as string[])).sort() },
      { key: "recipes", label: "Used in recipes", type: "number", get: (i) => i.used_in },
      { key: "prep", label: "Prep item", type: "choice", get: (i) => (i.is_prep ? "Yes" : "No"), options: ["Yes", "No"] },
    ];
    if (data?.can_manage) {
      list.splice(7, 0,
        { key: "cost", label: "Cost per unit", type: "number", prefix: "$", get: (i) => (i.avg_cost == null ? null : Math.round(i.avg_cost) / 100) },
        { key: "value", label: "Value", type: "number", prefix: "$", get: (i) => (i.value_cents == null || i.avg_cost == null ? null : i.value_cents / 100) });
    }
    return list;
  }, [data]);

  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    return applyFilters((data?.items ?? []).filter((i) =>
      !q || i.name.toLowerCase().includes(q) || i.barcode === search.trim()), fields, conditions);
  }, [data, search, fields, conditions]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const t = data.totals;
  const manage = data.can_manage;
  return (
    <div className="stack">
      {manage && t.no_cost > 0 && (
        <p className="small warn-text">⚠ {t.no_cost} item{t.no_cost === 1 ? " has" : "s have"} no cost yet, so {t.no_cost === 1 ? "it isn't" : "they aren't"} in the value. Scan a delivery invoice, or set the cost with Edit.</p>
      )}
      {notice && <p className="notice small">{notice}</p>}

      <div className="spread inv-toolbar">
        <FilterBar fields={fields} conditions={conditions} onChange={setConditions}>
          <input className="input fb-search" placeholder="Search or scan" value={search} onChange={(e) => setSearch(e.target.value)} />
        </FilterBar>
        <div className="row">
          {manage && <button className="btn primary" onClick={() => setEditing("new")}>+ Add item</button>}
        </div>
      </div>

      <div className="panel table-wrap inv-table-wrap">
        <table className="data inv-table">
          <thead>
            <tr>
              <th>Item</th>
              <th className="num">On hand</th>
              <th className="num"><span className="tip tip-left" tabIndex={0} data-tip="When stock gets down to this, you're alerted and it's added to a draft order.">Reorder at</span></th>
              <th>Last count</th>
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
                <td data-label="On hand" className="num">
                  {i.stock_qty == null ? <span className="muted">{i.is_prep ? "made to order" : "not counted"}</span> : (
                    <>
                      <strong>{qty(i.stock_qty)}</strong> <span className="muted">{i.unit ?? ""}</span>
                      {i.status !== "ok" && <span className={`pill ${STATUS_PILL[i.status]}`} style={{ marginLeft: 6 }}>{STATUS_LABEL[i.status]}</span>}
                    </>
                  )}
                </td>
                <td data-label="Reorder at" className="num">{i.reorder_point == null ? <span className="muted">–</span> : qty(i.reorder_point)}</td>
                <td data-label="Last count">
                  {i.last_count ? <span title={new Date(i.last_count.at).toLocaleString()}>{qty(i.last_count.qty)} <span className="muted small">{ago(i.last_count.at)}</span></span>
                    : <span className="muted">never</span>}
                </td>
                {manage && (
                  <>
                    <td data-label="Cost" className="num">{i.avg_cost == null ? <span className="muted">–</span> : <>{unitCost(i.avg_cost)}<span className="muted small">/{i.unit ?? "each"}</span></>}</td>
                    <td data-label="Value" className="num">{i.avg_cost == null ? "–" : money(i.value_cents)}</td>
                    <td className="num inv-actions">
                      {i.is_prep && <button className={`btn small ${i.has_recipe ? "" : "primary"}`} onClick={() => setRecipeFor(i.id)}>{i.has_recipe ? "Recipe" : "+ Recipe"}</button>}
                      {i.is_prep && i.has_recipe && <button className="btn small" onClick={() => setMaking(i)}>Made a batch</button>}
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
              : conditions.length ? "No items match these filters." : "No items match."}
          </p>
        )}
      </div>

      {editing && (
        <StockItemDialog base={base} item={editing === "new" ? null : editing} data={data}
          suppliers={suppliers.data ?? []}
          onClose={() => setEditing(null)}
          onDone={(m) => { setEditing(null); setNotice(m); void reload(); }} />
      )}
      {recipeFor && (
        <RecipeEditor base={base} itemId={recipeFor} stock={data.items} onClose={() => setRecipeFor(null)}
          onSaved={(m) => { setRecipeFor(null); setNotice(m); void reload(); }} />
      )}
      {making && making.batch_qty && (
        <MakeDialog base={base} prep={{ id: making.id, name: making.name, unit: making.unit, batch_qty: making.batch_qty }}
          onClose={() => setMaking(null)} onDone={(m) => { setMaking(null); setNotice(m); void reload(); }} />
      )}
    </div>
  );
}

const weekday = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" });

/** Add an ingredient, supply or prep item, or change one. Grouped: details, yield, supplier, restock. */
function StockItemDialog({ base, item, data, suppliers, onClose, onDone }: {
  base: string; item: StockItem | null; data: OnHand; suppliers: Supplier[];
  onClose: () => void; onDone: (message: string) => void;
}) {
  const [f, setF] = useState({
    name: item?.name ?? "", unit: normUnit(item?.unit) || "", category: item?.category ?? "",
    cost: item?.avg_cost != null && !item.is_prep ? (item.avg_cost / 100).toFixed(item.avg_cost < 100 ? 4 : 2) : "",
    quantity: "", reorderAt: item?.reorder_point != null ? String(item.reorder_point) : "",
    supplierId: item?.supplier_id ?? "",
    yieldPct: item?.default_yield_pct != null ? String(item.default_yield_pct) : "",
    prep: item?.is_prep ?? false, batch: item?.batch_qty != null ? String(item.batch_qty) : "",
    safety: item?.safety_days != null ? String(item.safety_days) : "",
  });
  const [advanced, setAdvanced] = useState(!!(item?.reorder_point != null || item?.safety_days != null));
  const [yieldTest, setYieldTest] = useState<{ ap: string; ep: string; cooked: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }));
  const unitShown = f.unit || "unit";
  const units = Array.from(new Set([...(f.unit ? [f.unit] : []), ...data.units]));

  const ap = num(yieldTest?.ap ?? ""), ep = num(yieldTest?.ep ?? ""), cooked = num(yieldTest?.cooked ?? "");
  const tested = ap && ap > 0 ? Math.round((((cooked ?? ep) ?? 0) / ap) * 1000) / 10 : null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const costCents = dollarsToCents(f.cost);
    if (f.cost.trim() && costCents == null) return setError("Enter the cost like 12.00.");
    const rpN = num(f.reorderAt), qtyN = num(f.quantity);
    const yieldN = num(f.yieldPct), batchN = num(f.batch);
    if (f.reorderAt.trim() && (rpN == null || rpN < 0)) return setError("Reorder point must be a number.");
    if (f.yieldPct.trim() && (yieldN == null || yieldN < 1 || yieldN > 100)) return setError("Yield is a % between 1 and 100.");
    if (f.prep && (batchN == null || batchN <= 0)) return setError("Say how much one batch makes.");
    const safetyN = num(f.safety);
    if (f.safety.trim() && (safetyN == null || safetyN < 0 || safetyN > 60)) return setError("Safety margin is a number of days.");
    const common = {
      unit: f.unit || null, category: f.category.trim() || null,
      reorder_point: rpN, supplier_id: f.supplierId || null, default_yield_pct: yieldN, safety_days: safetyN,
    };
    setBusy(true);
    try {
      if (!item) {
        if (!f.name.trim()) throw new Error("Give the item a name.");
        if (f.quantity.trim() && (qtyN == null || qtyN < 0)) throw new Error("On hand must be a number.");
        await api(`${base}/inventory/items`, { method: "POST", body: {
          ...common, name: f.name.trim(), unit_cost: f.prep ? null : costCents, qty: qtyN, batch_qty: f.prep ? batchN : null } });
        onDone(`${f.name.trim()} added to inventory.${f.prep ? " Add its recipe with its Recipe button." : ""}`);
        return;
      }
      await api(`${base}/inventory/items/${item.id}`, { method: "PUT", body: {
        ...common, ...(item.editable_name && f.name.trim() !== item.name ? { name: f.name.trim() } : {}),
        ...(costCents != null && !f.prep ? { unit_cost: costCents } : {}), batch_qty: f.prep ? batchN : 0 } });
      onDone(`${item.name} saved.`);
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't save.");
      setBusy(false);
    }
  }

  async function startCounting() {
    if (!item) return;
    await api(`${base}/inventory/items/${item.id}`, { method: "PUT", body: { track: true } });
    onDone(`Counting ${item.name} again. Set how much you have with Edit, or count it on the Count tab.`);
  }

  async function stopCounting() {
    if (!item || !window.confirm(`Stop counting ${item.name}? It stays in the list as "not counted", and its past changes stay in History.`)) return;
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
        {item && item.stock_qty == null && !item.is_prep && <button type="button" className="btn" onClick={() => void startCounting()}>Start counting</button>}
        <span style={{ flex: 1 }} />
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="stock-item-form" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>}>
      <form id="stock-item-form" className="stack" onSubmit={save}>
          {field("Name",
            <input className="input" value={f.name} maxLength={300} autoFocus={!item} disabled={!!item && !item.editable_name}
              onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Salmon fillet" />,
            item && !item.editable_name ? "From your POS: rename it on the Menu items tab." : undefined)}
          {field("Category", <><input className="input" list="inv-cats" value={f.category} onChange={(e) => set({ category: e.target.value })} placeholder="Produce, Protein…" />
            <datalist id="inv-cats">{data.categories.map((c) => <option key={c} value={c} />)}</datalist></>)}
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

        {field("Usual yield %", <div className="inv-amount">
          <input className="input" inputMode="decimal" value={f.yieldPct} onChange={(e) => set({ yieldPct: e.target.value })} placeholder="100" />
          <button type="button" className="btn small" onClick={() => setYieldTest(yieldTest ? null : { ap: "", ep: "", cooked: "" })}>
            {yieldTest ? "Close" : "Work it out"}
          </button>
        </div>, "The share that's usable after trimming and cooking. Recipes use it.")}
        {yieldTest && (
          <div className="inv-yield">
            <div className="inv-form-row">
              {field("As bought", <input className="input" inputMode="decimal" value={yieldTest.ap} onChange={(e) => setYieldTest({ ...yieldTest, ap: e.target.value })} placeholder="10" />)}
              {field("After trimming", <input className="input" inputMode="decimal" value={yieldTest.ep} onChange={(e) => setYieldTest({ ...yieldTest, ep: e.target.value })} placeholder="8.5" />)}
              {field("After cooking", <input className="input" inputMode="decimal" value={yieldTest.cooked} onChange={(e) => setYieldTest({ ...yieldTest, cooked: e.target.value })} placeholder="optional" />)}
            </div>
            <div className="spread small">
              <span>{tested != null ? <strong>Yield {tested}%</strong> : "Weigh it the same way each time."}</span>
              <button type="button" className="btn small primary" disabled={tested == null || tested <= 0 || tested > 100}
                onClick={() => { set({ yieldPct: String(tested) }); setYieldTest(null); }}>Use {tested ?? "–"}%</button>
            </div>
          </div>
        )}
        {field("Supplier", <select className="select" value={f.supplierId} onChange={(e) => set({ supplierId: e.target.value })}>
          <option value="">No supplier</option>
          {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>)}

        <details className="inv-advanced" open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
          <summary>Advanced</summary>
          <div className="inv-form-row">
            {field("Reorder point", <input className="input" inputMode="decimal" value={f.reorderAt} onChange={(e) => set({ reorderAt: e.target.value })}
              placeholder={item?.restock?.reorder_point != null ? `Suggested ${qty(item.restock.reorder_point)}` : "e.g. 15"} />,
              "Alert when stock gets down to this. Pass also warns from how fast it's used, so you can leave it empty.")}
            {field("Safety margin (days)", <input className="input" inputMode="decimal" value={f.safety} onChange={(e) => set({ safety: e.target.value })}
              placeholder={`${data.safety_days ?? 2} (default)`} />, "Extra days of stock kept for surprises: a busy night, a late delivery")}
          </div>
          {item?.restock && (
            <p className="small muted" style={{ margin: 0 }}>
              Uses about <strong>{qty(item.restock.usage)} {unitShown}</strong> a day · lasts {item.restock.lasts_days} days
              {item.restock.next_delivery && <> · next delivery {weekday(item.restock.next_delivery)}{item.restock.ready_on && item.restock.ready_on !== item.restock.next_delivery ? `, ready ${weekday(item.restock.ready_on)}` : ""}</>}
            </p>
          )}
        </details>
        {error && <p className="small error">{error}</p>}
      </form>
    </Dialog>
  );
}

