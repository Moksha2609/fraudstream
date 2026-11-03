import type { DriftReport, Stats } from "../types";
import { fmtInt, fmtMs, fmtPct } from "../format";
import Sparkline from "./Sparkline";

interface Props {
  stats: Stats | null;
  drift: DriftReport | null;
  throughputHistory: number[];
}

const DRIFT_LABEL: Record<DriftReport["status"], string> = {
  ok: "stable",
  warn: "moderate shift",
  drift: "drift detected",
  insufficient_data: "warming up",
};

export default function StatTiles({ stats, drift, throughputHistory }: Props) {
  const p99Slow = (stats?.latency_p99_ms ?? 0) > 100;
  return (
    <section className="tiles" aria-label="Key metrics">
      <Tile label="Scored" value={fmtInt(stats?.scored_total)} sub="transactions total" />
      <Tile
        label="Throughput"
        value={`${(stats?.throughput_per_s ?? 0).toFixed(1)}/s`}
        sub={<Sparkline values={throughputHistory} />}
      />
      <Tile
        label="Alerts"
        value={fmtInt(stats?.alerts_total)}
        sub={`${fmtPct(stats?.alert_rate, 2)} alert rate`}
      />
      <Tile
        label="Latency p50 / p99"
        value={
          <>
            {fmtMs(stats?.latency_p50_ms)} <span className="muted">/</span>{" "}
            <span className={p99Slow ? "text-bad" : ""}>{fmtMs(stats?.latency_p99_ms)}</span>
          </>
        }
        sub={p99Slow ? "p99 above 100 ms budget" : "model scoring, 100 ms budget"}
        tone={p99Slow ? "bad" : undefined}
      />
      <Tile label="Unreviewed" value={fmtInt(stats?.unreviewed)} sub="alerts awaiting a verdict" />
      <Tile
        label="Precision"
        value={fmtPct(stats?.precision_on_feedback)}
        sub={`${fmtInt(stats?.feedback_fraud)} fraud · ${fmtInt(stats?.feedback_false_alarm)} false alarms`}
      />
      <Tile
        label="Drift"
        value={<span className={`badge badge-${drift?.status ?? "idle"}`}>{drift ? DRIFT_LABEL[drift.status] : "–"}</span>}
        sub={drift ? `${fmtInt(drift.observed)} observed · window ${fmtInt(drift.window_size)}` : "no report yet"}
        tone={drift?.status === "drift" ? "bad" : drift?.status === "warn" ? "warn" : undefined}
      />
    </section>
  );
}

function Tile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "bad" | "warn";
}) {
  return (
    <div className={`tile${tone ? ` tile-${tone}` : ""}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub !== undefined && <div className="tile-sub">{sub}</div>}
    </div>
  );
}
