/** Small line icons for the menu, one per screen. Drawn at 24×24 with the text colour, so they follow the active
 * and hover colours and dark mode. */
const PATHS: Record<string, string[]> = {
  chat: ["M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"],
  today: ["M12 3v2", "M12 19v2", "M4.2 4.2l1.4 1.4", "M18.4 18.4l1.4 1.4", "M3 12h2", "M19 12h2", "M4.2 19.8l1.4-1.4", "M18.4 5.6l1.4-1.4", "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"],
  plan: ["M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z", "M4 10h16", "M8 3v4", "M16 3v4", "M8 14h3", "M8 17h6"],
  appOrders: ["M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z", "M11 18h2", "M9 8h6", "M9 11h4"],
  bookings: ["M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z", "M4 10h16", "M8 3v4", "M16 3v4", "M9 15l2 2 4-4"],
  inventory: ["M21 8 12 3 3 8v8l9 5 9-5Z", "M3 8l9 5 9-5", "M12 13v8"],
  supplies: ["M3 6h11v10H3Z", "M14 9h4l3 3v4h-7", "M7.5 18.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z", "M18.5 18.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z"],
  posOrders: ["M6 3h12v18l-3-2-3 2-3-2-3 2Z", "M9 8h6", "M9 12h6", "M9 16h3"],
  tables: ["M4 9h16", "M6 9v10", "M18 9v10", "M8 5h8", "M12 5v4"],
  reviews: ["M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9Z"],
  website: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z", "M3 12h18", "M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3Z"],
  alerts: ["M6 16V11a6 6 0 0 1 12 0v5l2 2H4Z", "M10 20a2 2 0 0 0 4 0"],
  settings: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"],
  fleet: ["M9 4 3 6v14l6-2 6 2 6-2V4l-6 2Z", "M9 4v14", "M15 6v14"],
  working: ["M9 6h11", "M9 12h11", "M9 18h11", "M4 6l1 1 2-2", "M4 12l1 1 2-2", "M4 18l1 1 2-2"],
  shift: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z", "M12 7v5l3 2"],
  activity: ["M3 12h4l3-8 4 16 3-8h4"],
  you: ["M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z", "M4 21a8 8 0 0 1 16 0"],
  discover: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z", "M15.5 8.5l-2 5-5 2 2-5Z"],
  bag: ["M5 8h14l-1 12H6Z", "M9 8V6a3 3 0 0 1 6 0v2"],
  foodLog: ["M6 3h10a2 2 0 0 1 2 2v16H8a2 2 0 0 1-2-2Z", "M6 17a2 2 0 0 1 2-2h10", "M10 7h5", "M10 10h3"],
  jobs: ["M4 8h16v11H4Z", "M9 8V6a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2", "M4 13h16"],
  earnings: ["M12 3v18", "M16 7.5c0-1.4-1.8-2.5-4-2.5s-4 1.1-4 2.5S9.8 10 12 10.5s4 1.3 4 3-1.8 2.5-4 2.5-4-1.1-4-2.5"],
  screens: ["M4 4h7v7H4Z", "M13 4h7v7h-7Z", "M4 13h7v7H4Z", "M13 13h7v7h-7Z"],
  agents: ["M17 1l4 4-4 4", "M3 11V9a4 4 0 0 1 4-4h14", "M7 23l-4-4 4-4", "M21 13v2a4 4 0 0 1-4 4H3"],
};

const BY_LABEL: Record<string, string> = {
  Chat: "chat", Today: "today", Plan: "plan", "App orders": "appOrders", Bookings: "bookings", Inventory: "inventory",
  Supplies: "supplies", "POS orders": "posOrders", Tables: "tables", Reviews: "reviews", Website: "website",
  Alerts: "alerts", Settings: "settings", Fleet: "fleet", "Working on": "working", "My shift": "shift",
  Activity: "activity", You: "you", Discover: "discover", Orders: "bag", "Food log": "foodLog", Profile: "you",
  Updates: "alerts", Jobs: "jobs", Earnings: "earnings", Screens: "screens", "Switch agent or customise": "agents",
};

export default function NavIcon({ label }: { label: string }) {
  const paths = PATHS[BY_LABEL[label] ?? ""];
  if (!paths) return <span className="nav-icon" aria-hidden />;
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden focusable="false">
      {paths.map((d) => <path key={d} d={d} />)}
    </svg>
  );
}
