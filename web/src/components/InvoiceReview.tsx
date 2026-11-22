import { useEffect, useState } from "react";
import { useApp } from "../context";
import { api, fetchBlob, type DraftLine, type InvoiceScan, type Supplier } from "../lib/api";
import { convert, normUnit, type OnHand, type StockItem } from "../lib/inventory";
import { defaultStockUnit, fmtQty, perInvoiceUnit, stockUnits } from "../lib/packs";
import ItemPicker from "./ItemPicker";
import { Dialog } from "./PosDialogs";

const dollars = (cents: number | null | undefined) => (cents == null ? "" : (cents / 100).toFixed(2));
const toCents = (v: string) => Math.round((Number(v) || 0) * 100);

type Imported = { lines: number; stock_updates: { item: string; added: number; unit?: string | null }[];
  created_items: string[]; alerted: boolean;
  issues: { description: string; issue: string; invoiced: number; received: number }[] };

/** Next to Matched inventory: matched (and how well), a new item about to be added, or no match yet. */
function MatchState({ line }: { line: DraftLine }) {
  if (line.item_id) {
    const pct = line.match?.startsWith("name ") ? line.match.slice(5) : null;
    const text = pct ? `${pct} match` : line.match === "remembered" ? "Matched before" : "Selected";
    const tip = pct ? `Found by name: ${pct} similar` : line.match === "remembered"
      ? "The same product as on an earlier invoice from this supplier" : "Chosen by you";
    return <span className="match-state ok" title={tip}><span aria-hidden>✓</span> {text}</span>;
  }
  if (line.new_name?.trim()) {
    return <span className="match-state ok" title="Not in your inventory yet: it's added under this name when you import"><span aria-hidden>✓</span> Add new</span>;
  }
  return <span className="match-state bad" title="Nothing in your inventory looks like this. Search for it, or type a name to add it as new."><span aria-hidden>✕</span> No match</span>;
}

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

