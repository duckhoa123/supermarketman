import { useState, type FormEvent } from "react";
import { useApi } from "../../hooks";
import { api, type Menu, type Quote, type RestaurantHit } from "../../lib/api";

type Cart = Record<string, number>;

/** Search nearby restaurants, open a menu, build an order and place it. */
export default function Discover() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [fulfilment, setFulfilment] = useState<"pickup" | "delivery">("pickup");
  const [openNow, setOpenNow] = useState(false);
  const results = useApi<{ count: number; restaurants: RestaurantHit[] }>(
    `/customer/restaurants?radius_km=8&fulfilment=${fulfilment}&open_now=${openNow}${query ? `&q=${encodeURIComponent(query)}` : ""}`
  );
  const [restaurant, setRestaurant] = useState<string | null>(null);
  const menu = useApi<Menu>(restaurant ? `/customer/restaurants/${restaurant}/menu` : null);
  const [cart, setCart] = useState<Cart>({});
  const [quote, setQuote] = useState<Quote | null>(null);
  const [message, setMessage] = useState("");
  const [shareContact, setShareContact] = useState(false);

  function search(e: FormEvent) {
    e.preventDefault();
    setQuery(q.trim());
  }

  function add(itemId: string, delta: number) {
    const next = { ...cart, [itemId]: Math.max(0, (cart[itemId] ?? 0) + delta) };
    if (!next[itemId]) delete next[itemId];
    setCart(next);
    setQuote(null);
  }

  const items = Object.entries(cart).map(([item_id, qty]) => ({ item_id, qty }));
  const body = { restaurant_id: restaurant, items, fulfilment, share_contact: shareContact };

  async function price() {
    setMessage("");
    try {
      setQuote(await api<Quote>("/customer/orders/quote", { method: "POST", body }));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't price the order.");
    }
  }

  async function place() {
    setMessage("");
    try {
      const r = await api<{ payment: { status: string } }>("/customer/orders", { method: "POST", body });
      setCart({});
      setQuote(null);
      setMessage(
        r.payment.status === "test" ? "Order placed (test mode, no card charged). Track it under Orders." : "Order placed."
      );
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't place the order.");
    }
  }

  if (restaurant && menu.data) {
    const r = menu.data.restaurant;
    const byCat: Record<string, typeof menu.data.items> = {};
    for (const i of menu.data.items) (byCat[i.category ?? "Menu"] ??= []).push(i);
    return (
      <>
        <div className="page-head">
          <div>
            <button className="btn small" onClick={() => setRestaurant(null)}>
              ← Back
            </button>
            <h1 style={{ marginTop: 8 }}>{r.name}</h1>
            <p>{[r.cuisine, r.address].filter(Boolean).join(" · ")}</p>
          </div>
        </div>
        <div className="grid two">
          <div className="stack">
            {Object.entries(byCat).map(([cat, list]) => (
              <section key={cat} className="panel">
                <h2>{cat}</h2>
                <ul className="list">
                  {list.map((i) => (
                    <li key={i.id} className="spread">
                      <div>
                        <strong>{i.name}</strong> <span className="muted">{i.price}</span>
                        {i.description && <div className="small muted">{i.description}</div>}
                        <div className="small muted">
                          {i.nutrition.calories != null && `${i.nutrition.calories} cal · ${i.nutrition.protein_g} g protein`}
                          {i.tags.length > 0 && ` · ${i.tags.join(", ").replace(/_/g, " ")}`}
                        </div>
                      </div>
                      {i.sold_out ? (
                        <span className="pill low">Sold out</span>
                      ) : (
                        <div className="row">
                          {cart[i.id] ? (
                            <>
                              <button className="btn small" onClick={() => add(i.id, -1)} aria-label="Remove one">−</button>
                              <span>{cart[i.id]}</span>
                            </>
                          ) : null}
                          <button className="btn small" onClick={() => add(i.id, 1)} aria-label={`Add ${i.name}`}>+</button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
          <section className="panel stack" style={{ alignSelf: "start", position: "sticky", top: 16 }}>
            <h2>Your order</h2>
            <div className="seg">
              {(["pickup", "delivery"] as const)
                .filter((f) => (f === "delivery" ? r.delivery : r.pickup))
                .map((f) => (
                  <button key={f} className={fulfilment === f ? "on" : ""} onClick={() => { setFulfilment(f); setQuote(null); }}>
                    {f === "pickup" ? "Pickup" : "Delivery"}
                  </button>
                ))}
            </div>
            {items.length === 0 && <p className="muted">Add something from the menu.</p>}
            {quote && (
              <div className="stack small" style={{ gap: 4 }}>
                {quote.lines.map((l) => (
                  <div key={l.item_id}>{l.qty}× {l.name}</div>
                ))}
                <div className="spread"><span>Food</span><span>{quote.subtotal}</span></div>
                <div className="spread"><span>GST</span><span>{quote.tax}</span></div>
                {fulfilment === "delivery" && <div className="spread"><span>Delivery ({quote.distance_km} km)</span><span>{quote.delivery_fee}</span></div>}
                <div className="spread"><strong>Total</strong><strong>{quote.total}</strong></div>
                <div className="muted">
                  About {quote.eta_minutes} min · {Math.round(quote.nutrition.calories)} cal
                  {!quote.nutrition.complete && " (some items have no nutrition info)"}
                </div>
              </div>
            )}
            <label className="check small">
              <input type="checkbox" checked={shareContact} onChange={(e) => setShareContact(e.target.checked)} />
              Share my name and email with {r.name}
            </label>
            {items.length > 0 &&
              (quote ? (
                <button className="btn primary" onClick={() => void place()}>Place order</button>
              ) : (
                <button className="btn" onClick={() => void price()}>See total</button>
              ))}
            {message && <p className="small">{message}</p>}
          </section>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Discover</h1>
          <p>Restaurants near your saved address. Set it under Profile.</p>
        </div>
      </div>
      <form className="row" onSubmit={search} style={{ marginBottom: 16 }}>
        <input className="input" style={{ flex: 1 }} placeholder="Ramen, vegan burger, pizza…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          {(["pickup", "delivery"] as const).map((f) => (
            <button type="button" key={f} className={fulfilment === f ? "on" : ""} onClick={() => setFulfilment(f)}>
              {f === "pickup" ? "Pickup" : "Delivery"}
            </button>
          ))}
        </div>
        <label className="check small">
          <input type="checkbox" checked={openNow} onChange={(e) => setOpenNow(e.target.checked)} /> Open now
        </label>
        <button className="btn primary">Search</button>
      </form>
      {results.error && <p className="error">{results.error}</p>}
      {results.data?.count === 0 && <p className="empty panel">No restaurants match yet. We're adding Vancouver restaurants every week.</p>}
      <div className="grid two">
        {results.data?.restaurants.map((r) => (
          <button key={r.id} className="panel tile" onClick={() => { setRestaurant(r.id); setCart({}); setQuote(null); setMessage(""); }}>
            <div className="spread">
              <strong>{r.name}</strong>
              {r.rating != null && <span className="stars">★ {r.rating}</span>}
            </div>
            <div className="small muted">
              {[r.cuisine, r.distance_km != null ? `${r.distance_km} km` : null, r.open_now === false ? "closed now" : r.open_now ? "open" : null]
                .filter(Boolean)
                .join(" · ")}
            </div>
            {r.matching_items.length > 0 && (
              <div className="small" style={{ marginTop: 6 }}>
                {r.matching_items.map((i) => `${i.name} ${i.price}`).join(" · ")}
              </div>
            )}
          </button>
        ))}
      </div>
    </>
  );
}
