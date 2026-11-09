import { useEffect, useRef, useState, type FormEvent } from "react";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api, type OrderMessage, type OrderTemplate, type PurchaseOrder, type Supplier } from "../../lib/api";
import { timeAgo } from "../../lib/format";

const STATUS_PILL: Record<string, string> = { draft: "warn", approved: "", sent: "good", received: "good", cancelled: "" };
const SENT_VIA: Record<string, string> = { email: "by email", sms: "by text", manual: "by you" };

/** Orders to suppliers (drafted when stock runs low; nothing is sent without approval), or the suppliers and the
 * order message template. Part of the Supplies page. */
export function PurchasingPanel({ view, onScanInvoice }: { view: "orders" | "suppliers"; onScanInvoice: () => void }) {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const orders = useApi<PurchaseOrder[]>(`${base}/purchase-orders`);
  const suppliers = useApi<Supplier[]>(`${base}/suppliers`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", email: "", phone: "", notes: "" });

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
      await suppliers.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add the supplier.");
    }
  }

  return (
    <>
      {error && <p className="error">{error}</p>}

      {view === "orders" && (
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
      )}

      {view === "suppliers" && (
      <>
      <section className="panel">
        <h2>Suppliers</h2>
        <ul className="list">
          {suppliers.data?.map((s) => <SupplierRow key={s.id} s={s} base={base} onSaved={suppliers.reload} />)}
        </ul>
        <form className="row" style={{ marginTop: 12 }} onSubmit={addSupplier}>
          <input className="input" style={{ flex: 2 }} required placeholder="Supplier name" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" style={{ flex: 2 }} type="email" placeholder="Order email" value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input className="input" style={{ flex: 1 }} placeholder="Phone (for texts and WhatsApp)" value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <button className="btn">Add</button>
        </form>
        <p className="small muted" style={{ marginTop: 8 }}>
          Link items to suppliers and set reorder amounts on the Stock page.
        </p>
      </section>
      <TemplateEditor base={base} />
      </>
      )}
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

/** The restaurant's own wording for supplier orders. */
function TemplateEditor({ base }: { base: string }) {
  const tpl = useApi<OrderTemplate>(`${base}/order-template`);
  const [text, setText] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const value = text ?? tpl.data?.template ?? "";

  function insert(key: string) {
    const el = box.current;
    const token = `{${key}}`;
    if (!el) return setText(value + token);
    const [a, b] = [el.selectionStart, el.selectionEnd];
    setText(value.slice(0, a) + token + value.slice(b));
    window.setTimeout(() => { el.focus(); el.setSelectionRange(a + token.length, a + token.length); }, 0);
  }

  async function save(template: string) {
    setStatus("");
    try {
      await api(`${base}/order-template`, { method: "PUT", body: { template } });
      setText(null);
      await tpl.reload();
      setStatus("Saved. New messages use this wording.");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Couldn't save.");
    }
  }

  if (!tpl.data) return null;
  return (
    <details className="panel tpl-details" style={{ marginTop: 16 }}>
      <summary>
        <h2>Order message template</h2>
        <span className="small muted">{tpl.data.is_default ? "Standard wording" : "Your wording"} · Edit ▾</span>
      </summary>
      <p className="small muted">
        Every order to a supplier starts from this message. Words in {"{curly brackets}"} are filled in for each order.
        Tap one to add it where your cursor is.
      </p>
      <div className="chips">
        {tpl.data.placeholders.map((p) => (
          <button key={p.key} type="button" className="chip" title={p.label} onClick={() => insert(p.key)}>
            {`{${p.key}}`} <span className="muted">{p.label}</span>
          </button>
        ))}
      </div>
      <textarea ref={box} className="textarea" rows={12} value={value} onChange={(e) => setText(e.target.value)}
        aria-label="Order message template" style={{ fontFamily: "inherit", marginTop: 8 }} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn primary small" disabled={text === null} onClick={() => void save(value)}>Save template</button>
        {!tpl.data.is_default && (
          <button className="btn small" onClick={() => window.confirm("Go back to the standard message?") && void save("")}>Use the standard message</button>
        )}
        {status && <span className="small muted">{status}</span>}
      </div>
    </details>
  );
}

function SupplierRow({ s, base, onSaved }: { s: Supplier; base: string; onSaved: () => Promise<void> }) {
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
        <button className="btn small" onClick={() => setEditing(true)}>Edit</button>
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
