import { useEffect, useRef, useState } from "react";

export type ChartPoint = { label: string; tip: string; value: number | null };

/** A smooth area chart with gridlines, axis labels and a hover readout. Sized to its container. */
export default function AreaChart({ points, format, height = 260, name }: {
  points: ChartPoint[]; format: (v: number) => string; height?: number; name: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pad = { left: 48, right: 14, top: 14, bottom: 30 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;
  const values = points.map((p) => p.value ?? 0);
  const top = niceMax(Math.max(0, ...values));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => top * f);
  const x = (i: number) => pad.left + (points.length <= 1 ? w / 2 : (i / (points.length - 1)) * w);
  const y = (v: number) => pad.top + h - (top ? (v / top) * h : 0);
  const xy = points.map((p, i) => [x(i), y(p.value ?? 0)] as const);
  const line = smooth(xy);
  const area = xy.length ? `${line} L${xy[xy.length - 1][0]},${pad.top + h} L${xy[0][0]},${pad.top + h} Z` : "";
  const every = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(w / 60))));

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left - pad.left;
    const i = Math.round((px / w) * (points.length - 1));
    setHover(i >= 0 && i < points.length ? i : null);
  }

  return (
    <div ref={box} className="area-chart">
      <svg width={width} height={height} role="img" aria-label={name} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={pad.left + w} y1={y(t)} y2={y(t)} className="ac-grid" />
            <text x={pad.left - 8} y={y(t) + 4} className="ac-label" textAnchor="end">{t ? format(t) : "0"}</text>
          </g>
        ))}
        {points.map((p, i) => (i % every === 0 || i === points.length - 1) && (
          <g key={p.label + i}>
            <line x1={x(i)} x2={x(i)} y1={pad.top + h} y2={pad.top + h + 4} className="ac-axis" />
            <text x={x(i)} y={pad.top + h + 18} className="ac-label" textAnchor="middle">{p.label}</text>
          </g>
        ))}
        <path d={area} className="ac-area" />
        <path d={line} className="ac-line" />
        {xy.length > 0 && <circle cx={xy[xy.length - 1][0]} cy={xy[xy.length - 1][1]} r={4} className="ac-dot" />}
        {hover != null && points[hover] && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + h} className="ac-hover" />
            <circle cx={x(hover)} cy={y(points[hover].value ?? 0)} r={5} className="ac-dot-hover" />
          </g>
        )}
      </svg>
      {hover != null && points[hover] && (
        <div className="ac-tip" style={{ left: Math.min(Math.max(x(hover), 70), width - 70), top: Math.max(y(points[hover].value ?? 0) - 52, 0) }}>
          <span>{points[hover].tip}</span>
          <strong>{points[hover].value == null ? "–" : format(points[hover].value!)}</strong>
        </div>
      )}
    </div>
  );
}

/** A round number just above the largest value, for the top gridline. */
function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/** A smooth path through the points (monotone, so it never overshoots below zero). */
function smooth(pts: readonly (readonly [number, number])[]) {
  if (!pts.length) return "";
  if (pts.length < 3) return pts.map(([a, b], i) => `${i ? "L" : "M"}${a},${b}`).join(" ");
  const d = [`M${pts[0][0]},${pts[0][1]}`];
  const slopes = pts.map((p, i) => {
    const prev = pts[i - 1], next = pts[i + 1];
    if (!prev || !next) return 0;
    const s1 = (p[1] - prev[1]) / (p[0] - prev[0]), s2 = (next[1] - p[1]) / (next[0] - p[0]);
    return s1 * s2 <= 0 ? 0 : (s1 + s2) / 2;
  });
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const dx = (x1 - x0) / 3;
    d.push(`C${x0 + dx},${y0 + slopes[i] * dx} ${x1 - dx},${y1 - slopes[i + 1] * dx} ${x1},${y1}`);
  }
  return d.join(" ");
}
