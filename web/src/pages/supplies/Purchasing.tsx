import { useEffect, useState, type FormEvent } from "react";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api, type OrderMessage, type PurchaseOrder, type Supplier } from "../../lib/api";
import { timeAgo } from "../../lib/format";
import SupplierOrderDialog from "../../components/SupplierOrderDialog";

const STATUS_PILL: Record<string, string> = { draft: "warn", approved: "", sent: "good", received: "good", cancelled: "" };
const SENT_VIA: Record<string, string> = { email: "by email", sms: "by text", manual: "by you" };

/** Suppliers and the orders to them (written with "Generate email", or drafted when stock runs low; nothing is
 * sent without approval). Part of the Supplies page. */
export function PurchasingPanel({ onScanInvoice }: { onScanInvoice: () => void }) {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const orders = useApi<PurchaseOrder[]>(`${base}/purchase-orders`);
  const suppliers = useApi<Supplier[]>(`${base}/suppliers`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", email: "", phone: "", notes: "" });
  const [adding, setAdding] = useState(false);
  const [writing, setWriting] = useState<Supplier | null>(null);
  const [notice, setNotice] = useState("");

  async function act(path: string, label: string, body: unknown = {}) {
    setBusy(label);
    setError("");
    try {
      await api(`${base}${path}`, { method: "POST", body });
      await orders.reload();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
      return false;
    } finally {
      setBusy("");
    }
  }

  async function addSupplier(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api(`${base}/suppliers`, {
        method: "POST",
        body: { name: form.name, email: form.email || undefined, phone: form.phone || undefined, notes: form.notes || undefined },
      });
      setForm({ name: "", email: "", phone: "", notes: "" });
      setAdding(false);
      await suppliers.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add the supplier.");
    }
  }

  return (
    <>
      {error && <p className="error">{error}</p>}

      {notice && <p className="notice">{notice}</p>}
      {writing && (
        <SupplierOrderDialog base={base} supplier={writing} onClose={() => setWriting(null)}
          onDone={(m) => { setWriting(null); setNotice(m); void orders.reload(); }} />
      )}

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="spread">
          <div>
            <h2>Your suppliers</h2>
            <p className="small muted">Press <strong>Generate email</strong> to write an order. AI can fill in the items from your sales and what you usually order from them.</p>
          </div>
          {!adding && <button className="btn" onClick={() => setAdding(true)}>+ Add a supplier</button>}
        </div>
        {adding && (
          <form className="row so-add" onSubmit={addSupplier}>
            <input className="input" style={{ flex: 2 }} required autoFocus placeholder="Supplier name" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className="input" style={{ flex: 2 }} type="email" placeholder="Order email" value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })} />
            <input className="input" style={{ flex: 1 }} placeholder="Phone (for texts and WhatsApp)" value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            <button className="btn primary">Add</button>
            <button type="button" className="btn" onClick={() => setAdding(false)}>Cancel</button>
          </form>
        )}
        {suppliers.data?.length === 0 && !adding && <p className="small muted">No suppliers yet. Add one to start ordering.</p>}
        <ul className="list">
          {suppliers.data?.map((s) => <SupplierRow key={s.id} s={s} base={base} onSaved={suppliers.reload} onWrite={setWriting} />)}
        </ul>
        <p className="small muted" style={{ marginTop: 8 }}>Link items to suppliers and set reorder amounts on the Stock page.</p>
      </section>

      <section className="panel">
        <div className="spread">
          <div>
            <h2>Orders to suppliers</h2>
            <p className="small muted">When stock runs low, an order to that item's supplier is drafted here. Check it, then send it.</p>
          </div>
          <button className="btn" disabled={busy !== ""} onClick={() => void act("/purchase-orders/draft", "draft")}>
            Draft orders for low stock
          </button>
        </div>
        {orders.data?.length === 0 && <p className="muted">No supplier orders yet.</p>}
        <ul className="list">
          {orders.data?.map((po) => {
            const canSend = po.status === "draft" || (po.status === "approved" && !!po.send_error);
            return (
              <li key={po.id} className="stack" style={{ gap: 8 }}>
                <div className="spread">
                  <strong>{po.supplier ?? "No supplier"}</strong>
                  <span className={`pill ${STATUS_PILL[po.status]}`}>{po.status === "approved" ? "sending" : po.status}</span>
                </div>
                {po.status === "draft" ? (
                  <Lines po={po} base={base} onSaved={orders.reload} />
                ) : (
                  <div className="small">{po.lines.map((l) => `${l.qty} ${l.unit ?? ""} ${l.name}`).join(" · ")}</div>
                )}
                <div className="small muted">
                  {po.created_at && `Drafted ${timeAgo(po.created_at)}`}
                  {po.delivery_date && ` · for ${new Date(`${po.delivery_date}T12:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}`}
                  {po.sent_at && ` · sent ${SENT_VIA[po.sent_via ?? "email"]} ${timeAgo(po.sent_at)}`}
                </div>
                {po.send_error && <p className="small error">{po.send_error}</p>}
                {open === po.id ? (
                  <Composer po={po} base={base} supplier={suppliers.data?.find((s) => s.id === po.supplier_id)}
                    busy={busy !== ""} canSend={canSend} onClose={() => setOpen(null)}
                    onSend={async (channel, message, delivery_date) => {
                      if (await act(`/purchase-orders/${po.id}/send`, po.id, { channel, message, delivery_date })) setOpen(null);
                    }} />
                ) : (
                  <div className="row">
                    {canSend && (
                      <button className="btn primary small" onClick={() => setOpen(po.id)}>Write message to supplier</button>
                    )}
                    {!canSend && po.message && (
                      <button className="btn small" onClick={() => setOpen(po.id)}>View message</button>
                    )}
                    {po.status === "draft" && (
                      <button className="btn small" disabled={busy !== ""} onClick={() => void act(`/purchase-orders/${po.id}/cancel`, po.id)}>
                        Cancel
                      </button>
                    )}
                    {(po.status === "approved" || po.status === "sent") && (
                      <>
                        <button className="btn small primary" onClick={onScanInvoice}>Delivery arrived: scan the invoice</button>
                        <button className="btn small" disabled={busy !== ""} onClick={() => void act(`/purchase-orders/${po.id}/received`, po.id)}>
                          Mark received without an invoice
                        </button>
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>

    </>
  );
}

/** Quantities on a draft, editable before the message is written. */
function Lines({ po, base, onSaved }: { po: PurchaseOrder; base: string; onSaved: () => Promise<void> }) {
  const [lines, setLines] = useState(po.lines);
  useEffect(() => setLines(po.lines), [po.lines]);

  async function save(next: PurchaseOrder["lines"]) {
    setLines(next);
    await api(`${base}/purchase-orders/${po.id}`, { method: "PATCH", body: { lines: next } });
    await onSaved();
  }

  return (
    <ul className="po-lines">
      {lines.map((l, i) => (
        <li key={`${l.name}-${i}`}>
          <input className="input" type="number" min={0.01} step="any" aria-label={`Quantity of ${l.name}`} value={l.qty}
            onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, qty: Number(e.target.value) } : x)))}
            onBlur={() => lines[i].qty > 0 && lines[i].qty !== po.lines[i]?.qty && void save(lines)} />
          <span className="small muted">{l.unit ?? ""}</span>
          <span style={{ flex: 1 }}>{l.name}</span>
          {lines.length > 1 && (
            <button className="icon-btn" aria-label={`Remove ${l.name}`} onClick={() => void save(lines.filter((_, j) => j !== i))}>✕</button>
          )}
        </li>
      ))}
    </ul>
  );
}

function digits(phone: string) {
  return phone.replace(/[^\d]/g, "");
}

/** The order written out from the template: pick a delivery date, edit the words, choose how to send. */
function Composer({ po, base, supplier, busy, canSend, onClose, onSend }: {
  po: PurchaseOrder; base: string; supplier: Supplier | undefined; busy: boolean; canSend: boolean;
  onClose: () => void; onSend: (channel: "email" | "sms" | "manual", message: string, delivery_date: string) => Promise<void>;
}) {
  const [msg, setMsg] = useState<OrderMessage | null>(null);
  const [date, setDate] = useState(po.delivery_date ?? "");
  const [text, setText] = useState("");
  const [edited, setEdited] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    void api<OrderMessage>(`${base}/purchase-orders/${po.id}/message${date ? `?delivery_date=${date}` : ""}`).then((m) => {
      if (!live) return;
      setMsg(m);
      if (!date) setDate(m.delivery_date);
      if (!edited) setText(m.body);
    });
    return () => {
      live = false;
    };
    // Re-write the message when the date changes, unless the manager has edited it by hand.
  }, [date]);

  if (!msg) return <p className="small muted">Writing the message…</p>;

  const email = supplier?.email ?? msg.to_email;
  const phone = supplier?.phone ?? msg.to_phone;
  const mailto = `mailto:${email ?? ""}?subject=${encodeURIComponent(msg.subject)}&body=${encodeURIComponent(text)}`;
  const whatsapp = `https://wa.me/${phone ? digits(phone) : ""}?text=${encodeURIComponent(text)}`;
  const sms = `sms:${phone ?? ""}?&body=${encodeURIComponent(text)}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the text is still there to select */
    }
  }

  return (
    <div className="po-message">
      {canSend && (
        <label className="row small">
          Delivery date
          <input className="input" type="date" style={{ width: "auto" }} value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      )}
      <textarea className="textarea" rows={Math.min(16, text.split("\n").length + 1)} value={text} readOnly={!canSend}
        onChange={(e) => { setText(e.target.value); setEdited(true); }} aria-label="Message to the supplier" />
      {canSend && edited && (
        <button className="linkish small" style={{ alignSelf: "start" }} onClick={() => { setText(msg.body); setEdited(false); }}>
          Undo my edits (use the template)
        </button>
      )}
      {canSend ? (
        <>
          <div className="row">
            <button className="btn primary small" disabled={busy || !email || !msg.ready.email} onClick={() => void onSend("email", text, date)}
              title={!email ? "Add this supplier's email below" : !msg.ready.email ? "Email sending isn't set up yet" : ""}>
              Send by email{email ? ` to ${email}` : ""}
            </button>
            {msg.ready.sms && phone && (
              <button className="btn small" disabled={busy} onClick={() => void onSend("sms", text, date)}>Send by text to {phone}</button>
            )}
          </div>
          {(!email || !msg.ready.email) && (
            <p className="small muted">
              {!email ? "No email for this supplier yet: add it under Suppliers, or send the message yourself." : "Sending email from the app isn't set up yet, so send it yourself for now."}
            </p>
          )}
          <div className="send-yourself">
            <span className="small strong">Or send it yourself, then mark it as sent</span>
            <div className="row">
              <button className="btn small" onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy message"}</button>
              {phone && <a className="btn small" href={whatsapp} target="_blank" rel="noreferrer">WhatsApp</a>}
              {phone && <a className="btn small" href={sms}>Text app</a>}
              <a className="btn small" href={mailto}>Email app</a>
              <button className="btn small primary" disabled={busy} onClick={() => void onSend("manual", text, date)}>I've sent it</button>
            </div>
          </div>
        </>
      ) : null}
      <button className="linkish small" style={{ alignSelf: "start" }} onClick={onClose}>Close</button>
    </div>
  );
}

function SupplierRow({ s, base, onSaved, onWrite }: { s: Supplier; base: string; onSaved: () => Promise<void>; onWrite: (s: Supplier) => void }) {
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState({ email: s.email ?? "", phone: s.phone ?? "", notes: s.notes ?? "" });
  const [err, setErr] = useState("");

  async function save(e: FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      await api(`${base}/suppliers/${s.id}`, {
        method: "PATCH", body: { name: s.name, email: f.email || null, phone: f.phone || null, notes: f.notes || null },
      });
      setEditing(false);
      await onSaved();
    } catch (x) {
      setErr(x instanceof Error ? x.message : "Couldn't save.");
    }
  }

  if (!editing) {
    return (
      <li className="spread">
        <div>
          <strong>{s.name}</strong>
          <div className="small muted">{[s.email ?? "no email yet", s.phone ?? "no phone yet", s.notes].filter(Boolean).join(" · ")}</div>
        </div>
        <div className="row">
          <button className="btn small primary" onClick={() => onWrite(s)}>✉ Generate email</button>
          <button className="btn small" onClick={() => setEditing(true)}>Edit</button>
        </div>
      </li>
    );
  }
  return (
    <li>
      <form className="stack" style={{ gap: 6 }} onSubmit={save}>
        <strong>{s.name}</strong>
        <div className="row">
          <input className="input" style={{ flex: 2 }} type="email" placeholder="Order email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
          <input className="input" style={{ flex: 1 }} placeholder="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
        </div>
        <input className="input" placeholder="Notes (e.g. order by 2pm for next-day delivery)" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} />
        {err && <p className="small error">{err}</p>}
        <div className="row">
          <button className="btn primary small">Save</button>
          <button type="button" className="btn small" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </form>
    </li>
  );
}
