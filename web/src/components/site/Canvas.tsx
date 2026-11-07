import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type Device = "desktop" | "tablet" | "phone";
export type Selection = { kind: "section"; sid: string } | { kind: "header" } | { kind: "footer" } | { kind: "menupage" } | null;
export type Box = { x: number; y: number; w: number; h: number };
export type Geometry = { selected: Box | null; hover: Box | null; hoverLabel: Selection; canvas: { w: number; h: number } };

const WIDTH: Record<Device, number> = { desktop: 1280, tablet: 820, phone: 390 };

function sameSel(a: Selection, b: Selection): boolean {
  if (!a || !b) return a === b;
  if (a.kind !== b.kind) return false;
  return a.kind !== "section" || (b.kind === "section" && a.sid === b.sid);
}

function selectionOf(el: Element | null): Selection {
  const host = el?.closest("[data-sid],[data-zone]");
  if (!host) return null;
  const sid = host.getAttribute("data-sid");
  if (sid) return { kind: "section", sid };
  const zone = host.getAttribute("data-zone");
  return zone === "footer" ? { kind: "footer" } : zone === "menupage" ? { kind: "menupage" } : { kind: "header" };
}

/** The page being built, drawn at a real screen width and scaled to fit. Text is typed straight onto it; clicks on
 * photos, sections, the header and the footer are reported up. The page itself runs no scripts: all of this is
 * done from here, through the frame's document. */
