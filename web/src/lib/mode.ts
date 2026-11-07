import type { AgentKind, Me } from "./api";

const KEY = "pass.agent";
const PENDING = "pass.fromWebsite";
const ITEM_KEY = /^[0-9a-f-]{8,36}$/; // a dish's id, or its first 8 characters
const MAX_LINKED_ITEMS = 30;

/** A cart in a link: "<item id or its first 8 characters>:<qty>,…". */
export function cartToParam(items: Record<string, number>): string {
  return Object.entries(items).map(([id, n]) => `${id}:${n}`).join(",");
}

export function cartFromParam(raw: string | null): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of (raw ?? "").split(",").slice(0, MAX_LINKED_ITEMS)) {
    const [id, n] = part.split(":");
    const qty = Math.floor(Number(n));
    if (ITEM_KEY.test(id ?? "") && qty >= 1) out[id] = Math.min(20, qty);
  }
  return out;
}

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
  // Dishes added on the website's menu: ?q.<first 8 characters of the dish's id>=<quantity> (0 for the rest of
  // the menu). Older pages sent ?add=<item id> once per dish. The menu in the app matches them to dishes.
  const items: Record<string, number> = {};
  const put = (key: string, qty: number) => {
    if (!ITEM_KEY.test(key) || !(qty >= 1)) return;
    if (!(key in items) && Object.keys(items).length >= MAX_LINKED_ITEMS) return;
    items[key] = Math.min(20, (items[key] ?? 0) + Math.floor(qty));
  };
  if (order) {
    params.forEach((value, name) => {
      if (name.startsWith("q.")) put(name.slice(2), Number(value));
    });
    for (const id of params.getAll("add")) put(id, 1);
  }
  try {
    window.localStorage.setItem(PENDING, JSON.stringify({
      restaurant: order || book, book: Boolean(book), date, time, party: party >= 1 && party <= 20 ? party : null,
      items: Object.keys(items).length ? items : null,
    }));
  } catch {
    /* ignore */
  }
}

const CLOVER = "pass.cloverLaunch";

/** Opened from Clover (via the API's launch path): remember it through sign-in and onboarding. */
export function captureCloverLaunch(): void {
  const params = new URLSearchParams(window.location.search);
  if (params.get("clover_launch") !== "1") return;
  const merchant = params.get("merchant_id") ?? "";
  try {
    window.localStorage.setItem(CLOVER, /^[A-Za-z0-9]{1,32}$/.test(merchant) ? merchant : "");
  } catch {
    /* ignore */
  }
}

/** The pending Clover launch (a merchant id, or "" if Clover didn't say), or null if there isn't one. */
export function pendingCloverLaunch(clear = false): string | null {
  try {
    const v = window.localStorage.getItem(CLOVER);
    if (clear) window.localStorage.removeItem(CLOVER);
    return v;
  } catch {
    return null;
  }
}

export type WebsiteLink = {
  restaurant: string; book: boolean; date?: string | null; time?: string | null; party?: number | null;
  items?: Record<string, number> | null; // dishes added on the website's menu
};

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
