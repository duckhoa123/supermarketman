import type { AgentProfile } from "../lib/api";

export const ICON_GLYPHS: Record<string, string> = {
  chef: "🧑‍🍳",
  whisk: "🥄",
  bowl: "🍜",
  fork: "🍴",
  leaf: "🌿",
  star: "⭐",
  scooter: "🛵",
  bike: "🚲",
  car: "🚗",
  map: "🗺️",
  robot: "🤖",
  smile: "🙂",
};

export const SWATCHES = ["#2563eb", "#16a34a", "#ea580c", "#9333ea", "#db2777", "#0891b2", "#ca8a04", "#2f5d50"];

export default function Avatar({ profile, size = 40 }: { profile: Pick<AgentProfile, "avatar" | "display_name">; size?: number }) {
  return (
    <span
      className="avatar"
      aria-hidden="true"
      style={{ width: size, height: size, fontSize: size * 0.5, background: profile.avatar.color }}
    >
      {ICON_GLYPHS[profile.avatar.icon] ?? "🙂"}
    </span>
  );
}
