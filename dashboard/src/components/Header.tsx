import type { Health, ModelInfo, StreamState } from "../types";

interface Props {
  model: ModelInfo | null;
  health: Health | null;
  stream: StreamState;
  notifications: NotificationPermission | "unsupported";
  onEnableNotifications: () => void;
}

const STREAM_LABEL: Record<StreamState, string> = {
  connecting: "connecting",
  live: "live",
  reconnecting: "reconnecting",
};

export default function Header({ model, health, stream, notifications, onEnableNotifications }: Props) {
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <h1>FraudStream</h1>
        <span className="muted">real-time transaction scoring</span>
      </div>
      <div className="header-meta">
        <span className="chip" title={model ? `trained ${model.trained_at}` : ""}>
          model <strong>{model?.version ?? "–"}</strong>
        </span>
        <span className="chip">
          threshold <strong>{model ? model.threshold.toFixed(3) : "–"}</strong>
        </span>
        <span className="chip" title={health ? `queue: ${health.queue}, uptime ${Math.round(health.uptime_s)}s` : ""}>
          <span className={`dot ${health?.status === "ok" ? "ok" : health ? "warn" : "idle"}`} />
          api {health?.status ?? "unreachable"}
        </span>
        <span className="chip">
          <span className={`dot ${stream === "live" ? "ok" : stream === "reconnecting" ? "bad" : "idle"}`} />
          stream {STREAM_LABEL[stream]}
        </span>
        {notifications === "default" && (
          <button className="btn btn-ghost" onClick={onEnableNotifications} type="button">
            Enable notifications
          </button>
        )}
      </div>
    </header>
  );
}
