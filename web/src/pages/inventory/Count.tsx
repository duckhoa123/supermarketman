import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import BarcodeScan, { canScan } from "../../components/BarcodeScan";
import { Dialog } from "../../components/PosDialogs";
import { useApp } from "../../context";
import { useApi } from "../../hooks";
import { api } from "../../lib/api";
import {
  draftKey, loadDraft, money, newClientId, qty, saveDraft, sendOrQueue, type Count, type OnHand, type StockItem,
} from "../../lib/inventory";

const EVERYTHING = "__all__";
const NO_AREA = "__none__";

/** A count in progress on this phone. The system amounts are frozen when it starts; it survives a reload or a
 * walk-in with no signal, and is sent when it's finished. */
type Draft = { client_id: string; area: string; started_at: string; expected: Record<string, number | null>;
  counts: Record<string, string> };

export default function CountPanel() {
  const { location } = useApp();
  const base = `/locations/${location.id}`;
  const onHand = useApi<OnHand>(`${base}/inventory/on-hand`);
  const history = useApi<{ counts: Count[]; can_approve: boolean }>(`${base}/stock-counts?limit=20`);
  const key = draftKey(location.id, "count");
  const [draft, setDraftState] = useState<Draft | null>(() => loadDraft<Draft>(key));
  const [result, setResult] = useState<Count | "queued" | null>(null);
  const [reviewing, setReviewing] = useState<Count | null>(null);
  const setDraft = useCallback((d: Draft | null) => { setDraftState(d); saveDraft(key, d); }, [key]);

  const data = onHand.data;
  const countable = useMemo(() => (data?.items ?? []).filter((i) => !(i.is_prep && i.stock_qty == null)), [data]);
  const inArea = (area: string) => countable.filter((i) =>
    area === EVERYTHING || (area === NO_AREA ? !i.storage_area_id : i.storage_area_id === area));

  function start(area: string) {
    const items = inArea(area);
    setResult(null);
    setDraft({ client_id: newClientId("count"), area, started_at: new Date().toISOString(),
      expected: Object.fromEntries(items.map((i) => [i.id, i.stock_qty])), counts: {} });
  }

  if (onHand.error) return <p className="error">{onHand.error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const areaName = (a: string) => a === EVERYTHING ? "Everything" : a === NO_AREA ? "No area" : data.areas.find((x) => x.id === a)?.name ?? "Area";
  const waiting = (history.data?.counts ?? []).filter((c) => c.status === "submitted");

  if (draft) {
    return (
      <CountSheet base={base} data={data} draft={draft} items={countable.filter((i) => i.id in draft.expected)}
        title={areaName(draft.area)} canApprove={data.can_manage} onChange={setDraft}
        onCancel={() => { if (window.confirm("Throw this count away? Nothing you've entered is kept.")) setDraft(null); }}
        onDone={(r) => { setDraft(null); setResult(r); void history.reload(); void onHand.reload(); }} />
    );
  }

  return (
    <div className="stack">
      {result === "queued" && (
        <p className="notice small">No connection: the count is saved on this phone and goes out as soon as you're back online.</p>
      )}
      {result && result !== "queued" && <CountResult count={result} manage={data.can_manage} />}

      {waiting.length > 0 && history.data?.can_approve && (
        <div className="panel stack" style={{ gap: 8 }}>
          <h2 style={{ margin: 0 }}>Waiting for your approval</h2>
          {waiting.map((c) => (
            <div key={c.id} className="spread inv-count-row">
              <span><strong>{c.area}</strong> <span className="small muted">by {c.counted_by ?? "someone"}, {new Date(c.counted_at).toLocaleString()} · {c.changed} of {c.items} different{c.big ? ` · ${c.big} big gap${c.big === 1 ? "" : "s"}` : ""}</span></span>
              <span className="row"><span className={c.variance_cents && c.variance_cents < 0 ? "error" : ""}>{money(c.variance_cents)}</span>
                <button className="btn small primary" onClick={() => setReviewing(c)}>Review</button></span>
            </div>
          ))}
        </div>
      )}

      <div className="panel stack">
        <div>
          <h2 style={{ margin: 0 }}>Start a count</h2>
          <p className="small muted">Pick an area. Several people can count different areas at the same time, each on their own phone. It works without signal.</p>
        </div>
        <div className="inv-count-areas">
          {data.areas.map((a) => (
            <button key={a.id} className="inv-count-area" onClick={() => start(a.id)} disabled={!inArea(a.id).length}>
              <strong>{a.name}</strong><span className="small muted">{inArea(a.id).length} items</span>
            </button>
          ))}
          {data.areas.length > 0 && inArea(NO_AREA).length > 0 && (
            <button className="inv-count-area" onClick={() => start(NO_AREA)}><strong>No area</strong><span className="small muted">{inArea(NO_AREA).length} items</span></button>
          )}
          <button className="inv-count-area" onClick={() => start(EVERYTHING)} disabled={!countable.length}>
            <strong>Everything</strong><span className="small muted">{countable.length} items · full count</span>
          </button>
        </div>
        {!countable.length && <p className="small muted">Nothing to count yet: add items on the On hand tab.</p>}
      </div>

      <div className="panel">
        <h2>Recent counts</h2>
        {(history.data?.counts ?? []).length === 0 ? <p className="small muted">No counts yet.</p> : (
          <div className="table-wrap">
            <table className="data small">
              <thead><tr><th>When</th><th>Area</th><th>By</th><th className="num">Items</th><th className="num">Different</th>
                {data.can_manage && <th className="num">Difference</th>}<th>Status</th></tr></thead>
              <tbody>
                {history.data!.counts.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => setReviewing(c)}>
                    <td>{new Date(c.counted_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td>
                    <td>{c.area}</td><td>{c.counted_by ?? "–"}</td><td className="num">{c.items}</td><td className="num">{c.changed}</td>
                    {data.can_manage && <td className={`num ${c.variance_cents && c.variance_cents < 0 ? "error" : ""}`}>{money(c.variance_cents)}</td>}
                    <td><span className={`pill ${c.status === "approved" ? "good" : c.status === "submitted" ? "warn" : ""}`}>{c.status === "submitted" ? "waiting" : c.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {reviewing && (
        <ReviewDialog base={base} count={reviewing} canApprove={!!history.data?.can_approve} manage={data.can_manage}
          onClose={() => setReviewing(null)} onDone={() => { setReviewing(null); void history.reload(); void onHand.reload(); }} />
      )}
    </div>
  );
}

function CountSheet({ base, data, draft, items: initial, title, canApprove, onChange, onCancel, onDone }: {
  base: string; data: OnHand; draft: Draft; items: StockItem[]; title: string; canApprove: boolean;
  onChange: (d: Draft) => void; onCancel: () => void; onDone: (r: Count | "queued") => void;
}) {
  const [items, setItems] = useState(initial);
  const [search, setSearch] = useState("");
  const [arranging, setArranging] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [approve, setApprove] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragId, setDragId] = useState<string | null>(null);
  const inputs = useRef<Record<string, HTMLInputElement | null>>({});
  const counted = items.filter((i) => (draft.counts[i.id] ?? "").trim() !== "").length;

  const find = useCallback((text: string) => {
    const q = text.trim().toLowerCase();
    if (!q) return;
    const hit = items.find((i) => i.barcode === text.trim()) ?? items.find((i) => i.name.toLowerCase().includes(q));
    if (hit) {
      inputs.current[hit.id]?.focus();
      inputs.current[hit.id]?.scrollIntoView({ block: "center", behavior: "smooth" });
    } else setError(`Nothing in this count matches “${text}”.`);
  }, [items]);

  useEffect(() => setError(""), [search]);

  function setCount(id: string, value: string) {
    onChange({ ...draft, counts: { ...draft.counts, [id]: value } });
  }

  function move(from: number, to: number) {
    if (to < 0 || to >= items.length) return;
    const next = [...items];
    const [x] = next.splice(from, 1);
    next.splice(to, 0, x);
    setItems(next);
  }

  async function saveOrder() {
    await api(`${base}/storage-areas/order`, { method: "POST", body: { item_ids: items.map((i) => i.id) } });
    setArranging(false);
  }

  async function submit() {
    const lines = items.filter((i) => (draft.counts[i.id] ?? "").trim() !== "").map((i) => ({
      item_id: i.id, qty: Number(draft.counts[i.id]), expected: draft.expected[i.id] ?? null }));
    if (!lines.length) return setError("Enter at least one amount.");
    if (lines.some((l) => Number.isNaN(l.qty) || l.qty < 0)) return setError("Amounts must be numbers, like 3.5.");
    const missing = items.length - lines.length;
    if (missing && !window.confirm(`${missing} item${missing === 1 ? " isn't" : "s aren't"} counted. Send the count without ${missing === 1 ? "it" : "them"}? ${missing === 1 ? "It keeps its" : "They keep their"} current amount.`)) return;
    setBusy(true);
    setError("");
    try {
      const area = draft.area === EVERYTHING || draft.area === "__none__" ? null : draft.area;
      const r = await sendOrQueue<Count>(`${base}/stock-counts`, {
        client_id: draft.client_id, storage_area_id: area, lines, started_at: draft.started_at,
        counted_at: new Date().toISOString(), approve: canApprove && approve }, `Count of ${title}`);
      onDone(r ?? "queued");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send the count.");
      setBusy(false);
    }
  }

  const shown = arranging ? items : items.filter((i) => !search.trim() || i.name.toLowerCase().includes(search.trim().toLowerCase()) || i.barcode === search.trim());
  return (
    <div className="stack inv-sheet">
      <div className="spread">
        <div>
          <h2 style={{ margin: 0 }}>Counting: {title}</h2>
          <p className="small muted">Started {new Date(draft.started_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}. Count what's on the shelf; part units are fine (0.4 of a bottle, 3.5 lb). Saved on this phone as you go.</p>
        </div>
        <div className="row">
          {canApprove && !arranging && <button className="btn small" onClick={() => setArranging(true)}>Change order</button>}
          <button className="btn small danger" onClick={onCancel}>Cancel count</button>
        </div>
      </div>
      {arranging ? (
        <div className="notice small">
          <span>Drag items (or use the arrows) into the order they sit on the shelves. Every count sheet uses this order.</span>
          <div className="row"><button className="btn small primary" onClick={() => void saveOrder()}>Save order</button>
            <button className="btn small" onClick={() => { setItems(initial); setArranging(false); }}>Cancel</button></div>
        </div>
      ) : (
        <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); find(search); }}>
          <input className="input" placeholder="Find an item, or scan its barcode" value={search} onChange={(e) => setSearch(e.target.value)} />
          {canScan() && <button type="button" className="btn" onClick={() => setScanning(true)} aria-label="Scan a barcode">Scan</button>}
        </form>
      )}
      {error && <p className="small error">{error}</p>}
      <ol className={`inv-sheet-list ${arranging ? "arranging" : ""}`}>
        {shown.map((i) => {
          const n = items.indexOf(i);
          const value = draft.counts[i.id] ?? "";
          return (
            <li key={i.id} draggable={arranging} className={dragId === i.id ? "dragging" : ""}
              onDragStart={() => setDragId(i.id)} onDragEnd={() => setDragId(null)}
              onDragOver={(e) => { if (arranging && dragId && dragId !== i.id) { e.preventDefault(); move(items.findIndex((x) => x.id === dragId), n); } }}>
              {arranging && <span className="inv-grip" aria-hidden>⠿</span>}
              <div className="inv-sheet-name">
                <strong>{i.name}</strong>
                <span className="small muted">{[i.unit ?? "each", data.areas.find((a) => a.id === i.storage_area_id)?.name].filter(Boolean).join(" · ")}</span>
              </div>
              {arranging ? (
                <span className="row" style={{ gap: 4 }}>
                  <button className="sb-icon" aria-label={`Move ${i.name} up`} disabled={n === 0} onClick={() => move(n, n - 1)}>↑</button>
                  <button className="sb-icon" aria-label={`Move ${i.name} down`} disabled={n === items.length - 1} onClick={() => move(n, n + 1)}>↓</button>
                </span>
              ) : (
                <label className="inv-sheet-qty">
                  <input ref={(el) => { inputs.current[i.id] = el; }} className={`input ${value.trim() ? "done" : ""}`} inputMode="decimal"
                    aria-label={`${i.name}, ${i.unit ?? "each"}`} placeholder="–" value={value}
                    onChange={(e) => setCount(i.id, e.target.value.replace(",", "."))} />
                  <span className="small muted">{i.unit ?? "each"}</span>
                </label>
              )}
            </li>
          );
        })}
      </ol>
      {!arranging && (
        <div className="inv-sheet-foot">
          <span className="small"><strong>{counted}</strong> of {items.length} counted</span>
          <div className="row">
            {canApprove && (
              <label className="check small"><input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} />Approve now</label>
            )}
            <button className="btn primary" disabled={busy || !counted} onClick={() => void submit()}>
              {busy ? "Sending…" : canApprove && approve ? "Finish and approve" : "Send for approval"}
            </button>
          </div>
        </div>
      )}
      {scanning && <BarcodeScan onClose={() => setScanning(false)} onCode={(code) => { setScanning(false); setSearch(code); find(code); }} />}
    </div>
  );
}

function CountLines({ count, manage }: { count: Count; manage: boolean }) {
  const lines = [...count.lines].sort((a, b) => Number(!!b.big) - Number(!!a.big) || Math.abs(b.value_cents ?? 0) - Math.abs(a.value_cents ?? 0));
  return (
    <div className="table-wrap">
      <table className="data small">
        <thead><tr><th>Item</th><th className="num">System</th><th className="num">Counted</th><th className="num">Difference</th>{manage && <th className="num">$</th>}</tr></thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.item_id} className={l.big ? "inv-big" : ""}>
              <td>{l.name}{l.big && <span className="pill low" style={{ marginLeft: 6 }}>check</span>}</td>
              <td className="num">{qty(l.expected)} <span className="muted">{l.unit ?? ""}</span></td>
              <td className="num">{qty(l.counted)}</td>
              <td className={`num ${l.delta < 0 ? "error" : ""}`}>{l.delta > 0 ? "+" : ""}{qty(l.delta)}</td>
              {manage && <td className={`num ${(l.value_cents ?? 0) < 0 ? "error" : ""}`}>{money(l.value_cents)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CountResult({ count, manage }: { count: Count; manage: boolean }) {
  return (
    <div className="panel stack" style={{ gap: 8 }}>
      <div className="spread">
        <h2 style={{ margin: 0 }}>{count.area}: {count.status === "approved" ? "count approved" : "sent for approval"}</h2>
        {manage && <strong className={count.variance_cents && count.variance_cents < 0 ? "error" : ""}>{money(count.variance_cents)}</strong>}
      </div>
      <p className="small muted">
        {count.changed} of {count.items} items differ from the system{count.big ? `; ${count.big} big gap${count.big === 1 ? "" : "s"} worth a second look` : ""}.
        {count.status === "approved" ? " Stock is updated." : " A manager approves it before stock changes."}
      </p>
      <CountLines count={count} manage={manage} />
    </div>
  );
}

function ReviewDialog({ base, count, canApprove, manage, onClose, onDone }: {
  base: string; count: Count; canApprove: boolean; manage: boolean; onClose: () => void; onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function act(what: "approve" | "reject") {
    if (what === "reject" && !window.confirm("Throw this count away? Stock won't change.")) return;
    setBusy(true);
    try {
      await api(`${base}/stock-counts/${count.id}/${what}`, { method: "POST" });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't do that.");
      setBusy(false);
    }
  }
  const open = count.status === "submitted";
  return (
    <Dialog title={`Count of ${count.area}`} onClose={onClose} width="min(720px, 100%)"
      footer={open && canApprove ? <div className="row" style={{ width: "100%" }}>
        <button className="btn danger" disabled={busy} onClick={() => void act("reject")}>Reject</button>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={onClose}>Later</button>
        <button className="btn primary" disabled={busy} onClick={() => void act("approve")}>Approve: update stock</button>
      </div> : undefined}>
      <p className="small muted">
        Counted by {count.counted_by ?? "someone"} on {new Date(count.counted_at).toLocaleString()}
        {count.approved_by ? `, approved by ${count.approved_by}` : ""}. {manage && <>Difference: <strong>{money(count.variance_cents)}</strong>.</>}
        {open && " Approving moves each item by counted − system, so sales during the count still count."}
      </p>
      <CountLines count={count} manage={manage} />
      {error && <p className="small error">{error}</p>}
    </Dialog>
  );
}
