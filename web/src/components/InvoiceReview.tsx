import { useEffect, useState } from "react";
import { useApp } from "../context";
import { api, fetchBlob, type DraftLine, type InventoryItem, type InvoiceScan, type Supplier } from "../lib/api";
import { Dialog } from "./PosDialogs";

const dollars = (cents: number | null | undefined) => (cents == null ? "" : (cents / 100).toFixed(2));
const toCents = (v: string) => Math.round((Number(v) || 0) * 100);
const NEW = "__new__";
const NONE = "__none__";

type Imported = { lines: number; stock_updates: { item: string; added: number; unit?: string | null }[];
  created_items: string[]; alerted: boolean;
  issues: { description: string; issue: string; invoiced: number; received: number }[] };

function Change({ line }: { line: DraftLine }) {
  if (line.kind !== "item") return null;
  if (line.change_pct == null) return null;
  if (line.change_pct === 0) return <span className="pill">same price</span>;
  return (
    <span className={`pill ${line.change_pct > 0 ? "low" : "good"}`}
      title={`Last paid $${dollars(line.last_price_cents)} on ${line.last_received_on}`}>
      {line.change_pct > 0 ? "▲" : "▼"} {Math.abs(line.change_pct)}% vs last time
    </span>
  );
}

/** Unmatched product lines are added to inventory as new stock items unless the owner says otherwise. */
function withDefaults(scan: InvoiceScan): [InvoiceScan, boolean] {
  if (scan.status !== "review") return [scan, false];
  let changed = false;
  const lines = scan.lines.map((l) => {
    if (l.kind === "item" && !l.item_id && l.match !== "none" && l.create_item === undefined) {
      changed = true;
      return { ...l, create_item: true };
    }
    return l;
  });
  return [{ ...scan, lines }, changed];
}

/** After an invoice is scanned: check every line in a pop-up, download it as Excel, and import it into inventory
 * (stock goes up, new products become stock items, price changes are flagged). */
