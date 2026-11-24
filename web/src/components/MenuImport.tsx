import { useEffect, useMemo, useRef, useState } from "react";
import { api, uploadFile } from "../lib/api";
import { convert, normUnit, qty, unitsFor, type StockItem } from "../lib/inventory";
import ItemPicker from "./ItemPicker";
import { Dialog } from "./PosDialogs";

type Line = { ingredient: string; item_id: string | null; match?: string | null; qty: number; unit: string | null };
type Dish = {
  key: string; name: string; category: string | null; price_cents: number | null; description: string | null;
  include: boolean; menu_item_id: string | null; suggested: boolean; lines: Line[];
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
const unitFor = (portion: string | null) => (!portion ? "kg" : ({ g: "kg", kg: "kg", oz: "lb", lb: "lb", ml: "l", l: "l",
  floz: "l", tsp: "l", tbsp: "l", cup: "l" } as Record<string, string>)[normUnit(portion)] ?? "each");
const show = (u: string | null | undefined) => (normUnit(u) === "l" ? "L" : normUnit(u) || "each");
const money = (c: number | null) => (c == null ? "" : (c / 100).toFixed(2));
const named = (l: Line) => !!(l.item_id || l.ingredient.trim());
const newKey = () => Math.random().toString(36).slice(2, 12);

/** Add new menu items and their recipes from a menu: upload it, check the suggested ingredients, review the new
 * inventory items, then create it all. Dishes already on the menu are left out. */
export default function MenuImport({ base, stock, onClose, onDone }: {
  base: string; stock: StockItem[]; onClose: () => void; onDone: (message: string) => void;
}) {
  const [imp, setImp] = useState<Import | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [sections, setSections] = useState<{ items: boolean; missing: boolean }>({ items: false, missing: false });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const file = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLElement>(null);
  const itemsRef = useRef<HTMLElement>(null);
  const missingRef = useRef<HTMLElement>(null);
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
    // A menu item without a suggestion starts with an empty ingredient row, ready to type in.
    setImp({ ...next, dishes: next.dishes.map((d) => (d.lines.length ? d : { ...d, lines: [{ ingredient: "", item_id: null, qty: 0, unit: null }] })) });
    // Menu items without a suggestion open ready for their ingredients.
    if (next.status === "review") setOpen(new Set(next.dishes.filter((d) => !d.lines.length).map((d) => d.key)));
  }

  async function start(f?: File) {
    setBusy(f ? "upload" : "blank");
    setError("");
    try {
      setImp(f ? await uploadFile<Import>(`${base}/menu-imports`, f, f.name)
        : await api<Import>(`${base}/menu-imports/blank`, { method: "POST", body: {} }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start.");
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
      used[k] = ingredients[k] ?? used[k] ?? { name: l.ingredient.trim(), unit: unitFor(l.qty ? l.unit : null), category: null };
    }
    setImp({ ...imp!, dishes, ingredients: used });
    setDirty(true);
  }
  const setDish = (key: string, patch: Partial<Dish>) => update(imp!.dishes.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  const setLine = (dish: Dish, n: number, patch: Partial<Line>) =>
    setDish(dish.key, { lines: dish.lines.map((l, j) => (j === n ? { ...l, ...patch } : l)) });
  const toggle = (key: string) => setOpen((s) => {
    const n = new Set(s);
    if (n.has(key)) n.delete(key);
    else n.add(key);
    return n;
  });

  function addDish() {
    const d: Dish = { key: newKey(), name: "", category: null, price_cents: null, description: null, include: true,
      menu_item_id: null, suggested: false, lines: [{ ingredient: "", item_id: null, qty: 0, unit: null }] };
    update([...imp!.dishes, d]);
    setOpen((s) => new Set(s).add(d.key));
    setSections((s) => ({ ...s, missing: true }));
    window.setTimeout(() => missingRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }), 50);
  }

  /** The unit an ingredient is counted in: its inventory item's, or the one chosen for a new item. */
  function stockUnit(l: Line) {
    if (l.item_id) return byId[l.item_id]?.unit ?? null;
    return imp!.ingredients[keyOf(l.ingredient)]?.unit ?? "kg";
  }
  function portionUnits(l: Line) {
    if (!l.item_id) return PORTION_UNITS; // a new item: its unit follows the portion's
    return unitsFor(stockUnit(l), byId[l.item_id]?.conversions ?? []).filter((u) => u !== "dozen");
  }
  function fits(l: Line) {
    if (!l.qty) return true; // no portion yet: fine
    return convert(1, l.unit || stockUnit(l), stockUnit(l), l.item_id ? byId[l.item_id]?.conversions ?? [] : []) != null;
  }
  /** A portion in a unit a new ingredient's stock unit can't take: it's counted in a matching unit instead. */
  function setUnit(dish: Dish, n: number, unit: string) {
    const l = dish.lines[n];
    const dishes = imp!.dishes.map((d) => (d.key === dish.key ? { ...d, lines: d.lines.map((x, j) => (j === n ? { ...x, unit } : x)) } : d));
    const k = keyOf(l.ingredient);
    const ing = imp!.ingredients[k];
    if (!l.item_id && ing && convert(1, unit, ing.unit) == null) update(dishes, { ...imp!.ingredients, [k]: { ...ing, unit: unitFor(unit) } });
    else update(dishes);
  }
  /** Another item with this name, if any: one already in inventory, or another new one. */
  function clash(oldKey: string, name: string): string | null {
    const k = keyOf(name);
    if (!k || k === oldKey) return null;
    const existing = stock.find((i) => keyOf(i.name) === k);
    if (existing) return `“${existing.name}” is already in your inventory. Keep this name and these menu items use that item instead of a new one.`;
    if (imp!.ingredients[k]) return `Another new item is called “${imp!.ingredients[k].name}”. Keep this name and they become one item.`;
    return null;
  }

  /** Rename a new item everywhere it's used in the menu items above; a name already in inventory uses that item. */
  function renameIngredient(oldKey: string, name: string) {
    const k = keyOf(name);
    if (!k) return;
    const existing = stock.find((i) => keyOf(i.name) === k);
    if (existing) {
      const units = unitsFor(existing.unit, existing.conversions ?? []);
      update(imp!.dishes.map((d) => ({ ...d, lines: d.lines.map((l) => (!l.item_id && keyOf(l.ingredient) === oldKey
        ? { ...l, item_id: existing.id, ingredient: existing.name, match: "chosen", unit: l.qty && units.includes(normUnit(l.unit)) ? l.unit : null }
        : l)) })));
      return;
    }
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
        dishes: imp.dishes.filter((d) => d.name.trim()).map((d) => ({ ...d, name: d.name.trim(), lines: d.lines.filter(named) })),
        ingredients: imp.ingredients,
      } });
      setImp({ ...out, dishes: imp.dishes });  // keep blank rows being filled in on screen
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
      setError(e instanceof Error ? e.message : "Couldn't create them.");
    } finally {
      setBusy("");
    }
  }

  async function action(name: "discard" | "retry") {
    if (name === "discard" && !window.confirm("Throw this away? Nothing has been added yet.")) return;
    setBusy(name);
    try {
      const out = await api<Import>(`${base}/menu-imports/${imp!.id}/${name}`, { method: "POST", body: {} });
      if (name === "discard") onClose();
      else setImp(out);
    } finally {
      setBusy("");
    }
  }

  // --- Numbers ---------------------------------------------------------------------------------------------------

  const dishes = (imp?.dishes ?? []).filter((d) => d.include);
  const real = dishes.filter((d) => d.name.trim());
  const suggested = dishes.filter((d) => d.suggested);
  const byHand = dishes.filter((d) => !d.suggested);
  const empty = real.filter((d) => !d.lines.some(named));
  const ready = real.length - empty.length;
  const newItems = Object.entries(imp?.ingredients ?? {}).filter(([k]) =>
    real.some((d) => d.lines.some((l) => !l.item_id && keyOf(l.ingredient) === k)));
  const broken = real.filter((d) => d.lines.some((l) => named(l) && !fits(l))).length;
  const unnamed = dishes.filter((d) => !d.name.trim() && d.lines.some(named)).length;
  /** From the board: open the section and scroll to it. */
  const go = (to: "menu" | "items" | "missing") => {
    if (to !== "menu") setSections((s) => ({ ...s, [to]: true }));
    const ref = { menu: menuRef, items: itemsRef, missing: missingRef }[to];
    window.setTimeout(() => ref.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  };

  const footer = imp?.status === "review" ? (
    <div className="inv-foot">
      <span className="small muted">
        {broken > 0 ? <span className="warn-text">{broken} menu item{broken === 1 ? " has a portion" : "s have portions"} in a unit that doesn't fit</span>
          : unnamed > 0 ? <span className="warn-text">Name every menu item you added</span>
            : empty.length > 0 ? `${empty.length} without ingredients won't be added` : "Portions can be left blank and filled in later"}
      </span>
      <div className="row">
        <button className="btn" disabled={busy !== ""} onClick={() => void action("discard")}>Discard</button>
        {dirty && <button className="btn" disabled={busy !== ""} onClick={() => void save()}>Save for later</button>}
        <button className="btn primary" disabled={busy !== "" || !ready || broken > 0 || unnamed > 0} onClick={() => void confirm()}
          title={`Adds ${ready} menu item${ready === 1 ? "" : "s"} with their recipes${newItems.length ? ` and ${newItems.length} inventory item${newItems.length === 1 ? "" : "s"}` : ""}`}>
          {busy === "confirm" ? "Importing…" : "Import"}
        </button>
      </div>
    </div>
  ) : imp?.status === "confirmed" ? (
    <div className="row" style={{ marginLeft: "auto" }}>
      <button className="btn primary" onClick={() => onDone(`${imp.result?.dishes_created.length ?? 0} menu items added with their recipes.`)}>Done</button>
    </div>
  ) : undefined;

  // --- One menu item (a render function, not a component, so typing doesn't lose focus) ---------------------------

  function dishCard(d: Dish) {
    const expanded = open.has(d.key);
    const bad = d.lines.some((l) => named(l) && !fits(l));
    return (
      <li key={d.key} className={`mi-dish ${bad ? "is-bad" : ""}`}>
        <div className="mi-dish-head">
          <button className="mi-dish-name" onClick={() => toggle(d.key)} aria-expanded={expanded}>
            <strong>{d.name || "New menu item"}</strong>
            {d.category && <span className="small muted"> · {d.category}</span>}
            {d.price_cents != null && <span className="small muted"> · ${money(d.price_cents)}</span>}
          </button>
          <button className="btn small" onClick={() => toggle(d.key)}>{expanded ? "Close" : "Edit"}</button>
          <button className="sb-icon" aria-label={`Don't add ${d.name || "this menu item"}`} title="Don't add this menu item"
            onClick={() => update(imp!.dishes.filter((x) => x.key !== d.key))}>✕</button>
        </div>
        {!expanded && (
          <p className="small muted mi-summary">
            {d.lines.some(named)
              ? d.lines.filter(named).map((l) => (l.qty ? `${l.ingredient} ${qty(l.qty)} ${show(l.unit || stockUnit(l))}` : l.ingredient)).join(" · ")
              : "No ingredients yet"}
          </p>
        )}
        {expanded && (
          <div className="stack mi-edit">
            <div className="mi-dish-fields">
              <label><span className="small muted">Name</span>
                <input className="input" value={d.name} placeholder="e.g. Pho Tai" onChange={(e) => setDish(d.key, { name: e.target.value })} /></label>
              <label><span className="small muted">Category</span>
                <input className="input" value={d.category ?? ""} placeholder="e.g. Noodle soups" onChange={(e) => setDish(d.key, { category: e.target.value || null })} /></label>
              <label><span className="small muted">Price ($)</span>
                <input className="input" type="number" step="0.01" min={0} value={money(d.price_cents)}
                  onChange={(e) => setDish(d.key, { price_cents: e.target.value ? Math.round(Number(e.target.value) * 100) : null })} /></label>
            </div>
            <div className="mi-lines">
              <div className="mi-line mi-line-head small muted">
                <span>Ingredient</span>
                <span className="tip" tabIndex={0} data-tip="Optional: how much goes on one plate. Leave it blank and fill it in later.">Portion</span>
                <span>Unit</span><span />
              </div>
              {d.lines.map((l, n) => {
                const okUnit = fits(l);
                const unit = l.qty && l.unit ? normUnit(l.unit) : normUnit(stockUnit(l));
                const units = portionUnits(l);
                return (
                  <div key={n} className="mi-line">
                    <div className="mi-ing">
                      <ItemPicker items={stock} itemId={l.item_id} itemName={l.item_id ? l.ingredient : null}
                        newName={l.item_id ? "" : l.ingredient}
                        onPickItem={(it) => setLine(d, n, { item_id: it.id, ingredient: it.name, match: "chosen",
                          unit: l.qty && unitsFor(it.unit, it.conversions ?? []).includes(normUnit(l.unit)) ? l.unit : null })}
                        onNewName={(name) => setLine(d, n, { item_id: null, ingredient: name, match: null })} />
                      {!l.item_id && l.ingredient.trim() && <span className="pill new" title="A new inventory item">New</span>}
                    </div>
                    <input className="input" type="number" min={0} step="any" aria-label="Portion" placeholder="–" value={l.qty || ""}
                      onChange={(e) => setLine(d, n, { qty: Number(e.target.value) || 0, unit: l.unit || unit })} />
                    <select className={`select ${okUnit ? "" : "is-invalid"}`} aria-label="Unit" value={unit}
                      title={okUnit ? undefined : `${l.ingredient} is counted in ${show(stockUnit(l))}: pick a unit that converts`}
                      onChange={(e) => setUnit(d, n, e.target.value)}>
                      {!units.includes(unit) && <option value={unit}>{show(unit)}{okUnit ? "" : " ✕"}</option>}
                      {units.map((u) => <option key={u} value={u}>{show(u)}</option>)}
                    </select>
                    <button className="sb-icon" aria-label={`Remove ${l.ingredient || "ingredient"}`} title="Remove"
                      onClick={() => setDish(d.key, { lines: d.lines.filter((_, j) => j !== n) })}>✕</button>
                  </div>
                );
              })}
              <button className="btn small" style={{ alignSelf: "start" }}
                onClick={() => setDish(d.key, { lines: [...d.lines, { ingredient: "", item_id: null, qty: 0, unit: null }] })}>
                + Add ingredient
              </button>
            </div>
          </div>
        )}
      </li>
    );
  }

  return (
    <Dialog title="Add menu items from your menu" onClose={onClose} width="min(1080px, 100%)" footer={footer}>
      {!imp && (
        <div className="stack mi-start">
          <p>Upload your menu and Pass picks out the menu items you don't have in Pass yet, and suggests the ingredients
            of each one, using your inventory items where it can. You check everything before anything is saved.</p>
          <div className="mi-drop">
            <input ref={file} type="file" hidden accept="image/*,application/pdf,.xlsx,.csv,.tsv,text/csv"
              onChange={(e) => void start(e.target.files?.[0])} />
            <input ref={camera} type="file" hidden accept="image/*" capture="environment"
              onChange={(e) => void start(e.target.files?.[0])} />
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn primary" disabled={busy !== ""} onClick={() => file.current?.click()}>
                {busy === "upload" ? "Uploading…" : "Choose a file"}
              </button>
              <button className="btn" disabled={busy !== ""} onClick={() => camera.current?.click()}>📷 Take a photo</button>
            </div>
            <p className="small muted">A photo or PDF of the menu, or an Excel (.xlsx) or CSV list of your dishes</p>
          </div>
          <button className="linkish small" style={{ alignSelf: "start" }} disabled={busy !== ""} onClick={() => void start()}>
            Or add menu items and their ingredients by hand
          </button>
          {pending.filter((p) => p.status !== "failed").map((p) => (
            <button key={p.id} className="linkish small" style={{ alignSelf: "start" }} onClick={() => void load(p.id)}>
              Continue {p.status === "processing" ? "the menu being read" : "where you left off"}: {p.file_name ?? "added by hand"}
              {p.dishes ? ` (${p.dishes} menu items)` : ""}
            </button>
          ))}
          {error && <p className="small error">{error}</p>}
        </div>
      )}

      {imp?.status === "processing" && (
        <div className="inv-reading">
          <span className="spinner" aria-hidden />
          {imp.progress?.total ? `Suggesting ingredients: ${imp.progress.done} of ${imp.progress.total} menu items…` : "Reading the menu…"}
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
          <p className="notice"><strong>{imp.result.dishes_created.length} menu item{imp.result.dishes_created.length === 1 ? "" : "s"} added with {imp.result.recipes === 1 ? "its recipe" : "their recipes"}.</strong></p>
          {imp.result.dishes_created.length > 0 && <p className="small">{imp.result.dishes_created.join(", ")}.</p>}
          {imp.result.items_created.length > 0 && <p className="small">New inventory items: {imp.result.items_created.join(", ")}.</p>}
          {imp.result.errors.length > 0 && <p className="small error">Not saved: {imp.result.errors.join("; ")}</p>}
          <p className="small muted">Next: fill in any missing portions with each menu item's Recipe button, and count
            your stock once so quantities start right. Costs fill in from your invoices.</p>
        </div>
      )}

      {imp?.status === "review" && (
        <>
          <div className="mi-board" role="group" aria-label="Overview">
            <button className="mi-stat" onClick={() => go("menu")} title="Go to the new menu items">
              <span className="mi-num">{real.length}</span><span>New menu items</span>
            </button>
            <button className="mi-stat" onClick={() => go("items")} title="Go to the inventory items these recipes add">
              <span className="mi-num">{newItems.length}</span><span>New inventory items</span>
            </button>
            <button className={`mi-stat ${empty.length ? "is-warn" : ""}`} onClick={() => go("missing")}
              title="Go to the menu items to add ingredients to by hand">
              <span className="mi-num">{byHand.length}</span><span>Unable to suggest recipe</span>
            </button>
          </div>
          {error && <p className="small error" role="alert">{error}</p>}
          <div className="stack mi-review">
            {imp.warnings.filter((w) => !w.startsWith("Already on your menu")).map((w, i) => <p key={i} className="small muted" style={{ margin: 0 }}>ℹ {w}</p>)}
            <section ref={menuRef} className="stack mi-section">
              <h3 className="mi-h">New menu items <span className="small muted">({suggested.length})</span></h3>
              {suggested.length > 0 ? (
                <>
                  <p className="small muted" style={{ margin: 0 }}>Check the ingredients of each menu item: swap, add or remove
                    them. Portions are optional for now.</p>
                  <ol className="mi-dishes">{suggested.map((d) => dishCard(d))}</ol>
                </>
              ) : <p className="small muted" style={{ margin: 0 }}>No new menu items with a suggested recipe.</p>}
            </section>

            <section ref={itemsRef} className="stack mi-section">
              <button className="mi-toggle" aria-expanded={sections.items} onClick={() => setSections((s) => ({ ...s, items: !s.items }))}>
                <span aria-hidden>{sections.items ? "▾" : "▸"}</span> New inventory items <span className="small muted">({newItems.length})</span>
              </button>
              {sections.items && (
                <>
                  <p className="small muted" style={{ margin: 0 }}>Added to your inventory with no stock and no cost yet: costs come
                    from your invoices, quantities from your first count. Check the names and what each is counted in.</p>
                  {newItems.length === 0 ? <p className="muted small">None: these recipes only use items you already have.</p> : (
                    <div className="mi-new-grid">
                      <span className="small muted">Name</span><span className="small muted">Counted in</span>
                      <span className="small muted">Category</span>
                      {newItems.map(([k, ing]) => (
                        <NewIngredient key={k} ing={ing}
                          onRename={(name) => renameIngredient(k, name)} clash={(name) => clash(k, name)}
                          onChange={(patch) => update(imp.dishes, { ...imp.ingredients, [k]: { ...ing, ...patch } })} />
                      ))}
                    </div>
                  )}
                </>
              )}
            </section>

            <section ref={missingRef} className="stack mi-section">
              <button className="mi-toggle" aria-expanded={sections.missing} onClick={() => setSections((s) => ({ ...s, missing: !s.missing }))}>
                <span aria-hidden>{sections.missing ? "▾" : "▸"}</span> Unable to suggest recipe <span className="small muted">({byHand.length})</span>
              </button>
              {sections.missing && (
                <>
                  <p className="small muted" style={{ margin: 0 }}>
                    {byHand.length ? "Add their ingredients by hand. Menu items left without ingredients aren't added."
                      : "Add a menu item here if one is missing."}
                  </p>
                  {byHand.length > 0 && <ol className="mi-dishes">{byHand.map((d) => dishCard(d))}</ol>}
                  <button className="btn" style={{ alignSelf: "start" }} onClick={addDish}>+ Add a menu item</button>
                </>
              )}
            </section>
          </div>
        </>
      )}
    </Dialog>
  );
}

