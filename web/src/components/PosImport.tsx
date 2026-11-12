import { useRef, useState } from "react";
import { uploadFile } from "../lib/api";

type Field = { key: string; label: string; required: boolean };
type Preview = {
  kind: "items" | "orders" | "sales_report"; label: string; sheet: string; rows: number; header_row?: number;
  columns?: string[]; fields?: Field[]; mapping?: Record<string, number | null>; sample?: Record<string, string>[];
  warnings: string[];
  day?: string | null; needs_day?: boolean; multi_day?: boolean;
  totals?: { sales: number; tax: number; tips: number; orders: number | null; busiest_hour: string | null };
};
type Result = { kind: string; added?: number; updated?: number; skipped?: number; from?: string | null; to?: string | null;
  day?: string; sales_cents?: number; orders?: number | null };

const money = (cents: number) =>
  new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol" }).format(cents / 100);
const hourLabel = (h: string) => { const n = Number(h); return `${n % 12 || 12} ${n < 12 ? "a.m." : "p.m."}`; };

/** Bring in the restaurant's data from files exported from Clover's dashboard, without connecting Clover. */
export default function PosImport({ locationId }: { locationId: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, number | null>>({});
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<string[]>([]);

  async function pick(f: File | undefined) {
    if (!f) return;
    setFile(f);
    setError("");
    setPreview(null);
    setBusy(true);
    try {
      const p = await uploadFile<Preview>(`/locations/${locationId}/imports/preview`, f, f.name);
      setPreview(p);
      setMapping(p.mapping ?? {});
      setDay(p.day ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't read that file.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function run() {
    if (!file || !preview) return;
    setBusy(true);
    setError("");
    try {
      const fields: Record<string, string> = { kind: preview.kind };
      if (preview.kind === "sales_report") {
        if (day) fields.day = day;
      } else {
        fields.mapping = JSON.stringify(mapping);
      }
      const r = await uploadFile<Result>(`/locations/${locationId}/imports`, file, file.name, fields);
      const line = r.kind === "sales_report"
        ? `${file.name}: sales for ${r.day} imported (${money(r.sales_cents ?? 0)}${r.orders ? `, ${r.orders} orders` : ""}).`
        : `${file.name}: ${r.added} new and ${r.updated} updated ${r.kind === "items" ? "items" : "orders"}`
          + (r.kind === "orders" && r.from ? ` from ${r.from}${r.to && r.to !== r.from ? ` to ${r.to}` : ""}` : "")
          + (r.skipped ? `, ${r.skipped} rows skipped (no name or date)` : "") + ".";
      setDone((d) => [line, ...d]);
      setPreview(null);
      setFile(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't import the file.");
    } finally {
      setBusy(false);
    }
  }

  const missing = preview?.fields?.filter((f) => f.required && mapping[f.key] == null) ?? [];
  const shown = preview?.fields?.filter((f) => mapping[f.key] != null) ?? [];

  return (
    <div className="stack" style={{ gap: 12 }}>
      <div>
        <p className="small muted" style={{ margin: 0 }}>
          No live connection needed: download your data from Clover's web dashboard and upload the files here.
          You can upload the same file again later to bring things up to date. Nothing is counted twice.
        </p>
        <ol className="small import-steps">
          <li><strong>Menu items</strong>: in Clover, <em>Items → ⋮ → Export</em>.</li>
          <li><strong>Orders and what sold</strong>: <em>Sales activity → Orders</em>, choose the dates, then <em>Export</em>.</li>
          <li><strong>A day's sales totals</strong>: <em>Reports → Sales</em>, choose one day, then <em>Export</em>.</li>
        </ol>
      </div>
      <div className="row">
        <button className="btn primary" disabled={busy} onClick={() => input.current?.click()}>
          {busy && !preview ? "Reading the file…" : "Choose a Clover export file"}
        </button>
        <span className="small muted">Excel (.xlsx) or CSV</span>
      </div>
      <input ref={input} type="file" hidden accept=".xlsx,.csv,.tsv,.txt,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
        onChange={(e) => void pick(e.target.files?.[0])} />
      {error && <p className="small error" role="alert">{error}</p>}

      {preview && (
        <div className="import-preview stack" style={{ gap: 10 }}>
          <div className="spread">
            <strong>{file?.name}: {preview.kind === "sales_report" ? "a Clover sales report" : `${preview.rows} ${preview.label}`}</strong>
            <button className="sb-icon" aria-label="Cancel" onClick={() => { setPreview(null); setFile(null); }}>✕</button>
          </div>
          {preview.warnings.map((w) => <p key={w} className="small warn-text" style={{ margin: 0 }}>⚠ {w}</p>)}

          {preview.kind === "sales_report" && preview.totals && (
            <>
              <div className="import-totals">
                <div><span className="small muted">Sales (incl. tax)</span><strong>{money(preview.totals.sales)}</strong></div>
                <div><span className="small muted">Tax</span><strong>{money(preview.totals.tax)}</strong></div>
                <div><span className="small muted">Tips</span><strong>{money(preview.totals.tips)}</strong></div>
                {preview.totals.orders != null && <div><span className="small muted">Orders</span><strong>{preview.totals.orders}</strong></div>}
                {preview.totals.busiest_hour && <div><span className="small muted">Busiest hour</span><strong>{hourLabel(preview.totals.busiest_hour)}</strong></div>}
              </div>
              <label className="row small">
                Which day is this report for?
                <input className="input" type="date" style={{ width: "auto" }} value={day} onChange={(e) => setDay(e.target.value)} />
              </label>
            </>
          )}

          {preview.kind !== "sales_report" && preview.fields && preview.columns && (
            <>
              <p className="small muted" style={{ margin: 0 }}>
                Check which column holds what. Pass guessed these from the column names
                {preview.header_row && preview.header_row > 1 ? ` (row ${preview.header_row}` : " ("}{preview.sheet ? ` of “${preview.sheet}”` : ""}).
              </p>
              <div className="import-map">
                {preview.fields.map((f) => (
                  <label key={f.key}>
                    <span>{f.label}{f.required && " *"}</span>
                    <select className="select" value={mapping[f.key] ?? ""}
                      onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value === "" ? null : Number(e.target.value) })}>
                      <option value="">Not in this file</option>
                      {preview.columns!.map((c, i) => <option key={i} value={i}>{c || `Column ${i + 1}`}</option>)}
                    </select>
                  </label>
                ))}
              </div>
              {preview.sample && preview.sample.length > 0 && shown.length > 0 && (
                <div className="table-wrap">
                  <table className="data small">
                    <thead><tr>{shown.map((f) => <th key={f.key}>{f.label}</th>)}</tr></thead>
                    <tbody>
                      {preview.sample.map((row, i) => (
                        <tr key={i}>{shown.map((f) => <td key={f.key}>{row[f.key] ?? ""}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="small muted">The first rows, as Pass will read them. Re-check the columns if anything looks wrong.</p>
                </div>
              )}
            </>
          )}

          <button className="btn primary" style={{ alignSelf: "start" }}
            disabled={busy || missing.length > 0 || (preview.kind === "sales_report" && !day)} onClick={() => void run()}>
            {busy ? "Importing…" : preview.kind === "sales_report" ? `Import sales${day ? ` for ${day}` : ""}`
              : `Import ${preview.rows} ${preview.label}`}
          </button>
          {missing.length > 0 && <p className="small error" style={{ margin: 0 }}>Choose a column for: {missing.map((f) => f.label).join(", ")}.</p>}
        </div>
      )}

      {done.length > 0 && (
        <div className="notice">
          {done.map((d) => <span key={d} className="small">✓ {d}</span>)}
          <span className="small muted">See them on Stock, Orders and Today.</span>
        </div>
      )}
    </div>
  );
}
