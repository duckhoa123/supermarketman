import ChatPanel from "../../components/ChatPanel";
import { useMe } from "../../context";
import { chatPath } from "../../lib/api";

const TOOL_LABELS: Record<string, string> = {
  get_my_deliveries: "Checked your jobs",
  get_shift: "Checked your shift",
  get_my_stats: "Added up your numbers",
  get_busy_areas: "Checked busy areas",
};

const STARTERS = [
  "What's my next stop?",
  "Where is it busy right now?",
  "How many deliveries per hour have I done this week?",
  "How close am I to my weekly goal?",
];

export default function DriverChat() {
  const { me } = useMe();
  const profile = me.agents.find((a) => a.agent === "driver");
  return (
    <ChatPanel
      path={chatPath("driver")}
      profile={profile}
      title={`${profile?.display_name ?? "Driver Assistant"}`}
      subtitle="Your jobs, routes, busy areas and earnings. Mark jobs done with the buttons on Jobs."
      starters={STARTERS}
      toolLabels={TOOL_LABELS}
      placeholder="Ask about your jobs or your week"
    />
  );
}
