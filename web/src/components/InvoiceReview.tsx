import { useEffect, useState } from "react";
import { useApp } from "../context";
import { api, fetchBlob, type DraftLine, type InventoryItem, type InvoiceScan, type Supplier } from "../lib/api";

const dollars = (cents: number | null | undefined) => (cents == null ? "" : (cents / 100).toFixed(2));
const toCents = (v: string) => Math.round((Number(v) || 0) * 100);

function Change({ line }: { line: DraftLine }) {
  if (line.kind !== "item") return null;
  if (line.change_pct == null) return <span className="pill">new</span>;
  if (line.change_pct === 0) return <span className="pill">same</span>;
  return (
    <span className={`pill ${line.change_pct > 0 ? "low" : "good"}`}
      title={`Last paid $${dollars(line.last_price_cents)} on ${line.last_received_on}`}>
      {line.change_pct > 0 ? "▲" : "▼"} {Math.abs(line.change_pct)}%
    </span>
  );
}

/** Check what the AI read off an invoice, fix anything, match lines to stock, then confirm. */
export default function InvoiceReview({ scanId, onClose }: { scanId: string; onClose: () => void }) {
  const { location } = useApp();
  const base = `/locations/${location.id}/invoices/${scanId}`;
  const [scan, setScan] = useState<InvoiceScan | null>(null);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false);

  async function load() {
    setScan(await api<InvoiceScan>(base));
  }

  useEffect(() => {
    void load();
    void api<{ items: InventoryItem[] }>(`/locations/${location.id}/inventory`).then((r) => setItems(r.items));
    void api<Supplier[]>(`/locations/${location.id}/suppliers`).then(setSuppliers);
    let url: string | null = null;
    fetchBlob(`${base}/file`)
      .then((b) => setPreview((url = URL.createObjectURL(b))))
      .catch(() => setPreview(null));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [scanId]); // eslint-disable-line react-hooks/exhaustive-deps

  // While the AI is reading, check back every few seconds.
  useEffect(() => {
    if (scan?.status !== "processing") return;
    const t = window.setTimeout(() => void load(), 3000);
    return () => window.clearTimeout(t);
  }, [scan]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!scan) return <p className="muted">Loading…</p>;
  const editable = scan.status === "review";

  function setLine(i: number, patch: Partial<DraftLine>) {
    if (!scan) return;
    const lines = scan.lines.map((l, j) => {
      if (j !== i) return l;
      const next = { ...l, ...patch };
      if ("qty" in patch || "unit_price_cents" in patch) next.line_total_cents = Math.round(next.qty * next.unit_price_cents);
      return next;
    });
    setScan({ ...scan, lines });
    setDirty(true);
  }

  async function save(): Promise<InvoiceScan | null> {
    if (!scan) return null;
    try {
      const out = await api<InvoiceScan>(base, {
        method: "PUT",
        body: {
          supplier_id: scan.supplier_id, supplier_name: scan.supplier_name, invoice_number: scan.invoice_number,
          invoice_date: scan.invoice_date, lines: scan.lines.map(({ last_price_cents, last_received_on, change_pct, ...l }) => l),
        },
      });
      setScan(out);
      setDirty(false);
      return out;
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't save.");
      return null;
    }
  }

  async function confirm(allowDuplicate = false) {
    setBusy(true);
    setMessage("");
    try {
      if (dirty && !(await save())) return;
      const r = await api<{ lines: number; stock_updates: { item: string; added: number }[]; price_changes: unknown[]; alerted: boolean }>(
        `${base}/confirm`, { method: "POST", body: { allow_duplicate: allowDuplicate } });
      setMessage(
        `Logged ${r.lines} lines.` +
          (r.stock_updates.length ? ` Stock updated for ${r.stock_updates.map((s) => `${s.item} (+${s.added})`).join(", ")}.` : "") +
          (r.alerted ? " Price changes were flagged to your team." : "")
      );
      await load();
    } catch (e) {
      const text = e instanceof Error ? e.message : "Couldn't confirm.";
      if (text.includes("already received") && window.confirm(`${text}\n\nLog it anyway?`)) return void (await confirm(true));
      setMessage(text);
    } finally {
      setBusy(false);
    }
  }

  async function action(path: string) {
    setBusy(true);
    try {
      await api(`${base}/${path}`, { method: "POST", body: {} });
      if (path === "discard") onClose();
      else await load();
    } finally {
      setBusy(false);
    }
  }

  const stockItems = items.filter((i) => i.stock_qty !== null);
  const otherItems = items.filter((i) => i.stock_qty === null);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="spread">
        <button className="btn small" onClick={onClose}>← All invoices</button>
        <span className={`pill ${scan.status === "confirmed" ? "good" : scan.status === "failed" ? "low" : "warn"}`}>{scan.status}</span>
      </div>
      <div className="invoice-layout">
        <section className="panel invoice-preview">
          {preview ? (
            scan.content_type === "application/pdf" ? (
              <object data={preview} type="application/pdf" aria-label="Invoice"><a href={preview} target="_blank" rel="noreferrer">Open the PDF</a></object>
            ) : (
              <a href={preview} target="_blank" rel="noreferrer"><img src={preview} alt="Scanned invoice" /></a>
            )
          ) : (
            <p className="muted small">No image stored for this invoice.</p>
          )}
        </section>
        <section className="panel stack" style={{ gap: 12 }}>
          {scan.status === "processing" && <p>Reading the invoice… this usually takes under a minute.</p>}
          {scan.status === "failed" && (
            <>
              <p className="error">{scan.error}</p>
              <div className="row">
                <button className="btn primary small" disabled={busy} onClick={() => void action("retry")}>Try again</button>
                <button className="btn small" disabled={busy} onClick={() => void action("discard")}>Discard</button>
              </div>
            </>
          )}
          {(scan.status === "review" || scan.status === "confirmed") && (
            <>
              <div className="row">
                <label className="stack" style={{ gap: 4, flex: 2, minWidth: 200 }}>
                  <span className="small">Supplier</span>
                  <input className="input" list="supplier-names" disabled={!editable} value={scan.supplier_name ?? ""}
                    onChange={(e) => {
                      const match = suppliers.find((s) => s.name === e.target.value);
                      setScan({ ...scan, supplier_name: e.target.value, supplier_id: match?.id ?? null });
                      setDirty(true);
                    }} />
                  <datalist id="supplier-names">{suppliers.map((s) => <option key={s.id} value={s.name} />)}</datalist>
                </label>
                <label className="stack" style={{ gap: 4, flex: 1, minWidth: 120 }}>
                  <span className="small">Invoice no.</span>
                  <input className="input" disabled={!editable} value={scan.invoice_number ?? ""}
                    onChange={(e) => { setScan({ ...scan, invoice_number: e.target.value }); setDirty(true); }} />
                </label>
                <label className="stack" style={{ gap: 4, flex: 1, minWidth: 150 }}>
                  <span className="small">Date</span>
                  <input className="input" type="date" disabled={!editable} value={scan.invoice_date ?? ""}
                    onChange={(e) => { setScan({ ...scan, invoice_date: e.target.value || null }); setDirty(true); }} />
                </label>
              </div>
              {scan.warnings.map((w, i) => <p key={i} className="small warn-text">⚠ {w}</p>)}
              <div className="table-wrap">
                <table className="data invoice-lines">
                  <thead>
                    <tr>
                      <th>Item on invoice</th><th className="num">Qty</th><th>Unit</th><th className="num">Unit $</th>
                      <th className="num">Total</th><th>Your stock item</th><th className="num" title="Stock units in one invoice unit, e.g. 24 bottles in a case">Per unit</th><th>Price</th>{editable && <th />}
                    </tr>
                  </thead>
                  <tbody>
                    {scan.lines.map((l, i) => (
                      <tr key={i}>
                        <td>
                          <input className="input" disabled={!editable} value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} />
                          {l.kind !== "item" && <span className="pill" style={{ marginTop: 4 }}>{l.kind}</span>}
                        </td>
                        <td className="num"><input className="input num-in" type="number" step="any" disabled={!editable} value={l.qty}
                          onChange={(e) => setLine(i, { qty: Number(e.target.value) })} /></td>
                        <td><input className="input unit-in" disabled={!editable} value={l.unit ?? ""} onChange={(e) => setLine(i, { unit: e.target.value || null })} /></td>
                        <td className="num"><input className="input num-in" type="number" step="0.01" disabled={!editable} value={dollars(l.unit_price_cents)}
                          onChange={(e) => setLine(i, { unit_price_cents: toCents(e.target.value) })} /></td>
                        <td className="num">${dollars(l.line_total_cents)}</td>
                        <td>
                          <select className="select" disabled={!editable || l.kind !== "item"} value={l.item_id ?? ""}
                            onChange={(e) => setLine(i, { item_id: e.target.value || null, match: e.target.value ? "chosen" : "none" })}>
                            <option value="">Not a stock item</option>
                            {stockItems.length > 0 && <optgroup label="Stock tracked">{stockItems.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}</optgroup>}
                            {otherItems.length > 0 && <optgroup label="Other items">{otherItems.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}</optgroup>}
                          </select>
                          {l.match && l.match !== "none" && l.match !== "chosen" && <div className="small muted">{l.match === "remembered" ? "from earlier invoices" : `matched by ${l.match}`}</div>}
                        </td>
                        <td className="num"><input className="input num-in" type="number" min={0.001} step="any" disabled={!editable || !l.item_id} value={l.pack_size}
                          onChange={(e) => setLine(i, { pack_size: Number(e.target.value) || 1 })} /></td>
                        <td><Change line={l} /></td>
                        {editable && (
                          <td><button className="btn small" aria-label="Remove line" onClick={() => { setScan({ ...scan, lines: scan.lines.filter((_, j) => j !== i) }); setDirty(true); }}>×</button></td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {editable && (
                <button className="btn small" style={{ alignSelf: "start" }} onClick={() => {
                  setScan({ ...scan, lines: [...scan.lines, { description: "", qty: 1, unit: null, unit_price_cents: 0, line_total_cents: 0, kind: "item", item_id: null, pack_size: 1, match: "none" }] });
                  setDirty(true);
                }}>+ Add a line</button>
              )}
              <div className="spread">
                <span className="small muted">
                  Lines total {scan.lines_total}
                  {scan.subtotal_cents != null && ` · invoice subtotal $${dollars(scan.subtotal_cents)}`}
                  {scan.total_cents != null && ` · total $${dollars(scan.total_cents)}`}
                </span>
                {editable && (
                  <div className="row">
                    {dirty && <button className="btn small" disabled={busy} onClick={() => void save()}>Save changes</button>}
                    <button className="btn small" disabled={busy} onClick={() => void action("discard")}>Discard</button>
                    <button className="btn primary" disabled={busy} onClick={() => void confirm()}>Confirm and add to stock</button>
                  </div>
                )}
              </div>
            </>
          )}
          {message && <p className="small up" role="status">{message}</p>}
        </section>
      </div>
    </div>
  );
}
