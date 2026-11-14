import { useMemo, useState } from "react";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { money, qty, type Cogs } from "../../lib/inventory";

const day = (d: Date) => d.toLocaleDateString("en-CA");
type Preset = "7" | "month" | "lastmonth" | "count" | "custom";

/** COGS = opening value + purchases − closing value, against food sales: the actual food cost %, next to what the
 * recipes say it should be. The gap is waste, over-portioning, theft or recipe errors, item by item. */
export default function FoodCost() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const today = new Date();
  const [preset, setPreset] = useState<Preset>("7");
  const [range, setRange] = useState({ start: day(new Date(Date.now() - 6 * 86400000)), end: day(today) });
  const { data, error } = useApi<Cogs>(`${base}/inventory/cogs?start=${range.start}&end=${range.end}`);
  const lastCount = data?.counts[0];

  function choose(p: Preset) {
    setPreset(p);
    const now = new Date();
    if (p === "7") setRange({ start: day(new Date(Date.now() - 6 * 86400000)), end: day(now) });
    if (p === "month") setRange({ start: day(new Date(now.getFullYear(), now.getMonth(), 1)), end: day(now) });
    if (p === "lastmonth") setRange({ start: day(new Date(now.getFullYear(), now.getMonth() - 1, 1)), end: day(new Date(now.getFullYear(), now.getMonth(), 0)) });
    if (p === "count" && data?.counts.length) {
      const [last, prev] = data.counts;
      setRange(prev ? { start: day(new Date(prev)), end: day(new Date(last)) } : { start: day(new Date(last)), end: day(now) });
    }
  }

  const gap = data && data.food_cost_pct != null && data.theoretical_pct != null ? Math.round((data.food_cost_pct - data.theoretical_pct) * 10) / 10 : null;
  const maxUsed = useMemo(() => Math.max(1, ...(data?.by_category ?? []).map((c) => Math.abs(c.used_cents))), [data]);

  return (
    <div className="stack">
      <div className="spread inv-toolbar">
        <div className="seg">
          <button className={preset === "7" ? "on" : ""} onClick={() => choose("7")}>Last 7 days</button>
          <button className={preset === "month" ? "on" : ""} onClick={() => choose("month")}>This month</button>
          <button className={preset === "lastmonth" ? "on" : ""} onClick={() => choose("lastmonth")}>Last month</button>
          {lastCount && <button className={preset === "count" ? "on" : ""} onClick={() => choose("count")}>{data!.counts.length > 1 ? "Between counts" : "Since last count"}</button>}
        </div>
        <div className="row small">
          <input className="input" type="date" style={{ width: "auto" }} value={range.start} onChange={(e) => { setPreset("custom"); setRange({ ...range, start: e.target.value }); }} />
          to
          <input className="input" type="date" style={{ width: "auto" }} value={range.end} onChange={(e) => { setPreset("custom"); setRange({ ...range, end: e.target.value }); }} />
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!data ? <p className="muted">Loading…</p> : (
        <>
          <div className="cogs-flow panel">
            <div><span className="small muted">Opening value</span><strong>{money(data.opening_cents, 0)}</strong></div>
            <span className="cogs-op">+</span>
            <div><span className="small muted">Purchases</span><strong>{money(data.received_cents, 0)}</strong></div>
            <span className="cogs-op">−</span>
            <div><span className="small muted">Closing value</span><strong>{money(data.closing_cents, 0)}</strong></div>
            <span className="cogs-op">=</span>
            <div className="cogs-total"><span className="small muted">Cost of goods sold</span><strong>{money(data.cogs_cents, 0)}</strong></div>
          </div>
          <div className="figures">
            <div className="figure"><div className="label">Food sales</div><div className="value">{money(data.food_sales_cents, 0)}</div>
              <div className="small muted">before tax and tips</div></div>
            <div className="figure"><div className="label">Actual food cost</div>
              <div className={`value ${data.food_cost_pct != null && data.food_cost_pct > data.target_pct ? "error" : ""}`}>{data.food_cost_pct != null ? `${data.food_cost_pct}%` : "–"}</div>
              <div className="small muted">target {data.target_pct}%</div></div>
            <div className="figure"><div className="label">Theoretical</div><div className="value">{data.theoretical_pct != null ? `${data.theoretical_pct}%` : "–"}</div>
              <div className="small muted">what the recipes say</div></div>
            <div className="figure"><div className="label">Gap</div>
              <div className={`value ${gap != null && gap > 2 ? "error" : gap != null ? "good-text" : ""}`}>{gap != null ? `${gap > 0 ? "+" : ""}${gap} pts` : "–"}</div>
              <div className="small muted">aim for under 2 points · waste {money(data.waste_cents, 0)}</div></div>
          </div>
          {data.opening_cents === 0 && data.received_cents > 0 && (
            <p className="small warn-text">⚠ Stock tracking started inside these dates, so what you already had counts as a purchase. Food cost % is reliable for periods after your first full count.</p>
          )}
          {data.food_sales_cents === 0 && <p className="small muted">No sales in these dates yet, so there's no food cost % to show. Sales come from your POS or from Clover export files in Settings.</p>}

          {data.by_category.length > 0 && (
            <div className="panel">
              <h2>By category</h2>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Category</th><th className="num">Purchases</th><th className="num">Used</th><th style={{ width: "30%" }} /><th className="num">Waste</th></tr></thead>
                  <tbody>
                    {data.by_category.map((c) => (
                      <tr key={c.category}>
                        <td>{c.category}</td><td className="num">{money(c.received_cents, 0)}</td><td className="num">{money(c.used_cents, 0)}</td>
                        <td><span className="cogs-bar" style={{ width: `${Math.max(2, (Math.abs(c.used_cents) / maxUsed) * 100)}%` }} /></td>
                        <td className="num">{c.waste_cents ? money(c.waste_cents, 0) : <span className="muted">–</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="panel">
            <h2>Variance: actual vs theoretical usage</h2>
            <p className="small muted" style={{ marginTop: -6, marginBottom: 10 }}>Biggest $ first. Theoretical is what sales used through the recipes; anything more is waste, counts or corrections.</p>
            {data.variance.length === 0 ? <p className="small muted">Nothing to compare yet. Variance appears once dishes have recipes and you've counted or logged waste.</p> : (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Item</th><th className="num">Theoretical</th><th className="num">Actual</th><th className="num">Variance</th><th className="num">$</th><th className="num">Of which waste</th></tr></thead>
                  <tbody>
                    {data.variance.map((v) => (
                      <tr key={v.item_id ?? v.name}>
                        <td data-label="Item">{v.name}<div className="small muted">{v.category}</div></td>
                        <td data-label="Theoretical" className="num">{qty(v.theoretical_qty)} <span className="muted">{v.unit ?? ""}</span></td>
                        <td data-label="Actual" className="num">{qty(v.actual_qty)}</td>
                        <td data-label="Variance" className={`num ${v.variance_qty > 0 ? "error" : ""}`}>{v.variance_qty > 0 ? "+" : ""}{qty(v.variance_qty)}{v.variance_pct != null && <span className="small muted"> ({v.variance_pct}%)</span>}</td>
                        <td data-label="$" className={`num ${v.variance_cents > 0 ? "error" : ""}`}>{money(v.variance_cents)}</td>
                        <td data-label="Of which waste" className="num">{v.waste_cents ? money(v.waste_cents) : <span className="muted">–</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
