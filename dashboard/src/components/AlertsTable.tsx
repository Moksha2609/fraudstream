import { useEffect, useState } from "react";
import type { Alert, AlertFilter, FeedbackLabel } from "../types";
import { api } from "../api";
import { fmtMoney, fmtNum, fmtPct, fmtTime, shortId } from "../format";

interface Props {
  alerts: Alert[];
  filter: AlertFilter;
  onFilter: (f: AlertFilter) => void;
  loading: boolean;
  error: string | null;
  flashIds: Set<number>;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  pendingIds: Set<number>;
  onFeedback: (id: number, label: FeedbackLabel) => void;
}

const FILTERS: { key: AlertFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unreviewed", label: "Unreviewed" },
  { key: "fraud", label: "Fraud" },
  { key: "false_alarm", label: "False alarm" },
];

export default function AlertsTable(props: Props) {
  const { alerts, filter, onFilter, loading, error, flashIds, selectedId, onSelect, pendingIds, onFeedback } = props;
  return (
    <section className="panel alerts-panel" aria-label="Live alerts">
      <div className="panel-head">
        <h2>Live alerts</h2>
        <div className="tabs" role="tablist">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              role="tab"
              type="button"
              aria-selected={filter === f.key}
              className={`tab${filter === f.key ? " active" : ""}`}
              onClick={() => onFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <span className="muted small">
          {alerts.length} shown · keys: <kbd>f</kbd> fraud, <kbd>x</kbd> false alarm
        </span>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="table-wrap">
        <table className="alerts">
          <thead>
            <tr>
              <th>Received</th>
              <th>Tx</th>
              <th className="num">Amount</th>
              <th>Score</th>
              <th>Status</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {alerts.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">
                  {loading ? "Loading…" : "No alerts yet. Start the replay and they will appear here live."}
                </td>
              </tr>
            )}
            {alerts.map((a) => (
              <AlertRow
                key={a.id}
                alert={a}
                flash={flashIds.has(a.id)}
                selected={selectedId === a.id}
                pending={pendingIds.has(a.id)}
                onSelect={() => onSelect(selectedId === a.id ? null : a.id)}
                onFeedback={(label) => onFeedback(a.id, label)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AlertRow({
  alert,
  flash,
  selected,
  pending,
  onSelect,
  onFeedback,
}: {
  alert: Alert;
  flash: boolean;
  selected: boolean;
  pending: boolean;
  onSelect: () => void;
  onFeedback: (label: FeedbackLabel) => void;
}) {
  const pct = Math.max(0, Math.min(1, alert.score));
  const tone = pct >= 0.9 ? "high" : pct >= 0.7 ? "mid" : "low";
  const cls = ["row", flash ? "flash" : "", selected ? "selected" : "", alert.feedback ? `fb-${alert.feedback}` : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <tr className={cls} onClick={onSelect} aria-expanded={selected}>
        <td className="mono">{fmtTime(alert.created_at)}</td>
        <td className="mono" title={alert.tx_id}>
          {shortId(alert.tx_id)}
        </td>
        <td className="num">{fmtMoney(alert.amount)}</td>
        <td>
          <div className="scorebar" title={`threshold ${alert.threshold.toFixed(3)}`}>
            <div className={`scorebar-fill ${tone}`} style={{ width: `${pct * 100}%` }} />
            <span className="scorebar-text">{fmtPct(pct, 1)}</span>
          </div>
        </td>
        <td>
          <StatusPill feedback={alert.feedback} />
        </td>
        <td onClick={(e) => e.stopPropagation()}>
          <div className="verdict">
            <button
              type="button"
              className={`btn btn-fraud${alert.feedback === "fraud" ? " on" : ""}`}
              disabled={pending || alert.feedback === "fraud"}
              onClick={() => onFeedback("fraud")}
            >
              Real fraud
            </button>
            <button
              type="button"
              className={`btn btn-ok${alert.feedback === "false_alarm" ? " on" : ""}`}
              disabled={pending || alert.feedback === "false_alarm"}
              onClick={() => onFeedback("false_alarm")}
            >
              False alarm
            </button>
          </div>
        </td>
      </tr>
      {selected && (
        <tr className="detail-row">
          <td colSpan={6}>
            <AlertDetail id={alert.id} fallback={alert} />
          </td>
        </tr>
      )}
    </>
  );
}

function StatusPill({ feedback }: { feedback: FeedbackLabel | null }) {
  if (feedback === "fraud") return <span className="pill pill-fraud">Fraud</span>;
  if (feedback === "false_alarm") return <span className="pill pill-ok">False alarm</span>;
  return <span className="pill pill-idle">Unreviewed</span>;
}

function AlertDetail({ id, fallback }: { id: number; fallback: Alert }) {
  const [detail, setDetail] = useState<Alert | null>(fallback.features ? fallback : null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .alert(id)
      .then((a) => alive && setDetail(a))
      .catch((e: unknown) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [id]);

  const a = detail ?? fallback;
  const feats = a.features ? Object.entries(a.features) : [];
  return (
    <div className="detail">
      <div className="detail-meta">
        <span>
          tx <code>{a.tx_id}</code>
        </span>
        <span>
          dataset time <code>{a.tx_time.toFixed(0)} s</code>
        </span>
        <span>
          model <code>{a.model_version}</code>
        </span>
        {a.true_label !== null && (
          <span className={`pill ${a.true_label === 1 ? "pill-fraud" : "pill-ok"}`} title="label carried by the replayed dataset; never used for scoring">
            ground truth from replay: {a.true_label === 1 ? "fraud" : "legit"}
          </span>
        )}
        {a.feedback_at && (
          <span className="muted">
            reviewed {fmtTime(a.feedback_at)}
            {a.feedback_note ? ` — ${a.feedback_note}` : ""}
          </span>
        )}
      </div>
      {err && <div className="error">{err}</div>}
      {feats.length === 0 && !err && <div className="muted">Loading features…</div>}
      {feats.length > 0 && (
        <div className="feature-grid">
          {feats.map(([k, v]) => (
            <div key={k} className="feature">
              <span className="feature-name">{k}</span>
              <span className="feature-value mono">{fmtNum(v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
