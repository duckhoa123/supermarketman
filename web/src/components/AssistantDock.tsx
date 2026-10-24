import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import Avatar from "./Avatar";
import ChatPanel, { type ChatConfig } from "./ChatPanel";

type AssistantApi = { open: (question?: string) => void };
const AssistantContext = createContext<AssistantApi>({ open: () => undefined });

/** Lets any screen open the chat, optionally asking a question straight away. */
export function useAssistant(): AssistantApi {
  return useContext(AssistantContext);
}

/** The agent's button in the bottom-right corner and the chat window it opens. The chat stays mounted while
 * closed, so a conversation carries on as the person moves between screens. */
export default function AssistantDock({ config, children }: { config: ChatConfig; children: ReactNode }) {
  const [open, setOpen] = useState(false);
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
  return (
    <AssistantContext.Provider value={{ open: api }}>
      {children}
      <div className={`chat-dialog ${open ? "open" : ""}`} role="dialog" aria-modal="false" aria-label={`Chat with ${name}`}
        aria-hidden={!open}>
        <ChatPanel {...config} open={open} onClose={() => setOpen(false)} prompt={prompt} />
      </div>
      {!open && (
        <button className="chat-launcher" onClick={() => setOpen(true)} aria-label={`Chat with ${name}`}
          title={`Chat with ${name}`}>
          {config.profile ? <Avatar profile={config.profile} size={56} /> : "💬"}
          {notices > 0 && <span className="badge">{notices}</span>}
        </button>
      )}
    </AssistantContext.Provider>
  );
}
