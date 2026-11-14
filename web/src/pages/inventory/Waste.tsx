import { useMemo, useState } from "react";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { money, newClientId, qty, sendOrQueue, unitsFor, type Movement, type OnHand, type StockItem } from "../../lib/inventory";

type WasteList = { days: number; entries: Movement[]; total_cents: number | null;
  by_reason: { reason: string; label: string; entries: number; value_cents: number | null }[] };

/** Log waste in three taps: the item, how much, why. Works with no signal; it's sent when the phone reconnects. */
export default function WastePanel() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const onHand = useApi<OnHand>(`${base}/inventory/on-hand`);
  const list = useApi<WasteList>(`${base}/waste?days=30`);
  const [search, setSearch] = useState("");
  const [item, setItem] = useState<StockItem | null>(null);
  const [amount, setAmount] = useState("");
  const [unit, setUnit] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const items = useMemo(() => (onHand.data?.items ?? []).filter((i) => !(i.is_prep && i.stock_qty == null)), [onHand.data]);
  const recent = useMemo(() => {
    const ids = Array.from(new Set((list.data?.entries ?? []).map((e) => e.item_id).filter(Boolean))) as string[];
    return ids.map((id) => items.find((i) => i.id === id)).filter(Boolean).slice(0, 8) as StockItem[];
  }, [list.data, items]);
  const matches = search.trim() ? items.filter((i) => i.name.toLowerCase().includes(search.trim().toLowerCase()) || i.barcode === search.trim()).slice(0, 12) : [];

  function pick(i: StockItem) {
    setItem(i);
    setUnit(i.unit ?? "each");
    setAmount("");
    setSearch("");
    setMessage("");
    setError("");
  }

  async function log() {
    if (!item) return;
    const n = Number(amount.replace(",", "."));
    if (!amount.trim() || Number.isNaN(n) || n <= 0) return setError("How much was thrown out?");
    if (!reason) return setError("Pick a reason.");
    setBusy(true);
    setError("");
    try {
      const r = await sendOrQueue<Movement>(`${base}/waste`, {
        item_id: item.id, qty: n, unit, reason, note: note.trim() || null, client_id: newClientId("waste"),
        at: new Date().toISOString() }, `Waste: ${item.name}`);
      setMessage(r ? `Logged: ${qty(n)} ${unit} ${item.name}${r.value_cents != null ? ` (${money(-r.value_cents)})` : ""}.`
        : `Saved on this phone: ${qty(n)} ${unit} ${item.name}. It's sent when you're back online.`);
      setItem(null);
      setAmount("");
      setReason("");
      setNote("");
      void list.reload();
      void onHand.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't log it.");
    } finally {
      setBusy(false);
    }
  }

  const reasons = onHand.data?.waste_reasons ?? {};
  const manage = onHand.data?.can_manage;
  return (
    <div className="inv-waste">
      <section className="panel stack">
        <h2 style={{ margin: 0 }}>Log waste</h2>
        {message && <p className="notice small" role="status">{message}</p>}
        {!item ? (
          <>
            <input className="input" placeholder="What was thrown out? Search or scan" value={search} autoFocus onChange={(e) => setSearch(e.target.value)} />
            <div className="inv-pick">
              {(matches.length ? matches : recent).map((i) => (
                <button key={i.id} className="inv-pick-item" onClick={() => pick(i)}>
                  <strong>{i.name}</strong><span className="small muted">{i.unit ?? "each"}</span>
                </button>
              ))}
            </div>
            {!matches.length && !search.trim() && (
              <p className="small muted">{recent.length ? "Recently wasted. Search for anything else." : "Search for an item to start."}</p>
            )}
            {search.trim() && !matches.length && <p className="small muted">Nothing matches.</p>}
          </>
        ) : (
          <>
            <div className="spread">
              <strong>{item.name}</strong>
              <button className="linkish small" onClick={() => setItem(null)}>Change</button>
            </div>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input className="input inv-big-input" inputMode="decimal" placeholder="How much" aria-label="Amount" autoFocus value={amount}
                onChange={(e) => setAmount(e.target.value)} />
              <select className="select" style={{ width: "auto" }} aria-label="Unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
                {unitsFor(item.unit, item.conversions).map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </div>
            <div className="chips" role="group" aria-label="Reason">
              {Object.entries(reasons).map(([k, label]) => (
                <button key={k} type="button" className={reason === k ? "chip on" : "chip"} onClick={() => setReason(k)}>{label}</button>
              ))}
            </div>
            <input className="input" maxLength={500} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
            {error && <p className="small error">{error}</p>}
            <button className="btn primary inv-wide" disabled={busy} onClick={() => void log()}>{busy ? "Logging…" : "Log waste"}</button>
          </>
        )}
      </section>

      <section className="panel stack">
        <div className="spread">
          <h2 style={{ margin: 0 }}>Last 30 days</h2>
          {manage && <strong>{money(list.data?.total_cents)}</strong>}
        </div>
        {(list.data?.by_reason ?? []).length > 0 && (
          <ul className="inv-reasons">
            {list.data!.by_reason.map((r) => (
              <li key={r.reason}><span>{r.label}</span><span className="muted small">{r.entries}×</span>{manage && <strong>{money(r.value_cents)}</strong>}</li>
            ))}
          </ul>
        )}
        {(list.data?.entries ?? []).length === 0 ? <p className="small muted">No waste logged.</p> : (
          <ul className="list inv-waste-list">
            {list.data!.entries.slice(0, 40).map((e) => (
              <li key={e.id} className="spread">
                <span><strong>{qty(-e.qty_delta)} {e.unit ?? ""}</strong> {e.item}
                  <span className="small muted"> · {reasons[e.reason ?? ""] ?? e.reason}{e.user ? ` · ${e.user}` : ""} · {new Date(e.at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>
                  {e.note && <span className="small muted"> · “{e.note}”</span>}
                </span>
                {manage && <span className="small">{money(-(e.value_cents ?? 0))}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
