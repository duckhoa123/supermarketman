import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { useApp } from "../context";
import { flushQueue, pendingCount } from "../lib/inventory";
import Stock from "./Stock";
import CountPanel from "./inventory/Count";
import FoodCost from "./inventory/FoodCost";
import Ledger from "./inventory/Ledger";
import OnHandPanel from "./inventory/OnHand";
import Recipes from "./inventory/Recipes";
import WastePanel from "./inventory/Waste";

type Tab = "onhand" | "count" | "waste" | "recipes" | "cost" | "ledger" | "menu";
const TABS: [Tab, string][] = [
  ["onhand", "On hand"], ["count", "Count"], ["waste", "Waste"], ["recipes", "Recipes"], ["cost", "Food cost"],
  ["ledger", "Ledger"], ["menu", "Menu items"],
];
const STAFF_TABS: Tab[] = ["onhand", "count", "waste"];  // staff count and log waste; costs are for managers

/** What's in stock and what it's worth, counts and waste (made for a phone in the walk-in, working offline),
 * recipes and plate costs, COGS and food cost %, and the ledger of every change. */
export default function Inventory() {
  const { location } = useApp();
  const [params, setParams] = useSearchParams();
  const tabs = location.role === "staff" ? TABS.filter(([t]) => STAFF_TABS.includes(t)) : TABS;
  const tab = (tabs.some(([t]) => t === params.get("tab")) ? params.get("tab") : "onhand") as Tab;
  const [pending, setPending] = useState(pendingCount());
  const [synced, setSynced] = useState("");

  // Counts and waste saved offline go out as soon as there's a connection.
  useEffect(() => {
    const update = () => setPending(pendingCount());
    const flush = async () => {
      if (!pendingCount()) return;
      const r = await flushQueue();
      if (r.sent || r.failed.length) {
        setSynced([r.sent ? `Sent ${r.sent} saved entr${r.sent === 1 ? "y" : "ies"}.` : "", ...r.failed].filter(Boolean).join(" "));
      }
      update();
    };
    void flush();
    window.addEventListener("online", flush);
    window.addEventListener("pass-inventory-queue", update);
    const timer = window.setInterval(flush, 30000);
    return () => {
      window.removeEventListener("online", flush);
      window.removeEventListener("pass-inventory-queue", update);
      window.clearInterval(timer);
    };
  }, []);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Inventory</h1>
          <p>Deliveries add stock, sales take ingredients out through your recipes, and counts and waste keep it honest.</p>
        </div>
      </div>
      {pending > 0 && (
        <p className="notice small" role="status">
          {pending} count or waste entr{pending === 1 ? "y is" : "ies are"} saved on this device, waiting for a connection.
          They're sent automatically.
        </p>
      )}
      {synced && <p className="notice small" role="status">{synced}</p>}
      <div className="seg supplies-tabs inv-tabs" role="tablist" aria-label="Inventory">
        {tabs.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""}
            onClick={() => setParams({ tab: t })}>{label}</button>
        ))}
      </div>
      {tab === "onhand" && <OnHandPanel onCount={() => setParams({ tab: "count" })} />}
      {tab === "count" && <CountPanel />}
      {tab === "waste" && <WastePanel />}
      {tab === "recipes" && <Recipes />}
      {tab === "cost" && <FoodCost />}
      {tab === "ledger" && <Ledger />}
      {tab === "menu" && <Stock embedded />}
    </>
  );
}

/** The old Stock address opens the menu items tab. */
export function OldStockLink() {
  return <Navigate replace to="/inventory?tab=menu" />;
}
