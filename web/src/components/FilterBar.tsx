import { useState } from "react";

/** Filter a list by any of its fields: pick a field, a condition that fits its type (text, number, date or a fixed
 * choice), and a value. Conditions add up (all must match) and show as chips you can remove. */
export type FieldType = "text" | "number" | "date" | "choice";
export type FilterField<T> = {
  key: string; label: string; type: FieldType; get: (row: T) => string | number | boolean | null | undefined;
  options?: string[]; prefix?: string;
};
export type Condition = { field: string; op: string; value: string; value2?: string };

const OPS: Record<FieldType, [string, string][]> = {
  text: [["contains", "contains"], ["not_contains", "doesn't contain"], ["starts", "starts with"], ["ends", "ends with"],
    ["eq", "is"], ["neq", "is not"], ["empty", "is empty"], ["not_empty", "is not empty"]],
  number: [["eq", "="], ["neq", "≠"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["between", "between"],
    ["empty", "is empty"], ["not_empty", "is not empty"]],
  date: [["on", "on"], ["before", "before"], ["after", "after"], ["between", "between"], ["empty", "never"],
    ["not_empty", "any date"]],
  choice: [["eq", "is"], ["neq", "is not"], ["empty", "is empty"], ["not_empty", "is not empty"]],
};
const NO_VALUE = new Set(["empty", "not_empty"]);

function matches(raw: string | number | boolean | null | undefined, type: FieldType, c: Condition): boolean {
  const empty = raw == null || raw === "";
  if (c.op === "empty") return empty;
  if (c.op === "not_empty") return !empty;
  if (empty) return c.op === "neq" || c.op === "not_contains";
  if (type === "number") {
    const n = Number(raw), a = Number(c.value), b = Number(c.value2);
    switch (c.op) {
      case "eq": return n === a;
      case "neq": return n !== a;
      case "gt": return n > a;
      case "gte": return n >= a;
      case "lt": return n < a;
      case "lte": return n <= a;
      case "between": return n >= Math.min(a, b) && n <= Math.max(a, b);
    }
    return true;
  }
  if (type === "date") {
    const d = String(raw).slice(0, 10), a = c.value, b = c.value2 ?? "";
    switch (c.op) {
      case "on": return d === a;
      case "before": return d < a;
      case "after": return d > a;
      case "between": return d >= (a < b ? a : b) && d <= (a < b ? b : a);
    }
    return true;
  }
  const s = String(raw).toLowerCase(), v = c.value.toLowerCase();
  switch (c.op) {
    case "contains": return s.includes(v);
    case "not_contains": return !s.includes(v);
    case "starts": return s.startsWith(v);
    case "ends": return s.endsWith(v);
    case "eq": return s === v;
    case "neq": return s !== v;
  }
  return true;
}

export function applyFilters<T>(rows: T[], fields: FilterField<T>[], conds: Condition[]): T[] {
  if (!conds.length) return rows;
  const byKey = Object.fromEntries(fields.map((f) => [f.key, f]));
  return rows.filter((r) => conds.every((c) => {
    const f = byKey[c.field];
    return !f || matches(f.get(r), f.type, c);
  }));
}

export function describe<T>(c: Condition, fields: FilterField<T>[]): string {
  const f = fields.find((x) => x.key === c.field);
  if (!f) return "";
  const op = OPS[f.type].find(([k]) => k === c.op)?.[1] ?? c.op;
  if (NO_VALUE.has(c.op)) return `${f.label} ${op}`;
  const v = (x: string) => (f.prefix ?? "") + (f.type === "date" ? new Date(`${x}T12:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : x);
  return c.op === "between" ? `${f.label} between ${v(c.value)} and ${v(c.value2 ?? "")}` : `${f.label} ${op} ${v(c.value)}`;
}

export default function FilterBar<T>({ fields, conditions, onChange }: {
  fields: FilterField<T>[]; conditions: Condition[]; onChange: (c: Condition[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Condition>({ field: fields[0]?.key ?? "", op: OPS[fields[0]?.type ?? "text"][0][0], value: "" });
  const field = fields.find((f) => f.key === draft.field) ?? fields[0];
  const ops = OPS[field.type];
  const needsValue = !NO_VALUE.has(draft.op);
  const ready = !needsValue || (draft.value.trim() !== "" && (draft.op !== "between" || (draft.value2 ?? "").trim() !== ""));

  function pickField(key: string) {
    const f = fields.find((x) => x.key === key)!;
    setDraft({ field: key, op: OPS[f.type][0][0], value: f.type === "choice" ? (f.options?.[0] ?? "") : "", value2: "" });
  }
  function add(e?: React.FormEvent) {
    e?.preventDefault();
    if (!ready) return;
    onChange([...conditions, { ...draft, value: draft.value.trim(), value2: draft.value2?.trim() }]);
    setDraft({ ...draft, value: field.type === "choice" ? draft.value : "", value2: "" });
  }
  const input = (value: string, set: (v: string) => void, label: string) =>
    field.type === "choice" ? (
      <select className="select" aria-label={label} value={value} onChange={(e) => set(e.target.value)}>
        {(field.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    ) : (
      <input className="input" aria-label={label} type={field.type === "date" ? "date" : "text"}
        inputMode={field.type === "number" ? "decimal" : undefined} value={value} placeholder={field.type === "number" ? `${field.prefix ?? ""}0` : "Value"}
        onChange={(e) => set(e.target.value)} />
    );

  return (
    <div className="fb">
      <div className="fb-row">
        <button type="button" className={`btn ${open || conditions.length ? "fb-on" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 5h18l-7 8v6l-4 2v-8Z" /></svg>
          Filter{conditions.length ? ` (${conditions.length})` : ""}
        </button>
        {conditions.map((c, i) => (
          <span key={i} className="fb-chip">
            {describe(c, fields)}
            <button type="button" aria-label={`Remove filter: ${describe(c, fields)}`} onClick={() => onChange(conditions.filter((_, j) => j !== i))}>✕</button>
          </span>
        ))}
        {conditions.length > 1 && <button type="button" className="linkish small" onClick={() => onChange([])}>Clear all</button>}
      </div>
      {open && (
        <form className="fb-panel" onSubmit={add}>
          <select className="select" aria-label="Field" value={draft.field} onChange={(e) => pickField(e.target.value)}>
            {fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
          <select className="select" aria-label="Condition" value={draft.op} onChange={(e) => setDraft({ ...draft, op: e.target.value })}>
            {ops.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          {needsValue && input(draft.value, (v) => setDraft({ ...draft, value: v }), "Value")}
          {needsValue && draft.op === "between" && <span className="muted small">and</span>}
          {needsValue && draft.op === "between" && input(draft.value2 ?? "", (v) => setDraft({ ...draft, value2: v }), "Second value")}
          <button className="btn primary" disabled={!ready}>Add filter</button>
        </form>
      )}
    </div>
  );
}
