import { useEffect, useRef, useState } from "react";
import { api, type OrderMessage, type Supplier } from "../lib/api";
import { Dialog } from "./PosDialogs";

type Line = { item_id?: string | null; name: string; qty: number; unit?: string | null; reason?: string };
type Suggestion = { lines: Line[]; ai: boolean; summary: string;
  basis: { deliveries: number; every_days: number; forecast_pct: number; last_delivery: string | null } };

function tomorrow() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toLocaleDateString("en-CA");
}

/** An order email to one supplier: starts from the restaurant's template; "Generate with AI" fills in the items
 * and quantities from this supplier's delivery history, stock and the forecast. Then save it as a draft or send. */
export default function SupplierOrderDialog({ base, supplier, onClose, onDone }: {
  base: string; supplier: Supplier; onClose: () => void; onDone: (message: string) => void;
}) {
  const [lines, setLines] = useState<Line[]>([]);
  const [date, setDate] = useState(tomorrow());
  const [msg, setMsg] = useState<OrderMessage | null>(null);
  const [text, setText] = useState("");
  const [edited, setEdited] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [tip, setTip] = useState<Suggestion | null>(null);
  const [copied, setCopied] = useState(false);
  const editedRef = useRef(edited);
  editedRef.current = edited;

  // The email follows the items and date, until the owner edits the words themselves.
  const valid = lines.filter((l) => l.name.trim() && l.qty > 0);
  const key = JSON.stringify([valid.map((l) => [l.name, l.qty, l.unit]), date]);
  useEffect(() => {
    let live = true;
    const t = window.setTimeout(() => {
      void api<OrderMessage>(`${base}/suppliers/${supplier.id}/order-message`, {
        method: "POST", body: { lines: valid.map(({ reason, ...l }) => l), delivery_date: date || undefined },
      }).then((m) => {
        if (!live) return;
        setMsg(m);
        if (!editedRef.current) setText(m.body);
      }).catch(() => undefined);
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  async function generate() {
    setBusy("ai");
    setError("");
    try {
      const s = await api<Suggestion>(`${base}/suppliers/${supplier.id}/order-suggestion`, { method: "POST" });
      setTip(s);
      if (s.lines.length) {
        setLines(s.lines);
        setEdited(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't work out the order.");
    } finally {
      setBusy("");
    }
  }

  function setLine(i: number, patch: Partial<Line>) {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  }

  async function save(send: "email" | "manual" | null) {
    if (!valid.length) return setError("Add at least one item to the order.");
    setBusy(send ?? "draft");
    setError("");
    try {
      await api(`${base}/purchase-orders`, {
        method: "POST",
        body: {
          supplier_id: supplier.id, lines: valid.map(({ reason, ...l }) => l), delivery_date: date || undefined,
          message: text, send: send ?? undefined,
        },
      });
      onDone(send === "email" ? `Order emailed to ${supplier.name}.`
        : send === "manual" ? `Order to ${supplier.name} saved as sent.`
        : `Order to ${supplier.name} saved as a draft. Find it under Orders.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the order.");
      setBusy("");
    }
  }

  async function openInEmailApp() {
    if (!valid.length) return setError("Add at least one item to the order.");
    const href = `mailto:${supplier.email ?? ""}?subject=${encodeURIComponent(msg?.subject ?? "")}&body=${encodeURIComponent(text)}`;
    window.open(href, "_blank");
    await save("manual");
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the text is there to select */
    }
  }

  const canEmail = !!supplier.email && !!msg?.ready.email;
  const footer = (
    <div className="inv-foot">
      <button className="btn" disabled={busy !== ""} onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy email"}</button>
      <div className="row">
        <button className="btn" disabled={busy !== ""} onClick={() => void save(null)}>{busy === "draft" ? "Saving…" : "Save as draft"}</button>
        {canEmail ? (
          <button className="btn primary" disabled={busy !== "" || !valid.length} onClick={() => void save("email")}>
            {busy === "email" ? "Sending…" : `Send email to ${supplier.email}`}
          </button>
        ) : (
          <button className="btn primary" disabled={busy !== "" || !valid.length} onClick={() => void openInEmailApp()}
            title="Opens your own email app with this message, and marks the order as sent">
            Send from my email app
          </button>
        )}
      </div>
    </div>
  );

  return (
    <Dialog title={<span>Order email to {supplier.name}</span>} onClose={onClose} width="min(980px, 100%)" footer={footer}>
      <div className="so-grid">
        <section className="stack" style={{ gap: 10, minWidth: 0 }}>
          <div className="spread">
            <h3 style={{ margin: 0 }}>Items</h3>
            <button className="btn seo-ai" disabled={busy !== ""} onClick={() => void generate()}>
              ✨ {busy === "ai" ? "Working it out…" : lines.length ? "Generate again with AI" : "Generate with AI"}
            </button>
          </div>
          {tip && (
            <div className="seo-tip">
              <span className="small">{tip.summary}</span>
              {tip.basis.deliveries > 0 && (
                <span className="small muted">
                  Delivers about every {tip.basis.every_days} days
                  {tip.basis.forecast_pct !== 0 && ` · coming days ${tip.basis.forecast_pct > 0 ? "+" : ""}${tip.basis.forecast_pct}% vs usual`}
                  {tip.basis.last_delivery && ` · last delivery ${new Date(`${tip.basis.last_delivery}T12:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`}
                </span>
              )}
            </div>
          )}
          {lines.length === 0 && (
            <p className="small muted">
              Press <strong>Generate with AI</strong> to fill in what to order from your sales and past deliveries from
              {" "}{supplier.name}, or add items yourself.
            </p>
          )}
          <ul className="so-lines">
            {lines.map((l, i) => (
              <li key={i}>
                <input className="input so-qty" type="number" min={0} step="any" aria-label="Quantity" value={l.qty}
                  onChange={(e) => setLine(i, { qty: Number(e.target.value) })} />
                <input className="input so-unit" aria-label="Unit" placeholder="unit" value={l.unit ?? ""}
                  onChange={(e) => setLine(i, { unit: e.target.value || null })} />
                <div className="so-name">
                  <input className="input" aria-label="Item" placeholder="Item" value={l.name}
                    onChange={(e) => setLine(i, { name: e.target.value, item_id: null })} />
                  {l.reason && <small className="muted">{l.reason}</small>}
                </div>
                <button className="sb-icon" aria-label={`Remove ${l.name}`} onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>
              </li>
            ))}
          </ul>
          <button className="btn small" style={{ alignSelf: "start" }} onClick={() => setLines([...lines, { name: "", qty: 1, unit: null }])}>+ Add an item</button>
          <label className="row small" style={{ marginTop: 6 }}>
            Delivery date
            <input className="input" type="date" style={{ width: "auto" }} value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
        </section>
        <section className="stack so-email" style={{ gap: 8, minWidth: 0 }}>
          <h3 style={{ margin: 0 }}>Email</h3>
          <div className="so-meta small">
            <span className="muted">To</span>
            <span>{supplier.email ?? <em className="muted">no email yet: add it under Suppliers</em>}</span>
            <span className="muted">Subject</span>
            <span>{msg?.subject ?? "…"}</span>
          </div>
          <textarea className="textarea so-body" rows={14} value={text} aria-label="Email to the supplier"
            onChange={(e) => { setText(e.target.value); setEdited(true); }} />
          {edited && msg && (
            <button className="linkish small" style={{ alignSelf: "start" }} onClick={() => { setText(msg.body); setEdited(false); }}>
              Undo my edits (use the template)
            </button>
          )}
          {!canEmail && (
            <p className="small muted">
              {!supplier.email ? "This supplier has no email address yet." : "Sending email straight from Pass isn't set up yet."}
              {" "}"Send from my email app" opens the message in your own email, and saves the order as sent.
            </p>
          )}
          {error && <p className="small error" role="alert">{error}</p>}
        </section>
      </div>
    </Dialog>
  );
}
