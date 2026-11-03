export const fmtInt = (n: number | null | undefined): string =>
  n == null ? "–" : n.toLocaleString(undefined, { maximumFractionDigits: 0 });

export const fmtPct = (x: number | null | undefined, digits = 1): string =>
  x == null ? "–" : `${(x * 100).toFixed(digits)}%`;

export const fmtMs = (x: number | null | undefined): string =>
  x == null ? "–" : x < 10 ? `${x.toFixed(2)} ms` : `${x.toFixed(1)} ms`;

export const fmtMoney = (x: number): string =>
  x.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });

export const fmtTime = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString(undefined, { hour12: false });
};

export const fmtNum = (x: number, digits = 3): string =>
  Math.abs(x) >= 1000 ? x.toFixed(0) : x.toFixed(digits);

export const shortId = (id: string): string => (id.length > 12 ? `${id.slice(0, 12)}…` : id);