function NewIngredient({ ing, onRename, onChange, clash }: {
  ing: Ingredient; onRename: (name: string) => void; onChange: (patch: Partial<Ingredient>) => void;
  clash: (name: string) => string | null;
}) {
  const [name, setName] = useState(ing.name);
  const warning = name.trim() !== ing.name ? clash(name) : null;
  const commit = () => { if (name.trim() && name.trim() !== ing.name) onRename(name.trim()); else setName(ing.name); };
  return (
    <>
      <input className={`input ${warning ? "is-warn" : ""}`} aria-label="Name" value={name} onChange={(e) => setName(e.target.value)}
        aria-describedby={warning ? `warn-${ing.name}` : undefined}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } else if (e.key === "Escape") setName(ing.name); }}
        onBlur={commit} />
      <select className="select" aria-label="Counted in" value={normUnit(ing.unit)} onChange={(e) => onChange({ unit: e.target.value })}>
        {STOCK_UNITS.map((u) => <option key={u} value={u}>{show(u)}</option>)}
      </select>
      <select className="select" aria-label="Category" value={ing.category ?? ""} onChange={(e) => onChange({ category: e.target.value || null })}>
        <option value="">Ingredients</option>
        {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
      </select>
      {warning && <p id={`warn-${ing.name}`} className="small warn-text mi-clash" role="status">⚠ {warning}</p>}
    </>
  );
}
