import { useState } from "react";
import { api, type Proposal } from "../lib/api";

const HEADINGS: Record<Proposal["kind"], string> = {
  review_reply: "Reply to review",
  low_stock_threshold: "Low-stock alert",
  alert_rule: "Alert setting",
  purchase_order: "Supplier order",
  customer_order: "Your order",
  booking: "Table booking",
  nutrition_goals: "Daily goals",
};

const BUTTONS: Partial<Record<Proposal["kind"], string>> = {
  review_reply: "Post reply",
  purchase_order: "Approve and send",
  customer_order: "Place order",
  booking: "Request table",
  nutrition_goals: "Save goals",
};

/** Something an agent prepared, waiting for a person's OK. Styled as a kitchen docket.
 * Nothing is posted, sent, ordered or booked until the button is pressed. */
export default function Docket({ proposal, locationId }: { proposal: Proposal; locationId?: string }) {
  const [state, setState] = useState<"waiting" | "saving" | "done" | "dismissed">("waiting");
  const [text, setText] = useState(proposal.reply_text ?? "");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");

  async function approve() {
    setState("saving");
    setError("");
    try {
      switch (proposal.kind) {
        case "review_reply":
          await api(`/locations/${locationId}/reviews/${proposal.review_id}/reply`, { method: "POST", body: { text } });
          break;
        case "low_stock_threshold":
          await api(`/locations/${locationId}/items/${proposal.item_id}`, {
            method: "PATCH",
            body: { low_stock_threshold: proposal.threshold },
          });
          break;
        case "alert_rule":
          await api(`/locations/${locationId}/alert-rules`, {
            method: "PUT",
            body: {
              kind: proposal.rule_kind,
              channels: proposal.channels ?? ["push"],
              params: proposal.params ?? {},
              enabled: proposal.enabled ?? true,
            },
          });
          break;
        case "purchase_order":
          await api(`/locations/${locationId}/purchase-orders/${proposal.purchase_order_id}/approve`, {
            method: "POST",
            body: {},
          });
          setResult("Approved. It's emailed to the supplier now; see Purchasing for its status.");
          break;
        case "customer_order": {
          const r = await api<{ payment: { status: string; message?: string } }>("/customer/orders", {
            method: "POST",
            body: {
              restaurant_id: proposal.restaurant_id,
              items: proposal.items,
              fulfilment: proposal.fulfilment,
              notes: proposal.notes ?? undefined,
            },
          });
          setResult(
            r.payment.status === "test"
              ? "Order placed (test mode: no card charged). Follow it under Orders."
              : "Order placed. Follow it under Orders."
          );
          break;
        }
        case "booking":
          await api("/customer/bookings", {
            method: "POST",
            body: {
              restaurant_id: proposal.restaurant_id,
              party_size: proposal.party_size,
              starts_at: proposal.starts_at,
              notes: proposal.notes ?? undefined,
            },
          });
          setResult("Requested. You'll get a notification when the restaurant confirms.");
          break;
        case "nutrition_goals":
          await api("/customer/profile", {
            method: "PUT",
            body: {
              calories_goal: proposal.goals?.calories,
              protein_goal_g: proposal.goals?.protein_g,
              carbs_goal_g: proposal.goals?.carbs_g,
              fat_goal_g: proposal.goals?.fat_g,
            },
          });
          break;
      }
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
      setState("waiting");
    }
  }

  const open = state === "waiting" || state === "saving";
  return (
    <div className={`docket ${open ? "" : "done"}`}>
      <div className="docket-head">
        <span>{HEADINGS[proposal.kind]}</span>
        <span>{state === "done" ? "Done" : state === "dismissed" ? "Dismissed" : "Needs your OK"}</span>
      </div>
      <div className="docket-body">{proposal.label}</div>
      {proposal.kind === "review_reply" &&
        (open ? (
          <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} />
        ) : (
          <p className="quote">{text}</p>
        ))}
      {proposal.kind === "purchase_order" && proposal.lines && (
        <ul className="docket-lines">
          {proposal.lines.map((l, i) => (
            <li key={i}>
              {l.qty} {l.unit ?? ""} {l.name}
            </li>
          ))}
        </ul>
      )}
      {proposal.kind === "customer_order" && proposal.quote && (
        <div className="docket-lines">
          {proposal.quote.lines.map((l) => (
            <div key={l.item_id} className="spread small">
              <span>
                {l.qty}× {l.name}
              </span>
            </div>
          ))}
          <div className="spread small muted">
            <span>Tax {proposal.quote.tax}</span>
            {proposal.fulfilment === "delivery" && <span>Delivery {proposal.quote.delivery_fee}</span>}
          </div>
          <div className="spread">
            <strong>Total {proposal.quote.total}</strong>
            <span className="small muted">about {proposal.quote.eta_minutes} min</span>
          </div>
        </div>
      )}
      {open && (
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary small" onClick={() => void approve()} disabled={state === "saving"}>
            {BUTTONS[proposal.kind] ?? "Approve"}
          </button>
          <button className="btn small" onClick={() => setState("dismissed")}>
            Dismiss
          </button>
        </div>
      )}
      {result && state === "done" && <p className="small up">{result}</p>}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
