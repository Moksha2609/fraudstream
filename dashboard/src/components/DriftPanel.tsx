import type { DriftReport } from "../types";
import { fmtPct } from "../format";

interface Props {
  drift: DriftReport | null;
  error: string | null;
}

const STATUS_TEXT: Record<DriftReport["status"], string> = {
  ok: "Stable",
  warn: "Moderate shift",
  drift: "Drift detected",
  insufficient_data: "Warming up",
};

const TOP_N = 12;
const SCALE_MAX = 0.5; // PSI value that fills the bar

export default function DriftPanel({ drift, error }: Props) {
  const feats = drift ? [...drift.features].sort((a, b) => b.psi - a.psi).slice(0, TOP_N) : [];
  const scaleMax = Math.max(SCALE_MAX, ...feats.map((f) => f.psi));
  const pos = (v: number) => `${Math.min(100, (v / scaleMax) * 100)}%`;

  return (
    <section className="panel drift-panel" aria-label="Model drift">
      <div className="panel-head">
        <h2>Model drift</h2>
        {drift && <span className={`badge badge-${drift.status}`}>{STATUS_TEXT[drift.status]}</span>}
      </div>
      {error && <div className="error">{error}</div>}
      {drift && (
        <>
          <div className="drift-summary">
            <div>
              <span className="muted small">score PSI</span>
              <div className="drift-kpi">{drift.score_psi == null ? "–" : drift.score_psi.toFixed(3)}</div>
            </div>
            <div>
              <span className="muted small">alert rate (live vs. training)</span>
              <div className="drift-kpi">
                {fmtPct(drift.alert_rate, 2)} <span className="muted">vs</span> {fmtPct(drift.reference_alert_rate, 2)}
              </div>
            </div>
            <div>
              <span className="muted small">drifted features</span>
              <div className="drift-kpi">{drift.drifted_features.length}</div>
            </div>
          </div>

          {drift.status === "insufficient_data" ? (
            <p className="muted small">
              Need more scored transactions before the sliding window ({drift.observed.toLocaleString()} /{" "}
              {drift.window_size.toLocaleString()}) can be compared with the training distribution.
            </p>
          ) : (
            <div className="psi-chart" role="img" aria-label="Per-feature population stability index">
              <div className="psi-guides">
                <span className="psi-guide warn" style={{ left: pos(0.1) }} data-label="0.1" />
                <span className="psi-guide drift" style={{ left: pos(0.2) }} data-label="0.2" />
              </div>
              {feats.map((f) => (
                <div key={f.name} className="psi-row">
                  <span className="psi-name mono">{f.name}</span>
                  <div className="psi-track">
                    <div className={`psi-bar ${f.status}`} style={{ width: pos(f.psi) }} />
                  </div>
                  <span className="psi-value mono">{f.psi.toFixed(3)}</span>
                </div>
              ))}
            </div>
          )}
          <p className="muted small explain">
            PSI compares the distribution of recent live values with the training data: below 0.1 is stable, 0.1–0.2 a
            moderate shift, above 0.2 the fraud pattern has likely changed and the model should be retrained.
          </p>
        </>
      )}
    </section>
  );
}
