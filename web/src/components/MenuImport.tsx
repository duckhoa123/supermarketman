import { useEffect, useMemo, useRef, useState } from "react";
import { api, uploadFile } from "../lib/api";
import { convert, normUnit, qty, unitsFor, type MenuCost, type StockItem } from "../lib/inventory";
import ItemPicker from "./ItemPicker";
import { Dialog } from "./PosDialogs";

type Line = { ingredient: string; item_id: string | null; match?: string | null; qty: number; unit: string | null };
type Dish = {
  key: string; name: string; category: string | null; price_cents: number | null; description: string | null;
  include: boolean; menu_item_id: string | null; menu_match: string | null; menu_name?: string | null;
  had_recipe: boolean; lines: Line[];
};
type Ingredient = { name: string; unit: string; category: string | null };
type Import = {
  id: string; status: "processing" | "review" | "confirmed" | "failed" | "discarded"; file_name: string | null;
  dishes: Dish[]; ingredients: Record<string, Ingredient>; progress: { done: number; total: number } | null;
  warnings: string[]; error: string | null; created_at: string | null;
  result: { recipes: number; dishes_created: string[]; items_created: string[]; errors: string[] } | null;
};
type Pending = Omit<Import, "dishes" | "ingredients"> & { dishes: number };

const STOCK_UNITS = ["kg", "g", "lb", "oz", "l", "ml", "each"];
const PORTION_UNITS = ["g", "kg", "oz", "lb", "ml", "l", "tsp", "tbsp", "cup", "each"];
const CATEGORIES = ["Produce", "Protein", "Dairy", "Dry goods", "Bakery", "Prep", "Beverage", "Alcohol", "Packaging",
  "Cleaning", "Supplies"];
const keyOf = (name: string) => name.trim().toLowerCase().replace(/\s+/g, " ");
const unitFor = (portion: string | null) => ({ g: "kg", kg: "kg", oz: "lb", lb: "lb", ml: "l", l: "l", floz: "l",
  tsp: "l", tbsp: "l", cup: "l" } as Record<string, string>)[normUnit(portion)] ?? "each";
const show = (u: string | null | undefined) => (normUnit(u) === "l" ? "L" : normUnit(u) || "each");
const money = (c: number | null) => (c == null ? "" : (c / 100).toFixed(2));

