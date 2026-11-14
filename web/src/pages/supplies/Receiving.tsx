import { useRef, useState } from "react";
import { useApi } from "../../hooks";
import { api, fetchBlob, uploadFile, type InvoiceScan, type LogLine, type Supplier } from "../../lib/api";
import { prepareUpload } from "../../lib/image";
import { timeAgo } from "../../lib/format";

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toLocaleDateString("en-CA");
}

const ISSUE_LABEL = { short: "Short", damaged: "Damaged", wrong: "Substituted" } as const;
const centsMoney = (c: number | null | undefined) => new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol" }).format((c ?? 0) / 100);

/** Credits owed by suppliers for short, damaged or substituted lines: open until someone marks them resolved. */
export function Credits({ locationId }: { locationId: string }) {
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const { data, error, reload } = useApi<{ credits: (LogLine & { credit_note: string | null; credit_resolved_at: string | null })[]; open_cents: number }>(
    `/locations/${locationId}/credits?status=${status}`);
  async function resolve(id: string) {
    const note = window.prompt("How was it resolved? (credit note number, refund, replaced…)", "");
    if (note === null) return;
    await api(`/locations/${locationId}/credits/${id}/resolve`, { method: "POST", body: { note: note || null } });
    await reload();
  }
  return (
    <section className="panel stack" style={{ gap: 12 }}>
      <div className="spread">
        <div>
          <h2 style={{ margin: 0 }}>Supplier credits</h2>
          <p className="small muted">Flag a short, damaged or substituted line when you check in a delivery, and it waits here until the supplier credits you.</p>
        </div>
        <div className="seg">
          <button className={status === "open" ? "on" : ""} onClick={() => setStatus("open")}>Open</button>
          <button className={status === "resolved" ? "on" : ""} onClick={() => setStatus("resolved")}>Resolved</button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {data && status === "open" && <p className="small"><strong>{centsMoney(data.open_cents)}</strong> owed to you across {data.credits.length} line{data.credits.length === 1 ? "" : "s"}.</p>}
      {data && data.credits.length === 0 ? <p className="small muted">{status === "open" ? "No open credits." : "None resolved yet."}</p> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Date</th><th>Supplier</th><th>Item</th><th>Problem</th><th className="num">Credit</th><th /></tr></thead>
            <tbody>
              {data?.credits.map((c) => (
                <tr key={c.id}>
                  <td>{c.received_on}</td><td>{c.supplier}<div className="small muted">{c.invoice_number}</div></td>
                  <td>{c.description}</td>
                  <td>{c.issue ? ISSUE_LABEL[c.issue] : "–"}<div className="small muted">invoiced {c.qty}, got {c.received_qty ?? c.qty}</div></td>
                  <td className="num">{centsMoney(c.credit_cents)}</td>
                  <td className="num">{c.credit_status === "open" ? <button className="btn small" onClick={() => void resolve(c.id)}>Mark resolved</button>
                    : <span className="small muted">{c.credit_note ?? "resolved"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Pct({ value }: { value: number | null }) {
  if (value == null || value === 0) return null;
  return <span className={`pill ${value > 0 ? "low" : "good"}`}>{value > 0 ? "▲" : "▼"} {Math.abs(value)}%</span>;
}

export function Invoices({ locationId, onOpen }: { locationId: string; onOpen: (id: string) => void }) {
  const { data, reload, error } = useApi<InvoiceScan[]>(`/locations/${locationId}/invoices`);
  const camera = useRef<HTMLInputElement>(null);
  const files = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function upload(list: FileList | null) {
    if (!list?.length) return;
    setBusy(true);
    setMessage("");
    try {
      let last: InvoiceScan | null = null;
      for (const file of Array.from(list)) {
        const { blob, name } = await prepareUpload(file);
        last = await uploadFile<InvoiceScan>(`/locations/${locationId}/invoices`, blob, name);
      }
      await reload();
      if (list.length === 1 && last) onOpen(last.id);
      else setMessage(`${list.length} invoices uploaded. Open each one to check it.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (camera.current) camera.current.value = "";
      if (files.current) files.current.value = "";
    }
  }

  const waiting = data?.filter((s) => s.status === "review" || s.status === "processing" || s.status === "failed") ?? [];
  const done = data?.filter((s) => s.status === "confirmed") ?? [];
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="panel scan-box">
        <div>
          <h2>Scan a delivery invoice</h2>
          <p className="small muted">Lay it flat in good light with the whole page in view. PDFs from supplier emails work too.</p>
        </div>
        <div className="row">
          <button className="btn primary" disabled={busy} onClick={() => camera.current?.click()}>{busy ? "Uploading…" : "Take a photo"}</button>
          <button className="btn" disabled={busy} onClick={() => files.current?.click()}>Upload photo or PDF</button>
        </div>
        <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={(e) => void upload(e.target.files)} />
        <input ref={files} type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => void upload(e.target.files)} />
        {message && <p className="small">{message}</p>}
      </section>
      {error && <p className="error">{error}</p>}
      {waiting.length > 0 && (
        <section className="panel">
          <h2>To check</h2>
          <ul className="list">
            {waiting.map((s) => (
              <li key={s.id} className="spread">
                <div>
                  <strong>{s.supplier_name ?? s.file_name ?? "Invoice"}</strong>
                  <div className="small muted">
                    {s.invoice_number && `#${s.invoice_number} · `}{s.lines.length} lines · uploaded {s.created_at && timeAgo(s.created_at)}
                  </div>
                </div>
                <div className="row">
                  <span className={`pill ${s.status === "failed" ? "low" : "warn"}`}>{s.status === "processing" ? "reading…" : s.status === "review" ? "needs checking" : "failed"}</span>
                  <button className="btn small primary" onClick={() => onOpen(s.id)}>Open</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="panel">
        <h2>Received</h2>
        {done.length === 0 && <p className="muted">Nothing received yet.</p>}
        <ul className="list">
          {done.map((s) => (
            <li key={s.id} className="spread">
              <div>
                <strong>{s.supplier_name}</strong>
                <div className="small muted">{s.invoice_date} · #{s.invoice_number ?? "–"} · {s.lines.length} lines · {s.lines_total}</div>
              </div>
              <button className="btn small" onClick={() => onOpen(s.id)}>View</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function LogSheet({ locationId }: { locationId: string }) {
  const [start, setStart] = useState(daysAgo(30));
  const [end, setEnd] = useState(daysAgo(0));
  const [supplier, setSupplier] = useState("");
  const [q, setQ] = useState("");
  const [issue, setIssue] = useState("");
  const qs = `start=${start}&end=${end}${supplier ? `&supplier_id=${supplier}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}${issue ? `&issue=${issue}` : ""}`;
  const { data, error } = useApi<{ count: number; total: string; lines: LogLine[] }>(`/locations/${locationId}/receiving-log?${qs}`);
  const suppliers = useApi<Supplier[]>(`/locations/${locationId}/suppliers`);

  async function download() {
    const blob = await fetchBlob(`/locations/${locationId}/receiving-log?${qs}&format=csv`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `receiving-log-${start}-to-${end}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <section className="panel stack" style={{ gap: 12 }}>
      <div className="row">
        <label className="row small">From <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="row small">to <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <select className="select" style={{ width: "auto" }} value={supplier} onChange={(e) => setSupplier(e.target.value)} aria-label="Supplier">
          <option value="">All suppliers</option>
          {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="select" style={{ width: "auto" }} value={issue} onChange={(e) => setIssue(e.target.value)} aria-label="Status">
          <option value="">Any status</option>
          <option value="any">Any problem</option>
          <option value="short">Short</option>
          <option value="damaged">Damaged</option>
          <option value="wrong">Substituted / wrong</option>
        </select>
        <input className="input" style={{ flex: 1, minWidth: 140 }} placeholder="Search item or invoice no." value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn" onClick={() => void download()}>Download spreadsheet (CSV)</button>
      </div>
      {error && <p className="error">{error}</p>}
      {data && <p className="small muted">{data.count} lines · {data.total} received</p>}
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th>Date</th><th>Supplier</th><th>Invoice</th><th>Item</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">Total</th><th>Change</th></tr>
          </thead>
          <tbody>
            {data?.lines.map((l) => (
              <tr key={l.id}>
                <td>{l.received_on}</td>
                <td>{l.supplier}</td>
                <td className="muted">{l.invoice_number}</td>
                <td>
                  {l.description}
                  {l.item && <div className="small muted">→ {l.item}{l.stock_added ? ` (+${l.stock_added})` : ""}</div>}
                </td>
                <td className="num">{l.qty} {l.unit ?? ""}
                  {l.issue && <div className="small error">{ISSUE_LABEL[l.issue]}{l.received_qty != null ? `: got ${l.received_qty}` : ""}{l.credit_status === "open" ? " · credit open" : l.credit_status === "resolved" ? " · credited" : ""}</div>}
                </td>
                <td className="num">{l.unit_price}{l.previous_unit_price && l.price_change_pct ? <div className="small muted">was {l.previous_unit_price}</div> : null}</td>
                <td className="num">{l.line_total}</td>
                <td><Pct value={l.price_change_pct} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function PriceChanges({ locationId }: { locationId: string }) {
  const [days, setDays] = useState(90);
  const { data } = useApi<{ count: number; increases: number; decreases: number; changes: LogLine[] }>(
    `/locations/${locationId}/price-changes?days=${days}`);
  return (
    <section className="panel stack" style={{ gap: 12 }}>
      <div className="spread">
        <p>{data ? `${data.increases} increases and ${data.decreases} decreases` : "…"} compared with the previous delivery.</p>
        <select className="select" style={{ width: "auto" }} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
          <option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={365}>Last year</option>
        </select>
      </div>
      {data?.count === 0 && <p className="muted">No price changes yet. They appear once a product has been delivered twice.</p>}
      <ul className="list">
        {data?.changes.map((c) => (
          <li key={c.id} className="spread">
            <div>
              <strong>{c.description}</strong>
              <div className="small muted">{c.supplier} · {c.received_on} · invoice {c.invoice_number}</div>
            </div>
            <div className="row">
              <span className="small">{c.previous_unit_price} → <strong>{c.unit_price}</strong>{c.unit ? ` / ${c.unit}` : ""}</span>
              <Pct value={c.price_change_pct} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
