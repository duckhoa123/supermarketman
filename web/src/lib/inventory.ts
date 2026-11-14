import { api, ApiError } from "./api";

export type StockStatus = "ok" | "approaching" | "at_par" | "below_par" | "out" | "negative" | "untracked";

export type Conversion = { qty: number; unit: string; to_qty: number; to_unit: string };

export type StockItem = {
  id: string; name: string; category: string | null; unit: string | null; provider: string;
  stock_qty: number | null; avg_cost: number | null; value_cents: number | null; status: StockStatus;
  par_level: number | null; reorder_point: number | null; reorder_qty: number | null;
  storage_area_id: string | null; supplier_id: string | null; supplier: string | null; barcode: string | null;
  shelf_order: number | null; default_yield_pct: number | null; conversions: Conversion[];
  is_prep: boolean; batch_qty: number | null; batch_unit: string | null; has_recipe: boolean;
  price_cents: number; used_in: number; editable_name: boolean;
  last_count: { qty: number; at: string } | null; daily_usage: number | null; days_of_cover: number | null;
};

export type Area = { id: string; name: string; sort: number; items: number; value_cents: number | null };

export type OnHand = {
  items: StockItem[]; areas: Area[]; unassigned: number; untracked: number; can_manage: boolean;
  totals: { items: number; value_cents: number | null; approaching: number; at_par: number; below_par: number;
    out: number; negative: number; no_cost: number };
  target_food_cost_pct: number; target_beverage_cost_pct: number; cost_method: "average" | "last";
  units: string[]; categories: string[]; waste_reasons: Record<string, string>; suggested_areas: string[];
};

export type Movement = {
  id: string; item_id: string | null; item: string; kind: string; qty_delta: number; qty_after: number;
  unit: string | null; unit_cost: number | null; value_cents: number | null; source: string | null; reason: string | null;
  note: string | null; user: string | null; at: string;
};

export type AuditEntry = { id: string; item_id: string | null; item: string; field: string; old: string | null;
  new: string | null; user: string | null; at: string };

export type CountLine = { item_id: string; name: string; unit: string | null; expected: number | null; counted: number;
  delta: number; value_cents?: number; big?: boolean };
export type Count = { id: string; area: string; storage_area_id: string | null; status: "submitted" | "approved" | "rejected";
  counted_at: string; counted_by: string | null; started_at: string | null; approved_by: string | null;
  approved_at: string | null; items: number; changed: number; big: number; variance_cents: number | null;
  lines: CountLine[] };

export type MenuCost = {
  id: string; name: string; category: string | null; price_cents: number; has_recipe: boolean; ingredients: number;
  complete: boolean; sold_30d: number; revenue_30d_cents: number; plate_cost_cents: number | null;
  food_cost_pct: number | null; suggested_price_cents: number | null; margin_cents: number | null;
  target_pct: number; over_target: boolean; previous_cost_cents: number | null; previous_at: string | null;
};
export type PrepCost = { id: string; name: string; unit: string | null; batch_qty: number; has_recipe: boolean;
  complete: boolean; ingredients: number; batch_cost_cents: number | null; unit_cost: number | null; stock_qty: number | null };
export type RecipeReport = { menu: MenuCost[]; prep: PrepCost[]; todo: MenuCost[]; target_food_cost_pct: number;
  target_beverage_cost_pct: number; with_recipe: number; sellers: number; over_target: number; theoretical_pct: number | null };

export type RecipeLineOut = { id: string; ingredient_id: string; name: string; qty: number; unit: string | null;
  stock_unit: string | null; yield_pct: number; is_prep: boolean; conversions: Conversion[]; stock_qty: number | null;
  unit_cost: number | null; cost_cents: number | null; problem: string | null };

export type RecipeDetail = {
  id: string; name: string; category: string | null; price_cents: number; provider: string; is_prep: boolean; batch_qty: number | null;
  unit: string | null; q_factor_cents: number | null; q_factor_pct: number | null; recipe_notes: string | null;
  item_target_pct: number | null; recipe_since: string | null; complete: boolean; target_food_cost_pct: number;
  ingredients_cents: number; q_cents: number; plate_cost_cents: number; food_cost_pct: number | null;
  suggested_price_cents: number | null; margin_cents: number | null; over_target: boolean; unit_cost?: number;
  lines: RecipeLineOut[];
  history: { plate_cost_cents: number; price_cents: number; reason: string | null; at: string }[];
};

