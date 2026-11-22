import { useEffect, useState } from "react";
import { useApp } from "../context";
import { api, fetchBlob, type DraftLine, type InvoiceScan, type Supplier } from "../lib/api";
import { normUnit, type OnHand, type StockItem } from "../lib/inventory";
import { defaultStockUnit, fmtQty, type Pack, packText, parsePack, perInvoiceUnit, stockUnits } from "../lib/packs";
import ItemPicker from "./ItemPicker";
import { Dialog } from "./PosDialogs";

const dollars = (cents: number | null | undefined) => (cents == null ? "" : (cents / 100).toFixed(2));
const toCents = (v: string) => Math.round((Number(v) || 0) * 100);
const NEW = "__new__";
const NONE = "__none__";

type Imported = { lines: number; stock_updates: { item: string; added: number; unit?: string | null }[];
  created_items: string[]; alerted: boolean;
  issues: { description: string; issue: string; invoiced: number; received: number }[] };

/** Where the line is going, worked out when the invoice was read: an item you already stock, or a new one. */
function Status({ line, target }: { line: DraftLine; target: string }) {
  if (line.kind !== "item") return null;
  if (target === NEW) return <span className="pill new" title="Not in your inventory yet: it's added when you import">New item</span>;
  if (target === NONE) return <span className="pill" title="Logged with the invoice, not added to stock">Not stocked</span>;
  const pct = line.match?.startsWith("name ") ? Number(line.match.slice(5).replace("%", "")) : null;
  if (pct != null && pct < 80) {
    return <span className="pill warn" title={`Only a ${pct}% name match. Check it's the same product.`}>Check the match</span>;
  }
  const how = line.match === "remembered" ? "Same product as on an earlier invoice"
    : pct != null ? `Matched by name (${pct}%)` : "Chosen by you";
  return <span className="pill good" title={how}>✓ In inventory</span>;
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

/** The pack as typed ("40 × 400 g"); kept as text while typing, read when it makes sense. */
function PackInput({ line, disabled, onChange }: { line: DraftLine; disabled: boolean; onChange: (p: Pack) => void }) {
  const [text, setText] = useState(() => packText(line));
  const [bad, setBad] = useState(false);
  return (
    <input className={`input ${bad ? "is-invalid" : ""}`} disabled={disabled} value={text} placeholder="e.g. 40 × 400 g"
      aria-invalid={bad}
      onChange={(e) => {
        setText(e.target.value);
        const p = parsePack(e.target.value);
        setBad(p === undefined);
        if (p !== undefined) onChange(p ?? { pack_count: null, each_qty: null, each_unit: null });
      }}
      onBlur={() => { if (!bad) setText(packText(line)); }} />
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

  function setTarget(i: number, value: string) {
    if (value === NEW) setLine(i, { item_id: null, create_item: true, match: "new" });
    else if (value === NONE) setLine(i, { item_id: null, create_item: false, match: "none" });
    else setLine(i, { item_id: value, create_item: false, match: "chosen", item_name: items.find((it) => it.id === value)?.name ?? null });
  }

  /** What a line's stock is counted in, and what one invoice unit adds (null: the pack doesn't say, so it's typed). */
  function stockOf(l: DraftLine) {
    const item = l.item_id ? items.find((it) => it.id === l.item_id) : undefined;
    const unit = normUnit(item?.unit || l.stock_unit || defaultStockUnit(l.unit, l, l.each_name));
    const per = perInvoiceUnit(l.unit, l, unit) ?? (l.pack_auto ? l.pack_size : null);
    return { unit, per, fixed: !!item?.unit };
  }

  /** Change the pack or the unit, and work out pack_size again when it can be. */
  function setPack(i: number, patch: Partial<DraftLine>) {
    if (!scan) return;
    const l = { ...scan.lines[i], ...patch, pack_auto: false };
    const { per } = stockOf(l);
    setLine(i, { ...patch, pack_auto: false, ...(per != null ? { pack_size: per } : {}) });
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
  const toImport = products.filter((l) => l.item_id || l.create_item).length;
  const newOnes = products.filter((l) => !l.item_id && l.create_item).length;
  const known = products.filter((l) => l.item_id).length;
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
                    {known} {known === 1 ? "product is" : "products are"} already in your inventory
                    {newOnes > 0 ? ` and ${newOnes} ${newOnes === 1 ? "is" : "are"} new` : ""}. Check each line against the
                    invoice, and change where it goes if a match is wrong.
                  </p>
                )}
                <ol className="inv-lines">
                  {scan.lines.map((l, i) => {
                    const target = l.item_id ?? (l.kind === "item" && l.create_item ? NEW : NONE);
                    const product = l.kind === "item";
                    const stock = stockOf(l);
                    const matched = l.item_id ? items.find((it) => it.id === l.item_id) : undefined;
                    const invUnit = l.unit || "unit";
                    const show = (u: string) => (u === "l" ? "L" : u);
                    return (
                      <li key={i} className={`inv-line ${l.kind !== "item" ? "is-fee" : ""} ${target === NEW ? "is-new" : ""}`}>
                        <div className="inv-line-top">
                          <span className="inv-n">{i + 1}</span>
                          {l.manual ? (
                            <input className="input inv-desc" aria-label="Item" disabled={!editable} value={l.name ?? ""}
                              placeholder="Item name" onChange={(e) => setLine(i, { name: e.target.value })} />
                          ) : (
                            <span className="inv-name" title={l.description}>{product ? l.name || l.description : l.description}</span>
                          )}
                          {l.kind !== "item" && <span className="pill">{l.kind}</span>}
                          <Status line={l} target={target} />
                          <Change line={l} />
                          {editable && (
                            <button className="sb-icon" aria-label={`Remove line ${i + 1}`} title="Remove this line"
                              onClick={() => { setScan({ ...scan, lines: scan.lines.filter((_, j) => j !== i) }); setDirty(true); }}>✕</button>
                          )}
                        </div>
                        {product && !l.manual && l.description !== (l.name || l.description) && (
                          <p className="inv-printed small muted">On the invoice: {l.description}{l.sku && !l.description.includes(l.sku) ? ` · ${l.sku}` : ""}</p>
                        )}
                        {product && (
                          <div className="inv-match">
                            <span className="small tip" tabIndex={0}
                              data-tip="The inventory item this goes into. Picked for you when Pass recognises the product; search to change it.">Matched inventory</span>
                            <ItemPicker items={items} value={l.item_id} valueName={l.item_name} disabled={!editable}
                              placeholder={target === NONE ? "Not added to stock" : "Search your inventory…"}
                              onPick={(it) => setTarget(i, it ? it.id : NEW)} onSkip={() => setTarget(i, NONE)} />
                            {target === NEW && <small className="muted">Leave blank to add this as a new item in your inventory.</small>}
                            {target === NONE && <small className="muted">Only logged with the invoice. Pick an item to add it to stock.</small>}
                            {l.item_id && (
                              <small className="muted">
                                {matched?.stock_qty != null ? <>On hand <strong>{fmtQty(matched.stock_qty)} {show(stock.unit)}</strong> · </> : null}
                                Counted in {show(stock.unit)}
                                {l.match === "remembered" ? " · same product as on an earlier invoice"
                                  : l.match?.startsWith("name ") ? ` · found by name (${l.match.slice(5)} match)` : ""}
                              </small>
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
                        {product && target !== NONE && (
                          <div className="inv-line-stock">
                            <label className="inv-pack">
                              <span className="tip" tabIndex={0} data-tip={`What one ${invUnit} holds, from the invoice: 40 × 400 g, 5 lb, or 24 for 24 bottles`}>Pack</span>
                              <PackInput key={`${scanId}-${i}`} line={l} disabled={!editable} onChange={(p) => setPack(i, p)} />
                            </label>
                            {!stock.fixed && (
                              <label className="inv-pack">
                                <span className="tip" tabIndex={0} data-tip="What the new item is counted in: by weight, by the pack, or by the case">Count in</span>
                                <select className="select" disabled={!editable} value={stock.unit}
                                  onChange={(e) => setPack(i, { stock_unit: e.target.value })}>
                                  {stockUnits(l.unit, l, l.each_name, stock.unit).map((u) => <option key={u} value={u}>{unitLabel(u, l)}</option>)}
                                </select>
                              </label>
                            )}
                            {stock.per != null ? (
                              <div className="inv-adds">
                                <span>Adds to stock</span>
                                <strong>+{fmtQty(l.qty * stock.per)} {show(stock.unit)}</strong>
                                {normUnit(l.unit) !== stock.unit && <small className="muted">1 {invUnit} = {fmtQty(stock.per)} {show(stock.unit)}</small>}
                              </div>
                            ) : (
                              <label className="inv-pack">
                                <span className="tip" tabIndex={0} data-tip={`The pack doesn't say. How many ${stock.unit} come in one ${invUnit}?`}>{show(stock.unit)} in 1 {invUnit}</span>
                                <input className="input" type="number" min={0.001} step="any" disabled={!editable} value={l.pack_size}
                                  onChange={(e) => setLine(i, { pack_size: Number(e.target.value) || 1 })} />
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
