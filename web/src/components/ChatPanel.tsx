import { useEffect, useRef, useState } from "react";
import Docket from "./Docket";
import Avatar from "./Avatar";
import { streamFrom, type AgentProfile, type ChatEvent, type Proposal } from "../lib/api";

type Turn =
  | { role: "user"; text: string }
  | { role: "ai"; text: string; tools: string[]; proposals: Proposal[]; error?: string; pending: boolean };

/** A conversation with one of the three agents. */
export default function ChatPanel({
  path,
  profile,
  title,
  subtitle,
  starters,
  toolLabels,
  placeholder,
  locationId,
  allowDeep = false,
  shareLocation = false,
}: {
  path: string;
  profile?: AgentProfile;
  title: string;
  subtitle: string;
  starters: string[];
  toolLabels: Record<string, string>;
  placeholder: string;
  locationId?: string;
  allowDeep?: boolean;
  shareLocation?: boolean;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [deep, setDeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [position, setPosition] = useState<{ lat: number; lng: number } | null>(null);
  const conversationId = useRef<string | undefined>(undefined);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => {
    // A new agent or restaurant starts a new conversation.
    setTurns([]);
    conversationId.current = undefined;
  }, [path]);

  function updateLast(fn: (t: Extract<Turn, { role: "ai" }>) => Extract<Turn, { role: "ai" }>) {
    setTurns((prev) => {
      const copy = prev.slice();
      const last = copy[copy.length - 1];
      if (last && last.role === "ai") copy[copy.length - 1] = fn(last);
      return copy;
    });
  }

  function askForPosition() {
    navigator.geolocation?.getCurrentPosition(
      (p) => setPosition({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => setPosition(null),
      { maximumAge: 300_000, timeout: 8000 }
    );
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
      await streamFrom(
        path,
        { message, conversation_id: conversationId.current, deep, ...(position ?? {}) },
        (e: ChatEvent) => {
          if (e.type === "meta") conversationId.current = e.conversation_id;
          else if (e.type === "text") updateLast((t) => ({ ...t, text: t.text + e.delta }));
          else if (e.type === "tool")
            updateLast((t) => ({ ...t, tools: [...t.tools, toolLabels[e.name] ?? e.name] }));
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
        <div className="row" style={{ alignItems: "center" }}>
          {profile && <Avatar profile={profile} size={44} />}
          <div>
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
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
            {starters.map((s) => (
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
                <Docket key={j} proposal={p} locationId={locationId} />
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
          placeholder={placeholder}
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
          Send
        </button>
        <div className="composer-opts">
          {allowDeep && (
            <label className="check">
              <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} />
              Deep analysis (slower, for why and what-if questions)
            </label>
          )}
          {shareLocation && (
            <label className="check">
              <input
                type="checkbox"
                checked={position !== null}
                onChange={(e) => (e.target.checked ? askForPosition() : setPosition(null))}
              />
              Use my current location (otherwise your saved address)
            </label>
          )}
        </div>
      </form>
    </div>
  );
}
