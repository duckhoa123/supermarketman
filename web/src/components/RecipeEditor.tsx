import { useMemo, useState } from "react";
import { useApi } from "../hooks";
import { api } from "../lib/api";
import { convert, money, normUnit, qty, unitCost, unitsFor, type RecipeDetail, type StockItem } from "../lib/inventory";
import NewPrep from "./NewPrep";
import { Dialog } from "./PosDialogs";

export const pctClass = (pct: number | null, target: number) => pct == null ? "" : pct > target + 0.05 ? "error" : "good-text";

type Line = { key: string; ingredient_id: string; qty: string; unit: string };
type Card = {
  name: string; description: string; category: string; price: string; batch: string; unit: string; minutes: string;
  status: "active" | "development"; steps: string; allergens: string; storage: string;
};
const yieldOf = (i: StockItem | undefined) => i?.default_yield_pct ?? 100;  // set on the inventory item
const PREP_TYPES = ["Broth", "Sauce", "Protein", "Dough", "Vegetable", "Dessert", "Other"];
const showUnit = (u: string) => (u === "l" ? "L" : u);

/** Edit a menu item (one plate) or a prep item (one batch): the card (name, description, type, yield, time, status),
 * the ingredients with live costing, the steps, and allergen and storage notes. */
export function RecipeEditor({ base, itemId, stock, onClose, onSaved, onDelete, categories = [] }: {
  base: string; itemId: string; stock: StockItem[]; onClose: () => void; onSaved: (m: string) => void;
  onDelete?: () => void; categories?: string[];
}) {
  const detail = useApi<RecipeDetail>(`${base}/recipes/${itemId}`);
  const [lines, setLines] = useState<Line[] | null>(null);
  const [card, setCard] = useState<Card | null>(null);
  const [q, setQ] = useState<{ mode: "cents" | "pct"; value: string } | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [extra, setExtra] = useState<StockItem[]>([]);  // prep items created from this recipe
  const [making, setMaking] = useState(false);
  const [nested, setNested] = useState<string | null>(null);  // a prep item's own recipe, opened on top
  const [showCost, setShowCost] = useState(false);
  const d = detail.data;
  const all = useMemo(() => [...stock, ...extra.filter((x) => !stock.some((s) => s.id === x.id))], [stock, extra]);
  const byId = useMemo(() => Object.fromEntries(all.map((s) => [s.id, s])), [all]);
  const choices = useMemo(() => all.filter((s) => s.id !== itemId).sort((a, b) => a.name.localeCompare(b.name)), [all, itemId]);
  const prepChoices = choices.filter((s) => s.is_prep);
  const stockChoices = choices.filter((s) => !s.is_prep);

  // Start the form from what's saved once it loads.
  if (d && lines === null) {
    setLines(d.lines.map((l) => ({ key: l.id, ingredient_id: l.ingredient_id, qty: String(l.qty || ""), unit: normUnit(l.unit) || normUnit(l.stock_unit) || "each" })));
    setCard({
      name: d.name, description: d.description ?? "", category: d.category ?? "", price: (d.price_cents / 100).toFixed(2),
      batch: d.batch_qty != null ? String(d.batch_qty) : "", unit: normUnit(d.unit) || "", minutes: d.prep_minutes != null ? String(d.prep_minutes) : "",
      status: d.recipe_status ?? "active", steps: d.recipe_notes ?? "", allergens: d.allergen_notes ?? "", storage: d.storage_notes ?? "",
    });
    setQ(d.q_factor_pct ? { mode: "pct", value: String(d.q_factor_pct) } : { mode: "cents", value: d.q_factor_cents ? (d.q_factor_cents / 100).toFixed(2) : "" });
    setTarget(d.item_target_pct != null ? String(d.item_target_pct) : "");
  }
  const set = (patch: Partial<Card>) => setCard((c) => (c ? { ...c, ...patch } : c));

  // Live costing while editing: the same formulas as the server (EP ÷ yield × cost), before saving.
  const live = useMemo(() => {
    const out = (lines ?? []).map((l) => {
      const ing = byId[l.ingredient_id];
      const n = Number(l.qty), y = yieldOf(ing);
      if (!ing || !(n > 0)) return { cost: null as number | null, problem: null as string | null, stockQty: null as number | null };
      const ep = convert(n, l.unit, ing.unit, ing.conversions);
      if (ep == null) return { cost: null, problem: `Can't turn ${l.unit} into ${ing.unit ?? "each"}: pick another unit`, stockQty: null };
      const ap = ep / (Math.max(1, Math.min(100, y)) / 100);
      return { cost: ing.avg_cost != null ? ap * ing.avg_cost : null, problem: null, stockQty: ap };
    });
    const ingredients = out.reduce((s, x) => s + (x.cost ?? 0), 0);
    const qv = Number(q?.value || 0);
    const qCents = q?.mode === "pct" ? ingredients * qv / 100 : qv * 100;
    const plate = ingredients + qCents;
    const p = Math.round(Number(card?.price || 0) * 100);
    const t = Number(target) || d?.target_food_cost_pct || 30;
    return { out, ingredients, qCents, plate, price: p, target: t, pct: p > 0 ? (plate / p) * 100 : null };
  }, [lines, byId, q, card, target, d]);

  function setLine(i: number, patch: Partial<Line>) {
    setLines((ls) => (ls ?? []).map((l, j) => (j === i ? { ...l, ...patch } : l)));
  }

  async function save() {
    if (!d || !lines || !card) return;
    if (!card.name.trim()) return setError("Give it a name.");
    if (d.is_prep && !(Number(card.batch) > 0)) return setError("Say how much one batch makes.");
    const clean = lines.filter((l) => l.ingredient_id);
    setBusy(true);
    setError("");
    try {
      await api<RecipeDetail>(`${base}/recipes/${itemId}`, { method: "PUT", body: {
        lines: clean.map((l) => ({ ingredient_id: l.ingredient_id, qty: Number(l.qty) || 0, unit: l.unit })),
        q_factor_cents: q?.mode === "cents" && q.value ? Math.round(Number(q.value) * 100) : null,
        q_factor_pct: q?.mode === "pct" && q.value ? Number(q.value) : null,
        recipe_notes: card.steps, target_food_cost_pct: target ? Number(target) : null,
        name: card.name.trim(), description: card.description, category: card.category || null,
        prep_minutes: card.minutes.trim() ? Math.round(Number(card.minutes)) : null, recipe_status: card.status,
        allergen_notes: card.allergens, storage_notes: card.storage,
        ...(d.is_prep ? { batch_qty: Number(card.batch), unit: card.unit || d.unit || "each" }
          : { price_cents: Math.round(Number(card.price || 0) * 100) }),
      } });
      onSaved(`${card.name.trim()} saved.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
      setBusy(false);
    }
  }

  function print() {
    if (!d || !lines || !card) return;
    const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
    const rowsHtml = lines.filter((l) => byId[l.ingredient_id]).map((l) =>
      `<tr><td>${esc(byId[l.ingredient_id].name)}</td><td>${l.qty ? `${esc(qty(Number(l.qty)))} ${esc(l.unit)}` : ""}</td></tr>`).join("");
    const steps = card.steps.split(/\n+/).map((s) => s.trim()).filter(Boolean);
    const w = window.open("", "_blank", "width=720,height=900");
    if (!w) return setError("Allow pop-ups to print the recipe card.");
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(card.name)}</title><style>
      body{font-family:system-ui,sans-serif;margin:32px;color:#111} h1{margin:0 0 4px;font-family:Georgia,serif;font-weight:400} .muted{color:#555}
      table{width:100%;border-collapse:collapse;margin:16px 0} td{padding:8px;border-bottom:1px solid #ddd;font-size:15px}
      td:last-child{text-align:right;white-space:nowrap} li{margin:8px 0;line-height:1.5} .box{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin-top:12px}
      </style></head><body><h1>${esc(card.name)}</h1><div class="muted">${esc(card.description)}</div>
      <p class="muted">${d.is_prep ? `One batch makes ${esc(card.batch)} ${esc(showUnit(card.unit))}` : esc(card.category)}${card.minutes ? ` · ${esc(card.minutes)} min` : ""}</p>
      <table>${rowsHtml}</table>${steps.length ? `<h3>Steps</h3><ol>${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
      ${card.allergens ? `<div class="box"><b>Allergens</b><br>${esc(card.allergens)}</div>` : ""}${card.storage ? `<div class="box"><b>Storage & kitchen notes</b><br>${esc(card.storage)}</div>` : ""}
      <p class="muted" style="margin-top:32px;font-size:12px">Printed ${new Date().toLocaleDateString()}</p></body></html>`);
    w.document.close();
    w.focus();
    w.print();
  }

  const kind = d?.is_prep ? "prep item" : "menu item";
  const title = (
    <div className="ed-title">
      <span>{d ? `Edit ${kind}` : "Edit"}</span>
      <small>{d?.is_prep ? "One prep item, a clear batch yield, and a repeatable method." : "One menu item, what goes on the plate, and how it's made."}</small>
    </div>
  );
  return (
    <Dialog title={title} onClose={onClose} width="min(860px, 100%)"
      footer={<div className="row" style={{ width: "100%" }}>
        {onDelete && <button className="btn ed-delete" onClick={onDelete} disabled={busy}>Delete {kind}</button>}
        <button className="btn" onClick={print} disabled={!d}>Print card</button>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !d} onClick={() => void save()}>{busy ? "Saving…" : `Save ${kind}`}</button>
      </div>}>
      {!d || !lines || !card ? <p className="muted">Loading…</p> : (
        <div className="ed">
          <label className="ed-field"><span>{d.is_prep ? "Prep item name" : "Menu item name"}</span>
            <input className="input" value={card.name} onChange={(e) => set({ name: e.target.value })} /></label>
          <label className="ed-field"><span>Short description</span>
            <textarea className="input" rows={2} maxLength={1000} value={card.description} onChange={(e) => set({ description: e.target.value })}
              placeholder={d.is_prep ? "e.g. Crab ball for crab noodle soup" : "e.g. Rare beef, rice noodles, herbs"} /></label>

          <div className="ed-row three">
            <label className="ed-field"><span>{d.is_prep ? "Prep item type" : "Category"}</span>
              {d.is_prep ? (
                <select className="select" value={card.category} onChange={(e) => set({ category: e.target.value })}>
                  <option value="">Choose…</option>
                  {Array.from(new Set([...PREP_TYPES, ...(card.category && !PREP_TYPES.includes(card.category) ? [card.category] : [])])).map((t) => <option key={t}>{t}</option>)}
                </select>
              ) : (
                <>
                  <input className="input" list="ed-categories" value={card.category} onChange={(e) => set({ category: e.target.value })} placeholder="e.g. Noodle soups" />
                  <datalist id="ed-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
                </>
              )}
            </label>
            {d.is_prep ? (
              <label className="ed-field"><span>Batch yield</span>
                <input className="input" inputMode="decimal" value={card.batch} onChange={(e) => set({ batch: e.target.value })} placeholder="50" /></label>
            ) : (
              <label className="ed-field"><span>Price ($)</span>
                <input className="input" inputMode="decimal" value={card.price} onChange={(e) => set({ price: e.target.value })} /></label>
            )}
            <label className="ed-field"><span>Prep + cook time (min)</span>
              <input className="input" inputMode="numeric" value={card.minutes} onChange={(e) => set({ minutes: e.target.value.replace(/[^\d]/g, "") })} placeholder="20" /></label>
          </div>

          <div className="ed-row two">
            {d.is_prep && (
              <label className="ed-field"><span>Yield unit (L, kg, portions, balls…)</span>
                <input className="input" value={showUnit(card.unit)} onChange={(e) => set({ unit: e.target.value.toLowerCase() === "l" ? "l" : e.target.value })} placeholder="balls" /></label>
            )}
            <label className="ed-field"><span>Status</span>
              <select className="select" value={card.status} onChange={(e) => set({ status: e.target.value as Card["status"] })}>
                <option value="active">Active</option>
                <option value="development">In development</option>
              </select></label>
          </div>

          <h3 className="ed-h">{d.is_prep ? "Batch ingredients" : "Ingredients"}</h3>
          <div className="ed-lines">
            {lines.length > 0 && <div className="ed-line ed-line-head"><span>Ingredient</span><span className="tip" tabIndex={0} data-tip={d.is_prep ? "How much goes into one batch" : "How much goes on one plate"}>Amount</span><span>Unit</span><span /></div>}
            {lines.map((l, i) => {
              const ing = byId[l.ingredient_id];
              const c = live.out[i];
              return (
                <div key={l.key} className="ed-line-wrap">
                  <div className="ed-line">
                    <select className="select" aria-label="Ingredient" value={l.ingredient_id} onChange={(e) => {
                      const next = byId[e.target.value];
                      setLine(i, { ingredient_id: e.target.value, unit: normUnit(next?.unit) || "each" });
                    }}>
                      <option value="">Choose…</option>
                      {prepChoices.length > 0 && <optgroup label="Prep items">{prepChoices.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>}
                      <optgroup label="Inventory items">{stockChoices.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>
                    </select>
                    <input className="input" inputMode="decimal" aria-label="Amount" value={l.qty} placeholder="–" onChange={(e) => setLine(i, { qty: e.target.value })} />
                    <select className="select" aria-label="Unit" value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })}>
                      {unitsFor(ing?.unit ?? l.unit, ing?.conversions ?? []).map((u) => <option key={u} value={u}>{showUnit(u)}</option>)}
                    </select>
                    <button className="ed-x" aria-label="Remove ingredient" onClick={() => setLines(lines.filter((_, j) => j !== i))}>×</button>
                  </div>
                  {ing?.is_prep && (
                    <small className="muted">Prep item: one batch makes {qty(ing.batch_qty)} {showUnit(ing.unit ?? "")} ·{" "}
                      <button className="linkish" onClick={() => setNested(ing.id)}>{ing.has_recipe ? "its recipe" : "add its recipe"}</button></small>
                  )}
                  {c?.problem && <small className="warn-text">{c.problem}</small>}
                </div>
              );
            })}
            <div className="row" style={{ gap: 8 }}>
              <button className="btn" onClick={() => setLines([...lines, { key: `n${Date.now()}`, ingredient_id: "", qty: "", unit: "each" }])}>+ Add ingredient</button>
              {!making && <button className="btn" onClick={() => setMaking(true)} title="Something made in the kitchen with its own recipe: a broth, sauce or dough">+ New prep item</button>}
            </div>
            {making && (
              <NewPrep base={base} onCancel={() => setMaking(false)} onCreated={(item) => {
                setExtra((x) => [...x, item]);
                setLines([...lines.filter((l) => l.ingredient_id), { key: `n${Date.now()}`, ingredient_id: item.id, qty: "", unit: normUnit(item.unit) || "each" }]);
                setMaking(false);
                setNested(item.id);
              }} />
            )}
          </div>

          <label className="ed-field"><span>Steps to make this {kind} · one step per line</span>
            <textarea className="input" rows={5} maxLength={5000} value={card.steps} onChange={(e) => set({ steps: e.target.value })}
              placeholder={"Add all ingredients to one bowl and mix\nRoll into 2 oz balls"} /></label>
          <label className="ed-field"><span>Allergen notes</span>
            <textarea className="input" rows={2} maxLength={2000} value={card.allergens} onChange={(e) => set({ allergens: e.target.value })} placeholder="e.g. Peanuts, celeriac, gluten" /></label>
          <label className="ed-field"><span>Storage & kitchen notes</span>
            <textarea className="input" rows={2} maxLength={2000} value={card.storage} onChange={(e) => set({ storage: e.target.value })} placeholder="e.g. Label with name, date and initials; keep in the walk-in" /></label>

          <div className="ed-cost">
            <button className="ed-cost-head" aria-expanded={showCost} onClick={() => setShowCost(!showCost)}>
              <span>{d.is_prep ? "Batch cost" : "Plate cost"} <strong>{money(live.plate)}</strong>
                {d.is_prep && Number(card.batch) > 0 && live.plate > 0 ? <span className="muted"> · {unitCost(live.plate / Number(card.batch))} per {showUnit(card.unit || "unit")}</span> : null}
                {!d.is_prep && live.pct != null ? <span className={pctClass(live.pct, live.target)}> · food cost {Math.round(live.pct * 10) / 10}%</span> : null}
              </span>
              <span className="muted small">{showCost ? "Hide ▴" : "Costing ▾"}</span>
            </button>
            {showCost && (
              <div className="ed-cost-body">
                {lines.map((l, i) => byId[l.ingredient_id] && (
                  <div key={l.key} className="spread small"><span>{byId[l.ingredient_id].name}</span>
                    <span>{live.out[i]?.cost != null ? money(live.out[i].cost!) : byId[l.ingredient_id].avg_cost == null ? "no cost yet" : "–"}</span></div>
                ))}
                <div className="spread small">
                  <span className="row" style={{ gap: 6 }}><span className="tip" tabIndex={0} data-tip="Oil, salt, garnish: too small to list. A fixed $ per plate, or a % of the ingredients.">Q-factor</span>
                    <input className="input num-in" inputMode="decimal" aria-label="Q-factor" value={q?.value ?? ""} placeholder="0" onChange={(e) => setQ({ mode: q?.mode ?? "cents", value: e.target.value })} />
                    <select className="select" style={{ width: "auto", padding: "2px 6px" }} aria-label="Q-factor as" value={q?.mode ?? "cents"} onChange={(e) => setQ({ mode: e.target.value as "cents" | "pct", value: q?.value ?? "" })}>
                      <option value="cents">$</option><option value="pct">%</option>
                    </select></span>
                  <span>{money(live.qCents)}</span>
                </div>
                {!d.is_prep && (
                  <>
                    <label className="spread small"><span className="tip" tabIndex={0} data-tip="Food cost % to aim for. Empty uses your restaurant's default.">Target food cost</span>
                      <span className="row" style={{ gap: 4 }}><input className="input num-in" inputMode="decimal" value={target ?? ""} placeholder={String(d.target_food_cost_pct)} onChange={(e) => setTarget(e.target.value)} />%</span></label>
                    <div className="spread small"><span>Price for target</span><span>{live.plate > 0 ? money(Math.round(live.plate / (live.target / 100))) : "–"}</span></div>
                    <div className="spread small"><span>Margin</span><span>{live.price ? money(live.price - Math.round(live.plate)) : "–"}</span></div>
                  </>
                )}
              </div>
            )}
          </div>
          {error && <p className="small error" role="alert">{error}</p>}
        </div>
      )}
      {nested && (
        <RecipeEditor base={base} itemId={nested} stock={all} onClose={() => setNested(null)}
          onSaved={() => {
            setExtra((x) => x.map((p) => (p.id === nested ? { ...p, has_recipe: true } : p)));
            setNested(null);
          }} />
      )}
    </Dialog>
  );
}

export function MakeDialog({ base, prep, onClose, onDone }: {
  base: string; prep: { id: string; name: string; unit: string | null; batch_qty: number }; onClose: () => void; onDone: (m: string) => void;
}) {
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
