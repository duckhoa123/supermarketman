import { useState, type FormEvent } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type PurchaseOrder, type Supplier } from "../lib/api";
import { timeAgo } from "../lib/format";

const STATUS_PILL: Record<string, string> = { draft: "warn", approved: "", sent: "good", received: "good", cancelled: "" };

/** Suppliers and the orders drafted when stock runs low. Nothing is sent without approval. */
export default function Purchasing() {
  const { location } = useApp();
  const orders = useApi<PurchaseOrder[]>(`/locations/${location.id}/purchase-orders`);
  const suppliers = useApi<Supplier[]>(`/locations/${location.id}/suppliers`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [form, setForm] = useState({ name: "", email: "", phone: "", notes: "" });

  async function act(path: string, label: string) {
    setBusy(label);
    setError("");
    try {
      await api(`/locations/${location.id}${path}`, { method: "POST", body: {} });
      await orders.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy("");
    }
  }

  async function addSupplier(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api(`/locations/${location.id}/suppliers`, {
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
      <div className="page-head">
        <div>
          <h1>Purchasing</h1>
          <p>When stock runs low, an order to that item's supplier is drafted here for you to approve.</p>
        </div>
        <button className="btn" disabled={busy !== ""} onClick={() => void act("/purchase-orders/draft", "draft")}>
          Draft orders for low stock
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <section className="panel">
        <h2>Orders</h2>
        {orders.data?.length === 0 && <p className="muted">No supplier orders yet.</p>}
        <ul className="list">
          {orders.data?.map((po) => (
            <li key={po.id} className="stack" style={{ gap: 6 }}>
              <div className="spread">
                <strong>{po.supplier ?? "No supplier"}</strong>
                <span className={`pill ${STATUS_PILL[po.status]}`}>{po.status}</span>
              </div>
              <div className="small">{po.lines.map((l) => `${l.qty} ${l.unit ?? ""} ${l.name}`).join(" · ")}</div>
              <div className="small muted">
                {po.created_at && `Drafted ${timeAgo(po.created_at)}`}
                {po.sent_at && ` · emailed ${timeAgo(po.sent_at)}`}
              </div>
              {po.send_error && <p className="small error">{po.send_error}</p>}
              <div className="row">
                {po.status === "draft" && (
                  <>
                    <button
                      className="btn primary small"
                      disabled={busy !== ""}
                      onClick={() => void act(`/purchase-orders/${po.id}/approve`, po.id)}
                    >
                      Approve and send
                    </button>
                    <button className="btn small" onClick={() => void act(`/purchase-orders/${po.id}/cancel`, po.id)}>
                      Cancel
                    </button>
                  </>
                )}
                {(po.status === "approved" || po.status === "sent") && (
                  <button className="btn small" onClick={() => void act(`/purchase-orders/${po.id}/received`, po.id)}>
                    Mark received (adds to stock)
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <h2>Suppliers</h2>
        <ul className="list">
          {suppliers.data?.map((s) => (
            <li key={s.id} className="spread">
              <div>
                <strong>{s.name}</strong>
                <div className="small muted">{[s.email ?? "no email yet", s.phone, s.notes].filter(Boolean).join(" · ")}</div>
              </div>
            </li>
          ))}
        </ul>
        <form className="row" style={{ marginTop: 12 }} onSubmit={addSupplier}>
          <input className="input" style={{ flex: 2 }} required placeholder="Supplier name" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" style={{ flex: 2 }} type="email" placeholder="Order email" value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input className="input" style={{ flex: 1 }} placeholder="Phone" value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <button className="btn">Add</button>
        </form>
        <p className="small muted" style={{ marginTop: 8 }}>
          Link items to suppliers and set reorder amounts on the Stock page.
        </p>
      </section>
    </>
  );
}
