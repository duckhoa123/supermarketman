import { useMemo, useState } from "react";
import { useApi } from "../hooks";
import { api } from "../lib/api";
import { convert, money, normUnit, qty, unitCost, unitsFor, type RecipeDetail, type StockItem } from "../lib/inventory";
import { Dialog } from "./PosDialogs";

export const pctClass = (pct: number | null, target: number) => pct == null ? "" : pct > target + 0.05 ? "error" : "good-text";

/** The recipe of a menu item (one plate) or a prep item (one batch), costed live as you edit; and recording a
 * batch of prep made. Opened from Menu items and from Inventory items. */
type Line = { key: string; ingredient_id: string; qty: string; unit: string };
const yieldOf = (i: StockItem | undefined) => i?.default_yield_pct ?? 100;  // set on the inventory item

export function RecipeEditor({ base, itemId, stock, onClose, onSaved }: {
  base: string; itemId: string; stock: StockItem[]; onClose: () => void; onSaved: (m: string) => void;
}) {
  const detail = useApi<RecipeDetail>(`${base}/recipes/${itemId}`);
  const [lines, setLines] = useState<Line[] | null>(null);
  const [q, setQ] = useState<{ mode: "cents" | "pct"; value: string } | null>(null);
  const [price, setPrice] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const d = detail.data;
  const byId = useMemo(() => Object.fromEntries(stock.map((s) => [s.id, s])), [stock]);
  const choices = useMemo(() => stock.filter((s) => s.id !== itemId).sort((a, b) => Number(b.is_prep) - Number(a.is_prep) || a.name.localeCompare(b.name)), [stock, itemId]);

  // Start the form from the saved recipe once it loads.
  if (d && lines === null) {
    setLines(d.lines.map((l) => ({ key: l.id, ingredient_id: l.ingredient_id, qty: String(l.qty), unit: normUnit(l.unit) || "each" })));
    setQ(d.q_factor_pct ? { mode: "pct", value: String(d.q_factor_pct) } : { mode: "cents", value: d.q_factor_cents ? (d.q_factor_cents / 100).toFixed(2) : "" });
    setPrice((d.price_cents / 100).toFixed(2));
    setTarget(d.item_target_pct != null ? String(d.item_target_pct) : "");
    setNotes(d.recipe_notes ?? "");
  }

  // Live costing while editing: the same formulas as the server (EP ÷ yield × cost), before saving.
  const live = useMemo(() => {
    const out = (lines ?? []).map((l) => {
      const ing = byId[l.ingredient_id];
      const n = Number(l.qty), y = yieldOf(ing);
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

  async function save() {
    if (!d || !lines) return;
    const clean = lines.filter((l) => l.ingredient_id && Number(l.qty) > 0);
    setBusy(true);
    setError("");
    try {
      const priceCents = Math.round(Number(price || 0) * 100);
      await api<RecipeDetail>(`${base}/recipes/${itemId}`, { method: "PUT", body: {
        lines: clean.map((l) => ({ ingredient_id: l.ingredient_id, qty: Number(l.qty), unit: l.unit })),
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
      `<tr><td>${esc(qty(Number(l.qty)))} ${esc(l.unit)}</td><td>${esc(byId[l.ingredient_id].name)}</td><td>${yieldOf(byId[l.ingredient_id]) !== 100 ? `${yieldOf(byId[l.ingredient_id])}% yield` : ""}</td></tr>`).join("");
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
            <div className="rec-lines">
              <div className="rec-line rec-head small muted">
                <span className="tip" tabIndex={0} data-tip={d.is_prep ? `What goes into one batch (it makes ${qty(d.batch_qty)} ${d.unit ?? ""})` : "What goes on one plate"}>Ingredient</span>
                <span className="tip" tabIndex={0} data-tip="How much is on the plate, after trimming and cooking (edible portion)">Portion</span>
                <span>Unit</span>
                <span className="tip" tabIndex={0} data-tip="From the inventory item: the share that's usable after trimming and cooking. Change it on Inventory items.">Yield %</span>
                <span className="num">Cost</span><span />
              </div>
              {lines.map((l, i) => {
                const ing = byId[l.ingredient_id];
                const c = live.out[i];
                return (
                  <div key={l.key} className="rec-line-wrap">
                    <div className="rec-line">
                      <select className="select" aria-label="Ingredient" value={l.ingredient_id} onChange={(e) => {
                        const next = byId[e.target.value];
                        setLine(i, { ingredient_id: e.target.value, unit: normUnit(next?.unit) || "each" });
                      }}>
                        <option value="">Choose…</option>
                        {choices.map((s) => <option key={s.id} value={s.id}>{s.is_prep ? "▸ " : ""}{s.name}</option>)}
                      </select>
                      <input className="input" inputMode="decimal" aria-label="Portion" value={l.qty} placeholder="8" onChange={(e) => setLine(i, { qty: e.target.value })} />
                      <select className="select" aria-label="Unit" value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })}>
                        {unitsFor(ing?.unit ?? l.unit, ing?.conversions ?? []).map((u) => <option key={u} value={u}>{u}</option>)}
                      </select>
                      <span className="rec-yield" aria-label="Yield %" title="Set on the inventory item">{ing ? `${yieldOf(ing)}%` : "–"}</span>
                      {ing && c?.stockQty != null && !c.problem ? (
                        <span className="num tip tip-left" tabIndex={0}
                          data-tip={`Takes ${qty(Math.round(c.stockQty * 1000) / 1000)} ${ing.unit ?? "each"} from stock${ing.avg_cost != null ? ` at ${unitCost(ing.avg_cost)}/${ing.unit ?? "each"}` : ""}`}>
                          {c.cost != null ? money(c.cost) : "–"}
                        </span>
                      ) : <span className="num">{c?.cost != null ? money(c.cost) : "–"}</span>}
                      <button className="sb-icon" aria-label="Remove ingredient" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>
                    </div>
                    {c?.problem && l.ingredient_id && <small className="warn-text">{c.problem}</small>}
                  </div>
                );
              })}
            </div>
            <span className="tip" tabIndex={-1} data-tip="Not in the list? Add it on the Inventory items tab first." style={{ textDecoration: "none" }}>
              <button className="btn small" style={{ alignSelf: "start" }} onClick={() => setLines([...lines, { key: `n${Date.now()}`, ingredient_id: "", qty: "", unit: "each" }])}>+ Add an ingredient</button>
            </span>
            <label className="stack" style={{ gap: 4 }}><span className="small tip" tabIndex={0} data-tip="Printed on the recipe card for the kitchen">Method and plating notes</span>
              <textarea className="textarea" rows={3} maxLength={5000} value={notes ?? ""} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. Sear skin side down 4 minutes; plate rice left, asparagus fanned right." />
            </label>
          </section>

          <aside className="rec-side stack" style={{ gap: 10 }}>
            <div className="rec-total">
              <div className="spread small"><span>Ingredients</span><span>{money(live.ingredients)}</span></div>
              <div className="spread small">
                <span className="row" style={{ gap: 6 }}><span className="tip" tabIndex={0} data-tip="Oil, salt, garnish: too small to list. A fixed $ per plate, or a % of the ingredients.">Q-factor</span>
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
                <label className="spread small">{canPrice ? <span>Menu price</span>
                  : <span className="tip" tabIndex={0} data-tip={`What-if only: the real price (${money(d.price_cents)}) is set in your POS`}>Try a price</span>}
                  <input className="input num-in" style={{ width: 90 }} inputMode="decimal" value={price ?? ""} onChange={(e) => setPrice(e.target.value)} />
                </label>
                <div className="spread"><span>Food cost</span>
                  <strong className={pctClass(live.pct, live.target)}>{live.pct != null ? `${Math.round(live.pct * 10) / 10}%` : "–"}</strong></div>
                <label className="spread small"><span className="tip" tabIndex={0} data-tip="Food cost % to aim for. Empty uses your restaurant's default.">Target</span>
                  <span className="row" style={{ gap: 4 }}><input className="input num-in" inputMode="decimal" value={target ?? ""} placeholder={String(d.target_food_cost_pct)} onChange={(e) => setTarget(e.target.value)} />%</span>
                </label>
                <div className="spread small"><span className="tip" tabIndex={0} data-tip="The menu price that would bring this dish to its target food cost">Price for target</span><span>{live.plate > 0 ? money(Math.round(live.plate / (live.target / 100))) : "–"}</span></div>
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
