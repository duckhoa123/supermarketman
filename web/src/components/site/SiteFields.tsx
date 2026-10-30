import type { SiteEditor, SiteField } from "../../lib/api";
import ImagePicker from "./ImagePicker";

type Img = { id: string; url: string };

/** What a form needs besides the values: photos, menu, icons. */
export type FieldContext = {
  images: Img[];
  uploadPath: string;
  onUploaded: (img: Img) => void;
  categories: string[];
  menuItems: SiteEditor["menu_items"];
  icons: SiteEditor["icons"];
};

export function defaults(fields: SiteField[]): Record<string, unknown> {
  return Object.fromEntries(fields.map((f) => [f.name, structuredClone(f.default)]));
}

export function Icon({ paths, size = 20 }: { paths: string[]; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {paths.map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

function IconPicker({ value, onChange, icons }: { value: string; onChange: (v: string) => void; icons: FieldContext["icons"] }) {
  return (
    <div className="icon-grid" role="radiogroup">
      {icons.map((i) => (
        <button key={i.key} type="button" role="radio" aria-checked={value === i.key} title={i.label} aria-label={i.label}
          className={`icon-tile ${value === i.key ? "on" : ""}`} onClick={() => onChange(i.key)}>
          <Icon paths={i.paths} />
        </button>
      ))}
    </div>
  );
}

function Items({ field, value, onChange, ctx }: {
  field: SiteField; value: Record<string, unknown>[]; onChange: (v: Record<string, unknown>[]) => void; ctx: FieldContext;
}) {
  const sub = field.fields ?? [];
  const rows = Array.isArray(value) ? value : [];
  const set = (i: number, row: Record<string, unknown>) => onChange(rows.map((r, j) => (j === i ? row : r)));
  const move = (i: number, by: number) => {
    const next = rows.slice();
    const j = i + by;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="small">{field.label}</span>
      {rows.map((row, i) => (
        <div key={i} className="item-row">
          <div className="item-head">
            <span className="small muted">{i + 1}</span>
            <span style={{ flex: 1 }} />
            <button type="button" className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
            <button type="button" className="icon-btn" aria-label="Move down" disabled={i === rows.length - 1} onClick={() => move(i, 1)}>↓</button>
            <button type="button" className="icon-btn" aria-label="Remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>✕</button>
          </div>
          <FieldList fields={sub} values={row} onChange={(r) => set(i, r)} ctx={ctx} />
        </div>
      ))}
      {rows.length < (field.limit ?? 6) && (
        <button type="button" className="btn small" style={{ alignSelf: "start" }} onClick={() => onChange([...rows, defaults(sub)])}>
          + {field.add_label ?? "Add"}
        </button>
      )}
    </div>
  );
}

export function Field({ field, value, onChange, ctx }: {
  field: SiteField; value: unknown; onChange: (v: unknown) => void; ctx: FieldContext;
}) {
  switch (field.kind) {
    case "text":
      return (
        <label className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          {field.long || (field.limit ?? 0) > 300 ? (
            <textarea className="textarea" rows={5} maxLength={field.limit} placeholder={field.placeholder}
              value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
          ) : (
            <input className="input" maxLength={field.limit} placeholder={field.placeholder}
              value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
          )}
        </label>
      );
    case "bool":
      return (
        <label className="check small">
          <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
          {field.label}
        </label>
      );
    case "choice":
      return (
        <label className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          <select className="select" value={String(value ?? field.default)} onChange={(e) => onChange(e.target.value)}>
            {field.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      );
    case "int":
      return (
        <label className="row small">
          {field.label}
          <input className="input" style={{ width: 80 }} type="number" min={field.min} max={field.max}
            value={Number(value ?? field.default)} onChange={(e) => onChange(Number(e.target.value))} />
        </label>
      );
    case "image":
      return (
        <div className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          <ImagePicker images={ctx.images} value={(value as string | null) ?? null} onChange={(v) => onChange(v)}
            uploadPath={ctx.uploadPath} onUploaded={ctx.onUploaded} />
        </div>
      );
    case "images":
      return (
        <div className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          <ImagePicker images={ctx.images} multiple values={(value as string[]) ?? []} onChange={(v) => onChange(v)}
            uploadPath={ctx.uploadPath} onUploaded={ctx.onUploaded} />
        </div>
      );
    case "icon":
      return (
        <div className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          <IconPicker value={String(value ?? field.default)} onChange={onChange} icons={ctx.icons} />
        </div>
      );
    case "categories": {
      const chosen = (value as string[]) ?? [];
      return (
        <div className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          <div className="row">
            {ctx.categories.map((c) => (
              <label key={c} className="check small">
                <input type="checkbox" checked={chosen.includes(c)}
                  onChange={(e) => onChange(e.target.checked ? [...chosen, c] : chosen.filter((x) => x !== c))} />
                {c}
              </label>
            ))}
          </div>
        </div>
      );
    }
    case "dish": {
      const groups = Array.from(new Set(ctx.menuItems.map((m) => m.category)));
      return (
        <label className="stack" style={{ gap: 4 }}>
          <span className="small">{field.label}</span>
          <select className="select" value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)}>
            <option value="">Choose a dish…</option>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {ctx.menuItems.filter((m) => m.category === g).map((m) => <option key={m.id} value={m.id}>{m.name} · {m.price}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
      );
    }
    case "items":
      return <Items field={field} value={value as Record<string, unknown>[]} onChange={onChange} ctx={ctx} />;
    default:
      return null;
  }
}

/** A form for a set of server-declared fields. */
export function FieldList({ fields, values, onChange, ctx, skip = [] }: {
  fields: SiteField[]; values: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void;
  ctx: FieldContext; skip?: string[];
}) {
  return (
    <div className="stack">
      {fields.filter((f) => !skip.includes(f.name)).map((f) => (
        <Field key={f.name} field={f} value={values[f.name] ?? f.default} ctx={ctx}
          onChange={(v) => onChange({ ...values, [f.name]: v })} />
      ))}
    </div>
  );
}
