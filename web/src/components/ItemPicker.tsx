import { useId, useMemo, useState } from "react";
import { qty, type StockItem } from "../lib/inventory";

type Props = {
  items: StockItem[];
  itemId: string | null; // the existing item it goes into
  itemName?: string | null; // shown if that item isn't in the list
  newName: string; // or the name of a new item to add
  suggestion?: string | null; // a name to offer for a new item
  disabled?: boolean;
  onPickItem: (item: StockItem) => void;
  onNewName: (name: string) => void;
};

/** Matched inventory: an existing item (search to change it), or a name typed for a new one. */
export default function ItemPicker({ items, itemId, itemName, newName, suggestion, disabled, onPickItem, onNewName }: Props) {
  const id = useId();
  const current = itemId ? items.find((i) => i.id === itemId) : undefined;
  const [typing, setTyping] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const value = itemId ? current?.name ?? itemName ?? "" : newName;
  const query = typing ? value : "";

  const matches = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const found = words.length ? items.filter((i) => words.every((w) => i.name.toLowerCase().includes(w))) : items;
    const first = words[0] ?? "";
    return [...found].sort((a, b) => Number(!a.name.toLowerCase().startsWith(first)) - Number(!b.name.toLowerCase().startsWith(first))
      || a.name.localeCompare(b.name)).slice(0, 60);
  }, [items, query]);

  const exact = (text: string) => items.find((i) => i.name.trim().toLowerCase() === text.trim().toLowerCase());
  const offer = typing ? (query.trim() && !exact(query) ? query.trim() : null) : !value && suggestion && !exact(suggestion) ? suggestion : null;
  const options: ({ kind: "new"; name: string } | { kind: "item"; item: StockItem })[] = [
    ...(offer ? [{ kind: "new" as const, name: offer }] : []),
    ...matches.map((item) => ({ kind: "item" as const, item })),
  ];

  function choose(k: number) {
    const o = options[k];
    if (!o) return;
    if (o.kind === "item") onPickItem(o.item);
    else onNewName(o.name);
    setTyping(false);
    setOpen(false);
  }

  return (
    <div className="picker">
      <input className="input" role="combobox" aria-expanded={open} aria-controls={`${id}-list`} aria-autocomplete="list"
        aria-label="Matched inventory" aria-activedescendant={open ? `${id}-${active}` : undefined} disabled={disabled}
        value={value} placeholder="Search your inventory, or type a name to add it"
        onFocus={(e) => { setOpen(true); setActive(0); e.target.select(); }}
        onBlur={() => window.setTimeout(() => {
          setOpen(false);
          if (typing) {
            const same = exact(value);
            if (same && !itemId) onPickItem(same); // typed an existing item's name: that's the item
            setTyping(false);
          }
        }, 120)}
        onChange={(e) => { setTyping(true); setOpen(true); setActive(0); onNewName(e.target.value); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, options.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Escape") { setOpen(false); }
          else if (e.key === "Enter" && open) { e.preventDefault(); choose(active); }
        }} />
      {value && !disabled && (
        <button type="button" className="picker-clear" aria-label="Clear" title="Clear"
          onMouseDown={(e) => e.preventDefault()} onClick={() => { onNewName(""); setTyping(false); }}>✕</button>
      )}
      {open && !disabled && options.length > 0 && (
        <ul className="picker-list" role="listbox" id={`${id}-list`}>
          {options.map((o, k) => (
            <li key={o.kind === "item" ? o.item.id : "new"} id={`${id}-${k}`} role="option" aria-selected={k === active}
              className={`${k === active ? "is-active" : ""} ${o.kind === "item" && o.item.id === itemId ? "is-chosen" : ""} ${o.kind === "new" ? "picker-new" : ""}`}
              onMouseEnter={() => setActive(k)} onMouseDown={(e) => { e.preventDefault(); choose(k); }}>
              {o.kind === "new" ? (
                <span>+ Add new “{o.name}”</span>
              ) : (
                <>
                  <span>{o.item.name}</span>
                  <span className="small muted">{o.item.stock_qty != null ? `${qty(o.item.stock_qty)} ${o.item.unit ?? ""}` : o.item.unit ?? ""}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
