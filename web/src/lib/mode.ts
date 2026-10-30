import type { AgentKind, Me } from "./api";

const KEY = "pass.agent";
const PENDING = "pass.fromWebsite";

/** A restaurant website's "Order online" / "Book a table" link: /?order=<id> or /?book=<id>. Kept until used,
 * so it survives signing in. */
export function captureWebsiteLink(): void {
  const params = new URLSearchParams(window.location.search);
  const order = params.get("order");
  const book = params.get("book");
  if (!order && !book) return;
  // The website's booking form also sends a date, time and party size: keep them only if they look right.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(params.get("date") ?? "") ? params.get("date") : null;
  const time = /^\d{2}:\d{2}$/.test(params.get("time") ?? "") ? params.get("time") : null;
  const party = Number(params.get("party"));
  try {
    window.localStorage.setItem(PENDING, JSON.stringify({
      restaurant: order || book, book: Boolean(book), date, time, party: party >= 1 && party <= 20 ? party : null,
    }));
  } catch {
    /* ignore */
  }
}

export type WebsiteLink = { restaurant: string; book: boolean; date?: string | null; time?: string | null; party?: number | null };

export function takeWebsiteLink(): WebsiteLink | null {
  try {
    const raw = window.localStorage.getItem(PENDING);
    window.localStorage.removeItem(PENDING);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

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