const unitLabel = (u: string, l: DraftLine) => {
  const inner = normUnit(l.each_name) !== "each" && l.each_name ? normUnit(l.each_name) : "each";
  if (u === inner && l.each_qty && l.each_unit && normUnit(l.unit) !== u) return `${u} (${l.each_qty} ${l.each_unit})`;
  return u === "l" ? "L" : u;
};

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
  const [items, setItems] = useState<StockItem[]>([]);
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
    void api<OnHand>(`/locations/${location.id}/inventory/on-hand`).then((r) => setItems(r.items));
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

  function pickItem(i: number, it: StockItem) {
    setPack(i, { item_id: it.id, item_name: it.name, create_item: false, match: "chosen", new_name: "", pack_manual: false });
  }

  function newName(i: number, name: string) {
    const was = scan?.lines[i];
    setPack(i, { item_id: null, item_name: null, create_item: true, match: "new", new_name: name,
      ...(was?.item_id ? { pack_manual: false } : {}) });
  }


  /** What a line's stock is counted in, and what one invoice unit adds (null: the pack doesn't say, so it's typed). */
  function stockOf(l: DraftLine) {
    const item = l.item_id ? items.find((it) => it.id === l.item_id) : undefined;
    const unit = normUnit(item?.unit || l.stock_unit || defaultStockUnit(l.unit, l, l.each_name));
    const auto = perInvoiceUnit(l.unit, l, unit)
      ?? (item?.conversions?.length ? convert(1, l.unit || unit, unit, item.conversions) : null)
      ?? (l.pack_auto ? l.pack_size : null);
    const per = l.pack_manual ? l.pack_size : auto;
    return { unit, per: per ?? l.pack_size, fixed: !!item?.unit, unsure: per == null };
  }

  /** Change the pack or the unit, and work out pack_size again when it can be. */
  function setPack(i: number, patch: Partial<DraftLine>) {
    if (!scan) return;
    const l = { ...scan.lines[i], ...patch, pack_auto: false };
    const { per, unsure } = stockOf(l);
    setLine(i, { ...patch, pack_auto: false, ...(!unsure ? { pack_size: per } : {}) });
  }

  async function save(): Promise<InvoiceScan | null> {
    if (!scan) return null;
    try {
      const out = await api<InvoiceScan>(base, {
        method: "PUT",
        body: {
          supplier_id: scan.supplier_id, supplier_name: scan.supplier_name, invoice_number: scan.invoice_number,
          invoice_date: scan.invoice_date,
          lines: scan.lines.filter((l) => l.description.trim() || l.name?.trim())
            .map(({ last_price_cents, last_received_on, change_pct, pack_auto, ...l }) => ({ ...l, description: l.description.trim() || (l.name ?? "").trim() })),
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

  const products = scan?.lines.filter((l) => l.kind === "item") ?? [];
  const known = products.filter((l) => l.item_id).length;
  const newOnes = products.filter((l) => !l.item_id && l.new_name?.trim()).length;
  const unmatched = products.length - known - newOnes;
  const toImport = known + newOnes;
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
            {unmatched > 0 && <span className="small warn-text">{unmatched} line{unmatched === 1 ? " has" : "s have"} no match</span>}
            <button className="btn primary" disabled={busy !== "" || scan.lines.length === 0 || unmatched > 0}
              title={unmatched > 0 ? "Match every line to an inventory item, or name it to add it as new" : undefined}
              onClick={() => void importIt()}>
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
                    {known} matched to your inventory{newOnes > 0 ? `, ${newOnes} to add as new` : ""}
                    {unmatched > 0 ? `, ${unmatched} with no match: search for the item, or type a name to add it as new` : ""}.
                  </p>
                )}
                <ol className="inv-lines">
                  {scan.lines.map((l, i) => {
                    const product = l.kind === "item";
                    const state = !product ? "" : l.item_id ? "" : l.new_name?.trim() ? "is-new" : "is-unmatched";
                    const stock = stockOf(l);
                    const matched = l.item_id ? items.find((it) => it.id === l.item_id) : undefined;
                    const invUnit = l.unit || "unit";
                    const show = (u: string) => (u === "l" ? "L" : u);
                    return (
                      <li key={i} className={`inv-line ${l.kind !== "item" ? "is-fee" : ""} ${state}`}>
                        <div className="inv-line-top">
                          <span className="inv-n">{i + 1}</span>
                          {l.manual ? (
                            <input className="input inv-desc" aria-label="Item on invoice" disabled={!editable} value={l.description}
                              placeholder="Item on the invoice" onChange={(e) => setLine(i, { description: e.target.value })} />
                          ) : (
                            <span className="inv-name">{l.description}</span>
                          )}
                          {l.kind !== "item" && <span className="pill">{l.kind}</span>}
                          <Change line={l} />
                          {editable && (
                            <button className="sb-icon" aria-label={`Remove line ${i + 1}`} title="Remove this line"
                              onClick={() => { setScan({ ...scan, lines: scan.lines.filter((_, j) => j !== i) }); setDirty(true); }}>✕</button>
                          )}
                        </div>
                        {product && (
                          <div className="inv-match">
                            <span className="small tip" tabIndex={0}
                              data-tip="The inventory item this goes into, picked for you when Pass recognises it. Search to change it, or type a new name to add it as a new item.">Matched inventory</span>
                            <div className="inv-match-row">
                              <ItemPicker items={items} itemId={l.item_id} itemName={l.item_name} newName={l.new_name ?? ""}
                                suggestion={l.name && l.name !== l.description ? l.name : null} disabled={!editable}
                                onPickItem={(it) => pickItem(i, it)} onNewName={(name) => newName(i, name)} />
                              <MatchState line={l} />
                            </div>
                            {l.item_id && matched && (
                              <small className="muted">On hand <strong>{matched.stock_qty != null ? fmtQty(matched.stock_qty) : "–"} {show(stock.unit)}</strong> · counted in {show(stock.unit)}</small>
                            )}
                          </div>
                        )}
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
                        {product && (
                          <div className="inv-line-stock">
                            {!stock.fixed && (
                              <label className="inv-pack">
                                <span className="tip" tabIndex={0} data-tip="What the new item is counted in: by weight, by the pack, or by the case">Count in</span>
                                <select className="select" disabled={!editable} value={stock.unit}
                                  onChange={(e) => setPack(i, { stock_unit: e.target.value, pack_manual: false })}>
                                  {stockUnits(l.unit, l, l.each_name, stock.unit).map((u) => <option key={u} value={u}>{unitLabel(u, l)}</option>)}
                                </select>
                              </label>
                            )}
                            {normUnit(l.unit) !== stock.unit && (
                              <label className="inv-pack">
                                <span className="tip" tabIndex={0}
                                  data-tip={stock.unsure ? `The invoice doesn't say how much is in one ${invUnit}. Enter it in ${show(stock.unit)}.` : `Worked out from the invoice${l.each_qty && l.each_unit ? ` (${l.pack_count && l.pack_count !== 1 ? `${l.pack_count} × ` : ""}${l.each_qty} ${l.each_unit})` : ""}. Change it if it's wrong.`}>
                                  In 1 {invUnit}
                                </span>
                                <span className={`inv-unit-input ${stock.unsure ? "is-unsure" : ""}`}>
                                  <input className="input" type="number" min={0.001} step="any" disabled={!editable}
                                    value={stock.unsure ? "" : Math.round(stock.per * 1000) / 1000} placeholder="?"
                                    onChange={(e) => setLine(i, { pack_size: Number(e.target.value) || 1, pack_manual: true, pack_auto: false })} />
                                  <span>{show(stock.unit)}</span>
                                </span>
                              </label>
                            )}
                          </div>
                        )}
                        {!product && (
                          <p className="small muted" style={{ margin: 0 }}>{l.kind === "fee" ? "A fee: logged with the invoice, not added to stock." : "A credit: logged with the invoice."}</p>
                        )}
                      </li>
                    );
                  })}
                </ol>
                {editable && (
                  <button className="btn small" style={{ alignSelf: "start" }} onClick={() => {
                    setScan({ ...scan, lines: [...scan.lines, { description: "", name: "", manual: true, qty: 1, unit: null, unit_price_cents: 0, line_total_cents: 0, kind: "item", item_id: null, pack_size: 1, match: "new", create_item: true }] });
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
