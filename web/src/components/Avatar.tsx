import type { AgentProfile } from "../lib/api";

export const ICON_GLYPHS: Record<string, string> = {
  panda: "🐼",
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

/** Picture avatars: a round head-and-shoulders image, and a full-body figure for the pop-up greeting. */
export const PICTURE_AVATARS: Record<string, { head: string; full: string }> = {
  panda: { head: "/avatars/panda-chef-head.webp", full: "/avatars/panda-chef.webp" },
};

export const SWATCHES = ["#2563eb", "#16a34a", "#ea580c", "#9333ea", "#db2777", "#0891b2", "#ca8a04", "#2f5d50"];

export default function Avatar({ profile, size = 40 }: { profile: Pick<AgentProfile, "avatar" | "display_name">; size?: number }) {
  const picture = PICTURE_AVATARS[profile.avatar.icon];
  if (picture) {
    return (
      <span className="avatar avatar-picture" aria-hidden="true" style={{ width: size, height: size }}>
        <img src={picture.head} alt="" draggable={false} />
      </span>
    );
  }
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
