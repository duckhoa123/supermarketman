import { useEffect, useRef, useState } from "react";
import Docket from "../components/Docket";
import { useApp } from "../context";
import { streamChat, type ChatEvent, type Proposal } from "../lib/api";

type Turn =
  | { role: "user"; text: string }
  | { role: "ai"; text: string; tools: string[]; proposals: Proposal[]; error?: string; pending: boolean };

const TOOL_LABELS: Record<string, string> = {
  get_sales_summary: "Checked sales",
  get_sales_breakdown: "Broke down sales",
  get_top_items: "Ranked menu items",
  get_inventory: "Checked stock",
  get_orders: "Looked at orders",
  get_tables: "Checked tables",
  get_reviews: "Read reviews",
  propose_review_reply: "Drafted a reply",
  propose_low_stock_threshold: "Prepared a stock alert",
  propose_alert_rule: "Prepared an alert",
};

const STARTERS = [
  "How did we go yesterday compared with last week?",
  "What's running low?",
  "What are our five best sellers this month?",
  "Any bad reviews this week I haven't replied to?",
  "Which hours are busiest on weekends?",
  "Why were sales down three days ago?",
];

export default function Assistant() {
  const { location } = useApp();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [deep, setDeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | undefined>(undefined);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  function updateLast(fn: (t: Extract<Turn, { role: "ai" }>) => Extract<Turn, { role: "ai" }>) {
    setTurns((prev) => {
      const copy = prev.slice();
      const last = copy[copy.length - 1];
      if (last && last.role === "ai") copy[copy.length - 1] = fn(last);
      return copy;
    });
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setTurns((prev) => [
      ...prev,
      { role: "user", text: message },
      { role: "ai", text: "", tools: [], proposals: [], pending: true },
    ]);
    try {
      await streamChat(
        location.id,
        { message, conversation_id: conversationId.current, deep },
        (e: ChatEvent) => {
          if (e.type === "meta") conversationId.current = e.conversation_id;
          else if (e.type === "text") updateLast((t) => ({ ...t, text: t.text + e.delta }));
          else if (e.type === "tool")
            updateLast((t) => ({ ...t, tools: [...t.tools, TOOL_LABELS[e.name] ?? e.name] }));
          else if (e.type === "action") updateLast((t) => ({ ...t, proposals: [...t.proposals, e.proposal] }));
          else if (e.type === "error") updateLast((t) => ({ ...t, error: e.message }));
          else if (e.type === "done") updateLast((t) => ({ ...t, pending: false }));
        }
      );
    } catch (err) {
      updateLast((t) => ({ ...t, pending: false, error: err instanceof Error ? err.message : "Something went wrong." }));
    } finally {
      updateLast((t) => ({ ...t, pending: false }));
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="page-head">
        <div>
          <h1>Ask about {location.name}</h1>
          <p>Answers come from your POS and review data, synced every few minutes.</p>
        </div>
        {turns.length > 0 && (
          <button
            className="btn small"
            onClick={() => {
              setTurns([]);
              conversationId.current = undefined;
            }}
          >
            New conversation
          </button>
        )}
      </div>

      <div className="thread">
        {turns.length === 0 && (
          <div className="starters">
            {STARTERS.map((s) => (
              <button key={s} onClick={() => void send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) =>
          t.role === "user" ? (
            <div key={i} className="msg-user">
              {t.text}
            </div>
          ) : (
            <div key={i} className="msg-ai">
              {t.tools.length > 0 && (
                <div className="tools">
                  {t.tools.map((tool, j) => (
                    <span key={j} className="tool-chip">
                      {tool}
                    </span>
                  ))}
                </div>
              )}
              {t.text || (t.pending && !t.error ? <span className="muted">Looking into it…</span> : null)}
              {t.error && <p className="error">{t.error}</p>}
              {t.proposals.map((p, j) => (
                <Docket key={j} proposal={p} locationId={location.id} />
              ))}
            </div>
          )
        )}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <textarea
          className="textarea"
          rows={1}
          placeholder="Ask anything about sales, stock, orders or reviews"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
        />
        <button className="btn primary" disabled={busy || !input.trim()}>
          Ask
        </button>
        <div className="composer-opts">
          <label className="check">
            <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} />
            Deep analysis (slower, for why and what-if questions)
          </label>
        </div>
      </form>
    </div>
  );
}
