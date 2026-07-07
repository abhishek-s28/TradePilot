import { cn } from "@/lib/utils";

export function Card({
  className, children, title, action,
}: {
  className?: string;
  children: React.ReactNode;
  title?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className={cn("bg-panel border border-border rounded-lg", className)}>
      {(title || action) && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="text-sm font-medium">{title}</div>
          <div>{action}</div>
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}

export function Stat({
  label, value, sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="bg-panel border border-border rounded-lg p-4">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-xl md:text-2xl font-semibold">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Pill({
  children, tone = "default",
}: {
  children: React.ReactNode;
  tone?: "default" | "bull" | "bear" | "warn";
}) {
  const toneCls = {
    default: "bg-panel2 text-muted",
    bull: "bg-bull/10 text-bull",
    bear: "bg-bear/10 text-bear",
    warn: "bg-yellow-500/10 text-yellow-400",
  }[tone];
  return (
    <span className={cn("px-2 py-0.5 rounded text-xs font-medium", toneCls)}>
      {children}
    </span>
  );
}
