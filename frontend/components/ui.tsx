"use client";

import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-border bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.03)] ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-text">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "success";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-white hover:opacity-90 border border-transparent disabled:opacity-50",
  secondary:
    "bg-surface text-text border border-border-strong hover:bg-surface-2 disabled:opacity-50",
  ghost: "bg-transparent text-muted hover:bg-surface-2 hover:text-text border border-transparent",
  danger:
    "bg-danger-soft text-danger border border-danger/30 hover:border-danger/60 disabled:opacity-50",
  success:
    "bg-success-soft text-success border border-success/30 hover:border-success/60 disabled:opacity-50",
};

export function Button({
  children,
  variant = "secondary",
  className = "",
  ...rest
}: {
  children: ReactNode;
  variant?: ButtonVariant;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      // whitespace-nowrap：按钮文字在窄容器里会被逐字折行，
      // 挤成竖排并溢出容器（页面头的操作区就是这种情况）。
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed ${BUTTON_STYLES[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

type Tone = "neutral" | "success" | "warn" | "danger" | "accent";

const TONE_STYLES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  success: "bg-success-soft text-success border-success/25",
  warn: "bg-warn-soft text-warn border-warn/25",
  danger: "bg-danger-soft text-danger border-danger/25",
  accent: "bg-accent-soft text-accent border-accent/25",
};

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap ${TONE_STYLES[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  const valueColor =
    tone === "danger"
      ? "text-danger"
      : tone === "warn"
        ? "text-warn"
        : tone === "success"
          ? "text-success"
          : "text-text";

  return (
    <Card className="px-5 py-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={`mt-1.5 text-2xl font-semibold tabular ${valueColor}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-faint">{hint}</div>}
    </Card>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <p className="text-sm font-medium text-muted">{title}</p>
      {hint && <p className="max-w-md text-xs text-faint">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 px-6 py-14 text-sm text-muted">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-border-strong border-t-accent" />
      {label ?? "加载中…"}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">
      {error}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-border-strong bg-surface px-2.5 py-1.5 text-sm text-text outline-none placeholder:text-faint focus:border-accent disabled:opacity-50";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold text-text">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ToolCallChip({ name, ok }: { name: string; ok: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
        ok
          ? "border-success/25 bg-success-soft text-success"
          : "border-warn/25 bg-warn-soft text-warn"
      }`}
    >
      {ok ? "✓" : "!"} {name}
    </span>
  );
}
