import { useEffect, useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type AppOrder } from "../lib/api";
import { timeAgo } from "../lib/format";

const ACTIONS: Record<string, { status: string; label: string; primary?: boolean }[]> = {
  placed: [
    { status: "accepted", label: "Accept", primary: true },
    { status: "cancelled", label: "Reject" },
  ],
  accepted: [{ status: "ready", label: "Ready", primary: true }],
};

/** Pickup and delivery orders from the customer app. Delivery orders go to our drivers once marked ready. */
export default function AppOrders() {
  const { location } = useApp();
  const { data, reload, error } = useApi<{ orders: AppOrder[] }>(`/locations/${location.id}/app-orders`);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    const t = window.setInterval(() => void reload(), 20_000);
    return () => window.clearInterval(t);
  }, [reload]);

  async function move(order: AppOrder, status: string) {
    setActionError("");
    try {
      await api(`/locations/${location.id}/app-orders/${order.id}/status`, { method: "POST", body: { status } });
      await reload();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't update the order.");
    }
  }

  const orders = data?.orders ?? [];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>App orders</h1>
          <p>Orders from the customer app. Mark delivery orders ready and the nearest driver is sent.</p>
        </div>
      </div>
      {(error || actionError) && <p className="error">{error || actionError}</p>}
      {orders.length === 0 && <p className="empty panel">No open orders. New ones appear here and as a notification.</p>}
      <div className="grid two">
        {orders.map((o) => (
          <section key={o.id} className="panel stack" style={{ gap: 8 }}>
            <div className="spread">
              <strong>
                {o.fulfilment === "delivery" ? "Delivery" : "Pickup"} · {o.subtotal}
              </strong>
              <span className="pill warn">{o.status.replace(/_/g, " ")}</span>
            </div>
            <ul className="list small">
              {o.lines.map((l) => (
                <li key={l.item_id}>
                  {l.qty}× {l.name}
                </li>
              ))}
            </ul>
            {o.notes && <p className="small">Note: {o.notes}</p>}
            <div className="small muted">
              Placed {timeAgo(o.placed_at)} · fee {o.platform_fee}
              {o.delivery && ` · driver ${o.delivery.driver_assigned ? "on the way" : "not assigned yet"}`}
            </div>
            <div className="row">
              {(ACTIONS[o.status] ?? []).map((a) => (
                <button key={a.status} className={`btn small ${a.primary ? "primary" : ""}`} onClick={() => void move(o, a.status)}>
                  {a.label}
                </button>
              ))}
              {o.status === "ready" && o.fulfilment === "pickup" && (
                <button className="btn small primary" onClick={() => void move(o, "picked_up")}>
                  Collected
                </button>
              )}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
