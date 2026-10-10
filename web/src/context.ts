import { createContext, useContext } from "react";
import type { Location, Me } from "./lib/api";

export type AppState = {
  me: Me;
  location: Location;
  unread: number;
  refreshUnread: () => void;
  refreshMe: () => Promise<void>;
};

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside the signed-in shell");
  return ctx;
}
