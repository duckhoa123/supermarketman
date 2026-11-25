import { useMemo, useState } from "react";
import { ItemDialog, Dialog } from "../../components/PosDialogs";
import { pctClass, RecipeEditor } from "../../components/RecipeEditor";
import { useApp } from "../../context";
import MenuImport from "../../components/MenuImport";
import { useApi } from "../../hooks";
import { api, type InventoryItem } from "../../lib/api";
import { money, qty, type MenuCost, type OnHand, type RecipeReport } from "../../lib/inventory";

type SortKey = "name" | "price" | "plate" | "pct" | "margin" | "sold" | "change";
type Filter = "all" | "over" | "none";

/** What you sell: every dish with its price, recipe, plate cost, food cost % and margin. Add, edit and delete
 * dishes (in Clover too, when it's connected), and open each dish's recipe. */
export default function MenuItems() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const report = useApi<RecipeReport>(`${base}/recipes`);
  const inv = useApi<{ items: InventoryItem[] }>(`${base}/inventory`);
  const onHand = useApi<OnHand>(`${base}/inventory/on-hand`);
  const pos = useApi<{ pos: string | null }>(`${base}/pos-write`);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "sold", desc: true });
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<InventoryItem | "new" | null>(null);
  const [recipeFor, setRecipeFor] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const [importing, setImporting] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState("");

  const byId = useMemo(() => Object.fromEntries((inv.data?.items ?? []).map((i) => [i.id, i])), [inv.data]);
  const categories = useMemo(() => Array.from(new Set((inv.data?.items ?? []).map((i) => i.category).filter(Boolean) as string[])).sort(), [inv.data]);
  const rows = useMemo(() => {
    const val = (r: MenuCost): number | string => ({
      name: r.name.toLowerCase(), price: r.price_cents, plate: r.plate_cost_cents ?? -1, pct: r.food_cost_pct ?? -1,
      margin: r.margin_cents ?? -1, sold: filter === "none" ? r.revenue_30d_cents : r.sold_30d,
      change: r.previous_cost_cents != null && r.plate_cost_cents != null ? r.plate_cost_cents - r.previous_cost_cents : -1e9,
    })[sort.key];
    const q = search.trim().toLowerCase();
    return (report.data?.menu ?? [])
      .filter((r) => (filter === "all" || (filter === "over" ? r.over_target : !r.has_recipe)) && (!q || r.name.toLowerCase().includes(q)))
      .sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1); });
  }, [report.data, sort, filter, search]);

  async function reload() {
    await Promise.all([report.reload(), inv.reload(), onHand.reload()]);
  }

  async function remove(list: InventoryItem[]) {
    if (!list.length) return;
    const p = pos.data?.pos;
    const fromPos = p ? list.filter((i) => i.provider === p).length : 0;
    const what = list.length === 1 ? `“${list[0].name}”` : `${list.length} items`;
    const where = fromPos ? ` They're deleted in ${p![0].toUpperCase()}${p!.slice(1)} too.` : "";
    if (!window.confirm(`Delete ${what}?${where} Past sales keep their history. This can't be undone.`)) return;
    setDeleting(true);
    try {
      const r = await api<{ deleted: number; failed: { name: string; error: string }[] }>(
        `${base}/items/delete`, { method: "POST", body: { ids: list.map((i) => i.id) } });
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

  if (report.error) return <p className="error">{report.error}</p>;
  if (!report.data) return <p className="muted">Loading…</p>;
  const d = report.data;
  const noRecipe = d.menu.filter((r) => !r.has_recipe).length;
  const th = (key: SortKey, label: string, num = true) => (
    <th className={`${num ? "num" : ""} sortable`} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}
      onClick={() => setSort({ key, desc: sort.key === key ? !sort.desc : key !== "name" })}>
      {label}{sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
    </th>
  );
  const allPicked = rows.length > 0 && rows.every((r) => picked.has(r.id));

  return (
    <div className="stack">
      <div className="figures">
        <div className="figure"><div className="label">Dishes with a recipe</div><div className="value">{d.with_recipe}<span className="small muted"> / {d.menu.length}</span></div>
          <div className="small muted">{d.todo.length ? `${d.todo.length} selling dish${d.todo.length === 1 ? "" : "es"} without one` : "every seller is costed"}</div></div>
        <div className="figure"><div className="label">Theoretical food cost</div>
          <div className={`value ${pctClass(d.theoretical_pct, d.target_food_cost_pct)}`}>{d.theoretical_pct != null ? `${d.theoretical_pct}%` : "–"}</div>
          <div className="small muted">target {d.target_food_cost_pct}% · last 30 days' sales</div></div>
        <div className="figure"><div className="label">Over target</div><div className={`value ${d.over_target ? "error" : ""}`}>{d.over_target}</div>
          <div className="small muted">dishes above their target %</div></div>
      </div>
      {notice && <p className="notice small">{notice}</p>}
      {importing && (
        <MenuImport base={base} stock={onHand.data?.items ?? []} onClose={() => { setImporting(false); void reload(); }}
          onDone={(message) => { setImporting(false); setNotice(message); void reload(); }} />
      )}

      <div className="spread inv-toolbar">
        <div className="chips" role="group" aria-label="Show">
          <button className={filter === "all" ? "chip on" : "chip"} onClick={() => setFilter("all")}>All <b>{d.menu.length}</b></button>
          <button className={filter === "over" ? "chip on" : "chip"} onClick={() => setFilter("over")}>Over target <b>{d.over_target}</b></button>
          <button className={filter === "none" ? "chip on" : "chip"} onClick={() => { setFilter("none"); setSort({ key: "sold", desc: true }); }}>No recipe <b>{noRecipe}</b></button>
        </div>
        <div className="row">
          <input className="input" style={{ width: 170 }} placeholder="Search dishes" value={search} onChange={(e) => setSearch(e.target.value)} />
          {picked.size > 0 && (
            <button className="btn danger" disabled={deleting}
              onClick={() => void remove(Array.from(picked).map((id) => byId[id]).filter(Boolean))}>
              {deleting ? "Deleting…" : `Delete ${picked.size} selected`}
            </button>
          )}
          <button className="btn" onClick={() => setSettings(true)} title="Food cost targets and how ingredients are costed">⚙ Cost settings</button>
          <button className="btn" onClick={() => setImporting(true)} title="Add new menu items and their recipes from your menu">✨ Import menu</button>
          <button className="btn primary" onClick={() => setEditing("new")}>+ Add item</button>
        </div>
      </div>

      <div className="panel table-wrap inv-table-wrap">
        <table className="data inv-cost-table">
          <thead><tr>
            <th className="sortable" style={{ width: 28 }}>
              <input type="checkbox" aria-label="Select all dishes" checked={allPicked}
                onChange={(e) => setPicked(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())} />
            </th>
            {th("name", "Dish", false)}{th("price", "Price")}{th("plate", "Plate cost")}{th("pct", "Food cost")}
            <th className="num">Suggested</th>{th("margin", "Margin")}{th("change", "Change")}{th("sold", filter === "none" ? "Sales 30d" : "Sold 30d")}<th />
          </tr></thead>
          <tbody>
            {rows.map((r) => {
              const item = byId[r.id];
              return (
                <tr key={r.id} className={`${r.over_target ? "inv-over" : ""} ${picked.has(r.id) ? "picked" : ""}`}>
                  <td className="menu-pick">
                    <input type="checkbox" aria-label={`Select ${r.name}`} checked={picked.has(r.id)}
                      onChange={(e) => setPicked((p) => { const n = new Set(p); if (e.target.checked) n.add(r.id); else n.delete(r.id); return n; })} />
                  </td>
                  <td data-label="Dish">
                    <strong>{r.name}</strong>{item?.provider === "clover" && <span className="pill" style={{ marginLeft: 6 }}>Clover</span>}
                    {r.category && <div className="small muted">{r.category}</div>}
                  </td>
                  <td data-label="Price" className="num">{money(r.price_cents)}</td>
                  <td data-label="Plate cost" className={`num ${r.has_recipe ? "" : "m-hide"}`}>{r.has_recipe ? <>{money(r.plate_cost_cents)}{!r.complete && <span className="pill warn tip tip-left" tabIndex={0} data-tip="Some ingredients have no cost yet" style={{ marginLeft: 4, textDecoration: "none" }}>!</span>}</> : <span className="muted">–</span>}</td>
                  <td data-label="Food cost" className={`num ${pctClass(r.food_cost_pct, r.target_pct)} ${r.has_recipe ? "" : "m-hide"}`}>
                    {r.food_cost_pct != null ? <><strong>{r.food_cost_pct}%</strong> <span className="small muted">/ {r.target_pct}%</span></> : "–"}
                  </td>
                  <td data-label="Suggested" className={`num ${r.over_target ? "" : "m-hide"}`}>{r.over_target ? money(r.suggested_price_cents) : <span className="muted">–</span>}</td>
                  <td data-label="Margin" className={`num ${r.has_recipe ? "" : "m-hide"}`}>{money(r.margin_cents)}</td>
                  <td data-label="Change" className={`num ${r.previous_cost_cents != null ? "" : "m-hide"}`}>
                    {r.previous_cost_cents != null && r.plate_cost_cents != null ? (
                      <span className={`tip tip-left ${r.plate_cost_cents > r.previous_cost_cents ? "error" : "good-text"}`} tabIndex={0}
                        data-tip={`Plate cost was ${money(r.previous_cost_cents)}${r.previous_at ? ` until ${new Date(r.previous_at).toLocaleDateString()}` : ""}`}>
                        {r.plate_cost_cents > r.previous_cost_cents ? "+" : "−"}{money(Math.abs(r.plate_cost_cents - r.previous_cost_cents))}
                      </span>
                    ) : <span className="muted">–</span>}
                  </td>
                  <td data-label={filter === "none" ? "Sales 30d" : "Sold 30d"} className="num">{filter === "none" ? money(r.revenue_30d_cents, 0) : qty(r.sold_30d)}</td>
                  <td className="num inv-actions">
                    <button className={`btn small ${r.has_recipe ? "" : "primary"}`} onClick={() => setRecipeFor(r.id)}>{r.has_recipe ? "Recipe" : "+ Recipe"}</button>
                    {item && <button className="btn small" onClick={() => setEditing(item)}>Edit</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="empty">{d.menu.length ? (filter === "none" ? "Every dish has a recipe." : "No dishes match.") : "No menu items with a price yet. Add one, or connect Clover in Settings."}</p>
        )}
      </div>

      {editing && (
        <ItemDialog locationId={location.id} item={editing === "new" ? null : editing} categories={categories}
          pos={pos.data?.pos ?? null} onClose={() => setEditing(null)} onDelete={(i) => void remove([i])}
          onDone={(m) => { setEditing(null); setNotice(m); void reload(); }} />
      )}
      {recipeFor && onHand.data && (
        <RecipeEditor base={base} itemId={recipeFor} stock={onHand.data.items} onClose={() => setRecipeFor(null)}
          onSaved={(m) => { setRecipeFor(null); setNotice(m); void reload(); }} />
      )}
      {settings && (
        <CostSettings base={base} food={d.target_food_cost_pct} drinks={d.target_beverage_cost_pct}
          method={onHand.data?.cost_method ?? "average"} onClose={() => setSettings(false)}
          onDone={() => { setSettings(false); void reload(); }} />
      )}
    </div>
  );
}

function CostSettings({ base, food, drinks, method, onClose, onDone }: {
  base: string; food: number; drinks: number; method: "average" | "last"; onClose: () => void; onDone: () => void;
}) {
  const [f, setF] = useState({ food: String(food), drinks: String(drinks), method });
  const [error, setError] = useState("");
  async function save() {
    const a = Number(f.food), b = Number(f.drinks);
    if (!(a >= 5 && a <= 90) || !(b >= 5 && b <= 90)) return setError("Targets are a % between 5 and 90.");
    try {
      await api(`${base}/inventory/settings`, { method: "PUT", body: { target_food_cost_pct: a, target_beverage_cost_pct: b, cost_method: f.method } });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
    }
  }
  return (
    <Dialog title="Cost settings" onClose={onClose} narrow
      footer={<div className="row" style={{ marginLeft: "auto" }}><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={() => void save()}>Save</button></div>}>
      <label className="spread"><span className="tip" tabIndex={0} data-tip="Food cost % to aim for on food. A dish can have its own target in its recipe.">Food target</span>
        <span className="row" style={{ gap: 4 }}><input className="input num-in" inputMode="decimal" value={f.food} onChange={(e) => setF({ ...f, food: e.target.value })} />%</span></label>
      <label className="spread"><span className="tip" tabIndex={0} data-tip="For dishes in a drinks category (beer, wine, coffee…)">Drinks target</span>
        <span className="row" style={{ gap: 4 }}><input className="input num-in" inputMode="decimal" value={f.drinks} onChange={(e) => setF({ ...f, drinks: e.target.value })} />%</span></label>
      <label className="spread"><span className="tip" tabIndex={0} data-tip="Weighted average mixes each delivery's price into the cost. Latest price uses the last invoice.">Cost ingredients at</span>
        <select className="select" style={{ width: "auto" }} value={f.method} onChange={(e) => setF({ ...f, method: e.target.value as "average" | "last" })}>
          <option value="average">Weighted average</option>
          <option value="last">Latest invoice price</option>
        </select></label>
      {error && <p className="small error">{error}</p>}
    </Dialog>
  );
}
