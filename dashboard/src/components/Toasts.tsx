import { fmtMoney, fmtPct, shortId } from "../format";
import type { Alert } from "../types";

export interface Toast {
  key: number;
  alert: Alert;
}

interface Props {
  toasts: Toast[];
  onDismiss: (key: number) => void;
}

export default function Toasts({ toasts, onDismiss }: Props) {
  if (toasts.length === 0) return null;
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.key} className="toast" onClick={() => onDismiss(t.key)} role="status">
          <strong>New alert</strong> {fmtMoney(t.alert.amount)} · score {fmtPct(t.alert.score)} ·{" "}
          <span className="mono">{shortId(t.alert.tx_id)}</span>
        </div>
      ))}
    </div>
  );
}
