import { useEffect, useState, type ReactNode } from "react";
import AssistantDock from "./AssistantDock";
import type { Notice } from "./ChatPanel";
import { useApp } from "../context";
import { api, chatPath, type AlertRow, type Me } from "../lib/api";

const RESTAURANT_TOOLS: Record<string, string> = {
  get_sales_summary: "Checked sales",
  get_sales_breakdown: "Broke down sales",
  get_top_items: "Ranked menu items",
  get_inventory: "Checked stock",
  get_orders: "Looked at orders",
  get_tables: "Checked tables",
  get_reviews: "Read reviews",
  propose_review_reply: "Drafted a reply",
  propose_low_stock_threshold: "Prepared a stock alert",
  propose_alert_rule: "Prepared an alert",
  get_forecast: "Forecast the coming days",
  get_report: "Built a report",
  get_labour: "Checked labour cost",
  get_food_cost: "Checked food cost",
  get_purchase_orders: "Checked supplier orders",
  propose_purchase_orders: "Drafted supplier orders",
  get_bookings: "Checked bookings",
  get_guests: "Looked at guests",
  get_marketing_insights: "Gathered marketing facts",
  get_app_orders: "Checked app orders",
  get_receiving_log: "Checked deliveries",
  get_price_changes: "Checked supplier prices",
  get_price_history: "Looked up price history",
};

const RESTAURANT_STARTERS = [
  "How did we go yesterday compared with last week?",
  "How busy will this weekend be, and what should we prep?",
  "What's running low? Draft the supplier orders.",
  "Which supplier prices went up this month?",
  "What was labour as a % of sales last week?",
  "Plan a promotion for our slowest night.",
];

const stripSpeaker = (title: string) => title.replace(/^[^:]{1,60}:\s*/, ""); // the avatar shows who's speaking

/** The Restaurant Manager, on every restaurant screen. Opens with anything it noticed on its own. */
export function RestaurantDock({ children }: { children: ReactNode }) {
  const { location, me, refreshUnread } = useApp();
  const profile = me.agents.find((a) => a.agent === "restaurant_manager");
  const [notices, setNotices] = useState<Notice[]>([]);

  useEffect(() => {
    api<{ alerts: AlertRow[] }>(`/locations/${location.id}/alerts?unread_only=true&limit=20`)
      .then((r) =>
        setNotices(
          r.alerts
            .filter((a) => a.kind === "price_change")
            .slice(0, 3)
            .map((a) => ({
              id: a.id,
              title: stripSpeaker(a.title),
              body: a.body,
              ask: `About "${stripSpeaker(a.title)}": how much will this cost us, and what are our options?`,
            }))
        )
      )
      .catch(() => undefined);
  }, [location.id]);

  function dismiss(id: string) {
    setNotices((n) => n.filter((x) => x.id !== id));
    void api(`/locations/${location.id}/alerts/${id}/read`, { method: "POST", body: {} }).then(refreshUnread);
  }

  return (
    <AssistantDock
      config={{
        path: chatPath("restaurant_manager", location.id),
        profile,
        greeting: `Hi, I'm ${profile?.display_name ?? "your manager"}. Ask me anything about ${location.name}: sales, stock, staff, suppliers, bookings or reviews.`,
        starters: RESTAURANT_STARTERS,
        toolLabels: RESTAURANT_TOOLS,
        placeholder: "Ask about sales, stock, staff…",
        locationId: location.id,
        allowDeep: true,
        notices,
        onDismissNotice: dismiss,
      }}
    >
      {children}
    </AssistantDock>
  );
}

const CUSTOMER_TOOLS: Record<string, string> = {
  search_restaurants: "Searched restaurants",
  get_menu: "Read the menu",
  propose_order: "Prepared your order",
  get_my_orders: "Checked your orders",
  propose_booking: "Prepared a booking",
  get_my_bookings: "Checked your bookings",
  get_nutrition: "Checked your day",
  log_food: "Logged food",
  propose_nutrition_goals: "Suggested goals",
};

const DRIVER_TOOLS: Record<string, string> = {
  get_my_deliveries: "Checked your jobs",
  get_shift: "Checked your shift",
  get_my_stats: "Added up your numbers",
  get_busy_areas: "Checked busy areas",
};

/** The Customer agent or the Driver Assistant, on every screen of that app. */
export function PersonalDock({ me, agent, children }: { me: Me; agent: "customer" | "driver"; children: ReactNode }) {
  const profile = me.agents.find((a) => a.agent === agent);
  const name = profile?.display_name ?? (agent === "customer" ? "your food assistant" : "your driver assistant");
  const config =
    agent === "customer"
      ? {
          path: chatPath("customer"),
          greeting: `Hi, I'm ${name}. Tell me what you feel like and I'll find it, order it or book it, and keep track of your goals.`,
          starters: [
            "Find me a vegetarian dinner under 700 calories near me",
            "What's good for pickup that's open now?",
            "Book a table for 4 on Saturday at 7pm",
            "How am I doing against my protein goal today?",
            "Where's my order?",
          ],
          toolLabels: CUSTOMER_TOOLS,
          placeholder: "What are you in the mood for?",
          shareLocation: true,
        }
      : {
          path: chatPath("driver"),
          greeting: `Hi, I'm ${name}. Ask about your jobs, where it's busy, or your week. Mark jobs done with the buttons on Jobs.`,
          starters: [
            "What's my next stop?",
            "Where is it busy right now?",
            "How many deliveries per hour have I done this week?",
            "How close am I to my weekly goal?",
          ],
          toolLabels: DRIVER_TOOLS,
          placeholder: "Ask about your jobs or your week",
        };
  return (
    <AssistantDock config={{ ...config, profile }}>
      {children}
    </AssistantDock>
  );
}
