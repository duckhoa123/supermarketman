import { useEffect, useState } from "react";
import { useApi } from "../../hooks";
import { api, type AppOrder, type BookingRow } from "../../lib/api";
import { timeAgo } from "../../lib/format";

const STEPS: Record<string, string[]> = {
  pickup: ["placed", "accepted", "ready", "picked_up"],
  delivery: ["placed", "accepted", "ready", "out_for_delivery", "delivered"],
};
const LABEL: Record<string, string> = {
  placed: "Sent", accepted: "Preparing", ready: "Ready", out_for_delivery: "On the way", delivered: "Delivered",
  picked_up: "Collected", cancelled: "Cancelled",
};

function eta(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
}

/** The customer's orders (live status, tipping) and table bookings. */
export default function MyOrders() {
  const orders = useApi<{ orders: AppOrder[] }>("/customer/orders");
  const bookings = useApi<{ bookings: BookingRow[] }>("/customer/bookings");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const t = window.setInterval(() => void orders.reload(), 20_000);
    return () => window.clearInterval(t);
  }, [orders.reload]);

  async function post(path: string, body: unknown, done: string) {
    setMessage("");
    try {
      await api(path, { method: "POST", body });
      setMessage(done);
      await orders.reload();
      await bookings.reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "That didn't work.");
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Orders</h1>
          <p>Live status of your orders. Tips go 100% to your driver.</p>
        </div>
      </div>
      {message && <p className="panel" role="status">{message}</p>}
      {orders.data?.orders.length === 0 && <p className="empty panel">No orders yet. Try Discover or ask your assistant.</p>}
      <div className="stack">
        {orders.data?.orders.map((o) => {
          const steps = STEPS[o.fulfilment];
          const at = steps.indexOf(o.status);
          return (
            <section key={o.id} className="panel stack" style={{ gap: 8 }}>
              <div className="spread">
                <strong>{o.restaurant}</strong>
                <span className="small muted">{timeAgo(o.placed_at)} · {o.total}</span>
              </div>
              {o.status === "cancelled" ? (
                <span className="pill low">Cancelled</span>
              ) : (
                <ol className="steps">
                  {steps.map((s, i) => (
                    <li key={s} className={i <= at ? "done" : ""}>{LABEL[s]}</li>
                  ))}
                </ol>
              )}
              <div className="small">{o.lines.map((l) => `${l.qty}× ${l.name}`).join(", ")}</div>
              {o.eta_at && !["delivered", "picked_up", "cancelled"].includes(o.status) && (
                <div className="small">Expected around {eta(o.delivery?.eta_at ?? o.eta_at)}</div>
              )}
              <div className="row">
                {o.status === "placed" && (
                  <button className="btn small" onClick={() => void post(`/customer/orders/${o.id}/cancel`, {}, "Order cancelled.")}>
                    Cancel
                  </button>
                )}
                {o.fulfilment === "delivery" && o.status === "delivered" && (
                  <>
                    <span className="small muted">Tip {o.tip !== "$0.00" ? `(now ${o.tip})` : ""}:</span>
                    {(o.tip_suggestions ?? [{ pct: 10, cents: 300, label: "$3.00" }, { pct: 15, cents: 500, label: "$5.00" }]).map((t) => (
                      <button key={t.pct} className="btn small"
                        onClick={() => void post(`/customer/orders/${o.id}/tip`, { tip_cents: t.cents }, `Thanks! ${t.label} goes to your driver.`)}>
                        {t.label}
                      </button>
                    ))}
                  </>
                )}
              </div>
            </section>
          );
        })}
      </div>

      <section className="panel" style={{ marginTop: 16 }}>
        <h2>Bookings</h2>
        {bookings.data?.bookings.length === 0 && <p className="muted">No bookings yet.</p>}
        <ul className="list">
          {bookings.data?.bookings.map((b) => (
            <li key={b.id} className="spread">
              <div>
                <strong>{b.restaurant}</strong>
                <div className="small muted">{b.local_time} · {b.party_size} people</div>
              </div>
              <div className="row">
                <span className={`pill ${b.status === "confirmed" ? "good" : b.status === "requested" ? "warn" : ""}`}>{b.status}</span>
                {["requested", "confirmed"].includes(b.status) && (
                  <button className="btn small" onClick={() => void post(`/customer/bookings/${b.id}/cancel`, {}, "Booking cancelled.")}>
                    Cancel
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