export default function InvoiceReview({ scanId, onClose }: { scanId: string; onClose: () => void }) {
  const { location } = useApp();
  const base = `/locations/${location.id}/invoices/${scanId}`;
  const [scan, setScan] = useState<InvoiceScan | null>(null);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [showPhoto, setShowPhoto] = useState(() => window.innerWidth > 900);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false);
  const [imported, setImported] = useState<Imported | null>(null);

  async function load() {
    const [s, changed] = withDefaults(await api<InvoiceScan>(base));
    setScan(s);
    if (changed) setDirty(true);
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

  const editable = scan?.status === "review";

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

  function setTarget(i: number, value: string) {
    if (value === NEW) setLine(i, { item_id: null, create_item: true, match: "new" });
    else if (value === NONE) setLine(i, { item_id: null, create_item: false, match: "none" });
    else setLine(i, { item_id: value, create_item: false, match: "chosen" });
  }

  async function save(): Promise<InvoiceScan | null> {
    if (!scan) return null;
    try {
      const out = await api<InvoiceScan>(base, {
        method: "PUT",
        body: {
          supplier_id: scan.supplier_id, supplier_name: scan.supplier_name, invoice_number: scan.invoice_number,
          invoice_date: scan.invoice_date,
          lines: scan.lines.filter((l) => l.description.trim()).map(({ last_price_cents, last_received_on, change_pct, ...l }) => l),
        },
      });
      setScan(withDefaults(out)[0]);
      setDirty(false);
      return out;
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't save.");
      return null;
    }
  }

  async function download() {
    setBusy("excel");
    setMessage("");
    try {
      if (dirty && editable && !(await save())) return;
      const blob = await fetchBlob(`${base}/export.xlsx`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(scan?.supplier_name || "invoice").replace(/[^\w-]+/g, "-")}-${scan?.invoice_number || scanId.slice(0, 8)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't make the Excel file.");
    } finally {
      setBusy("");
    }
  }

  async function importIt(allowDuplicate = false) {
    setBusy("import");
    setMessage("");
    try {
      if (dirty && !(await save())) return;
      const r = await api<Imported>(`${base}/confirm`, { method: "POST", body: { allow_duplicate: allowDuplicate } });
      setImported(r);
      await load();
    } catch (e) {
      const text = e instanceof Error ? e.message : "Couldn't import.";
      if (text.includes("already received") && window.confirm(`${text}\n\nImport it anyway?`)) return void (await importIt(true));
      setMessage(text);
    } finally {
      setBusy("");
    }
  }

  async function action(path: "discard" | "retry") {
    if (path === "discard" && !window.confirm("Throw this invoice away? Nothing is added to stock.")) return;
    setBusy(path);
    try {
      await api(`${base}/${path}`, { method: "POST", body: {} });
      if (path === "discard") onClose();
      else await load();
    } finally {
      setBusy("");
    }
  }

  const stockItems = items.filter((i) => i.stock_qty !== null);
  const otherItems = items.filter((i) => i.stock_qty === null);
  const products = scan?.lines.filter((l) => l.kind === "item") ?? [];
  const toImport = products.filter((l) => l.item_id || l.create_item).length;
  const newOnes = products.filter((l) => !l.item_id && l.create_item).length;
  const status = scan?.status;

  const title = (
    <span className="row" style={{ gap: 8 }}>
      {status === "confirmed" ? "Imported invoice" : status === "processing" ? "Reading the invoice…" : "Check the invoice"}
      {scan?.supplier_name && <span className="small muted">{scan.supplier_name}</span>}
    </span>
  );

  const footer = !scan ? undefined : (
    <div className="inv-foot">
      <span className="small muted">
        {scan.lines.length > 0 && <>Lines total <strong>{scan.lines_total}</strong></>}
        {scan.total_cents != null && ` · invoice total $${dollars(scan.total_cents)}`}
      </span>
      <div className="row">
        {(status === "review" || status === "confirmed") && scan.lines.length > 0 && (
          <button className="btn" disabled={busy !== ""} onClick={() => void download()}>
            {busy === "excel" ? "Preparing…" : "⬇ Download Excel"}
          </button>
        )}
        {editable && (
          <>
            <button className="btn" disabled={busy !== ""} onClick={() => void action("discard")}>Discard</button>
            {dirty && <button className="btn" disabled={busy !== ""} onClick={() => void save()}>Save for later</button>}
            <button className="btn primary" disabled={busy !== "" || scan.lines.length === 0} onClick={() => void importIt()}>
              {busy === "import" ? "Importing…" : toImport ? `Import ${toImport} item${toImport === 1 ? "" : "s"} to inventory` : "Log the invoice"}
            </button>
          </>
        )}
        {status === "confirmed" && <button className="btn primary" onClick={onClose}>Done</button>}
      </div>
    </div>
  );

  return (
    <Dialog title={title} onClose={onClose} width="min(1180px, 100%)" footer={footer}>
      {!scan && <p className="muted">Loading…</p>}
      {scan && (
        <div className={`inv-dialog ${showPhoto && preview ? "with-photo" : ""}`}>
          {showPhoto && preview && (
            <aside className="inv-photo">
              {scan.content_type === "application/pdf" ? (
                <object data={preview} type="application/pdf" aria-label="Invoice"><a href={preview} target="_blank" rel="noreferrer">Open the PDF</a></object>
              ) : (
                <a href={preview} target="_blank" rel="noreferrer" title="Open full size"><img src={preview} alt="Scanned invoice" /></a>
              )}
            </aside>
          )}
          <div className="stack inv-main">
            {preview && (
              <button className="linkish small" style={{ alignSelf: "start" }} onClick={() => setShowPhoto(!showPhoto)}>
                {showPhoto ? "Hide the invoice photo" : "Show the invoice photo"}
              </button>
            )}
            {status === "processing" && (
              <div className="inv-reading"><span className="spinner" aria-hidden /> Reading every line of the invoice… this usually takes under a minute.</div>
            )}
            {status === "failed" && (
              <>
                <p className="error">{scan.error}</p>
                <div className="row">
                  <button className="btn primary small" disabled={busy !== ""} onClick={() => void action("retry")}>Try again</button>
                  <button className="btn small" disabled={busy !== ""} onClick={() => void action("discard")}>Discard</button>
                </div>
              </>
            )}
            {imported && (
              <div className="notice inv-done" role="status">
                <strong>Imported {imported.lines} lines.</strong>
                {imported.stock_updates.length > 0 && (
                  <span>Stock went up: {imported.stock_updates.map((s) => `${s.item} +${s.added}${s.unit ? ` ${s.unit}` : ""}`).join(", ")}.</span>
                )}
                {imported.created_items.length > 0 && <span>New stock items: {imported.created_items.join(", ")}. Find them under Inventory.</span>}
                {imported.issues?.length > 0 && (
                  <span>Flagged: {imported.issues.map((x) => `${x.description} (${x.issue}: ${x.received} of ${x.invoiced})`).join(", ")}. Ask the supplier for a credit.</span>
                )}
                {imported.alerted && <span>Price changes were flagged to your team.</span>}
              </div>
            )}
            {(status === "review" || status === "confirmed") && (
              <>
                <div className="inv-head">
                  <label className="stack" style={{ gap: 4 }}>
                    <span className="small">Supplier</span>
                    <input className="input" list="supplier-names" disabled={!editable} value={scan.supplier_name ?? ""}
                      onChange={(e) => {
                        const match = suppliers.find((s) => s.name === e.target.value);
                        setScan({ ...scan, supplier_name: e.target.value, supplier_id: match?.id ?? null });
                        setDirty(true);
                      }} />
                    <datalist id="supplier-names">{suppliers.map((s) => <option key={s.id} value={s.name} />)}</datalist>
                  </label>
                  <label className="stack" style={{ gap: 4 }}>
                    <span className="small">Invoice no.</span>
                    <input className="input" disabled={!editable} value={scan.invoice_number ?? ""}
                      onChange={(e) => { setScan({ ...scan, invoice_number: e.target.value }); setDirty(true); }} />
                  </label>
                  <label className="stack" style={{ gap: 4 }}>
                    <span className="small">Date</span>
                    <input className="input" type="date" disabled={!editable} value={scan.invoice_date ?? ""}
                      onChange={(e) => { setScan({ ...scan, invoice_date: e.target.value || null }); setDirty(true); }} />
                  </label>
                </div>
                {scan.warnings.map((w, i) => <p key={i} className="small warn-text">⚠ {w}</p>)}
                {editable && (
                  <p className="small muted">
                    Check each line against the invoice. Fix anything the AI misread, and choose where it goes in your
                    inventory{newOnes > 0 ? `: ${newOnes} product${newOnes === 1 ? " is" : "s are"} new and will be added as stock items` : ""}.
                  </p>
                )}
                <ol className="inv-lines">
                  {scan.lines.map((l, i) => {
                    const target = l.item_id ?? (l.kind === "item" && l.create_item ? NEW : NONE);
                    return (
                      <li key={i} className={`inv-line ${l.kind !== "item" ? "is-fee" : ""} ${target === NEW ? "is-new" : ""}`}>
                        <div className="inv-line-top">
                          <span className="inv-n">{i + 1}</span>
                          <input className="input inv-desc" aria-label="Item on invoice" disabled={!editable} value={l.description}
                            placeholder="Item on the invoice" onChange={(e) => setLine(i, { description: e.target.value })} />
                          {l.kind !== "item" && <span className="pill">{l.kind}</span>}
                          <Change line={l} />
                          {editable && (
                            <button className="sb-icon" aria-label={`Remove line ${i + 1}`} title="Remove this line"
                              onClick={() => { setScan({ ...scan, lines: scan.lines.filter((_, j) => j !== i) }); setDirty(true); }}>✕</button>
                          )}
                        </div>
                        <div className="inv-line-fields">
                          <label><span>Qty</span>
                            <input className="input" type="number" step="any" disabled={!editable} value={l.qty}
                              onChange={(e) => setLine(i, { qty: Number(e.target.value) })} /></label>
                          <label><span>Unit</span>
                            <input className="input" disabled={!editable} value={l.unit ?? ""} placeholder="e.g. case"
                              onChange={(e) => setLine(i, { unit: e.target.value || null })} /></label>
                          <label><span>Unit price ($)</span>
                            <input className="input" type="number" step="0.01" disabled={!editable} value={dollars(l.unit_price_cents)}
                              onChange={(e) => setLine(i, { unit_price_cents: toCents(e.target.value) })} /></label>
                          <label><span>Total</span><output>${dollars(l.line_total_cents)}</output></label>
                        </div>
                        {l.kind === "item" ? (
                          <div className="inv-line-stock">
                            <label className="inv-target"><span>In your inventory</span>
                              <select className="select" disabled={!editable} value={target} onChange={(e) => setTarget(i, e.target.value)}>
                                <option value={NEW}>➕ Add as a new stock item</option>
                                {stockItems.length > 0 && <optgroup label="Your stock items">{stockItems.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}</optgroup>}
                                {otherItems.length > 0 && <optgroup label="Other items">{otherItems.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}</optgroup>}
                                <option value={NONE}>Don't add to stock (just log it)</option>
                              </select>
                              {l.match && !["none", "chosen", "new"].includes(l.match) && l.item_id && (
                                <small className="muted">{l.match === "remembered" ? "Matched from earlier invoices" : `Matched by name (${l.match.replace("name ", "")})`}</small>
                              )}
                            </label>
                            {target !== NONE && (
                              <label className="inv-pack" title="How many stock units are in one invoice unit, e.g. 24 bottles in a case">
                                <span>Stock units in each</span>
                                <input className="input" type="number" min={0.001} step="any" disabled={!editable} value={l.pack_size}
                                  onChange={(e) => setLine(i, { pack_size: Number(e.target.value) || 1 })} />
                              </label>
                            )}
                            <label className="inv-arrived"><span>What arrived</span>
                              <select className={`select ${l.issue ? "has-issue" : ""}`} disabled={!editable} value={l.issue ?? ""}
                                onChange={(e) => {
                                  const issue = (e.target.value || null) as DraftLine["issue"];
                                  setLine(i, { issue, received_qty: issue ? (issue === "wrong" ? 0 : l.received_qty ?? l.qty) : null });
                                }}>
                                <option value="">✓ All of it, fine</option>
                                <option value="short">Short: less arrived</option>
                                <option value="damaged">Damaged</option>
                                <option value="wrong">Substituted / wrong item</option>
                              </select>
                            </label>
                            {l.issue && (
                              <label className="inv-pack" title="How many good ones you're keeping, in invoice units. Stock goes up by this.">
                                <span>Good qty received</span>
                                <input className="input" type="number" min={0} step="any" disabled={!editable} value={l.received_qty ?? l.qty}
                                  onChange={(e) => setLine(i, { received_qty: Math.max(0, Number(e.target.value) || 0) })} />
                              </label>
                            )}
                          </div>
                        ) : (
                          <p className="small muted" style={{ margin: 0 }}>{l.kind === "fee" ? "A fee: logged with the invoice, not added to stock." : "A credit: logged with the invoice."}</p>
                        )}
                      </li>
                    );
                  })}
                </ol>
                {editable && (
                  <button className="btn small" style={{ alignSelf: "start" }} onClick={() => {
                    setScan({ ...scan, lines: [...scan.lines, { description: "", qty: 1, unit: null, unit_price_cents: 0, line_total_cents: 0, kind: "item", item_id: null, pack_size: 1, match: "new", create_item: true }] });
                    setDirty(true);
                  }}>+ Add a line</button>
                )}
              </>
            )}
            {message && <p className="small error" role="alert">{message}</p>}
          </div>
        </div>
      )}
    </Dialog>
  );
}
