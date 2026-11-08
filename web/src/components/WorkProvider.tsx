import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type AgentKind, type AgentProfile } from "../lib/api";
import { agentPaths, type Work } from "../lib/agentHome";

export type AgentHomeState = {
  agent: AgentKind;
  profile?: AgentProfile;
  locationId?: string;
  paths: ReturnType<typeof agentPaths>;
  work: Work | null;
  error: string;
  reloadWork: () => Promise<void>;
};

const Ctx = createContext<AgentHomeState | null>(null);

/** The agent's home data, shared by Chat, Working on, Activity and the nav badge, refreshed every minute. */
export function WorkProvider({ agent, profile, locationId, children }: {
  agent: AgentKind;
  profile?: AgentProfile;
  locationId?: string;
  children: ReactNode;
}) {
  const paths = agentPaths(agent, locationId);
  const [work, setWork] = useState<Work | null>(null);
  const [error, setError] = useState("");

  const reloadWork = useCallback(async () => {
    try {
      setWork(await api<Work>(paths.work));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load this.");
    }
  }, [paths.work]);

  useEffect(() => {
    setWork(null);
    void reloadWork();
    const t = window.setInterval(() => void reloadWork(), 60_000);
    const onFocus = () => document.visibilityState === "visible" && void reloadWork();
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.clearInterval(t);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [reloadWork]);

  return <Ctx.Provider value={{ agent, profile, locationId, paths, work, error, reloadWork }}>{children}</Ctx.Provider>;
}

export function useAgentHome(): AgentHomeState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAgentHome must be used inside WorkProvider");
  return ctx;
}
