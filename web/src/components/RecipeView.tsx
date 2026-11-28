import { useState } from "react";
import { useApi } from "../hooks";
import { money, qty, type RecipeDetail } from "../lib/inventory";
import { pctClass } from "./RecipeEditor";
import { Dialog } from "./PosDialogs";

/** A menu item's recipe to read: what goes in (scaled to how many plates), how to make it, and what it costs. */
export default function RecipeView({ base, itemId, onClose, onEditRecipe, onEditItem }: {
  base: string; itemId: string; onClose: () => void; onEditRecipe: () => void; onEditItem?: () => void;
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
        {onEditItem && <button className="btn" onClick={onEditItem}>Edit item</button>}
        <button className="btn primary" onClick={onEditRecipe}>{r?.lines.length ? "Edit recipe" : "+ Add recipe"}</button>
      </div>}>
      {error && <p className="error">{error}</p>}
      {!r ? <p className="muted">Loading…</p> : (
        <div className="stack rv">
          <div className="rv-meta">
            <span className="dish-pill">{r.category || "Menu"} · Menu item</span>
            {r.price_cents > 0 && <span className="rv-price">{money(r.price_cents)}</span>}
            <label className="rv-make">Make
              <input className="input" type="number" min={1} step={1} value={make} onChange={(e) => setMake(e.target.value)} aria-label="Plates" />
              plate{n === 1 ? "" : "s"}
            </label>
          </div>
          {n !== 1 && <p className="small muted" style={{ margin: 0 }}>Recipe is for one plate. Quantities below are scaled to {qty(n)} plates.</p>}
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
                  <span>Plate cost {money(r.plate_cost_cents)}{!r.complete && " (some ingredients have no cost yet)"}</span>
                  {r.food_cost_pct != null && <span>Food cost <b className={pctClass(r.food_cost_pct, r.target_food_cost_pct)}>{r.food_cost_pct}%</b> · target {r.item_target_pct ?? r.target_food_cost_pct}%</span>}
                </div>
              )}
            </section>
            <section>
              <h3 className="rv-h">How to make it</h3>
              {steps.length === 0 ? <p className="muted">No method written yet. Add the steps under notes in Edit recipe.</p> : (
                <ol className="rv-steps">{steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
              )}
            </section>
          </div>
        </div>
      )}
    </Dialog>
  );
}
