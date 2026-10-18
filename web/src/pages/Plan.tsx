import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import type { Forecast, Report } from "../lib/api";

const compact = (cents: number | null) =>
  cents == null
    ? "–"
    : new Intl.NumberFormat(undefined, { style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol", notation: "compact", maximumFractionDigits: 1 }).format(cents / 100);

type Insights = {
  slow_days: { weekday: string; avg_sales: string }[];
  slow_hours: { weekday: string; hour: string; avg_sales: string; pct_of_peak_hour: number }[];
  rising_items: { name: string; change_pct: number }[];
  falling_items: { name: string; change_pct: number }[];
  upcoming_days: { day: string; name: string; kind: string }[];
  guests: { marketing_consent: number; lapsed_45_days_with_consent: number; regulars_5_plus_visits: number };
};

/** Forecast, end-of-day/week/month reports, labour and the facts behind a marketing plan. */
export default function Plan() {
  const { location } = useApp();
  const [kind, setKind] = useState<"day" | "week" | "month">("day");
  const fc = useApi<Forecast>(`/locations/${location.id}/forecast?days=7`);
  const report = useApi<Report>(`/locations/${location.id}/reports/${kind}`);
  const insights = useApi<Insights>(`/locations/${location.id}/marketing`);
  const days = fc.data?.days ?? [];
  const max = Math.max(1, ...days.map((d) => d.expected_cents ?? 0));
  const trend = days.flatMap((d) => d.factors).find((f) => f.name === "recent trend");
  const special = days.filter((d) => d.factors.some((f) => f.name !== "recent trend"));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plan</h1>
          <p>The week ahead from your own sales history, BC holidays and the weather.</p>
        </div>
      </div>

      <div className="grid two">
        <section className="panel">
          <div className="spread">
            <h2>Next 7 days</h2>
            {fc.data && <strong>{fc.data.total_expected}</strong>}
          </div>
          {fc.error && <p className="error">{fc.error}</p>}
          <div className="forecast">
            {days.map((d) => (
              <div key={d.day} className="forecast-day" title={d.factors.map((f) => `${f.name}: ${f.effect_pct}%`).join("\n")}>
                <div className="bar-wrap">
                  <div className="bar" style={{ height: `${((d.expected_cents ?? 0) / max) * 100}%` }} />
                </div>
                <div className="small">{d.weekday}</div>
                <div className="forecast-amount muted">{compact(d.expected_cents)}</div>
                {d.holiday && <span className="pill warn">{d.holiday}</span>}
              </div>
            ))}
          </div>
          {trend && (
            <p className="small muted">
              All days include the recent trend ({trend.effect_pct > 0 ? "+" : ""}
              {trend.effect_pct}%, {trend.source}).
            </p>
          )}
          {special.length > 0 && (
            <ul className="list small">
              {special.map((d) => (
                <li key={d.day}>
                  <strong>{d.weekday}</strong>:{" "}
                  {d.factors
                    .filter((f) => f.name !== "recent trend")
                    .map((f) => `${f.name} ${f.effect_pct > 0 ? "+" : ""}${f.effect_pct}% (${f.source})`)
                    .join("; ")}
                </li>
              ))}
            </ul>
          )}
          {fc.data && !fc.data.weather_included && (
            <p className="small muted">
              No weather forecast yet. It's fetched each morning once the restaurant's map position is set in Settings.
            </p>
          )}
        </section>

        <section className="panel">
          <h2>Prep list for tomorrow</h2>
          {fc.data?.prep_list_tomorrow.length ? (
            <table className="data">
              <tbody>
                {fc.data.prep_list_tomorrow.map((p) => (
                  <tr key={p.name}>
                    <td>{p.name}</td>
                    <td className="num">{p.expected_qty}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">Needs a few weeks of sales history.</p>
          )}
        </section>
      </div>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="spread">
          <h2>Report</h2>
          <div className="seg" role="group" aria-label="Report">
            {(["day", "week", "month"] as const).map((k) => (
              <button key={k} className={k === kind ? "on" : ""} onClick={() => setKind(k)}>
                {{ day: "Yesterday", week: "Last week", month: "Last month" }[k]}
              </button>
            ))}
          </div>
        </div>
        {report.error && <p className="error">{report.error}</p>}
        {report.data && (
          <>
            <div className="figures" style={{ marginTop: 12 }}>
              <div className="figure">
                <div className="label">Labour</div>
                <div className="value">
                  {report.data.labour.labour_pct_of_sales != null ? `${report.data.labour.labour_pct_of_sales}%` : "–"}
                </div>
                <div className="small muted">
                  {report.data.labour.cost} for {report.data.labour.hours} h
                </div>
              </div>
              <div className="figure">
                <div className="label">Food cost</div>
                <div className="value">
                  {report.data.food_cost.food_cost_pct != null ? `${report.data.food_cost.food_cost_pct}%` : "–"}
                </div>
                <div className="small muted">of sales, from recipe costs</div>
              </div>
            </div>
            <p className="report-text">{report.data.text}</p>
            {report.data.labour.note && <p className="small muted">{report.data.labour.note}</p>}
          </>
        )}
      </section>

      {insights.data && (
        <section className="panel" style={{ marginTop: 16 }}>
          <h2>Marketing ideas start here</h2>
          <p className="small muted" style={{ marginBottom: 12 }}>
            Ask the assistant to "plan a promotion for our slowest night" to turn these into a plan.
          </p>
          <div className="grid two">
            <div>
              <h3>Slowest times</h3>
              <ul className="list small">
                {insights.data.slow_days.map((d) => (
                  <li key={d.weekday}>
                    {d.weekday}: about {d.avg_sales} a day
                  </li>
                ))}
                {insights.data.slow_hours.map((h) => (
                  <li key={h.weekday + h.hour}>
                    {h.weekday} {h.hour}: {h.pct_of_peak_hour}% of that day's busiest hour
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Coming up</h3>
              <ul className="list small">
                {insights.data.upcoming_days.map((d) => (
                  <li key={d.day}>
                    {d.day}: {d.name}
                  </li>
                ))}
                {insights.data.rising_items.map((i) => (
                  <li key={i.name}>
                    Rising: {i.name} (+{i.change_pct}%)
                  </li>
                ))}
              </ul>
              <p className="small muted">
                {insights.data.guests.marketing_consent} guests agreed to hear from you;{" "}
                {insights.data.guests.lapsed_45_days_with_consent} haven't visited in 45 days.
              </p>
            </div>
          </div>
        </section>
      )}
    </>
  );
}
