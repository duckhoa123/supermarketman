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

/** For the Customer agent and Driver Assistant screens, which don't belong to a restaurant. */
export type MeState = { me: Me; refreshMe: () => Promise<void> };
export const MeContext = createContext<MeState | null>(null);

export function useMe(): MeState {
  const ctx = useContext(MeContext);
  if (!ctx) throw new Error("useMe must be used inside the signed-in shell");
  return ctx;
}
