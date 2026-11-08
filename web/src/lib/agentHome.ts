import type { AgentKind, Proposal } from "./api";

/** One thing an agent is waiting on, doing, will do, or did (see backend/app/services/agent_work.py). */
export type WorkState = "needs_you" | "in_progress" | "scheduled" | "done";

export type WorkEntry = {
  id: string;
  kind: string;
  state: WorkState;
  title: string;
  detail: string;
  at: string | null;
  source: string;
  link: string | null;
  by_you: boolean;
  proposal: Proposal | null;
  steps: { label: string; done: boolean; current: boolean }[];
};

export type ProteinGoal = {
  title: string;
  target: number;
  unit: string;
  met_days: number;
  days: { day: string; value: number; met: boolean; today: boolean; future: boolean }[];
};

export type EarningsGoal = {
  title: string;
  earned: string;
  earned_cents: number;
  target: string | null;
  target_cents: number | null;
  pct: number | null;
  hours: number;
  deliveries: number;
  on_time_pct: number | null;
};

export type Work = Record<WorkState, WorkEntry[]> & { goal?: ProteinGoal | EarningsGoal | null };

export type Memory = { id: string; text: string; source: "chat" | "app"; created_at: string };

export type Connection = {
  provider: string;
  name: string;
  can: string;
  status: string; // active | error | disconnected | test | ask
  last_synced_at?: string | null;
  last_error?: string | null;
};

export type Access = { connections: Connection[]; spend: { spent_usd: number; budget_usd: number; limited: boolean } };

/** Where each agent's home data lives: per restaurant for the Restaurant Manager, per person for the others. */
export function agentPaths(agent: AgentKind, locationId?: string) {
  const base = agent === "restaurant_manager" ? `/locations/${locationId}` : `/agents/${agent}`;
  return {
    work: `${base}/work`,
    memory: `${base}/memory`,
    access: agent === "restaurant_manager" ? `${base}/agent-access` : `${base}/access`,
    conversations: `${base}/conversations`,
  };
}

export function isEarningsGoal(goal: Work["goal"]): goal is EarningsGoal {
  return !!goal && "earned_cents" in goal;
}

/** "2:14 pm", "Yesterday, 2:14 pm", "Mon 3 Nov, 2:14 pm"; in the future: "Today at 8:00 am", "Tomorrow at…". */
export function whenLabel(iso: string | null, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).toLowerCase();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86_400_000);
  if (diff === 0) return d > now ? `Today at ${time}` : time;
  if (diff === -1) return `Yesterday, ${time}`;
  if (diff === 1) return `Tomorrow at ${time}`;
  return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}, ${time}`;
}

/** The heading an entry is grouped under on the Activity screen. */
export function dayGroup(entry: WorkEntry, now = new Date()): string {
  if (entry.state === "scheduled") return "Coming up";
  if (entry.state === "needs_you") return "Waiting on you";
  if (entry.state === "in_progress") return "Under way";
  if (!entry.at) return "Earlier";
  const d = new Date(entry.at);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86_400_000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
}
