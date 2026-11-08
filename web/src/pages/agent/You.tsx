import { useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "../../components/Avatar";
import { useAgentHome } from "../../components/WorkProvider";
import { useApi } from "../../hooks";
import { api, type AgentKind } from "../../lib/api";
import { whenLabel, type Access, type Memory } from "../../lib/agentHome";

const ALWAYS_ASKS: Record<AgentKind, string[]> = {
  restaurant_manager: ["Supplier orders", "Emails and texts to suppliers", "Posting review replies",
    "Stock alerts and settings", "Anything charged or refunded"],
  customer: ["Placing an order", "Booking a table", "Tips and anything charged", "Changing your goals"],
  driver: ["Picked up and delivered are yours to tap", "Starting and ending shifts"],
};

const STATUS: Record<string, { label: string; tone: string }> = {
  active: { label: "Connected", tone: "ok" },
  error: { label: "Needs reconnecting", tone: "bad" },
  disconnected: { label: "Not connected", tone: "off" },
  test: { label: "Test mode: nothing is charged", tone: "off" },
  ask: { label: "Asks each time", tone: "off" },
};

/** What the agent remembers (and forgetting it), what it can reach, and the rule that it always asks first. */
export default function You() {
  const { agent, profile, paths } = useAgentHome();
  const name = profile?.display_name ?? "Your agent";
  const memories = useApi<{ memories: Memory[] }>(paths.memory);
  const access = useApi<Access>(paths.access);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [forgotten, setForgotten] = useState<Memory | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api(paths.memory, { method: "POST", body: { text: text.trim() } });
      setText("");
      await memories.reload();
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't save that.");
    } finally {
      setBusy(false);
    }
  }

  async function forget(m: Memory) {
    try {
      await api(`${paths.memory}/${m.id}`, { method: "DELETE" });
      setForgotten(m);
      await memories.reload();
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't forget that.");
    }
  }

  async function undoForget() {
    if (!forgotten) return;
    await api(paths.memory, { method: "POST", body: { text: forgotten.text } }).catch(() => undefined);
    setForgotten(null);
    await memories.reload();
  }

  const spend = access.data?.spend;
  const spendPct = spend ? Math.min(100, Math.round((spend.spent_usd / Math.max(spend.budget_usd, 0.01)) * 100)) : 0;
  const list = memories.data?.memories ?? [];

  return (
    <>
      <div className="you-head">
        {profile && <Avatar profile={profile} size={64} />}
        <div>
          <h1>{name}</h1>
          <p className="muted">{profile?.label}{profile?.tone ? ` · tone: ${profile.tone}` : ""}</p>
        </div>
        <Link to="/agents" className="btn small">Customise</Link>
      </div>

      <section className="always-asks" aria-labelledby="asks-h">
        <h2 id="asks-h">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2" />
            <path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
          {agent === "driver" ? `${name} only reads` : `${name} always asks you first`}
        </h2>
        <ul>{ALWAYS_ASKS[agent].map((x) => <li key={x}>{x}</li>)}</ul>
        <p className="small">This can't be switched off. {agent === "driver" ? "Job status changes only when you tap it." : `${name} prepares; you tap to confirm.`}</p>
      </section>

      <section className="panel" aria-labelledby="mem-h">
        <div className="spread">
          <h2 id="mem-h">What {name} remembers</h2>
          <span className="small muted">{list.length} {list.length === 1 ? "note" : "notes"}</span>
        </div>
        {memories.error && <p className="error small">{memories.error}</p>}
        {list.length === 0 && !memories.loading && (
          <p className="small muted">Nothing yet. Say “remember…” in a chat, or add a note here.</p>
        )}
        <ul className="list memory-list">
          {list.map((m) => (
            <li key={m.id} className="spread">
              <span>
                {m.text}
                <span className="small muted memory-src">
                  {m.source === "app" ? "You added this" : "From a chat"} · {whenLabel(m.created_at)}
                </span>
              </span>
              <button className="btn small danger" onClick={() => void forget(m)}>Forget</button>
            </li>
          ))}
        </ul>
        {forgotten && (
          <p className="small forgot" role="status">
            Forgotten: “{forgotten.text}”. <button className="linkish small" onClick={() => void undoForget()}>Undo</button>
          </p>
        )}
        <form className="row memory-add" onSubmit={add}>
          <label className="sr-only" htmlFor="mem-new">Something for {name} to remember</label>
          <input id="mem-new" className="input" maxLength={200} placeholder="e.g. We close early on Mondays"
            value={text} onChange={(e) => setText(e.target.value)} />
          <button className="btn primary small" disabled={busy || !text.trim()}>Remember</button>
        </form>
        {error && <p className="error small">{error}</p>}
      </section>

      <section className="panel" aria-labelledby="reach-h">
        <h2 id="reach-h">What {name} can reach</h2>
        {access.error && <p className="error small">{access.error}</p>}
        <ul className="list reach-list">
          {access.data?.connections.map((c) => {
            const st = STATUS[c.status] ?? { label: c.status, tone: "off" };
            return (
              <li key={c.provider}>
                <div className="spread">
                  <strong>{c.name}</strong>
                  <span className={`reach-status ${st.tone}`}>{st.label}</span>
                </div>
                <p className="small muted">{c.can}</p>
                {c.last_error && <p className="small error">{c.last_error}</p>}
                {c.last_synced_at && <p className="small muted">Last checked {whenLabel(c.last_synced_at)}</p>}
              </li>
            );
          })}
        </ul>
        {agent === "restaurant_manager" && <Link to="/settings" className="small">Connect or disconnect in Settings</Link>}
      </section>

      {spend && (
        <section className="panel" aria-labelledby="spend-h">
          <div className="spread">
            <h2 id="spend-h">AI spend this month</h2>
            <strong>${spend.spent_usd.toFixed(2)} <span className="muted">of ${spend.budget_usd.toFixed(2)}</span></strong>
          </div>
          <div className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={spendPct}
            aria-label="AI spend"><span style={{ width: `${spendPct}%` }} /></div>
          <p className="small muted">{spend.limited ? `Over the limit: ${name} is using the cheaper model until next month.`
            : `Above the limit, ${name} switches to the cheaper model.`}</p>
        </section>
      )}
    </>
  );
}
