import { Fragment, useState } from "react";
import { useApi } from "../../hooks";
import { api } from "../../lib/api";
import { areaIcon, KIND_LABEL, money, qty } from "../../lib/inventory";

type Move = { kind: string; qty_delta: number; at: string; note: string | null; reason: string | null; user: string | null;
  before_count: boolean };
type Line = { item_id: string; name: string; unit: string | null; expected: number | null; counted: number; delta: number;
  value_cents?: number; big?: boolean; during_qty: number; during: Move[]; now: number | null; after: number | null;
  before?: number; item_counted_at?: string | null };
type ReportCount = { id: string; area: string; status: "submitted" | "approved" | "rejected"; counted_at: string;
  started_at: string | null; counted_by: string | null; approved_by: string | null; items: number; changed: number;
  big: number; variance_cents: number | null; lines: Line[] };
type Report = { day: string; counts: ReportCount[]; waiting: number; days: string[] };

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const dayLabel = (d: string) => new Date(`${d}T12:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

/** The manager's view of a day's counts, every area together: for each item the amount when the count started,
 * what changed while it was being counted (tap to see the entries), what was counted, and what stock becomes. */
export default function CountReport({ base, day, onDay, onChanged }: {
  base: string; day: string; onDay: (d: string) => void; onChanged: () => void;
}) {
  const { data, error, reload } = useApi<Report>(`${base}/stock-counts/report${day ? `?day=${day}` : ""}`);
  const shownDay = day || data?.day || "";
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  async function approve(ids: string[]) {
    setBusy("approve");
    try {
      const r = await api<{ approved: number }>(`${base}/stock-counts/approve`, { method: "POST", body: { ids } });
      setMessage(`Approved ${r.approved} count${r.approved === 1 ? "" : "s"}. Stock is updated.`);
      await reload();
      onChanged();
    } finally {
      setBusy("");
    }
  }
  async function recount(c: ReportCount) {
    const note = window.prompt(`Ask for a recount of ${c.area}? Stock won't change. Add a note for the team (optional):`, "");
    if (note === null) return;
    setBusy(c.id);
    try {
      await api(`${base}/stock-counts/${c.id}/reject`, { method: "POST", body: { note: note || null } });
      setMessage(`Recount of ${c.area} requested. The team has been told.`);
      await reload();
      onChanged();
    } finally {
      setBusy("");
    }
  }

  const days = Array.from(new Set([shownDay, ...(data?.days ?? [])].filter(Boolean))).sort().reverse();
  const waiting = (data?.counts ?? []).filter((c) => c.status === "submitted");
  return (
    <section className="panel stack">
      <div className="spread" style={{ flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Count report</h2>
        <div className="row">
          <select className="select" style={{ width: "auto" }} aria-label="Day" value={shownDay} onChange={(e) => onDay(e.target.value)}>
            {days.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
          </select>
          {waiting.length > 1 && (
            <button className="btn primary" disabled={!!busy} onClick={() => void approve(waiting.map((c) => c.id))}>
              {busy === "approve" ? "Approving…" : `Approve all ${waiting.length}`}
            </button>
          )}
        </div>
      </div>
      {message && <p className="notice small">{message}</p>}
      {error && <p className="error">{error}</p>}
      {data && data.counts.length === 0 && <p className="small muted">No counts on {dayLabel(shownDay)}.</p>}
      {data?.counts.map((c) => (
        <div key={c.id} className={`cr-count ${c.status}`}>
          <div className="spread cr-head">
            <div>
              <strong>{areaIcon(c.area)} {c.area}</strong>
              <span className="small muted"> · {c.counted_by ?? "Someone"}, {c.started_at ? `${time(c.started_at)}–` : ""}{time(c.counted_at)}
                {" · "}{c.changed} of {c.items} different{c.big ? ` · ${c.big} to check` : ""}</span>
            </div>
            <div className="row">
              <strong className={c.variance_cents && c.variance_cents < 0 ? "error" : ""}>{money(c.variance_cents)}</strong>
              {c.status === "submitted" ? (
                <>
                  <button className="btn small" disabled={!!busy} onClick={() => void recount(c)}>Ask for recount</button>
                  <button className="btn small primary" disabled={!!busy} onClick={() => void approve([c.id])}>Approve</button>
                </>
              ) : (
                <span className={`pill ${c.status === "approved" ? "good" : ""}`}>
                  {c.status === "approved" ? `approved${c.approved_by ? ` by ${c.approved_by}` : ""}` : "recount asked"}
                </span>
              )}
            </div>
          </div>
          <div className="table-wrap">
            <table className="data small cr-table">
              <thead><tr>
                <th>Item</th>
                <th className="num"><span className="tip" tabIndex={0} data-tip="What Pass had when the count started">At start</span></th>
                <th className="num"><span className="tip" tabIndex={0} data-tip="Sales, deliveries and waste while it was being counted. Tap to see them.">During count</span></th>
                <th className="num">Counted</th>
                <th className="num"><span className="tip" tabIndex={0} data-tip="Counted − what Pass had when the item was counted (at start, plus changes before it was counted): what's really missing or extra">Difference</span></th>
                <th className="num">$</th>
                <th className="num"><span className="tip tip-left" tabIndex={0} data-tip={c.status === "submitted" ? "Stock now, and what it becomes when you approve" : "Stock now"}>Stock</span></th>
              </tr></thead>
              <tbody>
                {c.lines.map((l) => {
                  const key = `${c.id}:${l.item_id}`;
                  return (
                    <Fragment key={key}>
                      <tr className={l.big ? "inv-big" : ""}>
                        <td>{l.name}{l.item_counted_at && <div className="small muted">counted {time(l.item_counted_at)}</div>}</td>
                        <td className="num">{l.expected == null ? <span className="muted">none</span> : <>{qty(l.expected)} <span className="muted">{l.unit ?? ""}</span></>}</td>
                        <td className="num">
                          {l.during.length ? (
                            <button className="linkish" aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>
                              {l.during_qty > 0 ? "+" : ""}{qty(l.during_qty)} ({l.during.length}) {open === key ? "▴" : "▾"}
                            </button>
                          ) : <span className="muted">–</span>}
                        </td>
                        <td className="num"><strong>{qty(l.counted)}</strong></td>
                        <td className={`num ${l.delta < 0 ? "error" : l.delta > 0 ? "good-text" : ""}`}>{l.delta > 0 ? "+" : ""}{qty(l.delta)}</td>
                        <td className={`num ${(l.value_cents ?? 0) < 0 ? "error" : ""}`}>{money(l.value_cents)}</td>
                        <td className="num nowrap">{l.now == null ? "–" : qty(l.now)}{l.after != null && l.after !== l.now && <span className="muted"> → <strong>{qty(l.after)}</strong></span>}</td>
                      </tr>
                      {open === key && (
                        <tr className="cr-moves">
                          <td colSpan={7}>
                            <ul>
                              {l.during.map((m, n) => (
                                <li key={n}>
                                  <span className="muted">{time(m.at)}</span> <span className={`pill kind-${m.kind}`}>{KIND_LABEL[m.kind] ?? m.kind}</span>{" "}
                                  <strong>{m.qty_delta > 0 ? "+" : ""}{qty(m.qty_delta)} {l.unit ?? ""}</strong>
                                  {m.reason && <span className="muted"> · {m.reason.replace("_", " ")}</span>}
                                  {m.note && <span className="muted"> · {m.note}</span>}
                                  {m.user && <span className="muted"> · {m.user}</span>}
                                  <span className={`small ${m.before_count ? "muted" : "warn-text"}`}> · {m.before_count ? "before it was counted: already in the count" : "after it was counted: kept on top of the count"}</span>
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </section>
  );
}
