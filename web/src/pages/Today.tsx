import { useState } from "react";
import { Link } from "react-router-dom";
import AreaChart, { type ChartPoint } from "../components/AreaChart";
import { useApp } from "../context";
import { useApi } from "../hooks";
import type { Dashboard } from "../lib/api";
import { money, pct } from "../lib/format";
import type { Cogs } from "../lib/inventory";
import FoodCost from "./inventory/FoodCost";

type Totals = { gross_cents: number; orders: number; tip_cents: number; avg_ticket_cents: number | null };
type Series = {
  range: string; unit: "day" | "week" | "month"; currency: string;
  points: (Totals & { start: string })[];
  this_month: Totals; last_month: Totals; this_month_label: string; last_month_label: string;
};
type Range = "daily" | "3m" | "6m" | "12m";
const RANGES: [Range, string][] = [["daily", "Daily"], ["3m", "3 months"], ["6m", "6 months"], ["12m", "12 months"]];
type Metric = "gross_cents" | "orders";

const localDay = (d: Date) => d.toLocaleDateString("en-CA");

/** Home: the month at a glance, sales over time, today's floor, and food cost. */
export default function Today() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const manager = location.role !== "staff";
  const [range, setRange] = useState<Range>("daily");
  const today = useApi<Dashboard>(`${base}/dashboard?period=today`);
  const series = useApi<Series>(`${base}/dashboard/series?range=${range}`);
  const now = new Date();
  const cogs = useApi<Cogs>(manager ? `${base}/inventory/cogs?start=${localDay(new Date(now.getFullYear(), now.getMonth(), 1))}&end=${localDay(now)}` : null);

  const cur = location.currency;
  const compact = (cents: number) => new Intl.NumberFormat(undefined, {
    style: "currency", currency: cur, notation: "compact", maximumFractionDigits: cents >= 100000 ? 2 : 0 }).format(cents / 100);
  const whole = (n: number) => new Intl.NumberFormat(undefined, { notation: n >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n);
  const s = series.data;
  const t = today.data;

  function points(metric: Metric): ChartPoint[] {
    if (!s) return [];
    return s.points.map((p) => {
      const d = new Date(`${p.start}T12:00:00`);
      const label = s.unit === "month" ? d.toLocaleDateString(undefined, { month: "short" })
        : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      const tip = s.unit === "month" ? d.toLocaleDateString(undefined, { month: "long", year: "numeric" })
        : s.unit === "week" ? `Week of ${label}` : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
      return { label, tip, value: p[metric] };
    });
  }

  const charts: { metric: Metric; title: string; info: string; fmt: (v: number) => string }[] = [
    { metric: "gross_cents", title: "Sales", info: "Total sales before tax and tips, from your POS", fmt: compact },
    { metric: "orders", title: "Orders", info: "Number of orders (tickets) closed", fmt: whole },
  ];

  const kpis = [
    { label: "Sales today", value: t?.summary.gross ?? "–", info: "So far today, before tax and tips",
      sub: t?.summary.comparison?.gross_change_pct != null ? `${pct(t.summary.comparison.gross_change_pct)} vs last week` : null },
    { label: `Sales in ${s?.this_month_label ?? "this month"}`, value: s ? compact(s.this_month.gross_cents) : "–",
      info: "This month so far", sub: s ? `${s.last_month_label} ${compact(s.last_month.gross_cents)}` : null },
    { label: "Orders this month", value: s ? whole(s.this_month.orders) : "–", info: "Orders closed this month so far",
      sub: s ? `${s.last_month_label} ${whole(s.last_month.orders)}` : null },
    manager
      ? { label: "Food cost this month", value: cogs.data?.food_cost_pct != null ? `${cogs.data.food_cost_pct}%` : "–",
        info: "What food cost against food sales, from stock counts and invoices", warn: cogs.data?.food_cost_pct != null && cogs.data.food_cost_pct > cogs.data.target_pct,
        sub: cogs.data ? `target ${cogs.data.target_pct}%` : null }
      : { label: "Review rating, 7 days", value: t?.reviews.average_rating?.toFixed(1) ?? "–", info: "Average star rating", sub: null },
  ];

  const hours = t?.by_hour.rows ?? [];
  const maxHour = Math.max(1, ...hours.map((h) => h.gross_cents));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Home</h1>
          <p>{location.name}, updated from your POS{t?.as_of ? ` · as of ${new Date(t.as_of).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}</p>
        </div>
      </div>
      {(today.error || series.error) && <p className="error">{today.error || series.error}</p>}

      <div className="kpi-band">
        {kpis.map((k) => (
          <div key={k.label} className="kpi-card">
            <div className="kpi-head"><span>{k.label}</span><span className="info-dot tip" tabIndex={0} data-tip={k.info}>i</span></div>
            <div className={`kpi-value ${"warn" in k && k.warn ? "error" : ""}`}>{k.value}</div>
            {k.sub && <div className="kpi-sub">{k.sub}</div>}
          </div>
        ))}
      </div>

      <div className="seg range-seg" role="group" aria-label="Range">
        {RANGES.map(([r, label]) => (
          <button key={r} className={r === range ? "on" : ""} onClick={() => setRange(r)}>{label}</button>
        ))}
      </div>

      <div className="chart-grid">
        {charts.map((c) => {
          const now = s?.this_month[c.metric];
          const before = s?.last_month[c.metric];
          return (
            <section key={c.metric} className="chart-card">
              <div className="chart-head">
                <h2>{c.title}</h2>
                <span className="info-dot tip" tabIndex={0} data-tip={c.info}>i</span>
              </div>
              <div className="chart-figure">
                <strong>{now != null ? c.fmt(now) : "–"}</strong>
                {before != null && <span>{s!.last_month_label} {c.fmt(before)}</span>}
              </div>
              {s ? <AreaChart points={points(c.metric)} format={c.fmt} name={`${c.title} over time`} /> : <div className="chart-empty muted small">Loading…</div>}
              <div className="chart-legend"><span className="legend-swatch" />{c.title}</div>
            </section>
          );
        })}
      </div>

      {t && (
        <div className="chart-grid three">
          <section className="chart-card">
            <div className="chart-head"><h2>Today by hour</h2></div>
            {hours.length === 0 ? <p className="empty">No sales yet today.</p> : (
              <>
                <div className="bars">
                  {hours.map((h) => (
                    <div key={h.hour} className="bar" style={{ height: `${(h.gross_cents / maxHour) * 100}%` }}
                      title={`${h.hour}: ${money(h.gross_cents, cur)}`} />
                  ))}
                </div>
                <div className="bars-axis"><span>{hours[0].hour}</span><span>{hours[hours.length - 1].hour}</span></div>
              </>
            )}
          </section>
          <section className="chart-card">
            <div className="chart-head"><h2>Best sellers today</h2></div>
            <ul className="list">
              {t.top_items.rows.map((r) => (
                <li key={r.name} className="spread"><span>{r.name}</span><span className="muted">{r.qty} sold · {r.revenue}</span></li>
              ))}
              {t.top_items.rows.length === 0 && <li className="muted">No items sold yet.</li>}
            </ul>
          </section>
          <section className="chart-card">
            <div className="chart-head"><h2>Running low</h2><Link to="/inventory" className="small">All stock</Link></div>
            <ul className="list">
              {t.low_stock.items.map((i) => (
                <li key={i.id} className="spread"><span>{i.name}</span><span className="pill low">{i.stock_qty} {i.unit ?? "left"}</span></li>
              ))}
              {t.low_stock.items.length === 0 && <li className="muted">Nothing is below its alert level.</li>}
            </ul>
          </section>
        </div>
      )}

      {manager && (
        <section id="food-cost" className="stack home-food-cost">
          <div>
            <h2 style={{ margin: 0 }}>Food cost</h2>
            <p className="small muted" style={{ margin: "4px 0 0" }}>What your food actually cost against food sales, next to
              what your recipes say it should be.</p>
          </div>
          <FoodCost />
        </section>
      )}
    </>
  );
}
