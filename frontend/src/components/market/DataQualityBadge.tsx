export type DataQualityStatus =
  | "realtime"
  | "delayed"
  | "cached"
  | "stale"
  | "simulated"
  | "fallback"
  | "subscription_required"
  | "unavailable"
  | "partial";

type Props = {
  status: DataQualityStatus;
  provider?: string | null;
  timestamp?: string | null;
  delaySeconds?: number | null;
  compact?: boolean;
};

const LABELS: Record<DataQualityStatus, string> = {
  realtime: "Real-time",
  delayed: "Delayed",
  cached: "Cached",
  stale: "Stale",
  simulated: "Simulated",
  fallback: "Fallback",
  subscription_required: "Subscription required",
  unavailable: "Unavailable",
  partial: "Partial",
};

const CLASSES: Record<DataQualityStatus, string> = {
  realtime: "border-terminal-pos/50 text-terminal-pos",
  delayed: "border-amber-400/50 text-amber-300",
  cached: "border-sky-400/50 text-sky-300",
  stale: "border-terminal-neg/50 text-terminal-neg",
  simulated: "border-fuchsia-400/50 text-fuchsia-300",
  fallback: "border-orange-400/50 text-orange-300",
  subscription_required: "border-terminal-neg/50 text-terminal-neg",
  unavailable: "border-terminal-muted/50 text-terminal-muted",
  partial: "border-yellow-400/50 text-yellow-300",
};

export function DataQualityBadge({ status, provider, timestamp, delaySeconds, compact = false }: Props) {
  const label = LABELS[status] ?? status;
  const title = [
    `Status: ${label}`,
    provider ? `Provider: ${provider}` : null,
    timestamp ? `Timestamp: ${timestamp}` : null,
    delaySeconds != null ? `Delay: ${delaySeconds}s` : null,
  ]
    .filter(Boolean)
    .join(" | ");
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-sm border px-1.5 py-0.5 text-[10px] uppercase tracking-normal ${CLASSES[status] ?? CLASSES.unavailable}`}
      title={title}
      data-testid="data-quality-badge"
      data-status={status}
    >
      {compact ? label.replace(" required", "") : label}
    </span>
  );
}
