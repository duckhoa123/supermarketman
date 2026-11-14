import { useMemo, useState } from "react";
import { Dialog } from "../../components/PosDialogs";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api } from "../../lib/api";
import {
  convert, money, normUnit, qty, unitCost, unitsFor, type MenuCost, type OnHand, type PrepCost, type RecipeDetail,
  type RecipeReport, type StockItem,
} from "../../lib/inventory";

type SortKey = "name" | "price" | "plate" | "pct" | "margin" | "sold" | "change";
type Filter = "all" | "over" | "none";

const pctClass = (pct: number | null, target: number) => pct == null ? "" : pct > target + 0.05 ? "error" : "good-text";

export default function Recipes() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const report = useApi<RecipeReport>(`${base}/recipes`);
  const onHand = useApi<OnHand>(`${base}/inventory/on-hand`);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "sold", desc: true });
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [making, setMaking] = useState<PrepCost | null>(null);
  const [notice, setNotice] = useState("");

  const rows = useMemo(() => {
    const val = (r: MenuCost): number | string => ({
      name: r.name.toLowerCase(), price: r.price_cents, plate: r.plate_cost_cents ?? -1, pct: r.food_cost_pct ?? -1,
      margin: r.margin_cents ?? -1, sold: r.sold_30d,
      change: r.previous_cost_cents != null && r.plate_cost_cents != null ? r.plate_cost_cents - r.previous_cost_cents : -1e9,
    })[sort.key];
    const q = search.trim().toLowerCase();
    return (report.data?.menu ?? [])
      .filter((r) => (filter === "all" || (filter === "over" ? r.over_target : !r.has_recipe)) && (!q || r.name.toLowerCase().includes(q)))
      .sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * (sort.desc ? -1 : 1); });
  }, [report.data, sort, filter, search]);

  async function setting(body: Record<string, unknown>) {
    await api(`${base}/inventory/settings`, { method: "PUT", body });
    await Promise.all([report.reload(), onHand.reload()]);
  }

  if (report.error) return <p className="error">{report.error}</p>;
  if (!report.data || !onHand.data) return <p className="muted">Loading…</p>;
  const d = report.data;
  const th = (key: SortKey, label: string, num = true) => (
    <th className={`${num ? "num" : ""} sortable`} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}
      onClick={() => setSort({ key, desc: sort.key === key ? !sort.desc : key !== "name" })}>
      {label}{sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <div className="stack">
      <div className="figures">
        <div className="figure"><div className="label">Dishes with a recipe</div><div className="value">{d.with_recipe}<span className="small muted"> / {d.menu.length}</span></div>
          <div className="small muted">{d.todo.length ? `${d.todo.length} selling dish${d.todo.length === 1 ? "" : "es"} without one` : "every seller is costed"}</div></div>
        <div className="figure"><div className="label">Theoretical food cost</div>
          <div className={`value ${pctClass(d.theoretical_pct, d.target_food_cost_pct)}`}>{d.theoretical_pct != null ? `${d.theoretical_pct}%` : "–"}</div>
          <div className="small muted">what the recipes say, weighted by last 30 days' sales</div></div>
        <div className="figure"><div className="label">Over target</div><div className={`value ${d.over_target ? "error" : ""}`}>{d.over_target}</div>
          <div className="small muted">dishes above their target %</div></div>
      </div>

      <div className="panel inv-settings">
        <label className="row small">Food target
          <input className="input num-in" type="number" min={5} max={90} step={0.5} defaultValue={d.target_food_cost_pct}
            onBlur={(e) => Number(e.target.value) !== d.target_food_cost_pct && void setting({ target_food_cost_pct: Number(e.target.value) })} />%
        </label>
        <label className="row small">Drinks target
          <input className="input num-in" type="number" min={5} max={90} step={0.5} defaultValue={d.target_beverage_cost_pct}
            onBlur={(e) => Number(e.target.value) !== d.target_beverage_cost_pct && void setting({ target_beverage_cost_pct: Number(e.target.value) })} />%
        </label>
        <label className="row small">Cost ingredients at
          <select className="select" style={{ width: "auto" }} value={onHand.data.cost_method} onChange={(e) => void setting({ cost_method: e.target.value })}>
            <option value="average">Weighted average</option>
            <option value="last">Latest invoice price</option>
          </select>
        </label>
      </div>

      {notice && <p className="notice small">{notice}</p>}

      {d.todo.length > 0 && (
        <div className="panel stack" style={{ gap: 8 }}>
          <div>
            <h2 style={{ margin: 0 }}>Recipes to do</h2>
            <p className="small muted">These sell but have no recipe, so their cost and the ingredients they use aren't counted. Best sellers first.</p>
          </div>
          <div className="inv-todo">
            {d.todo.slice(0, 12).map((r) => (
              <button key={r.id} className="inv-todo-item" onClick={() => setEditing(r.id)}>
                <strong>{r.name}</strong><span className="small muted">{qty(r.sold_30d)} sold · {money(r.revenue_30d_cents, 0)}</span>
                <span className="small linkish">+ Add recipe</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="panel stack">
        <div className="spread">
          <h2 style={{ margin: 0 }}>Menu cost report</h2>
          <div className="row">
            <input className="input" style={{ width: 160 }} placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
            <div className="seg">
              {(["all", "over", "none"] as Filter[]).map((f) => (
                <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{f === "all" ? "All" : f === "over" ? "Over target" : "No recipe"}</button>
              ))}
            </div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data inv-cost-table">
            <thead><tr>{th("name", "Dish", false)}{th("price", "Price")}{th("plate", "Plate cost")}{th("pct", "Food cost")}
              <th className="num">Suggested</th>{th("margin", "Margin")}{th("change", "Change")}{th("sold", "Sold 30d")}<th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={r.over_target ? "inv-over" : ""}>
                  <td data-label="Dish"><strong>{r.name}</strong>{r.category && <div className="small muted">{r.category}</div>}</td>
                  <td data-label="Price" className="num">{money(r.price_cents)}</td>
                  <td data-label="Plate cost" className="num">{r.has_recipe ? <>{money(r.plate_cost_cents)}{!r.complete && <span className="pill warn" title="Some ingredients have no cost" style={{ marginLeft: 4 }}>!</span>}</> : <span className="muted">–</span>}</td>
                  <td data-label="Food cost" className={`num ${pctClass(r.food_cost_pct, r.target_pct)}`}>
                    {r.food_cost_pct != null ? <><strong>{r.food_cost_pct}%</strong> <span className="small muted">/ {r.target_pct}%</span></> : "–"}
                  </td>
                  <td data-label="Suggested" className="num">{r.over_target ? money(r.suggested_price_cents) : <span className="muted">{r.has_recipe ? "–" : ""}</span>}</td>
                  <td data-label="Margin" className="num">{money(r.margin_cents)}</td>
                  <td data-label="Change" className="num">
                    {r.previous_cost_cents != null && r.plate_cost_cents != null ? (
                      <span className={r.plate_cost_cents > r.previous_cost_cents ? "error" : "good-text"}
                        title={`Was ${money(r.previous_cost_cents)}${r.previous_at ? ` until ${new Date(r.previous_at).toLocaleDateString()}` : ""}`}>
                        {r.plate_cost_cents > r.previous_cost_cents ? "+" : "−"}{money(Math.abs(r.plate_cost_cents - r.previous_cost_cents))}
                      </span>
                    ) : <span className="muted">–</span>}
                  </td>
                  <td data-label="Sold 30d" className="num">{qty(r.sold_30d)}</td>
                  <td className="num"><button className="btn small" onClick={() => setEditing(r.id)}>{r.has_recipe ? "Recipe" : "+ Recipe"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p className="empty">{d.menu.length ? "No dishes match." : "No menu items with a price yet."}</p>}
        </div>
      </div>

      <div className="panel stack">
        <div>
          <h2 style={{ margin: 0 }}>Prep items</h2>
          <p className="small muted">Sauces, doughs, stocks: made in batches and used in other recipes. Add one on the On hand tab (tick “a prep item”).</p>
        </div>
        {d.prep.length === 0 ? <p className="small muted">No prep items yet.</p> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Prep item</th><th className="num">One batch</th><th className="num">Batch cost</th><th className="num">Cost</th><th className="num">On hand</th><th /></tr></thead>
              <tbody>
                {d.prep.map((p) => (
                  <tr key={p.id}>
                    <td data-label="Prep item"><strong>{p.name}</strong>{!p.has_recipe && <div className="small warn-text">No recipe yet</div>}</td>
                    <td data-label="One batch" className="num">{qty(p.batch_qty)} {p.unit ?? ""}</td>
                    <td data-label="Batch cost" className="num">{money(p.batch_cost_cents)}</td>
                    <td data-label="Cost" className="num">{p.unit_cost != null ? <>{unitCost(p.unit_cost)}<span className="small muted">/{p.unit ?? "each"}</span></> : "–"}</td>
                    <td data-label="On hand" className="num">{p.stock_qty != null ? `${qty(p.stock_qty)} ${p.unit ?? ""}` : <span className="muted small">not kept: sales use its ingredients</span>}</td>
                    <td className="num inv-actions">
                      <button className="btn small" onClick={() => setEditing(p.id)}>Recipe</button>
                      {p.has_recipe && <button className="btn small" onClick={() => setMaking(p)}>Made a batch</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editing && (
        <RecipeEditor base={base} itemId={editing} stock={onHand.data.items} units={onHand.data.units}
          onClose={() => setEditing(null)}
          onSaved={(m) => { setEditing(null); setNotice(m); void report.reload(); void onHand.reload(); }}
          onIngredientAdded={() => void onHand.reload()} />
      )}
      {making && (
        <MakeDialog base={base} prep={making} onClose={() => setMaking(null)}
          onDone={(m) => { setMaking(null); setNotice(m); void report.reload(); void onHand.reload(); }} />
      )}
    </div>
  );
}

type Line = { key: string; ingredient_id: string; qty: string; unit: string; yield_pct: string };

function RecipeEditor({ base, itemId, stock, units, onClose, onSaved, onIngredientAdded }: {
  base: string; itemId: string; stock: StockItem[]; units: string[]; onClose: () => void; onSaved: (m: string) => void;
  onIngredientAdded: () => void;
}) {
  const detail = useApi<RecipeDetail>(`${base}/recipes/${itemId}`);
  const [lines, setLines] = useState<Line[] | null>(null);
  const [q, setQ] = useState<{ mode: "cents" | "pct"; value: string } | null>(null);
  const [price, setPrice] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [adding, setAdding] = useState<{ name: string; unit: string; cost: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const d = detail.data;
  const byId = useMemo(() => Object.fromEntries(stock.map((s) => [s.id, s])), [stock]);
  const choices = useMemo(() => stock.filter((s) => s.id !== itemId).sort((a, b) => Number(b.is_prep) - Number(a.is_prep) || a.name.localeCompare(b.name)), [stock, itemId]);

  // Start the form from the saved recipe once it loads.
  if (d && lines === null) {
    setLines(d.lines.map((l) => ({ key: l.id, ingredient_id: l.ingredient_id, qty: String(l.qty), unit: normUnit(l.unit) || "each", yield_pct: String(l.yield_pct) })));
    setQ(d.q_factor_pct ? { mode: "pct", value: String(d.q_factor_pct) } : { mode: "cents", value: d.q_factor_cents ? (d.q_factor_cents / 100).toFixed(2) : "" });
    setPrice((d.price_cents / 100).toFixed(2));
    setTarget(d.item_target_pct != null ? String(d.item_target_pct) : "");
    setNotes(d.recipe_notes ?? "");
  }

  // Live costing while editing: the same formulas as the server (EP ÷ yield × cost), before saving.
  const live = useMemo(() => {
    const out = (lines ?? []).map((l) => {
      const ing = byId[l.ingredient_id];
      const n = Number(l.qty), y = Number(l.yield_pct) || 100;
      if (!ing || !(n > 0)) return { cost: null as number | null, problem: ing ? null : "Choose an ingredient", stockQty: null as number | null };
      const ep = convert(n, l.unit, ing.unit, ing.conversions);
      if (ep == null) return { cost: null, problem: `Can't turn ${l.unit} into ${ing.unit ?? "each"}: add a conversion to ${ing.name}`, stockQty: null };
      const ap = ep / (Math.max(1, Math.min(100, y)) / 100);
      return { cost: ing.avg_cost != null ? ap * ing.avg_cost : null, problem: ing.avg_cost == null ? `No cost for ${ing.name} yet` : null, stockQty: ap };
    });
    const ingredients = out.reduce((s, x) => s + (x.cost ?? 0), 0);
    const qv = Number(q?.value || 0);
    const qCents = q?.mode === "pct" ? ingredients * qv / 100 : qv * 100;
    const plate = ingredients + qCents;
    const p = Math.round(Number(price || 0) * 100);
    const t = Number(target) || d?.target_food_cost_pct || 30;
    return { out, ingredients, qCents, plate, price: p, target: t, pct: p > 0 ? (plate / p) * 100 : null };
  }, [lines, byId, q, price, target, d]);

  function setLine(i: number, patch: Partial<Line>) {
    setLines((ls) => (ls ?? []).map((l, j) => (j === i ? { ...l, ...patch } : l)));
  }

  async function addIngredient() {
    if (!adding?.name.trim()) return;
    try {
      const cost = Number(adding.cost.replace(/[$,\s]/g, ""));
      const r = await api<{ id: string }>(`${base}/inventory/items`, { method: "POST", body: {
        name: adding.name.trim(), unit: adding.unit || null, unit_cost: adding.cost.trim() && !Number.isNaN(cost) ? cost * 100 : null } });
      onIngredientAdded();
      setLines((ls) => [...(ls ?? []), { key: r.id + Date.now(), ingredient_id: r.id, qty: "", unit: normUnit(adding.unit) || "each", yield_pct: "100" }]);
      setAdding(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add the ingredient.");
    }
  }

  async function save() {
    if (!d || !lines) return;
    const clean = lines.filter((l) => l.ingredient_id && Number(l.qty) > 0);
    setBusy(true);
    setError("");
    try {
      const priceCents = Math.round(Number(price || 0) * 100);
      await api<RecipeDetail>(`${base}/recipes/${itemId}`, { method: "PUT", body: {
        lines: clean.map((l) => ({ ingredient_id: l.ingredient_id, qty: Number(l.qty), unit: l.unit, yield_pct: Number(l.yield_pct) || 100 })),
        q_factor_cents: q?.mode === "cents" && q.value ? Math.round(Number(q.value) * 100) : null,
        q_factor_pct: q?.mode === "pct" && q.value ? Number(q.value) : null,
        recipe_notes: notes, target_food_cost_pct: target ? Number(target) : null,
        ...(canPrice && priceCents !== d.price_cents ? { price_cents: priceCents } : {}),
      } });
      onSaved(`${d.name}: recipe saved.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the recipe.");
      setBusy(false);
    }
  }

  function print() {
    if (!d || !lines) return;
    const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
    const rowsHtml = lines.filter((l) => byId[l.ingredient_id]).map((l) =>
      `<tr><td>${esc(qty(Number(l.qty)))} ${esc(l.unit)}</td><td>${esc(byId[l.ingredient_id].name)}</td><td>${Number(l.yield_pct) !== 100 ? `${esc(l.yield_pct)}% yield` : ""}</td></tr>`).join("");
    const w = window.open("", "_blank", "width=720,height=900");
    if (!w) return setError("Allow pop-ups to print the recipe card.");
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(d.name)}</title><style>
      body{font-family:system-ui,sans-serif;margin:32px;color:#111} h1{margin:0 0 4px} .muted{color:#555}
      table{width:100%;border-collapse:collapse;margin:16px 0} td{padding:8px;border-bottom:1px solid #ddd;font-size:15px}
      td:first-child{white-space:nowrap;font-weight:600;width:1%} pre{white-space:pre-wrap;font:inherit;font-size:15px;line-height:1.5}
      </style></head><body><h1>${esc(d.name)}</h1><div class="muted">${d.is_prep ? `One batch makes ${qty(d.batch_qty)} ${esc(d.unit ?? "")}` : esc(d.category ?? "")}</div>
      <table>${rowsHtml}</table>${notes ? `<h3>Method and plating</h3><pre>${esc(notes)}</pre>` : ""}
      <p class="muted" style="margin-top:32px;font-size:12px">Printed ${new Date().toLocaleDateString()}</p></body></html>`);
    w.document.close();
    w.focus();
    w.print();
  }

  const canPrice = d ? !d.is_prep && ["pass", "demo"].includes(d.provider) : false;
  return (
    <Dialog title={d ? `${d.is_prep ? "Prep recipe" : "Recipe"}: ${d.name}` : "Recipe"} onClose={onClose} width="min(980px, 100%)"
      footer={<div className="row" style={{ width: "100%" }}>
        <button className="btn" onClick={print} disabled={!d}>Print card</button>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !d} onClick={() => void save()}>{busy ? "Saving…" : "Save recipe"}</button>
      </div>}>
      {!d || !lines ? <p className="muted">Loading…</p> : (
        <div className="rec-grid">
          <section className="stack" style={{ gap: 10, minWidth: 0 }}>
            <p className="small muted" style={{ margin: 0 }}>
              {d.is_prep ? `One batch makes ${qty(d.batch_qty)} ${d.unit ?? ""}. List what goes into one batch.`
                : "Each ingredient as it goes on the plate (edible portion), and the yield of the ingredient as bought."}
            </p>
            <div className="rec-lines">
              <div className="rec-line rec-head small muted"><span>Ingredient</span><span>Portion</span><span>Unit</span><span>Yield %</span><span className="num">Cost</span><span /></div>
              {lines.map((l, i) => {
                const ing = byId[l.ingredient_id];
                const c = live.out[i];
                return (
                  <div key={l.key} className="rec-line-wrap">
                    <div className="rec-line">
                      <select className="select" aria-label="Ingredient" value={l.ingredient_id} onChange={(e) => {
                        const next = byId[e.target.value];
                        setLine(i, { ingredient_id: e.target.value, unit: normUnit(next?.unit) || "each",
                          yield_pct: next?.default_yield_pct != null ? String(next.default_yield_pct) : "100" });
                      }}>
                        <option value="">Choose…</option>
                        {choices.map((s) => <option key={s.id} value={s.id}>{s.is_prep ? "▸ " : ""}{s.name}</option>)}
                      </select>
                      <input className="input" inputMode="decimal" aria-label="Portion" value={l.qty} placeholder="8" onChange={(e) => setLine(i, { qty: e.target.value })} />
                      <select className="select" aria-label="Unit" value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })}>
                        {unitsFor(ing?.unit ?? l.unit, ing?.conversions ?? []).map((u) => <option key={u} value={u}>{u}</option>)}
                      </select>
                      <input className="input" inputMode="decimal" aria-label="Yield %" value={l.yield_pct} onChange={(e) => setLine(i, { yield_pct: e.target.value })} />
                      <span className="num">{c?.cost != null ? money(c.cost) : "–"}</span>
                      <button className="sb-icon" aria-label="Remove ingredient" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>
                    </div>
                    {c?.problem && l.ingredient_id && <small className="warn-text">{c.problem}</small>}
                    {ing && c?.stockQty != null && !c.problem && (
                      <small className="muted">Takes {qty(Math.round(c.stockQty * 1000) / 1000)} {ing.unit ?? "each"} from stock{ing.avg_cost != null ? ` at ${unitCost(ing.avg_cost)}/${ing.unit ?? "each"}` : ""}</small>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="row">
              <button className="btn small" onClick={() => setLines([...lines, { key: `n${Date.now()}`, ingredient_id: "", qty: "", unit: "each", yield_pct: "100" }])}>+ Add an ingredient</button>
              {!adding && <button className="btn small" onClick={() => setAdding({ name: "", unit: "", cost: "" })}>+ New ingredient</button>}
            </div>
            {adding && (
              <div className="rec-new row" style={{ flexWrap: "nowrap" }}>
                <input className="input" placeholder="Name" value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })} autoFocus />
                <input className="input" list="rec-units" placeholder="Unit (lb)" style={{ width: 100 }} value={adding.unit} onChange={(e) => setAdding({ ...adding, unit: e.target.value })} />
                <datalist id="rec-units">{units.map((u) => <option key={u} value={u} />)}</datalist>
                <input className="input" inputMode="decimal" placeholder="$ per unit" style={{ width: 110 }} value={adding.cost} onChange={(e) => setAdding({ ...adding, cost: e.target.value })} />
                <button className="btn small primary" onClick={() => void addIngredient()}>Add</button>
                <button className="sb-icon" aria-label="Cancel" onClick={() => setAdding(null)}>✕</button>
              </div>
            )}
            <label className="stack" style={{ gap: 4 }}><span className="small">Method and plating notes (on the printed card)</span>
              <textarea className="textarea" rows={3} maxLength={5000} value={notes ?? ""} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. Sear skin side down 4 minutes; plate rice left, asparagus fanned right." />
            </label>
          </section>

          <aside className="rec-side stack" style={{ gap: 10 }}>
            <div className="rec-total">
              <div className="spread small"><span>Ingredients</span><span>{money(live.ingredients)}</span></div>
              <div className="spread small">
                <span className="row" style={{ gap: 6 }}>Q-factor
                  <input className="input num-in" inputMode="decimal" aria-label="Q-factor" value={q?.value ?? ""} placeholder="0" onChange={(e) => setQ({ mode: q?.mode ?? "cents", value: e.target.value })} />
                  <select className="select" style={{ width: "auto", padding: "2px 6px" }} aria-label="Q-factor as" value={q?.mode ?? "cents"} onChange={(e) => setQ({ mode: e.target.value as "cents" | "pct", value: q?.value ?? "" })}>
                    <option value="cents">$</option><option value="pct">%</option>
                  </select>
                </span>
                <span>{money(live.qCents)}</span>
              </div>
              <div className="spread rec-plate"><span>{d.is_prep ? "Batch cost" : "Plate cost"}</span><strong>{money(live.plate)}</strong></div>
              {d.is_prep && d.batch_qty ? <div className="spread small"><span>Per {d.unit ?? "unit"}</span><span>{unitCost(live.plate / d.batch_qty)}</span></div> : null}
            </div>
            {!d.is_prep && (
              <div className="rec-total">
                <label className="spread small"><span>{canPrice ? "Menu price" : "Try a price"}</span>
                  <input className="input num-in" style={{ width: 90 }} inputMode="decimal" value={price ?? ""} onChange={(e) => setPrice(e.target.value)} />
                </label>
                {!canPrice && Math.round(Number(price || 0) * 100) !== d.price_cents && (
                  <small className="muted">What-if only: the real price ({money(d.price_cents)}) is set in your POS.</small>
                )}
                <div className="spread"><span>Food cost</span>
                  <strong className={pctClass(live.pct, live.target)}>{live.pct != null ? `${Math.round(live.pct * 10) / 10}%` : "–"}</strong></div>
                <label className="spread small"><span>Target</span>
                  <span className="row" style={{ gap: 4 }}><input className="input num-in" inputMode="decimal" value={target ?? ""} placeholder={String(d.target_food_cost_pct)} onChange={(e) => setTarget(e.target.value)} />%</span>
                </label>
                <div className="spread small"><span>Price for target</span><span>{live.plate > 0 ? money(Math.round(live.plate / (live.target / 100))) : "–"}</span></div>
                <div className="spread small"><span>Margin</span><span>{live.price ? money(live.price - Math.round(live.plate)) : "–"}</span></div>
              </div>
            )}
            {d.history.length > 1 && (
              <div className="rec-total">
                <span className="small muted">Plate cost history</span>
                {d.history.slice(0, 6).map((h, n) => (
                  <div key={n} className="spread small"><span className="muted">{new Date(h.at).toLocaleDateString(undefined, { month: "short", day: "numeric" })} · {h.reason}</span><span>{money(h.plate_cost_cents)}</span></div>
                ))}
              </div>
            )}
            {error && <p className="small error">{error}</p>}
          </aside>
        </div>
      )}
    </Dialog>
  );
}

function MakeDialog({ base, prep, onClose, onDone }: { base: string; prep: PrepCost; onClose: () => void; onDone: (m: string) => void }) {
  const [batches, setBatches] = useState("1");
  const [error, setError] = useState("");
  async function make() {
    const n = Number(batches);
    if (!(n > 0)) return setError("How many batches?");
    try {
      const r = await api<{ made: number; unit: string | null }>(`${base}/inventory/prep/${prep.id}/make`, { method: "POST", body: { batches: n } });
      onDone(`Made ${qty(r.made)} ${r.unit ?? ""} of ${prep.name}. Its ingredients came out of stock.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't record it.");
    }
  }
  return (
    <Dialog title={`Made ${prep.name}`} onClose={onClose} narrow
      footer={<div className="row" style={{ marginLeft: "auto" }}><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={() => void make()}>Record it</button></div>}>
      <label className="row">How many batches?
        <input className="input num-in" inputMode="decimal" value={batches} onChange={(e) => setBatches(e.target.value)} autoFocus />
      </label>
      <p className="small muted">{qty(Number(batches) * prep.batch_qty || 0)} {prep.unit ?? ""} goes into stock, and its ingredients come out. From then on, sales use the {prep.name} you keep.</p>
      {error && <p className="small error">{error}</p>}
    </Dialog>
  );
}
