import { useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../context";
import { useApi } from "../hooks";
import type { Dashboard } from "../lib/api";
import { money, pct, PERIODS } from "../lib/format";

export default function Today() {
  const { location } = useApp();
  const [period, setPeriod] = useState("today");
  const { data, error, loading } = useApi<Dashboard>(`/locations/${location.id}/dashboard?period=${period}`);

  const s = data?.summary;
  const change = s?.comparison?.gross_change_pct;
  const ordersChange = s?.comparison?.orders_change_pct;
  const maxDay = Math.max(1, ...(data?.trend_28d.map((d) => d.gross_cents) ?? [1]));
  const hours = data?.by_hour.rows ?? [];
  const maxHour = Math.max(1, ...hours.map((h) => h.gross_cents));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{PERIODS.find((p) => p.value === period)?.label ?? "Sales"}</h1>
          <p>{s?.note ?? `${location.name}, updated from your POS`}</p>
        </div>
        <div className="seg" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p.value} className={p.value === period ? "on" : ""} onClick={() => setPeriod(p.value)}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      {loading && !data && <p className="muted">Loading…</p>}

      {data && s && (
        <>
          <div className="figures">
            <div className="figure">
              <div className="label">Sales</div>
              <div className="value">{s.gross}</div>
              {change !== null && change !== undefined && (
                <div className={change >= 0 ? "up small" : "down small"}>
                  {pct(change)} vs same days last week
                </div>
              )}
            </div>
            <div className="figure">
              <div className="label">Orders</div>
              <div className="value">{s.orders}</div>
              {ordersChange != null && <div className="small muted">{pct(ordersChange)} vs last week</div>}
            </div>
            <div className="figure">
              <div className="label">Average ticket</div>
              <div className="value">{s.avg_ticket}</div>
            </div>
            <div className="figure">
              <div className="label">Review rating, 7 days</div>
              <div className="value">{data.reviews.average_rating?.toFixed(1) ?? "–"}</div>
            </div>
          </div>

          <div className="grid two">
            <section className="panel">
              <h2>Last 28 days</h2>
              <div className="bars" aria-label="Daily sales for the last 28 days">
                {data.trend_28d.map((d, i) => (
                  <div
                    key={d.day}
                    className={`bar ${i === data.trend_28d.length - 1 ? "today" : ""}`}
                    style={{ height: `${(d.gross_cents / maxDay) * 100}%` }}
                    title={`${d.day}: ${money(d.gross_cents, location.currency)}`}
                  />
                ))}
              </div>
              <div className="bars-axis">
                <span>{data.trend_28d[0]?.day.slice(5)}</span>
                <span>Today</span>
              </div>
            </section>

            <section className="panel">
              <h2>Sales by hour</h2>
              {hours.length === 0 ? (
                <p className="empty">No sales in this period yet.</p>
              ) : (
                <>
                  <div className="bars">
                    {hours.map((h) => (
                      <div
                        key={h.hour}
                        className="bar"
                        style={{ height: `${(h.gross_cents / maxHour) * 100}%` }}
                        title={`${h.hour}: ${money(h.gross_cents, location.currency)}`}
                      />
                    ))}
                  </div>
                  <div className="bars-axis">
                    <span>{hours[0].hour}</span>
                    <span>{hours[hours.length - 1].hour}</span>
                  </div>
                </>
              )}
            </section>

            <section className="panel">
              <h2>Best sellers</h2>
              <ul className="list">
                {data.top_items.rows.map((r) => (
                  <li key={r.name} className="spread">
                    <span>{r.name}</span>
                    <span className="muted">
                      {r.qty} sold · {r.revenue}
                    </span>
                  </li>
                ))}
                {data.top_items.rows.length === 0 && <li className="muted">No items sold yet.</li>}
              </ul>
            </section>

            <section className="panel">
              <div className="spread" style={{ marginBottom: 12 }}>
                <h2 style={{ margin: 0 }}>Running low</h2>
                <Link to="/stock" className="small">
                  All stock
                </Link>
              </div>
              <ul className="list">
                {data.low_stock.items.map((i) => (
                  <li key={i.id} className="spread">
                    <span>{i.name}</span>
                    <span className="pill low">
                      {i.stock_qty} {i.unit ?? "left"}
                    </span>
                  </li>
                ))}
                {data.low_stock.items.length === 0 && <li className="muted">Nothing is below its alert level.</li>}
              </ul>
            </section>
          </div>
        </>
      )}
    </>
  );
}
