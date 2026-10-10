import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
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
  const { data, error } = useApi<{ total_matching: number; orders: OrderRow[] }>(
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
        </div>
      </div>
      {error && <p className="error">{error}</p>}
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
