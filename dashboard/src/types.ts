export type FeedbackLabel = "fraud" | "false_alarm";

export interface Alert {
  id: number;
  tx_id: string;
  score: number;
  threshold: number;
  amount: number;
  tx_time: number;
  model_version: string;
  created_at: string;
  feedback: FeedbackLabel | null;
  feedback_at: string | null;
  feedback_note: string | null;
  true_label: number | null;
  features: Record<string, number> | null;
}

export interface Stats {
  scored_total: number;
  alerts_total: number;
  alert_rate: number;
  feedback_fraud: number;
  feedback_false_alarm: number;
  unreviewed: number;
  precision_on_feedback: number | null;
  throughput_per_s: number;
  latency_p50_ms: number | null;
  latency_p99_ms: number | null;
  model_version: string;
  threshold: number;
}

export type DriftStatus = "ok" | "warn" | "drift" | "insufficient_data";

export interface DriftFeature {
  name: string;
  psi: number;
  status: "ok" | "warn" | "drift";
}

export interface DriftReport {
  window_size: number;
  observed: number;
  status: DriftStatus;
  score_psi: number | null;
  alert_rate: number | null;
  reference_alert_rate: number | null;
  features: DriftFeature[];
  drifted_features: string[];
  computed_at: string;
}

export interface ModelInfo {
  version: string;
  threshold: number;
  trained_at: string;
  n_train: number;
  metrics: Record<string, unknown>;
  params: Record<string, unknown>;
  feature_names: string[];
}

export interface Health {
  status: string;
  queue: string;
  queue_ok: boolean;
  uptime_s: number;
  sse_clients: number;
}

export type AlertFilter = "all" | "unreviewed" | "fraud" | "false_alarm";

export type StreamState = "connecting" | "live" | "reconnecting";
