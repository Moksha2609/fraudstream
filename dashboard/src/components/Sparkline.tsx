interface Props {
  values: number[];
  width?: number;
  height?: number;
}

/** Tiny inline SVG line chart for a short numeric series. */
export default function Sparkline({ values, width = 120, height = 24 }: Props) {
  if (values.length < 2) return <span className="muted">collecting…</span>;
  const max = Math.max(...values, 1e-9);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / span) * (height - 2) - 1).toFixed(1)}`);
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-label="throughput trend">
      <polyline points={pts.join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
