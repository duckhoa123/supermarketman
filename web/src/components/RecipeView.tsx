import { useState } from "react";
import { useApi } from "../hooks";
import { money, qty, type RecipeDetail } from "../lib/inventory";
import { pctClass } from "./RecipeEditor";
import { Dialog } from "./PosDialogs";

/** A menu item's recipe to read: what goes in (scaled to how many plates), how to make it, and what it costs. */
export default function RecipeView({ base, itemId, onClose, onEditRecipe, onEditItem, onMake }: {
  base: string; itemId: string; onClose: () => void; onEditRecipe: () => void; onEditItem?: () => void; onMake?: () => void;
}) {
  const { data: r, error } = useApi<RecipeDetail>(`${base}/recipes/${itemId}`);
  const [make, setMake] = useState("1");
  const n = Math.max(0, Number(make) || 0);
  const steps = (r?.recipe_notes ?? "").split(/\n+/).map((s) => s.replace(/^\s*(\d+[.)]|[-•*])\s*/, "").trim()).filter(Boolean);

  const title = r ? (
    <div className="rv-title">
      <span>{r.name}</span>
      {r.description && <small>{r.description}</small>}
    </div>
  ) : "Recipe";

  return (
    <Dialog title={title} onClose={onClose} width="min(1000px, 100%)"
      footer={<div className="row" style={{ marginLeft: "auto" }}>
        {onMake && <button className="btn" onClick={onMake}>Made a batch</button>}
        {onEditItem && !r?.is_prep && <button className="btn" onClick={onEditItem}>Edit item</button>}
        <button className="btn primary" onClick={onEditRecipe}>{r?.lines.length ? "Edit recipe" : "+ Add recipe"}</button>
      </div>}>
      {error && <p className="error">{error}</p>}
      {!r ? <p className="muted">Loading…</p> : (
        <div className="stack rv">
          <div className="rv-meta">
            <span className="dish-pill">{r.is_prep ? `${r.category ? `${r.category} · ` : ""}Prep item` : `${r.category || "Menu"} · Menu item`}</span>
            {!r.is_prep && r.price_cents > 0 && <span className="rv-price">{money(r.price_cents)}</span>}
            {r.prep_minutes ? <span className="rv-price">{r.prep_minutes} min</span> : null}
            {r.recipe_status === "development" && <span className="dish-pill is-dev">In development</span>}
            <label className="rv-make">Make
              <input className="input" type="number" min={1} step={1} value={make} onChange={(e) => setMake(e.target.value)} aria-label={r.is_prep ? "Batches" : "Plates"} />
              {r.is_prep ? `batch${n === 1 ? "" : "es"}` : `plate${n === 1 ? "" : "s"}`}
            </label>
          </div>
          {r.description && <p style={{ margin: 0 }}>{r.description}</p>}
          {r.is_prep
            ? <p className="small muted" style={{ margin: 0 }}>One batch makes {qty(r.batch_qty)} {r.unit === "l" ? "L" : r.unit ?? ""}.{n !== 1 ? ` Quantities below are for ${qty(n)} batches (${qty((r.batch_qty ?? 0) * n)} ${r.unit === "l" ? "L" : r.unit ?? ""}).` : ""}</p>
            : n !== 1 && <p className="small muted" style={{ margin: 0 }}>Recipe is for one plate. Quantities below are scaled to {qty(n)} plates.</p>}
          <div className="rv-cols">
            <section>
              <h3 className="rv-h">Ingredients</h3>
              {r.lines.length === 0 ? <p className="muted">No ingredients yet.</p> : (
                <ul className="rv-list">
                  {r.lines.map((l) => (
                    <li key={l.id}><span>{l.name}</span><span className="rv-qty">{l.qty ? `${qty(l.qty * n)} ${l.unit ?? l.stock_unit ?? ""}` : "–"}</span></li>
                  ))}
                </ul>
              )}
              {r.lines.length > 0 && (
                <div className="rv-box">
                  <strong>Cost</strong>
                  <span>{r.is_prep ? "Batch cost" : "Plate cost"} {money(r.plate_cost_cents)}{!r.complete && " (some ingredients have no cost yet)"}</span>
                  {r.is_prep && r.batch_qty ? <span>Per {r.unit === "l" ? "L" : r.unit ?? "unit"}: {money(r.plate_cost_cents / r.batch_qty)}</span> : null}
                  {!r.is_prep && r.food_cost_pct != null && <span>Food cost <b className={pctClass(r.food_cost_pct, r.target_food_cost_pct)}>{r.food_cost_pct}%</b> · target {r.item_target_pct ?? r.target_food_cost_pct}%</span>}
                </div>
              )}
            </section>
            <section>
              <h3 className="rv-h">How to make it</h3>
              {steps.length === 0 ? <p className="muted">No method written yet. Add the steps in Edit recipe.</p> : (
                <ol className="rv-steps">{steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
              )}
              {r.allergen_notes && <div className="rv-box"><strong>Allergens</strong><span>{r.allergen_notes}</span></div>}
              {r.storage_notes && <div className="rv-box"><strong>Storage & kitchen notes</strong><span>{r.storage_notes}</span></div>}
            </section>
          </div>
        </div>
      )}
    </Dialog>
  );
}