export default function Canvas({ html, device, editing, selected, onSelect, onText, onImage, onImages, onAddEnd,
  reveal, overlay }: {
  html: string;
  device: Device;
  editing: boolean;
  selected: Selection;
  onSelect: (s: Selection) => void;
  onText: (target: string, value: string) => void;
  onImage: (target: string) => void;
  onImages: (target: string) => void;
  onAddEnd: () => void;
  reveal: { sid: string; n: number } | null;
  overlay: (g: Geometry) => ReactNode;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const scroll = useRef(0);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<Selection>(null);
  const [tick, setTick] = useState(0);
  const handlers = useRef({ onSelect, onText, onImage, onImages, onAddEnd, editing });
  handlers.current = { onSelect, onText, onImage, onImages, onAddEnd, editing };

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const width = WIDTH[device];
  const pad = device === "desktop" ? 0 : 24;
  const scale = size.w ? Math.min(1, (size.w - pad * 2) / width) : 1;
  const frameH = size.h ? (size.h - pad * 2) / scale : 800;
  const left = size.w ? Math.max(pad, (size.w - width * scale) / 2) : 0;

  const doc = () => frame.current?.contentDocument ?? null;

  // Wire the page up each time it loads (every re-render of the preview replaces the document).
  const attach = useCallback(() => {
    const d = doc();
    const win = frame.current?.contentWindow;
    if (!d || !win) return;
    win.scrollTo(0, scroll.current);
    let raf = 0;
    const bump = () => {
      if (raf) return;
      raf = win.requestAnimationFrame(() => {
        raf = 0;
        scroll.current = win.scrollY;
        setTick((t) => t + 1);
      });
    };
    win.addEventListener("scroll", bump, { passive: true });
    win.addEventListener("resize", bump);
    d.querySelectorAll("img").forEach((img) => img.addEventListener("load", bump, { once: true }));

    d.addEventListener("mousemove", (ev) => {
      if (!handlers.current.editing) return;
      const s = selectionOf(ev.target as Element);
      setHover((h) => (sameSel(h, s) ? h : s));
    });
    d.addEventListener("mouseleave", () => setHover(null));

    // Never leave the editor by clicking a link or submitting the booking form on the page.
    d.addEventListener("submit", (ev) => ev.preventDefault(), true);

    d.addEventListener("click", (ev) => {
      const t = ev.target as Element;
      const link = t.closest("a, button");
      if (link) ev.preventDefault();
      if (!handlers.current.editing) return;
      const text = t.closest<HTMLElement>("[data-e]");
      if (text) {
        handlers.current.onSelect(selectionOf(text));
        startTyping(text, ev);
        return;
      }
      const img = t.closest("[data-img]");
      if (img) {
        ev.preventDefault();
        handlers.current.onSelect(selectionOf(img));
        handlers.current.onImage(img.getAttribute("data-img")!);
        return;
      }
      const imgs = t.closest("[data-imgs]");
      if (imgs) {
        handlers.current.onSelect(selectionOf(imgs));
        handlers.current.onImages(imgs.getAttribute("data-imgs")!);
        return;
      }
      if (t.closest("[data-add]")) {
        handlers.current.onAddEnd();
        return;
      }
      const label = t.closest("label, summary, input, select");
      if (!label || link) handlers.current.onSelect(selectionOf(t));
    }, true);

    function startTyping(el: HTMLElement, ev: MouseEvent) {
      if (el.isContentEditable) return;
      const before = el.innerText;
      try {
        el.contentEditable = "plaintext-only";
      } catch {
        el.contentEditable = "true";
      }
      if (el.contentEditable !== "plaintext-only") el.contentEditable = "true";
      el.classList.add("editing");
      el.classList.remove("edit-empty");
      el.focus();
      // Put the cursor where they clicked.
      const range = (d as Document & { caretRangeFromPoint?: (x: number, y: number) => Range | null })
        .caretRangeFromPoint?.(ev.clientX, ev.clientY);
      if (range) {
        const sel = win!.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
      const multiline = el.hasAttribute("data-ml");
      const finish = (keep: boolean) => {
        el.removeEventListener("keydown", onKey);
        el.removeEventListener("blur", onBlur);
        // Read the text while it's still editable: afterwards the browser folds line breaks into spaces.
        const typed = el.innerText;
        el.contentEditable = "false";
        el.removeAttribute("contenteditable");
        el.classList.remove("editing");
        let value = multiline
          ? typed.split(/\n+/).map((x) => x.trim()).filter(Boolean).join("\n\n")
          : typed.replace(/\s+/g, " ").trim();
        if (!keep) {
          el.innerText = before;
          value = before;
        }
        if (!value) {
          el.innerHTML = "";
          el.classList.add("edit-empty");
        }
        if (keep && value !== before.trim()) handlers.current.onText(el.getAttribute("data-e")!, value);
      };
      const onKey = (k: KeyboardEvent) => {
        if (k.key === "Escape") {
          k.preventDefault();
          finish(false);
          el.blur();
        } else if (k.key === "Enter" && !multiline) {
          k.preventDefault();
          el.blur();
        }
      };
      const onBlur = () => finish(true);
      el.addEventListener("keydown", onKey);
      el.addEventListener("blur", onBlur);
    }
    bump();
  }, []);

  useEffect(() => {
    if (!reveal) return;
    const el = doc()?.querySelector(`[data-sid="${CSS.escape(reveal.sid)}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [reveal]);

  useEffect(() => setHover(null), [editing, html]);

  function boxOf(s: Selection): Box | null {
    const d = doc();
    if (!d || !s) return null;
    const els = s.kind === "section"
      ? Array.from(d.querySelectorAll(`[data-sid="${CSS.escape(s.sid)}"]`))
      : Array.from(d.querySelectorAll(`[data-zone="${s.kind}"]`));
    if (!els.length) return null;
    const rs = els.map((e) => e.getBoundingClientRect());
    const top = Math.min(...rs.map((r) => r.top));
    const bottom = Math.max(...rs.map((r) => r.bottom));
    const l = Math.min(...rs.map((r) => r.left));
    const r = Math.max(...rs.map((x) => x.right));
    return { x: left + l * scale, y: pad + top * scale, w: (r - l) * scale, h: (bottom - top) * scale };
  }

  void tick; // geometry below is recomputed whenever the page scrolls or loads
  const geometry: Geometry = {
    selected: editing ? boxOf(selected) : null,
    hover: editing && !sameSel(hover, selected) ? boxOf(hover) : null,
    hoverLabel: hover,
    canvas: size,
  };

  return (
    <div ref={wrap} className={`sb-canvas sb-${device}`}>
      <iframe ref={frame} title="Your website" srcDoc={html} sandbox="allow-same-origin" onLoad={attach}
        style={{ width, height: frameH, transform: `scale(${scale})`, left, top: pad }} />
      <div className="sb-overlay">{overlay(geometry)}</div>
    </div>
  );
}
