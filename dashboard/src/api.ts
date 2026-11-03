import { useEffect, useRef, useState } from "react";
import type {
  Alert,
  AlertFilter,
  DriftReport,
  FeedbackLabel,
  Health,
  ModelInfo,
  Stats,
  StreamState,
} from "./types";

const BASE = "/api";

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return (await r.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return (await r.json()) as T;
}

export const api = {
  stats: () => getJson<Stats>("/stats"),
  drift: () => getJson<DriftReport>("/drift"),
  model: () => getJson<ModelInfo>("/model"),
  health: () => getJson<Health>("/health"),
  alerts: (filter: AlertFilter, limit = 200) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (filter !== "all") q.set("status", filter);
    return getJson<Alert[]>(`/alerts?${q.toString()}`);
  },
  alert: (id: number) => getJson<Alert>(`/alerts/${id}`),
  feedback: (id: number, label: FeedbackLabel, note?: string) =>
    postJson<Alert>(`/alerts/${id}/feedback`, note ? { label, note } : { label }),
};

/** Poll a JSON endpoint every `intervalMs`. Returns the latest value and error. */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number): {
  data: T | null;
  error: string | null;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const v = await fetcherRef.current();
        if (alive) {
          setData(v);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) timer = window.setTimeout(tick, intervalMs);
      }
    };
    void tick();
    return () => {
      alive = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [intervalMs]);

  return { data, error };
}

/** Subscribe to the alert SSE feed. `onAlert` is called for each new alert. */
export function useAlertStream(onAlert: (a: Alert) => void): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  const cbRef = useRef(onAlert);
  cbRef.current = onAlert;

  useEffect(() => {
    const es = new EventSource(`${BASE}/alerts/stream`);
    es.addEventListener("ready", () => setState("live"));
    es.addEventListener("ping", () => setState("live"));
    es.addEventListener("alert", (ev) => {
      try {
        cbRef.current(JSON.parse((ev as MessageEvent<string>).data) as Alert);
      } catch {
        /* ignore malformed event */
      }
    });
    es.onopen = () => setState("live");
    es.onerror = () => setState("reconnecting"); // EventSource auto-reconnects
    return () => es.close();
  }, []);

  return state;
}
