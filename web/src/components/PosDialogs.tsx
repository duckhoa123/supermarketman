import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type InventoryItem } from "../lib/api";

function money(cents: number) {
  return new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol" }).format(cents / 100);
}

function toCents(text: string): number | null {
  const n = Number(text.replace(/[$,\s]/g, ""));
  return text.trim() === "" || Number.isNaN(n) || n < 0 ? null : Math.round(n * 100);
}

export function Dialog({ title, onClose, children, footer, narrow = false, width }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; narrow?: boolean; width?: string;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="sb-modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sb-modal" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "Dialog"}
        style={narrow ? { width: "min(460px, 100%)" } : width ? { width } : undefined}>
        <div className="sb-flyout-head">
          <h2>{title}</h2>
          <button className="sb-icon" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="sb-modal-body stack">{children}</div>
        {footer && <div className="sb-modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Add a menu item, or change one. With Clover connected it's saved in Clover too. */
export function ItemDialog({ locationId, item, categories, pos, onDone, onClose }: {
  locationId: string; item: InventoryItem | null; categories: string[]; pos: string | null;
  onDone: (message: string) => void; onClose: () => void;
}) {
  const [name, setName] = useState(item?.name ?? "");
  const [price, setPrice] = useState(item ? (item.price_cents / 100).toFixed(2) : "");
  const [category, setCategory] = useState(item?.category ?? "");
  const [stock, setStock] = useState(item?.stock_qty != null ? String(item.stock_qty) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inPos = item ? item.provider === pos : !!pos;
  const where = pos ? pos[0].toUpperCase() + pos.slice(1) : "";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const cents = toCents(price);
    if (!name.trim()) return setError("Give the item a name.");
    if (cents === null) return setError("Enter a price, like 12.50.");
    const qty = stock.trim() === "" ? null : Number(stock);
    if (qty !== null && (Number.isNaN(qty) || qty < 0)) return setError("Stock must be a number, or leave it empty.");
    setBusy(true);
    setError("");
    const body: Record<string, unknown> = { name: name.trim(), price_cents: cents, category: category.trim() || null };
    if (qty !== null || item?.stock_qty != null) body.stock_qty = qty;
    try {
      if (item) await api(`/locations/${locationId}/items/${item.id}/details`, { method: "PUT", body });
      else await api(`/locations/${locationId}/items`, { method: "POST", body });
      onDone(`${name.trim()} ${item ? "updated" : "added"}${inPos ? ` in Pass and ${where}` : ""}.`);
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't save.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={item ? `Edit ${item.name}` : "Add an item"} onClose={onClose} narrow
      footer={<div className="row" style={{ marginLeft: "auto" }}>
        <button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="item-form" disabled={busy}>{busy ? (inPos ? `Saving to ${where}…` : "Saving…") : "Save"}</button>
      </div>}>
      <form id="item-form" className="stack" onSubmit={save}>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small">Name</span>
          <input className="input" maxLength={127} value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="e.g. Chicken parmigiana" />
        </label>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <label className="stack" style={{ gap: 4, flex: 1 }}>
            <span className="small">Price ($)</span>
            <input className="input" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="12.00" />
          </label>
          <label className="stack" style={{ gap: 4, flex: 1 }}>
            <span className="small">In stock (optional)</span>
            <input className="input" inputMode="decimal" value={stock} onChange={(e) => setStock(e.target.value)} placeholder="Not tracked" />
          </label>
        </div>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small">Category</span>
          <input className="input" list="item-categories" maxLength={100} value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Mains" />
          <datalist id="item-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
        </label>
        <p className="small muted">
          {inPos ? `Saved in ${where} too, so it shows on your ${where} devices straight away.`
            : item && item.provider === "demo" ? "A demo item: changed in Pass only."
            : pos ? `This item isn't from ${where}, so it's changed in Pass only.`
            : "No POS connected: the item is kept in Pass. Connect Clover in Settings to add items there too."}
        </p>
        {error && <p className="small error">{error}</p>}
      </form>
    </Dialog>
  );
}

type Line = { key: string; item_id?: string; name: string; unit_price_cents: number; qty: number };

/** Ring up an order in Pass: it's created in Clover as an open order, paid for at the Clover device. */
export function NewOrderDialog({ locationId, pos, onDone, onClose }: {
  locationId: string; pos: string; onDone: (message: string) => void; onClose: () => void;
}) {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [note, setNote] = useState("");
  const [title, setTitle] = useState("");
  const [custom, setCustom] = useState({ name: "", price: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const where = pos[0].toUpperCase() + pos.slice(1);

  useEffect(() => {
    const t = window.setTimeout(() => {
      void api<{ items: InventoryItem[] }>(`/locations/${locationId}/inventory${search ? `?search=${encodeURIComponent(search)}` : ""}`)
        .then((r) => setItems(r.items)).catch(() => setItems([]));
    }, 200);
    return () => window.clearTimeout(t);
  }, [locationId, search]);

  const total = useMemo(() => lines.reduce((n, l) => n + l.unit_price_cents * l.qty, 0), [lines]);

  function add(i: InventoryItem) {
    setLines((ls) => {
      const found = ls.find((l) => l.item_id === i.id);
      return found ? ls.map((l) => (l === found ? { ...l, qty: Math.min(99, l.qty + 1) } : l))
        : [...ls, { key: i.id, item_id: i.id, name: i.name, unit_price_cents: i.price_cents, qty: 1 }];
    });
  }
  function bump(key: string, by: number) {
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, qty: Math.min(99, l.qty + by) } : l)).filter((l) => l.qty > 0));
  }
  function addCustom() {
    const cents = toCents(custom.price);
    if (!custom.name.trim() || cents === null) return setError("A custom line needs a name and a price.");
    setError("");
    setLines((ls) => [...ls, { key: `c${Date.now()}`, name: custom.name.trim(), unit_price_cents: cents, qty: 1 }]);
    setCustom({ name: "", price: "" });
  }

  async function send() {
    setBusy(true);
    setError("");
    try {
      await api(`/locations/${locationId}/pos-orders`, {
        method: "POST",
        body: {
          title: title.trim() || undefined, note: note.trim() || undefined,
          lines: lines.map((l) => (l.item_id ? { item_id: l.item_id, qty: l.qty } : { name: l.name, unit_price_cents: l.unit_price_cents, qty: l.qty })),
        },
      });
      onDone(`Order sent to ${where}: ${money(total)}. Take payment on your ${where} device.`);
    } catch (x) {
      setError(x instanceof Error ? x.message : "Couldn't send the order.");
      setBusy(false);
    }
  }

  return (
    <Dialog title="New order" onClose={onClose}
      footer={<div className="spread" style={{ width: "100%" }}>
        <strong>Total {money(total)} <span className="small muted">before tax</span></strong>
        <div className="row">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || lines.length === 0} onClick={() => void send()}>
            {busy ? `Sending to ${where}…` : `Send to ${where}`}
          </button>
        </div>
      </div>}>
      <div className="pos-order">
        <div className="stack" style={{ gap: 8, minWidth: 0 }}>
          <input className="input" placeholder="Search your menu" value={search} onChange={(e) => setSearch(e.target.value)} />
          <div className="pos-menu">
            {items.map((i) => (
              <button key={i.id} className="pos-menu-item" onClick={() => add(i)}>
                <strong>{i.name}</strong>
                <span className="small muted">{i.category ?? ""}</span>
                <span className="small">{i.price}</span>
              </button>
            ))}
            {items.length === 0 && <p className="small muted">No items found. Add them on the Stock page.</p>}
          </div>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input className="input" style={{ flex: 2 }} placeholder="Custom item" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
            <input className="input" style={{ flex: 1 }} placeholder="$" inputMode="decimal" value={custom.price} onChange={(e) => setCustom({ ...custom, price: e.target.value })} />
            <button className="btn small" type="button" onClick={addCustom}>Add</button>
          </div>
        </div>
        <div className="stack" style={{ gap: 8 }}>
          <h3 style={{ margin: 0 }}>This order</h3>
          {lines.length === 0 && <p className="small muted">Tap items on the left to add them.</p>}
          <ul className="pos-lines">
            {lines.map((l) => (
              <li key={l.key}>
                <span className="pos-qty">
                  <button aria-label={`One less ${l.name}`} onClick={() => bump(l.key, -1)}>−</button>
                  <b>{l.qty}</b>
                  <button aria-label={`One more ${l.name}`} onClick={() => bump(l.key, 1)}>+</button>
                </span>
                <span className="pos-line-name">{l.name}</span>
                <span>{money(l.unit_price_cents * l.qty)}</span>
              </li>
            ))}
          </ul>
          <input className="input" maxLength={60} placeholder="Name or table (optional), e.g. Table 4" value={title} onChange={(e) => setTitle(e.target.value)} />
          <textarea className="textarea" rows={2} maxLength={500} placeholder="Note for the kitchen (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <p className="small muted">It arrives in {where} as an open order, ready to take payment on your {where} device.</p>
          {error && <p className="small error">{error}</p>}
        </div>
      </div>
    </Dialog>
  );
}
