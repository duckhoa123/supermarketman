import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { NewOrderDialog } from "../components/PosDialogs";
import { PERIODS } from "../lib/format";

type OrderRow = {
  id: string;
  opened: string;
  status: string;
  total: string;
  table: string | null;
  employee: string | null;
  items: string;
};

export default function Orders() {
  const { location } = useApp();
  const [period, setPeriod] = useState("today");
  const [status, setStatus] = useState("");
  const pos = useApi<{ pos: string | null }>(`/locations/${location.id}/pos-write`);
  const [ordering, setOrdering] = useState(false);
  const [notice, setNotice] = useState("");
  const { data, error, reload } = useApi<{ total_matching: number; orders: OrderRow[] }>(
    `/locations/${location.id}/orders?period=${period}&limit=50${status ? `&status=${status}` : ""}`
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Orders</h1>
          <p>{data ? `${data.total_matching} orders` : "Orders from your POS"}</p>
        </div>
        <div className="row">
          <select className="select" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
            {PERIODS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          <select className="select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="paid">Paid</option>
            <option value="refunded">Refunded</option>
            <option value="void">Void</option>
          </select>
          <button className="btn primary" onClick={() => (pos.data?.pos ? setOrdering(true)
            : setNotice("Connect Clover in Settings first: orders you make here are sent to Clover."))}>
            + New order
          </button>
        </div>
      </div>
      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}</p>}
      {ordering && pos.data?.pos && (
        <NewOrderDialog locationId={location.id} pos={pos.data.pos} onClose={() => setOrdering(false)}
          onDone={(m) => { setOrdering(false); setNotice(m); if (period !== "today") setPeriod("today"); else void reload(); }} />
      )}
      {data && (
        <div className="panel table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Opened</th>
                <th>Table</th>
                <th>Staff</th>
                <th>Items</th>
                <th>Status</th>
                <th className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.orders.map((o) => (
                <tr key={o.id}>
                  <td>{o.opened}</td>
                  <td>{o.table ?? "–"}</td>
                  <td>{o.employee ?? "–"}</td>
                  <td className="small">{o.items}</td>
                  <td>
                    <span className={`pill ${o.status === "open" ? "warn" : o.status === "paid" ? "good" : ""}`}>
                      {o.status}
                    </span>
                  </td>
                  <td className="num">{o.total}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.orders.length === 0 && <p className="empty">No orders in this period.</p>}
        </div>
      )}
    </>
  );
}
