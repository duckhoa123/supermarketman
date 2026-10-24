import { useEffect, useRef, useState } from "react";
import Docket from "./Docket";
import Avatar from "./Avatar";
import { streamFrom, type AgentProfile, type ChatEvent, type Proposal } from "../lib/api";

export type Notice = { id: string; title: string; body: string; ask: string };

export type ChatConfig = {
  path: string;
  profile?: AgentProfile;
  greeting: string;
  starters: string[];
  toolLabels: Record<string, string>;
  placeholder: string;
  locationId?: string;
  allowDeep?: boolean;
  shareLocation?: boolean;
  /** Things the agent noticed on its own (e.g. a supplier price rise), shown at the top of the chat. */
  notices?: Notice[];
  onDismissNotice?: (id: string) => void;
};

type Turn =
  | { role: "user"; text: string }
  | { role: "ai"; text: string; tools: string[]; proposals: Proposal[]; error?: string; pending: boolean };

/** A conversation with one of the three agents, shown inside the chat window. */
export default function ChatPanel({
  path,
  profile,
  greeting,
  starters,
  toolLabels,
  placeholder,
  locationId,
  allowDeep = false,
  shareLocation = false,
  notices = [],
  onDismissNotice,
  open,
  onClose,
  prompt,
}: ChatConfig & {
  open: boolean;
  onClose: () => void;
  /** A question another screen asked to send (changes `nonce` each time). */
  prompt?: { text: string; nonce: number } | null;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [deep, setDeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [position, setPosition] = useState<{ lat: number; lng: number } | null>(null);
  const conversationId = useRef<string | undefined>(undefined);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => {
    // A new agent or restaurant starts a new conversation.
    setTurns([]);
    conversationId.current = undefined;
  }, [path]);

  useEffect(() => {
    if (open) window.setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  useEffect(() => {
    if (prompt?.text) void send(prompt.text);
  }, [prompt?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

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
      <header className="chat-head">
        {profile && <Avatar profile={profile} size={36} />}
        <div className="chat-who">
          <strong>{profile?.display_name ?? "Assistant"}</strong>
          <span className="small muted">{profile?.label}</span>
        </div>
        {turns.length > 0 && (
          <button
            className="btn small"
            onClick={() => {
              setTurns([]);
              conversationId.current = undefined;
            }}
          >
            New chat
          </button>
        )}
        <button className="icon-btn" onClick={onClose} aria-label="Close chat">
          ✕
        </button>
      </header>

      <div className="thread">
        {notices.map((n) => (
          <section key={n.id} className="notice">
            <strong>{n.title}</strong>
            <p className="small body">{n.body}</p>
            <div className="row">
              <button className="btn primary small" disabled={busy} onClick={() => { onDismissNotice?.(n.id); void send(n.ask); }}>
                Ask {profile?.display_name ?? "about this"}
              </button>
              <button className="btn small" onClick={() => onDismissNotice?.(n.id)}>Got it</button>
            </div>
          </section>
        ))}
        {turns.length === 0 && (
          <>
            <p className="msg-ai">{greeting}</p>
            <div className="starters">
              {starters.map((s) => (
                <button key={s} onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </>
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
          ref={inputRef}
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
        {(allowDeep || shareLocation) && (
          <div className="composer-opts">
            {allowDeep && (
              <label className="check">
                <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} />
                Deep analysis (slower, for why and what-if)
              </label>
            )}
            {shareLocation && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={position !== null}
                  onChange={(e) => (e.target.checked ? askForPosition() : setPosition(null))}
                />
                Use my current location
              </label>
            )}
          </div>
        )}
      </form>
    </div>
  );
}
