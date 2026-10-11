import { getToken } from "./auth";

export const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function headers(json = true): Promise<Record<string, string>> {
  const h: Record<string, string> = {};
  const token = await getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: init.method ?? "GET",
    headers: await headers(init.body !== undefined),
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

export type ChatEvent =
  | { type: "meta"; conversation_id: string; model: string; budget_limited: boolean }
  | { type: "text"; delta: string }
  | { type: "tool"; name: string }
  | { type: "action"; proposal: Proposal }
  | { type: "error"; message: string }
  | { type: "done"; model: string; cost_usd: number; proposals: Proposal[] };

export type Proposal = {
  kind: "review_reply" | "low_stock_threshold" | "alert_rule";
  label: string;
  review_id?: string;
  reply_text?: string;
  item_id?: string;
  item_name?: string;
  threshold?: number;
  rule_kind?: string;
  channels?: string[];
  enabled?: boolean;
  params?: Record<string, unknown>;
};

/** POST a chat message and call `onEvent` for each server-sent event as it arrives. */
export async function streamChat(
  locationId: string,
  body: { message: string; conversation_id?: string; deep?: boolean },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_URL}/locations/${locationId}/chat`, {
    method: "POST",
    headers: await headers(),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf("\n\n");
    while (idx !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as ChatEvent);
      }
      idx = buffer.indexOf("\n\n");
    }
  }
}

// --- Shapes returned by the API (only the fields the UI uses) ---------------------------------

export type Location = { id: string; name: string; timezone: string; currency: string };
export type Me = {
  id: string;
  email: string;
  name: string | null;
  phone: string | null;
  locations: Location[];
  channels: Record<"push" | "email" | "whatsapp" | "sms", boolean>;
  demo_available: boolean;
};

export type Summary = {
  gross: string;
  gross_cents: number;
  orders: number;
  avg_ticket: string;
  note?: string;
  comparison?: { gross: string; gross_change_pct: number | null; orders_change_pct: number | null; mode: string };
};

export type InventoryItem = {
  id: string;
  name: string;
  category: string | null;
  stock_qty: number | null;
  unit: string | null;
  low_stock_threshold: number | null;
  low: boolean;
  price: string;
};

export type ReviewRow = {
  id: string;
  source: string;
  rating: number;
  author: string | null;
  posted: string;
  text: string;
  replied: boolean;
  sentiment: string | null;
  topics: string[];
  suggested_reply: string | null;
};

export type Dashboard = {
  location: Location;
  as_of: string;
  summary: Summary;
  by_hour: { rows: { hour: string; gross_cents: number }[] };
  top_items: { rows: { name: string; revenue: string; qty: number }[] };
  low_stock: { items: InventoryItem[] };
  reviews: { average_rating: number | null; reviews: ReviewRow[] };
  trend_28d: { day: string; gross_cents: number }[];
};

export type AlertRow = { id: string; kind: string; title: string; body: string; created_at: string; read: boolean };
export type IntegrationRow = {
  provider: string;
  status: string;
  external_id: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  choices: { name: string; title: string }[] | null;
};
