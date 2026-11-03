import { useCallback, useEffect, useRef, useState } from "react";
import { api, useAlertStream, usePolling } from "./api";
import type { Alert, AlertFilter, FeedbackLabel } from "./types";
import Header from "./components/Header";
import StatTiles from "./components/StatTiles";
import AlertsTable from "./components/AlertsTable";
import DriftPanel from "./components/DriftPanel";
import Toasts, { type Toast } from "./components/Toasts";
import { fmtMoney, fmtPct } from "./format";

const MAX_ALERTS = 500;
const POLL_MS = 3000;
const FLASH_MS = 1500;
const TOAST_MS = 4000;
const HISTORY_LEN = 30;

function matchesFilter(a: Alert, f: AlertFilter): boolean {
  if (f === "all") return true;
  if (f === "unreviewed") return a.feedback === null;
  return a.feedback === f;
}

export default function App() {
  const { data: stats } = usePolling(api.stats, POLL_MS);
  const { data: drift, error: driftError } = usePolling(api.drift, POLL_MS);
  const { data: health } = usePolling(api.health, POLL_MS * 2);
  const { data: model } = usePolling(api.model, 30_000);

  const [filter, setFilter] = useState<AlertFilter>("all");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [flashIds, setFlashIds] = useState<Set<number>>(new Set());
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set());
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [throughputHistory, setThroughputHistory] = useState<number[]>([]);
  const [notifPerm, setNotifPerm] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );
  const filterRef = useRef(filter);
  filterRef.current = filter;

  // ---- history of alerts for the active filter -------------------------
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .alerts(filter)
      .then((rows) => {
        if (!alive) return;
        setAlerts(rows.slice(0, MAX_ALERTS));
        setAlertsError(null);
      })
      .catch((e: unknown) => alive && setAlertsError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [filter]);

  // ---- throughput sparkline --------------------------------------------
  useEffect(() => {
    if (!stats) return;
    setThroughputHistory((h) => [...h, stats.throughput_per_s].slice(-HISTORY_LEN));
  }, [stats]);

  // ---- live alerts via SSE ---------------------------------------------
  const onAlert = useCallback((a: Alert) => {
    const f = filterRef.current;
    if (f === "all" || f === "unreviewed") {
      setAlerts((prev) => (prev.some((p) => p.id === a.id) ? prev : [a, ...prev].slice(0, MAX_ALERTS)));
      setFlashIds((s) => new Set(s).add(a.id));
      window.setTimeout(
        () =>
          setFlashIds((s) => {
            const n = new Set(s);
            n.delete(a.id);
            return n;
          }),
        FLASH_MS,
      );
    }
    const key = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { key, alert: a }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.key !== key)), TOAST_MS);
    if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.hidden) {
      try {
        new Notification("FraudStream alert", {
          body: `${fmtMoney(a.amount)} scored ${fmtPct(a.score)}`,
          tag: `alert-${a.id}`,
        });
      } catch {
        /* notifications unavailable */
      }
    }
  }, []);
  const stream = useAlertStream(onAlert);

  // ---- feedback ---------------------------------------------------------
  const submitFeedback = useCallback(
    async (id: number, label: FeedbackLabel) => {
      const prev = alerts.find((a) => a.id === id);
      if (!prev || prev.feedback === label) return;
      setPendingIds((s) => new Set(s).add(id));
      // optimistic update
      setAlerts((rows) => rows.map((a) => (a.id === id ? { ...a, feedback: label } : a)));
      try {
        const saved = await api.feedback(id, label);
        setAlerts((rows) =>
          rows
            .map((a) => (a.id === id ? { ...a, ...saved, features: a.features ?? saved.features } : a))
            .filter((a) => matchesFilter(a, filterRef.current)),
        );
      } catch (e) {
        setAlerts((rows) => rows.map((a) => (a.id === id ? prev : a)));
        setAlertsError(e instanceof Error ? e.message : String(e));
      } finally {
        setPendingIds((s) => {
          const n = new Set(s);
          n.delete(id);
          return n;
        });
      }
    },
    [alerts],
  );

  // ---- keyboard shortcuts ----------------------------------------------
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (selectedId === null) return;
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (e.key === "f") void submitFeedback(selectedId, "fraud");
      else if (e.key === "x") void submitFeedback(selectedId, "false_alarm");
      else if (e.key === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selectedId, submitFeedback]);

  const enableNotifications = () => {
    if (typeof Notification === "undefined") return;
    void Notification.requestPermission().then(setNotifPerm);
  };

  return (
    <div className="app">
      <Header
        model={model}
        health={health}
        stream={stream}
        notifications={notifPerm}
        onEnableNotifications={enableNotifications}
      />
      <StatTiles stats={stats} drift={drift} throughputHistory={throughputHistory} />
      <main className="grid">
        <AlertsTable
          alerts={alerts}
          filter={filter}
          onFilter={setFilter}
          loading={loading}
          error={alertsError}
          flashIds={flashIds}
          selectedId={selectedId}
          onSelect={setSelectedId}
          pendingIds={pendingIds}
          onFeedback={(id, label) => void submitFeedback(id, label)}
        />
        <DriftPanel drift={drift} error={driftError} />
      </main>
      <Toasts toasts={toasts} onDismiss={(key) => setToasts((t) => t.filter((x) => x.key !== key))} />
    </div>
  );
}
