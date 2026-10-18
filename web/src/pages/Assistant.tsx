import ChatPanel from "../components/ChatPanel";
import { useApp } from "../context";
import { chatPath } from "../lib/api";

const TOOL_LABELS: Record<string, string> = {
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
};

const STARTERS = [
  "How did we go yesterday compared with last week?",
  "How busy will this weekend be, and what should we prep?",
  "What's running low? Draft the supplier orders.",
  "What was labour as a % of sales last week?",
  "Plan a promotion for our slowest night.",
  "Any bad reviews this week I haven't replied to?",
];

export default function Assistant() {
  const { location, me } = useApp();
  const profile = me.agents.find((a) => a.agent === "restaurant_manager");
  return (
    <ChatPanel
      key={location.id}
      path={chatPath("restaurant_manager", location.id)}
      profile={profile}
      title={`Ask ${profile?.display_name ?? "your manager"} about ${location.name}`}
      subtitle="Sales, stock, labour, bookings and reviews from your POS and the app, plus forecasts."
      starters={STARTERS}
      toolLabels={TOOL_LABELS}
      placeholder="Ask about sales, stock, staff…"
      locationId={location.id}
      allowDeep
    />
  );
}
