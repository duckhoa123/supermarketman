import ChatPanel from "../../components/ChatPanel";
import { useMe } from "../../context";
import { chatPath } from "../../lib/api";

const TOOL_LABELS: Record<string, string> = {
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

const STARTERS = [
  "Find me a vegetarian dinner under 700 calories near me",
  "What's good for pickup that's open now?",
  "Book a table for 4 on Saturday at 7pm",
  "How am I doing against my protein goal today?",
  "Where's my order?",
];

export default function CustomerChat() {
  const { me } = useMe();
  const profile = me.agents.find((a) => a.agent === "customer");
  return (
    <ChatPanel
      path={chatPath("customer")}
      profile={profile}
      title={`Hi, I'm ${profile?.display_name ?? "your food assistant"}`}
      subtitle="Tell me what you feel like. I'll find it, order it or book it, and keep track of your goals."
      starters={STARTERS}
      toolLabels={TOOL_LABELS}
      placeholder="What are you in the mood for?"
      shareLocation
    />
  );
}