/** Set up recipes from a menu: upload it, check what the AI suggests for each dish, then create it all. */
export default function MenuImport({ base, stock, menu, onClose, onDone }: {
  base: string; stock: StockItem[]; menu: MenuCost[]; onClose: () => void; onDone: (message: string) => void;
}) {
  const [imp, setImp] = useState<Import | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const byId = useMemo(() => Object.fromEntries(stock.map((i) => [i.id, i])), [stock]);

  useEffect(() => {
    void api<Pending[]>(`${base}/menu-imports`).then(setPending).catch(() => setPending([]));
  }, [base]);

  // While the AI reads the menu, check back every few seconds.
  useEffect(() => {
    if (imp?.status !== "processing") return;
    const t = window.setTimeout(() => void load(imp.id), 3000);
    return () => window.clearTimeout(t);
  }, [imp]); // eslint-disable-line react-hooks/exhaustive-deps

  async function load(id: string) {
    const next = await api<Import>(`${base}/menu-imports/${id}`);
    setImp(next);
    if (next.status === "review" && !open) setOpen(next.dishes.find((d) => d.include)?.key ?? null);
  }

  async function upload(f: File | undefined) {
    if (!f) return;
    setBusy("upload");
    setError("");
    try {
      setImp(await uploadFile<Import>(`${base}/menu-imports`, f, f.name));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't upload the menu.");
    } finally {
      setBusy("");
    }
  }

  // --- Editing ---------------------------------------------------------------------------------------------------

  function update(dishes: Dish[], ingredients = imp!.ingredients) {
    // Every new ingredient that's used has its settings; unused ones are dropped.
    const used: Record<string, Ingredient> = {};
    for (const d of dishes) for (const l of d.lines) {
      if (l.item_id || !l.ingredient.trim()) continue;
      const k = keyOf(l.ingredient);
      used[k] = ingredients[k] ?? used[k] ?? { name: l.ingredient.trim(), unit: unitFor(l.unit), category: null };
    }
    setImp({ ...imp!, dishes, ingredients: used });
    setDirty(true);
  }
  const setDish = (key: string, patch: Partial<Dish>) => update(imp!.dishes.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  const setLine = (dish: Dish, n: number, patch: Partial<Line>) =>
    setDish(dish.key, { lines: dish.lines.map((l, j) => (j === n ? { ...l, ...patch } : l)) });

  /** The unit an ingredient is counted in: its inventory item's, or the one chosen for a new item. */
  function stockUnit(l: Line) {
    if (l.item_id) return byId[l.item_id]?.unit ?? null;
    return imp!.ingredients[keyOf(l.ingredient)]?.unit ?? unitFor(l.unit);
  }
  function portionUnits(l: Line) {
    if (!l.item_id) return PORTION_UNITS; // a new item: its unit follows the portion's
    const item = byId[l.item_id];
    return unitsFor(stockUnit(l), item?.conversions ?? []).filter((u) => u !== "dozen");
  }

  /** A new ingredient's portion in a unit its stock unit can't take: it's counted in a matching unit instead. */
  function setUnit(dish: Dish, n: number, unit: string) {
    const l = dish.lines[n];
    const dishes = imp!.dishes.map((d) => (d.key === dish.key ? { ...d, lines: d.lines.map((x, j) => (j === n ? { ...x, unit } : x)) } : d));
    const k = keyOf(l.ingredient);
    const ing = imp!.ingredients[k];
    if (!l.item_id && ing && convert(1, unit, ing.unit) == null) update(dishes, { ...imp!.ingredients, [k]: { ...ing, unit: unitFor(unit) } });
    else update(dishes);
  }
  function fits(l: Line) {
    const item = l.item_id ? byId[l.item_id] : undefined;
    return convert(1, l.unit || stockUnit(l), stockUnit(l), item?.conversions ?? []) != null;
  }

  function renameIngredient(oldKey: string, name: string) {
    const k = keyOf(name);
    if (!k) return;
    const ingredients = { ...imp!.ingredients };
    const info = ingredients[oldKey];
    delete ingredients[oldKey];
    ingredients[k] = { ...info, name };
    update(imp!.dishes.map((d) => ({ ...d, lines: d.lines.map((l) => (!l.item_id && keyOf(l.ingredient) === oldKey ? { ...l, ingredient: name } : l)) })), ingredients);
  }

  // --- Saving --------------------------------------------------------------------------------------------------

  async function save(): Promise<boolean> {
    if (!imp) return false;
    try {
      const out = await api<Import>(`${base}/menu-imports/${imp.id}`, { method: "PUT", body: {
        dishes: imp.dishes.map((d) => ({ ...d, lines: d.lines.filter((l) => l.item_id || l.ingredient.trim()) })),
        ingredients: imp.ingredients,
      } });
      setImp(out);
      setDirty(false);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
      return false;
    }
  }

  async function confirm() {
    setBusy("confirm");
    setError("");
    try {
      if (dirty && !(await save())) return;
      const result = await api<Import["result"]>(`${base}/menu-imports/${imp!.id}/confirm`, { method: "POST", body: {} });
      setImp({ ...imp!, status: "confirmed", result });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create the recipes.");
    } finally {
      setBusy("");
    }
  }

  async function action(name: "discard" | "retry") {
    if (name === "discard" && !window.confirm("Throw this menu import away? Nothing has been added yet.")) return;
    setBusy(name);
    try {
      const out = await api<Import>(`${base}/menu-imports/${imp!.id}/${name}`, { method: "POST", body: {} });
      if (name === "discard") onClose();
      else setImp(out);
    } finally {
      setBusy("");
    }
  }

  // --- Views -----------------------------------------------------------------------------------------------------

  const ticked = imp?.dishes.filter((d) => d.include) ?? [];
  const newDishes = ticked.filter((d) => !d.menu_item_id).length;
  const usedNew = Object.entries(imp?.ingredients ?? {}).filter(([k]) =>
    ticked.some((d) => d.lines.some((l) => !l.item_id && keyOf(l.ingredient) === k)));
  const broken = ticked.flatMap((d) => d.lines.filter((l) => (l.item_id || l.ingredient.trim()) && (!fits(l) || !(l.qty > 0))).map(() => d.key));

  const footer = imp?.status === "review" ? (
    <div className="inv-foot">
      <span className="small muted">{ticked.length} dish{ticked.length === 1 ? "" : "es"} ticked
        {newDishes > 0 ? ` (${newDishes} new to your menu)` : ""} · {usedNew.length} new inventory item{usedNew.length === 1 ? "" : "s"}</span>
      <div className="row">
        {broken.length > 0 && <span className="small warn-text">{broken.length} portion{broken.length === 1 ? "" : "s"} to fix</span>}
        <button className="btn" disabled={busy !== ""} onClick={() => void action("discard")}>Discard</button>
        {dirty && <button className="btn" disabled={busy !== ""} onClick={() => void save()}>Save for later</button>}
        <button className="btn primary" disabled={busy !== "" || !ticked.length || broken.length > 0} onClick={() => void confirm()}>
          {busy === "confirm" ? "Creating…" : `Create ${ticked.length} recipe${ticked.length === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  ) : imp?.status === "confirmed" ? (
    <div className="row" style={{ marginLeft: "auto" }}>
      <button className="btn primary" onClick={() => onDone(`${imp.result?.recipes ?? 0} recipes created from your menu.`)}>Done</button>
    </div>
  ) : undefined;

  return (
    <Dialog title="Set up recipes from your menu" onClose={onClose} width="min(1080px, 100%)" footer={footer}>
      {!imp && (
        <div className="stack mi-start">
          <p>Upload your menu and Pass suggests what goes into each dish, using your inventory items where it can.
            You check and change everything before anything is saved.</p>
          <div className="mi-drop">
            <input ref={file} type="file" hidden accept="image/*,application/pdf,.xlsx,.csv,.tsv,text/csv"
              onChange={(e) => void upload(e.target.files?.[0])} />
            <input ref={camera} type="file" hidden accept="image/*" capture="environment"
              onChange={(e) => void upload(e.target.files?.[0])} />
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn primary" disabled={busy !== ""} onClick={() => file.current?.click()}>
                {busy === "upload" ? "Uploading…" : "Choose a file"}
              </button>
              <button className="btn" disabled={busy !== ""} onClick={() => camera.current?.click()}>📷 Take a photo</button>
            </div>
            <p className="small muted">A photo or PDF of the menu, or an Excel (.xlsx) or CSV list of your dishes</p>
          </div>
          {pending.filter((p) => p.status !== "failed").map((p) => (
            <button key={p.id} className="linkish small" style={{ alignSelf: "start" }} onClick={() => void load(p.id)}>
              Continue {p.status === "processing" ? "the menu being read" : "your review"}: {p.file_name ?? "menu"}
              {p.dishes ? ` (${p.dishes} dishes)` : ""}
            </button>
          ))}
          {error && <p className="small error">{error}</p>}
        </div>
      )}

      {imp?.status === "processing" && (
        <div className="inv-reading">
          <span className="spinner" aria-hidden />
          {imp.progress?.total
            ? `Suggesting recipes: ${imp.progress.done} of ${imp.progress.total} dishes…`
            : "Reading the menu…"}
        </div>
      )}

      {imp?.status === "failed" && (
        <div className="stack">
          <p className="error">{imp.error}</p>
          <div className="row">
            <button className="btn primary small" disabled={busy !== ""} onClick={() => void action("retry")}>Try again</button>
            <button className="btn small" disabled={busy !== ""} onClick={() => void action("discard")}>Discard</button>
          </div>
        </div>
      )}

      {imp?.status === "confirmed" && imp.result && (
        <div className="stack">
          <p className="notice"><strong>{imp.result.recipes} recipe{imp.result.recipes === 1 ? "" : "s"} saved.</strong></p>
          {imp.result.dishes_created.length > 0 && <p className="small">Added to your menu: {imp.result.dishes_created.join(", ")}.</p>}
          {imp.result.items_created.length > 0 && <p className="small">New inventory items: {imp.result.items_created.join(", ")}.</p>}
          {imp.result.errors.length > 0 && <p className="small error">Not saved: {imp.result.errors.join("; ")}</p>}
          <p className="small muted">Next: count your stock once so quantities start right. Costs fill in from your
            invoices, and plate costs follow.</p>
        </div>
      )}

      {imp?.status === "review" && (
        <div className="stack mi-review">
          <p className="small muted">
            These are suggestions, with typical restaurant portions. Check each dish against how your kitchen makes it:
            change portions, swap or add ingredients, and untick anything you don't want.
          </p>
          {imp.warnings.map((w, i) => <p key={i} className="small warn-text">⚠ {w}</p>)}
          {error && <p className="small error" role="alert">{error}</p>}
          <div className="row" style={{ gap: 8 }}>
            <button className="linkish small" onClick={() => update(imp.dishes.map((d) => ({ ...d, include: true })))}>Tick all</button>
            <button className="linkish small" onClick={() => update(imp.dishes.map((d) => ({ ...d, include: false })))}>Untick all</button>
          </div>

          <ol className="mi-dishes">
            {imp.dishes.map((d) => {
              const expanded = open === d.key;
              const bad = d.include && d.lines.some((l) => (l.item_id || l.ingredient.trim()) && (!fits(l) || !(l.qty > 0)));
              return (
                <li key={d.key} className={`mi-dish ${d.include ? "" : "is-off"} ${bad ? "is-bad" : ""}`}>
                  <div className="mi-dish-head">
                    <input type="checkbox" aria-label={`Include ${d.name}`} checked={d.include}
                      onChange={(e) => setDish(d.key, { include: e.target.checked })} />
                    <button className="mi-dish-name" onClick={() => setOpen(expanded ? null : d.key)} aria-expanded={expanded}>
                      <strong>{d.name}</strong>
                      {d.category && <span className="small muted"> · {d.category}</span>}
                    </button>
                    {d.menu_item_id
                      ? <span className="pill good" title={`Linked to “${d.menu_name ?? d.name}” on your menu`}>On your menu</span>
                      : <span className="pill new" title="Added to your menu when you create the recipes">New dish{d.price_cents ? ` · $${money(d.price_cents)}` : ""}</span>}
                    {d.had_recipe && <span className="pill" title="Its current recipe is shown. Tick it to save changes.">Has a recipe</span>}
                    <button className="btn small" onClick={() => setOpen(expanded ? null : d.key)}>{expanded ? "Close" : "Edit"}</button>
                  </div>
                  {!expanded && (
                    <p className="small muted mi-summary">
                      {d.lines.length ? d.lines.map((l) => `${l.ingredient} ${qty(l.qty)} ${show(l.unit)}`).join(" · ") : "No ingredients yet"}
                    </p>
                  )}
                  {expanded && (
                    <div className="stack mi-edit">
                      <div className="mi-dish-fields">
                        <label><span className="small muted">On your menu</span>
                          <select className="select" value={d.menu_item_id ?? ""}
                            onChange={(e) => {
                              const m = menu.find((x) => x.id === e.target.value);
                              setDish(d.key, { menu_item_id: m?.id ?? null, menu_name: m?.name ?? null, menu_match: m ? "chosen" : null });
                            }}>
                            <option value="">➕ Add as a new dish</option>
                            {menu.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                          </select>
                        </label>
                        {!d.menu_item_id && (
                          <>
                            <label><span className="small muted">Name</span>
                              <input className="input" value={d.name} onChange={(e) => setDish(d.key, { name: e.target.value })} /></label>
                            <label><span className="small muted">Price ($)</span>
                              <input className="input" type="number" step="0.01" min={0} value={money(d.price_cents)}
                                onChange={(e) => setDish(d.key, { price_cents: e.target.value ? Math.round(Number(e.target.value) * 100) : null })} /></label>
                          </>
                        )}
                      </div>
                      <div className="mi-lines">
                        <div className="mi-line mi-line-head small muted">
                          <span>Ingredient</span><span>Portion</span><span>Unit</span><span />
                        </div>
                        {d.lines.map((l, n) => {
                          const okUnit = fits(l);
                          return (
                            <div key={n} className="mi-line">
                              <div className="mi-ing">
                                <ItemPicker items={stock} itemId={l.item_id} itemName={l.item_id ? l.ingredient : null}
                                  newName={l.item_id ? "" : l.ingredient}
                                  onPickItem={(it) => {
                                    const units = unitsFor(it.unit, it.conversions ?? []);
                                    setLine(d, n, { item_id: it.id, ingredient: it.name, match: "chosen",
                                      unit: units.includes(normUnit(l.unit)) ? l.unit : normUnit(it.unit) || "each" });
                                  }}
                                  onNewName={(name) => setLine(d, n, { item_id: null, ingredient: name, match: null })} />
                                {!l.item_id && l.ingredient.trim() && <span className="pill new" title="A new inventory item">New</span>}
                              </div>
                              <input className="input" type="number" min={0} step="any" aria-label="Portion" value={l.qty || ""}
                                onChange={(e) => setLine(d, n, { qty: Number(e.target.value) || 0 })} />
                              <select className={`select ${okUnit ? "" : "is-invalid"}`} aria-label="Unit" value={normUnit(l.unit)}
                                title={okUnit ? undefined : `${l.ingredient} is counted in ${show(stockUnit(l))}: pick a unit that converts`}
                                onChange={(e) => setUnit(d, n, e.target.value)}>
                                {!okUnit && <option value={normUnit(l.unit)}>{show(l.unit)} ✕</option>}
                                {portionUnits(l).map((u) => <option key={u} value={u}>{show(u)}</option>)}
                              </select>
                              <button className="sb-icon" aria-label={`Remove ${l.ingredient}`} title="Remove"
                                onClick={() => setDish(d.key, { lines: d.lines.filter((_, j) => j !== n) })}>✕</button>
                            </div>
                          );
                        })}
                        <button className="btn small" style={{ alignSelf: "start" }}
                          onClick={() => setDish(d.key, { include: true, lines: [...d.lines, { ingredient: "", item_id: null, qty: 0, unit: "g" }] })}>
                          + Add ingredient
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>

          {usedNew.length > 0 && (
            <section className="stack mi-new">
              <h3 style={{ margin: 0 }}>New inventory items <span className="small muted">({usedNew.length})</span></h3>
              <p className="small muted" style={{ margin: 0 }}>Added to your inventory with no stock and no cost yet: costs come
                from your invoices, quantities from your first count. Same name in several dishes = one item.</p>
              <div className="mi-new-grid">
                <span className="small muted">Name</span><span className="small muted">Counted in</span><span className="small muted">Category</span>
                {usedNew.map(([k, ing]) => (
                  <NewIngredient key={k} ing={ing}
                    onRename={(name) => renameIngredient(k, name)}
                    onChange={(patch) => update(imp.dishes, { ...imp.ingredients, [k]: { ...ing, ...patch } })} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </Dialog>
  );
}

function NewIngredient({ ing, onRename, onChange }: {
  ing: Ingredient; onRename: (name: string) => void; onChange: (patch: Partial<Ingredient>) => void;
}) {
  const [name, setName] = useState(ing.name);
  return (
    <>
      <input className="input" value={name} onChange={(e) => setName(e.target.value)}
        onBlur={() => { if (name.trim() && name.trim() !== ing.name) onRename(name.trim()); else setName(ing.name); }} />
      <select className="select" value={normUnit(ing.unit)} onChange={(e) => onChange({ unit: e.target.value })}>
        {STOCK_UNITS.map((u) => <option key={u} value={u}>{show(u)}</option>)}
      </select>
      <select className="select" value={ing.category ?? ""} onChange={(e) => onChange({ category: e.target.value || null })}>
        <option value="">Ingredients</option>
        {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
      </select>
    </>
  );
}
