import type { AgentKind, Me } from "./api";

const KEY = "pass.agent";

export function storedMode(): AgentKind | null {
  try {
    return (window.localStorage.getItem(KEY) as AgentKind | null) ?? null;
  } catch {
    return null;
  }
}

export function storeMode(mode: AgentKind | null): void {
  try {
    if (mode) window.localStorage.setItem(KEY, mode);
    else window.localStorage.removeItem(KEY);
  } catch {
    /* private mode: the choice just isn't remembered */
  }
}

/** The agent to open: the remembered one if this account still has it, else a sensible default. */
export function initialMode(me: Me): AgentKind | null {
  const available = new Set(me.agents.map((a) => a.agent));
  const stored = storedMode();
  if (stored && available.has(stored)) return stored;
  if (me.locations.length) return "restaurant_manager";
  if (me.is_driver) return "driver";
  return null; // let them choose
}
