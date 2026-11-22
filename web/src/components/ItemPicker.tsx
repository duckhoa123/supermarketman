import { useId, useMemo, useState } from "react";
import { qty, type StockItem } from "../lib/inventory";

type Props = {
  items: StockItem[];
  value: string | null; // the chosen item's id; null = none (a new item)
  valueName?: string | null; // shown if the item isn't in the list
  disabled?: boolean;
  placeholder?: string;
  onPick: (item: StockItem | null) => void;
  onSkip?: () => void; // "don't add to stock"
};

/** A search box over your inventory items: type to filter, pick one, or clear it. */
export default function ItemPicker({ items, value, valueName, disabled, placeholder, onPick, onSkip }: Props) {
  const id = useId();
  const current = value ? items.find((i) => i.id === value) : undefined;
  const [text, setText] = useState<string | null>(null); // null while not typing: shows the chosen item
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const shown = text ?? (current?.name ?? (value ? valueName ?? "" : ""));

  const matches = useMemo(() => {
    const words = (text ?? "").toLowerCase().split(/\s+/).filter(Boolean);
    const found = words.length
      ? items.filter((i) => words.every((w) => i.name.toLowerCase().includes(w)))
      : items;
    const first = words[0] ?? "";
    return [...found].sort((a, b) => Number(!a.name.toLowerCase().startsWith(first)) - Number(!b.name.toLowerCase().startsWith(first))
      || a.name.localeCompare(b.name)).slice(0, 60);
  }, [items, text]);

  function pick(item: StockItem | null) {
    onPick(item);
    setText(null);
    setOpen(false);
  }

  const options = matches.length + (onSkip ? 1 : 0);

  return (
    <div className="picker">
      <input className="input" role="combobox" aria-expanded={open} aria-controls={`${id}-list`} aria-autocomplete="list"
        aria-activedescendant={open ? `${id}-${active}` : undefined} disabled={disabled} value={shown}
        placeholder={placeholder ?? "Search your inventory…"}
        onFocus={(e) => { setOpen(true); setActive(0); e.target.select(); }}
        onBlur={() => window.setTimeout(() => { setOpen(false); setText(null); }, 120)}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
          if (!e.target.value.trim() && value) onPick(null); // emptied: it's a new item
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, options - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Escape") { setOpen(false); setText(null); }
          else if (e.key === "Enter" && open) {
            e.preventDefault();
            if (active < matches.length) pick(matches[active]);
            else if (onSkip) { onSkip(); setText(null); setOpen(false); }
          }
        }} />
      {shown && !disabled && (
        <button type="button" className="picker-clear" aria-label="Clear: add as a new item" title="Clear: add as a new item"
          onMouseDown={(e) => e.preventDefault()} onClick={() => pick(null)}>✕</button>
      )}
      {open && !disabled && (
        <ul className="picker-list" role="listbox" id={`${id}-list`}>
          {matches.map((it, k) => (
            <li key={it.id} id={`${id}-${k}`} role="option" aria-selected={k === active}
              className={`${k === active ? "is-active" : ""} ${it.id === value ? "is-chosen" : ""}`}
              onMouseEnter={() => setActive(k)} onMouseDown={(e) => { e.preventDefault(); pick(it); }}>
              <span>{it.name}</span>
              <span className="small muted">{it.stock_qty != null ? `${qty(it.stock_qty)} ${it.unit ?? ""}` : it.unit ?? ""}</span>
            </li>
          ))}
          {matches.length === 0 && (
            <li className="picker-empty small muted" aria-disabled>Nothing called “{text}”. Leave it blank to add it as a new item.</li>
          )}
          {onSkip && (
            <li id={`${id}-${matches.length}`} role="option" aria-selected={active === matches.length}
              className={`picker-skip ${active === matches.length ? "is-active" : ""}`}
              onMouseEnter={() => setActive(matches.length)}
              onMouseDown={(e) => { e.preventDefault(); onSkip(); setText(null); setOpen(false); }}>
              Don't add to stock (just log it)
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
