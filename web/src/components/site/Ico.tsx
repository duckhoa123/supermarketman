const PATHS: Record<string, string[]> = {
  back: ["M15 5l-7 7 7 7"],
  palette: ["M12 3a9 9 0 1 0 0 18c1.1 0 1.6-.8 1.6-1.6 0-1.1-1-1.4-1-2.5 0-1 .8-1.6 1.9-1.6H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3z",
    "M7.5 11.5h.01", "M10 7.5h.01", "M14.5 7.5h.01", "M17 11h.01"],
  layers: ["M12 3l9 5-9 5-9-5z", "M3 13l9 5 9-5"],
  image: ["M4 5h16v14H4z", "M4 16l5-5 4 4 3-3 4 4", "M15.5 9h.01"],
  settings: ["M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14 3h-4l-.6 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2L10 21h4l.6-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"],
  undo: ["M9 14L4 9l5-5", "M4 9h10a6 6 0 0 1 0 12h-3"],
  redo: ["M15 14l5-5-5-5", "M20 9H10a6 6 0 0 0 0 12h3"],
  eye: ["M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"],
  eyeOff: ["M3 3l18 18", "M10.6 6.1A10 10 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3 3.6", "M6.6 6.6A17 17 0 0 0 2 12s3.6 6 10 6a9.6 9.6 0 0 0 4.2-1"],
  pencil: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
  desktop: ["M3 4h18v12H3z", "M8 20h8", "M12 16v4"],
  tablet: ["M6 3h12v18H6z", "M11 18h2"],
  phone: ["M8 2h8v20H8z", "M11 19h2"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  plus: ["M12 5v14", "M5 12h14"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
  sliders: ["M4 6h10", "M18 6h2", "M4 12h4", "M12 12h8", "M4 18h12", "M20 18h0", "M14 4v4", "M8 10v4", "M16 16v4"],
  layout: ["M3 4h18v16H3z", "M3 10h18", "M10 10v10"],
  fill: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 3v18"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v6H4V6h6"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"],
  check: ["M5 12l5 5L20 7"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-4-4"],
  lock: ["M6 11h12v9H6z", "M8.5 11V8a3.5 3.5 0 0 1 7 0v3"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  tag: ["M3 12V4h8l9 9-8 8z", "M7.5 7.5h.01"],
  share: ["M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M8.6 13.5l6.8 4", "M15.4 6.5l-6.8 4"],
  store: ["M4 9l1.5-5h13L20 9", "M4 9h16v2a3 3 0 0 1-5.3 2A3 3 0 0 1 12 14a3 3 0 0 1-2.7-1A3 3 0 0 1 4 11z", "M5 13v7h14v-7", "M10 20v-4h4v4"],
  globe: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M3 12h18", "M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3z"],
  chart: ["M4 20V10", "M10 20V4", "M16 20v-7", "M22 20H2"],
  card: ["M3 6h18v12H3z", "M3 10h18"],
  right: ["M9 5l7 7-7 7"],
};

/** Line icons for the website editor. */
export default function Ico({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {(PATHS[name] ?? []).map((d) => <path key={d} d={d} />)}
    </svg>
  );
}
