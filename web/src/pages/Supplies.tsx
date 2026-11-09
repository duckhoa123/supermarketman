import { useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import InvoiceReview from "../components/InvoiceReview";
import { useApp } from "../context";
import { PurchasingPanel } from "./supplies/Purchasing";
import { Invoices, LogSheet, PriceChanges } from "./supplies/Receiving";

type Tab = "orders" | "deliveries" | "log" | "prices" | "suppliers";
const TABS: [Tab, string][] = [
  ["orders", "Orders"], ["deliveries", "Deliveries"], ["log", "Log sheet"], ["prices", "Price changes"],
  ["suppliers", "Suppliers"],
];

/** Purchasing and receiving in one place: order from suppliers, check in what arrives (scan the invoice: stock goes
 * up and price changes are flagged), the log of everything received, and the suppliers themselves. */
export default function Supplies() {
  const { location } = useApp();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some(([t]) => t === params.get("tab")) ? params.get("tab") : "orders") as Tab;
  const scan = params.get("scan");
  const go = (t: Tab, extra: Record<string, string> = {}) => setParams({ tab: t, ...extra });
  const [version, setVersion] = useState(0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Supplies</h1>
          <p>Order from your suppliers, then scan the invoice when it arrives: stock goes up and price changes are flagged.</p>
        </div>
      </div>
      <div className="seg supplies-tabs" role="tablist" aria-label="Supplies">
        {TABS.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => go(t)}>{label}</button>
        ))}
      </div>
      {(tab === "orders" || tab === "suppliers") && (
        <PurchasingPanel view={tab} onScanInvoice={() => go("deliveries")} />
      )}
      {tab === "deliveries" && (
        <>
          <Invoices key={version} locationId={location.id} onOpen={(id) => go("deliveries", { scan: id })} />
          {scan && (
            <InvoiceReview key={scan} scanId={scan} onClose={() => { go("deliveries"); setVersion((v) => v + 1); }} />
          )}
        </>
      )}
      {tab === "log" && <LogSheet locationId={location.id} />}
      {tab === "prices" && <PriceChanges locationId={location.id} />}
    </>
  );
}

/** Old addresses (/purchasing, /receiving?tab=…&scan=…) still work. */
export function OldSuppliesLink({ from }: { from: "purchasing" | "receiving" }) {
  const [params] = useSearchParams();
  const old = params.get("tab");
  const tab = from === "purchasing" ? "orders" : old === "log" || old === "prices" ? old : "deliveries";
  const scan = params.get("scan");
  return <Navigate replace to={`/supplies?tab=${tab}${scan ? `&scan=${encodeURIComponent(scan)}` : ""}`} />;
}
