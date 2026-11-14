import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api } from "../../lib/api";
import { KIND_LABEL, money, qty, unitCost, type AuditEntry, type Movement, type OnHand } from "../../lib/inventory";

/** Every change to stock, newest first: who, when, how much, at what cost, and why. Nothing here is ever edited;
 * a correction is a new line. Recipe, par and unit changes are on the second view. */
export default function Ledger() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const [params, setParams] = useSearchParams();
  const itemId = params.get("item") ?? "";
  const [kind, setKind] = useState("");
  const [view, setView] = useState<"stock" | "changes">("stock");
  const [rows, setRows] = useState<Movement[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");
  const onHand = useApi<OnHand>(`${base}/inventory/on-hand`);
  const audit = useApi<{ changes: AuditEntry[] }>(view === "changes" ? `${base}/inventory/audit${itemId ? `?item_id=${itemId}` : ""}` : null);

  async function load(before?: string) {
    try {
      const q = new URLSearchParams({ limit: "100" });
      if (itemId) q.set("item_id", itemId);
      if (kind) q.set("kind", kind);
      if (before) q.set("before", before);
      const r = await api<{ movements: Movement[]; more: boolean }>(`${base}/stock-movements?${q}`);
      setRows((old) => (before ? [...old, ...r.movements] : r.movements));
      setMore(r.more);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load the ledger.");
    }
  }
  useEffect(() => { if (view === "stock") void load(); }, [itemId, kind, view]); // eslint-disable-line react-hooks/exhaustive-deps

  const item = onHand.data?.items.find((i) => i.id === itemId);
  return (
    <div className="stack">
      <div className="spread inv-toolbar">
        <div className="seg">
          <button className={view === "stock" ? "on" : ""} onClick={() => setView("stock")}>Stock changes</button>
          <button className={view === "changes" ? "on" : ""} onClick={() => setView("changes")}>Recipe & par changes</button>
        </div>
        <div className="row">
          <select className="select" style={{ width: "auto", maxWidth: 220 }} aria-label="Item" value={itemId}
            onChange={(e) => setParams(e.target.value ? { tab: "ledger", item: e.target.value } : { tab: "ledger" })}>
            <option value="">All items</option>
            {(onHand.data?.items ?? []).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
          {view === "stock" && (
            <select className="select" style={{ width: "auto" }} aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Everything</option>
              {Object.entries(KIND_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          )}
        </div>
      </div>
      {item && (
        <p className="small">
          <strong>{item.name}</strong>: {qty(item.stock_qty)} {item.unit ?? ""} on hand
          {item.avg_cost != null && <> at {unitCost(item.avg_cost)}/{item.unit ?? "each"} = {money(item.value_cents)}</>}.
        </p>
      )}
      {error && <p className="error">{error}</p>}

      {view === "stock" ? (
        <div className="panel table-wrap">
          <table className="data small inv-ledger">
            <thead><tr><th>When</th>{!itemId && <th>Item</th>}<th>What</th><th className="num">Change</th><th className="num">After</th>
              <th className="num">At</th><th className="num">Value</th><th>By</th><th>Note</th></tr></thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.id}>
                  <td data-label="When">{new Date(m.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td>
                  {!itemId && <td data-label="Item">{m.item}</td>}
                  <td data-label="What"><span className={`pill kind-${m.kind}`}>{KIND_LABEL[m.kind] ?? m.kind}</span>{m.reason && <span className="muted"> {m.reason.replace("_", " ")}</span>}</td>
                  <td data-label="Change" className={`num ${m.qty_delta < 0 ? "error" : m.qty_delta > 0 ? "good-text" : ""}`}>{m.qty_delta > 0 ? "+" : ""}{qty(m.qty_delta)} <span className="muted">{m.unit ?? ""}</span></td>
                  <td data-label="After" className={`num ${m.qty_after < 0 ? "error" : ""}`}>{qty(m.qty_after)}</td>
                  <td data-label="At" className="num">{unitCost(m.unit_cost)}</td>
                  <td data-label="Value" className="num">{money(m.value_cents)}</td>
                  <td data-label="By">{m.user ?? <span className="muted">{m.kind === "sale" ? "sales" : m.kind === "pos" ? "POS" : "Pass"}</span>}</td>
                  <td data-label="Note" className="muted">{m.note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p className="empty">No stock changes yet.</p>}
          {more && <button className="btn small" style={{ margin: 12 }} onClick={() => void load(rows[rows.length - 1]?.at)}>Load older</button>}
        </div>
      ) : (
        <div className="panel table-wrap">
          <table className="data small">
            <thead><tr><th>When</th><th>Item</th><th>Changed</th><th>From</th><th>To</th><th>By</th></tr></thead>
            <tbody>
              {(audit.data?.changes ?? []).map((a) => (
                <tr key={a.id}>
                  <td>{new Date(a.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td>
                  <td>{a.item}</td><td>{a.field}</td><td className="muted">{a.old ?? "–"}</td><td>{a.new ?? "–"}</td><td>{a.user ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(audit.data?.changes ?? []).length === 0 && <p className="empty">No recipe, par or unit changes yet.</p>}
        </div>
      )}
    </div>
  );
}
