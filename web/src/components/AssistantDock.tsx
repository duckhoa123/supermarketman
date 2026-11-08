import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import Avatar, { PICTURE_AVATARS } from "./Avatar";
import ChatPanel, { type ChatConfig } from "./ChatPanel";

/** What the agent says when it pops up as someone opens the app. */
export type Popup = {
  greeting: string;
  text: string;
  lines?: string[];
  more?: number;
  /** Nothing needs attention: say hello briefly, then step back. */
  quiet?: boolean;
  actions?: { label: string; to?: string; ask?: string; primary?: boolean }[];
};

type AssistantApi = { open: (question?: string) => void };
const AssistantContext = createContext<AssistantApi>({ open: () => undefined });

/** Lets any screen open the chat, optionally asking a question straight away. */
export function useAssistant(): AssistantApi {
  return useContext(AssistantContext);
}

/** The agent's button in the bottom-right corner and the chat window it opens. The chat stays mounted while
 * closed, so a conversation carries on as the person moves between screens. */
export default function AssistantDock({ config, popup, hidden = false, children }: {
  config: ChatConfig;
  popup?: Popup | null;
  /** On the agent's home the chat is the page itself, so the corner button and pop-up step aside. */
  hidden?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [greeting, setGreeting] = useState(false);
  const [prompt, setPrompt] = useState<{ text: string; nonce: number } | null>(null);
  const notices = config.notices?.length ?? 0;

  const api = useCallback<AssistantApi["open"]>((question) => {
    setOpen(true);
    if (question) setPrompt({ text: question, nonce: Date.now() });
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const name = config.profile?.display_name ?? "assistant";
  if (hidden) return <AssistantContext.Provider value={{ open: api }}>{children}</AssistantContext.Provider>;
  return (
    <AssistantContext.Provider value={{ open: api }}>
      {children}
      <div className={`chat-dialog ${open ? "open" : ""}`} role="dialog" aria-modal="false" aria-label={`Chat with ${name}`}
        aria-hidden={!open}>
        <ChatPanel {...config} open={open} onClose={() => setOpen(false)} prompt={prompt} />
      </div>
      {!open && !greeting && (
        <button className="chat-launcher" onClick={() => setOpen(true)} aria-label={`Chat with ${name}`}
          title={`Chat with ${name}`}>
          {config.profile ? <Avatar profile={config.profile} size={56} /> : "💬"}
          {notices > 0 && <span className="badge">{notices}</span>}
        </button>
      )}
      {config.profile && popup && !open && (
        <Greeter popup={popup} profile={config.profile} storageKey={`pass.greeted:${config.path}`}
          onVisible={setGreeting} onAsk={api} />
      )}
    </AssistantContext.Provider>
  );
}

type Phase = "off" | "in" | "typing" | "talk" | "out";

/** The agent pops up from the corner once per visit and says what needs attention. */
function Greeter({ popup, profile, storageKey, onVisible, onAsk }: {
  popup: Popup;
  profile: NonNullable<ChatConfig["profile"]>;
  storageKey: string;
  onVisible: (v: boolean) => void;
  onAsk: (question?: string) => void;
}) {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>("off");
  const figure = PICTURE_AVATARS[profile.avatar.icon]?.full;
  useEffect(() => () => onVisible(false), [onVisible]); // the corner button comes back if this goes away

  useEffect(() => {
    try {
      if (window.sessionStorage.getItem(storageKey)) return;
    } catch {
      /* private mode: greet anyway */
    }
    const timers = [
      window.setTimeout(() => { setPhase("in"); onVisible(true); }, 600),
      window.setTimeout(() => setPhase("typing"), 1300),
      window.setTimeout(() => {
        setPhase("talk");
        try {
          window.sessionStorage.setItem(storageKey, "1");
        } catch {
          /* ignore */
        }
      }, 2200),
    ];
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [storageKey, onVisible]);

  // A quiet hello steps back by itself; alerts stay until the person acts on them.
  useEffect(() => {
    if (phase !== "talk" || !popup.quiet) return;
    const t = window.setTimeout(() => close(), 6500);
    return () => window.clearTimeout(t);
  });

  function close() {
    setPhase("out");
    window.setTimeout(() => { setPhase("off"); onVisible(false); }, 450);
  }

  function act(a: NonNullable<Popup["actions"]>[number]) {
    close();
    if (a.to) navigate(a.to);
    if (a.ask !== undefined) window.setTimeout(() => onAsk(a.ask || undefined), 460);
  }

  if (phase === "off") return null;
  return (
    <div className={`greeter greeter-${phase} ${figure ? "has-figure" : ""}`}>
      {(phase === "typing" || phase === "talk" || phase === "out") && (
        <div className="greeter-bubble" role="status" aria-live="polite">
          {phase === "typing" ? (
            <span className="typing" aria-label={`${profile.display_name} is typing`}><i /><i /><i /></span>
          ) : (
            <>
              <button className="greeter-close" aria-label="Close" onClick={close}>✕</button>
              <p className="greeter-hi">{popup.greeting}</p>
              <p className="greeter-text">{popup.text}</p>
              {popup.lines && popup.lines.length > 0 && (
                <ul className="greeter-lines">
                  {popup.lines.map((l) => <li key={l}>{l}</li>)}
                  {popup.more ? <li className="muted">and {popup.more} more</li> : null}
                </ul>
              )}
              {popup.actions && popup.actions.length > 0 && (
                <div className="greeter-actions">
                  {popup.actions.map((a) => (
                    <button key={a.label} className={`btn small ${a.primary ? "primary" : ""}`} onClick={() => act(a)}>{a.label}</button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
      <button className="greeter-figure" onClick={() => act({ label: "chat", ask: "" })} aria-label={`Chat with ${profile.display_name}`}>
        {figure ? <img src={figure} alt="" draggable={false} /> : <Avatar profile={profile} size={84} />}
      </button>
    </div>
  );
}
