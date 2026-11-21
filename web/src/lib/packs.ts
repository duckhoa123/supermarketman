/** Packs on invoice lines: "1 case of 40 × 400 g" → what one invoice unit adds to stock (16 kg).
 * The same rules as the server (services/invoice_math.py), so the review shows the result while you type. */
import { convert, normUnit, unitKind } from "./inventory";

export type Pack = { pack_count: number | null; each_qty: number | null; each_unit: string | null };

const N = String.raw`(\d+(?:\.\d+)?)`;
const M = String.raw`(fl\.? ?oz|kgs?|kilos?|grams?|gm|gr|g|lbs?|#|oz|mls?|ml|ltr|lt|litres?|liters?|l)`;
const COUNT_SIZE = new RegExp(String.raw`^${N}\s*[x×*/]\s*${N}\s*${M}$`, "i");
const SIZE_COUNT = new RegExp(String.raw`^${N}\s*${M}\s*[x×*]\s*(\d+)$`, "i");
const SIZE = new RegExp(String.raw`^${N}\s*${M}$`, "i");
const COUNT = new RegExp(String.raw`^(\d+)\s*(?:x|×|pk|pack|ct|count|pcs|each|ea)?$`, "i");
const MULTI = new Set(["case", "box", "carton", "ctn", "crate", "flat", "tray", "pack"]);

const measure = (u: string | null | undefined) => {
  const k = unitKind(u);
  return k === "mass" || k === "volume";
};

/** What's typed in the Pack box: "40 × 400 g", "40x400g", "5 lb", "24". null when empty, undefined when unreadable. */
export function parsePack(text: string): Pack | null | undefined {
  const t = text.trim();
  if (!t) return null;
  let m = COUNT_SIZE.exec(t);
  if (m) return { pack_count: Number(m[1]), each_qty: Number(m[2]), each_unit: normUnit(m[3]) };
  m = SIZE_COUNT.exec(t);
  if (m) return { pack_count: Number(m[3]), each_qty: Number(m[1]), each_unit: normUnit(m[2]) };
  m = SIZE.exec(t);
  if (m) return { pack_count: 1, each_qty: Number(m[1]), each_unit: normUnit(m[2]) };
  m = COUNT.exec(t);
  if (m && Number(m[1]) > 0) return { pack_count: Number(m[1]), each_qty: null, each_unit: null };
  return undefined;
}

export function packText(p: Partial<Pack>): string {
  const { pack_count: c, each_qty: q, each_unit: u } = p;
  if (q && u) return `${c && c !== 1 ? `${c} × ` : ""}${q} ${u}`;
  return c && c > 1 ? String(c) : "";
}

function countUnknown(inv: string, p: Partial<Pack>) {
  if (!MULTI.has(inv) || (p.pack_count ?? 1) > 1 || !p.each_qty || !p.each_unit) return false;
  return unitKind(p.each_unit) === "volume" && (convert(p.each_qty, p.each_unit, "ml") ?? 0) <= 2000;
}

/** How much one invoice unit adds to stock, in the stock unit; null when the pack doesn't say. */
export function perInvoiceUnit(invUnit: string | null | undefined, p: Partial<Pack>, stockUnit: string | null | undefined): number | null {
  const inv = normUnit(invUnit), unit = normUnit(stockUnit);
  const count = p.pack_count || 1;
  if (!unit) return null;
  if (unit === inv) return 1;
  if (countUnknown(inv, p)) return null;
  if (measure(unit)) {
    if (measure(inv)) return convert(1, inv, unit);
    if (p.each_qty && p.each_unit) return convert(count * p.each_qty, p.each_unit, unit);
    return null;
  }
  if (measure(inv) && p.each_qty && p.each_unit) {
    const per = convert(1, inv, p.each_unit);
    return per == null ? null : per / p.each_qty;
  }
  return count > 1 && inv !== "each" && inv !== "" ? count : null;
}

/** What a new item is counted in: food by weight, bottles and cans one by one, else the invoice unit. */
export function defaultStockUnit(invUnit: string | null | undefined, p: Partial<Pack>, eachName?: string | null): string {
  const inv = normUnit(invUnit), size = normUnit(p.each_unit);
  const inner = eachName && normUnit(eachName) !== inv ? normUnit(eachName) : "each";
  if (measure(inv)) return inv;
  if (countUnknown(inv, p)) return inner;
  if (p.each_qty && measure(size)) {
    if (unitKind(size) === "mass") return size === "g" || size === "mg" ? "kg" : size;
    if ((p.pack_count ?? 1) > 1) return inner;
    return ["ml", "cl", "dl"].includes(size) ? "l" : size;
  }
  if ((p.pack_count ?? 1) > 1) return inner;
  return inv || "each";
}

/** Units a new item from this line could be counted in, the suggested one first. */
export function stockUnits(invUnit: string | null | undefined, p: Partial<Pack>, eachName?: string | null, current?: string | null): string[] {
  const family: Record<string, string[]> = { mass: ["kg", "g", "lb", "oz"], volume: ["l", "ml"] };
  const out = [defaultStockUnit(invUnit, p, eachName)];
  for (const u of [normUnit(invUnit), normUnit(p.each_unit)]) {
    const k = u ? unitKind(u) : null;
    if (k && family[k]) out.push(...family[k]);
  }
  if ((p.pack_count ?? 1) > 1 || p.each_qty) out.push(eachName ? normUnit(eachName) : "each");
  out.push(normUnit(invUnit) || "each");
  if (current) out.push(normUnit(current));
  return Array.from(new Set(out.filter(Boolean)));
}

export const fmtQty = (n: number) => (Math.round(n * 1000) / 1000).toLocaleString(undefined, { maximumFractionDigits: 3 });