export type Cogs = {
  start: string; end: string; opening_cents: number; received_cents: number; closing_cents: number; cogs_cents: number;
  food_sales_cents: number; food_cost_pct: number | null; theoretical_cents: number; theoretical_pct: number | null;
  waste_cents: number; target_pct: number; counts: string[];
  by_category: { category: string; received_cents: number; used_cents: number; waste_cents: number }[];
  variance: { item_id: string | null; name: string; category: string; unit: string | null; theoretical_qty: number;
    actual_qty: number; variance_qty: number; variance_pct: number | null; variance_cents: number; waste_cents: number }[];
};

export const money = (cents: number | null | undefined, digits = 2) =>
  cents == null ? "–" : new Intl.NumberFormat("en-CA", {
    style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol", minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(cents / 100);

/** A unit cost can be a fraction of a cent (a gram of saffron); show enough digits to mean something. */
export const unitCost = (cents: number | null | undefined) =>
  cents == null ? "–" : money(cents, cents < 10 ? 4 : cents < 100 ? 3 : 2);

export const qty = (n: number | null | undefined) =>
  n == null ? "–" : Number.isInteger(n) ? String(n) : String(Math.round(n * 1000) / 1000);

// --- Units: the same table as the server (services/inventory.py), for live plate costs while editing -----------

const MASS: Record<string, number> = { mg: 0.001, g: 1, kg: 1000, oz: 28.349523125, lb: 453.59237 };
const VOLUME: Record<string, number> = { ml: 1, cl: 10, dl: 100, l: 1000, tsp: 4.92892159375, tbsp: 14.78676478125,
  floz: 29.5735295625, cup: 236.5882365, pt: 473.176473, qt: 946.352946, gal: 3785.411784 };
const COUNT: Record<string, number> = { each: 1, dozen: 12 };
export const PACK_UNITS = ["case", "bottle", "can", "bag", "box", "pack", "tub", "bunch"];
const ALIASES: Record<string, string> = {
  gram: "g", grams: "g", gr: "g", kilo: "kg", kilos: "kg", kgs: "kg", ounce: "oz", ounces: "oz", lbs: "lb", pound: "lb",
  pounds: "lb", litre: "l", liter: "l", litres: "l", liters: "l", ltr: "l", "fl oz": "floz", ea: "each", pc: "each",
  pcs: "each", piece: "each", pieces: "each", unit: "each", units: "each", ct: "each", "": "each", dz: "dozen",
  cases: "case", cs: "case", bottles: "bottle", btl: "bottle", cans: "can", bags: "bag", boxes: "box", packs: "pack",
};
export const normUnit = (u: string | null | undefined) => {
  const k = (u ?? "").trim().toLowerCase().replace(/\.$/, "");
  return ALIASES[k] ?? k;
};
const dim = (u: string): [string, number] | null =>
  u in MASS ? ["mass", MASS[u]] : u in VOLUME ? ["volume", VOLUME[u]] : u in COUNT ? ["count", COUNT[u]] : null;

function basic(n: number, a: string, b: string): number | null {
  if (a === b) return n;
  const da = dim(a), db = dim(b);
  return da && db && da[0] === db[0] ? (n * da[1]) / db[1] : null;
}

/** The same rules as the server: same-kind units convert by themselves; an item's own conversions
 * ("1 case = 40 lb", "1 cup = 4.25 oz") bridge kinds. */
export function convert(n: number, from: string | null | undefined, to: string | null | undefined, conversions: Conversion[] = []): number | null {
  const a = normUnit(from), b = normUnit(to);
  const direct = basic(n, a, b);
  if (direct != null) return direct;
  for (const c of conversions) {
    const ua = normUnit(c.unit), ub = normUnit(c.to_unit);
    if (!(c.qty > 0) || !(c.to_qty > 0)) continue;
    for (const [x, qx, y, qy] of [[ua, c.qty, ub, c.to_qty], [ub, c.to_qty, ua, c.qty]] as [string, number, string, number][]) {
      const here = basic(n, a, x);
      if (here != null) {
        const there = basic((here / qx) * qy, y, b);
        if (there != null) return there;
      }
    }
  }
  return null;
}

/** Units a portion of this ingredient can be written in: its own, the same kind, and any its conversions reach. */
export function unitsFor(stockUnit: string | null, conversions: Conversion[] = []): string[] {
  const u = normUnit(stockUnit) || "each";
  const all = [...Object.keys(MASS), ...Object.keys(VOLUME), ...Object.keys(COUNT)];
  const extra = conversions.flatMap((c) => [normUnit(c.unit), normUnit(c.to_unit)]);
  return Array.from(new Set([u, ...all.filter((x) => convert(1, x, u, conversions) != null), ...extra]));
}

export const STATUS_LABEL: Record<StockStatus, string> = {
  ok: "OK", approaching: "Near par", at_par: "At par", below_par: "Below par", out: "Out", negative: "Below zero",
  untracked: "Not counted",
};
export const STATUS_PILL: Record<StockStatus, string> = {
  ok: "good", approaching: "", at_par: "warn", below_par: "low", out: "low", negative: "low", untracked: "",
};

export const KIND_LABEL: Record<string, string> = {
  opening: "Opening", receive: "Delivery", sale: "Sold", waste: "Waste", count: "Count", adjust: "Adjusted",
  pos: "From POS", import: "Import", revalue: "Cost change", prep: "Prep",
};

// --- Offline: counts and waste made in a walk-in with no signal are kept on the phone and sent later -----------

type Pending = { key: string; path: string; body: unknown; label: string; at: string };
const QUEUE = "pass.inventory.pending";

function readQueue(): Pending[] {
  try { return JSON.parse(localStorage.getItem(QUEUE) || "[]") as Pending[]; } catch { return []; }
}
function writeQueue(q: Pending[]) {
  try { localStorage.setItem(QUEUE, JSON.stringify(q)); } catch { /* storage full or blocked */ }
  window.dispatchEvent(new Event("pass-inventory-queue"));
}

export const pendingCount = () => readQueue().length;
export const newClientId = (prefix: string) =>
  `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/** Send now; if there's no connection, keep it to send when there is. The body carries a client_id, so sending
 * twice is harmless. Returns the server's answer, or null if it was queued. */
export async function sendOrQueue<T>(path: string, body: unknown, label: string): Promise<T | null> {
  try {
    return await api<T>(path, { method: "POST", body });
  } catch (e) {
    if (e instanceof ApiError) throw e;  // the server said no: don't keep retrying it
    writeQueue([...readQueue(), { key: newClientId("q"), path, body, label, at: new Date().toISOString() }]);
    return null;
  }
}

let flushing = false;
/** Send whatever was kept offline. Returns how many went through. */
export async function flushQueue(): Promise<{ sent: number; failed: string[] }> {
  if (flushing) return { sent: 0, failed: [] };
  flushing = true;
  let sent = 0;
  const failed: string[] = [];
  try {
    for (const p of readQueue()) {
      try {
        await api(p.path, { method: "POST", body: p.body });
        sent += 1;
        writeQueue(readQueue().filter((x) => x.key !== p.key));
      } catch (e) {
        if (e instanceof ApiError) {
          failed.push(`${p.label}: ${e.message}`);
          writeQueue(readQueue().filter((x) => x.key !== p.key));
        } else break;  // still offline
      }
    }
  } finally {
    flushing = false;
  }
  return { sent, failed };
}

export function draftKey(locationId: string, what: string) {
  return `pass.inventory.${what}.${locationId}`;
}
export function loadDraft<T>(key: string): T | null {
  try { const v = localStorage.getItem(key); return v ? (JSON.parse(v) as T) : null; } catch { return null; }
}
export function saveDraft(key: string, value: unknown) {
  try { if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}
