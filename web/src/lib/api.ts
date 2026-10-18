import { getToken } from "./auth";

// Trailing slashes are trimmed so "https://api.example.com/" works too (otherwise /me would become //me).
export const API_URL = ((import.meta.env.VITE_API_URL as string | undefined) || "http://localhost:8000")
  .trim()
  .replace(/\/+$/, "");

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

export type AgentKind = "restaurant_manager" | "customer" | "driver";

export type AgentProfile = {
  agent: AgentKind;
  label: string;
  display_name: string;
  avatar: { icon: string; color: string };
  tone: string;
  customised: boolean;
  ready?: boolean;
};

export type ChatEvent =
  | { type: "meta"; conversation_id: string; model: string; budget_limited: boolean; agent?: AgentKind }
  | { type: "text"; delta: string }
  | { type: "tool"; name: string }
  | { type: "action"; proposal: Proposal }
  | { type: "error"; message: string }
  | { type: "done"; model: string; cost_usd: number; proposals: Proposal[] };

export type Proposal = {
  kind:
    | "review_reply"
    | "low_stock_threshold"
    | "alert_rule"
    | "purchase_order"
    | "customer_order"
    | "booking"
    | "nutrition_goals";
  label: string;
  // purchase_order
  purchase_order_id?: string;
  supplier?: string;
  lines?: { name: string; qty: number; unit?: string | null }[];
  // customer_order / booking
  restaurant_id?: string;
  restaurant?: string;
  fulfilment?: "pickup" | "delivery";
  items?: { item_id: string; qty: number }[];
  notes?: string | null;
  quote?: Quote;
  party_size?: number;
  starts_at?: string;
  // nutrition_goals
  goals?: Partial<Record<"calories" | "protein_g" | "carbs_g" | "fat_g", number>>;
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

export type ChatBody = { message: string; conversation_id?: string; deep?: boolean; lat?: number; lng?: number };

/** The chat endpoint for an agent: per restaurant for the Restaurant Manager, per person for the others. */
export function chatPath(agent: AgentKind, locationId?: string): string {
  return agent === "restaurant_manager" ? `/locations/${locationId}/chat` : `/agents/${agent}/chat`;
}

/** POST a chat message to the Restaurant Manager (kept for existing callers). */
export function streamChat(
  locationId: string,
  body: ChatBody,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamFrom(chatPath("restaurant_manager", locationId), body, onEvent, signal);
}

/** POST a chat message and call `onEvent` for each server-sent event as it arrives. */
export async function streamFrom(
  path: string,
  body: ChatBody,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, {
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
  agents: AgentProfile[];
  is_driver: boolean;
  is_fleet_admin: boolean;
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
  supplier_id: string | null;
  reorder_qty: number | null;
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

// --- Ecosystem shapes ---------------------------------------------------------------------------

export type Quote = {
  lines: { item_id: string; name: string; qty: number; total_cents: number }[];
  subtotal: string;
  tax: string;
  delivery_fee: string;
  total: string;
  total_cents: number;
  distance_km: number | null;
  eta_minutes: number;
  nutrition: { calories: number; protein_g: number; carbs_g: number; fat_g: number; complete: boolean };
  tip_suggestions?: { pct: number; cents: number; label: string }[];
};

export type ForecastDay = {
  day: string;
  weekday: string;
  expected: string | null;
  expected_cents: number | null;
  low: string | null;
  high: string | null;
  holiday?: string;
  note?: string;
  factors: { name: string; effect_pct: number; source: string }[];
};

export type Forecast = {
  days: ForecastDay[];
  total_expected: string;
  weather_included: boolean;
  prep_list_tomorrow: { name: string; expected_qty: number }[];
  method: string;
};

export type Report = {
  kind: "day" | "week" | "month";
  from: string;
  to: string;
  text: string;
  labour: { hours: number; cost: string; labour_pct_of_sales: number | null; note: string | null };
  food_cost: { food_cost_pct: number | null };
};

export type Supplier = { id: string; name: string; email: string | null; phone: string | null; notes: string | null };

export type PurchaseOrder = {
  id: string;
  supplier_id: string | null;
  supplier: string | null;
  status: "draft" | "approved" | "sent" | "received" | "cancelled";
  lines: { item_id?: string; name: string; qty: number; unit?: string | null }[];
  note: string | null;
  created_at: string | null;
  sent_at: string | null;
  send_error: string | null;
};

export type BookingRow = {
  id: string;
  name: string;
  phone: string | null;
  party_size: number;
  starts_at: string;
  local_time: string;
  status: string;
  source: string;
  notes: string | null;
  restaurant?: string;
};

export type GuestRow = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  visits: number;
  total_spend: string;
  last_visit: string | null;
  marketing_consent: boolean;
  notes: string | null;
};

export type AppOrder = {
  id: string;
  location_id: string;
  fulfilment: "pickup" | "delivery";
  status: string;
  lines: { item_id: string; name: string; qty: number }[];
  subtotal: string;
  tax: string;
  delivery_fee: string;
  tip: string;
  total: string;
  payment_status: string;
  placed_at: string;
  eta_at: string | null;
  notes: string | null;
  restaurant?: string;
  platform_fee?: string;
  delivery_address?: string | null;
  delivery?: { status: string; driver_assigned: boolean; eta_at: string | null };
  tip_suggestions?: { pct: number; cents: number; label: string }[];
};

export type RestaurantHit = {
  id: string;
  name: string;
  cuisine: string | null;
  address: string | null;
  distance_km: number | null;
  open_now: boolean | null;
  rating: number | null;
  pickup: boolean;
  delivery: boolean;
  bookings: boolean;
  matching_items: { id: string; name: string; price: string; calories: number | null }[];
};

export type MenuItem = {
  id: string;
  name: string;
  category: string | null;
  description: string | null;
  price: string;
  price_cents: number;
  sold_out: boolean;
  tags: string[];
  nutrition: { calories: number | null; protein_g: number | null; carbs_g: number | null; fat_g: number | null };
};

export type Menu = {
  restaurant: {
    id: string;
    name: string;
    cuisine: string | null;
    address: string | null;
    pickup: boolean;
    delivery: boolean;
    bookings: boolean;
  };
  items: MenuItem[];
};

export type CustomerProfile = {
  home_address: string | null;
  home_lat: number | null;
  home_lng: number | null;
  diet_tags: string[];
  goals: Record<"calories" | "protein_g" | "carbs_g" | "fat_g", number | null>;
};

export type NutritionDay = {
  day: string;
  totals: Record<"calories" | "protein_g" | "carbs_g" | "fat_g", number>;
  goals: Record<"calories" | "protein_g" | "carbs_g" | "fat_g", number | null>;
  remaining: Record<"calories" | "protein_g" | "carbs_g" | "fat_g", number | null>;
  entries: { id: string; name: string; source: string; calories: number | null }[];
};

export type DeliveryJob = {
  id: string;
  status: "assigned" | "picked_up";
  restaurant: string | null;
  pickup: { address: string | null; lat: number; lng: number };
  dropoff: { address: string | null; lat: number; lng: number };
  customer_name: string | null;
  notes: string | null;
  items: number | null;
  distance_km: number;
  eta_at: string | null;
  tip: string;
  navigate_url: string;
};

export type DriverStats = {
  hours: number;
  deliveries: number;
  deliveries_per_hour: number | null;
  on_time_pct: number | null;
  km: number;
  wages: string;
  tips: string;
  earnings: string;
  earnings_cents: number;
};

export type DriverMe = {
  driver: {
    id: string;
    name: string;
    role: "driver" | "dispatcher";
    vehicle: string;
    status: "off" | "on_shift" | "on_call";
    hourly_wage: string;
    weekly_goal_cents: number | null;
  };
  shift: { started_at: string } | null;
  today: DriverStats;
  this_week: DriverStats;
  goal_progress_pct: number | null;
  deliveries: DeliveryJob[];
};

export type PersonalAlert = { id: string; kind: string; title: string; body: string; created_at: string; read: boolean };
