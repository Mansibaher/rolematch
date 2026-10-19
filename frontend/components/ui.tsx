"use client";

import Link from "next/link";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { useId } from "react";
import type { ReqStatus } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/api";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "quiet" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-pine text-white hover:bg-pine-deep border border-pine",
  secondary: "bg-panel text-ink border border-rule-strong hover:border-ink",
  quiet: "text-pine hover:underline border border-transparent px-1",
  danger: "bg-panel text-danger border border-danger/50 hover:bg-danger-wash",
};

export function Button({ variant = "secondary", className, busy, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
      className={cx("inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed", VARIANTS[variant], className)}
    >
      {busy && <Spinner small />}
      {children}
    </button>
  );
}

export function ButtonLink({ href, variant = "secondary", className, children, external }: { href: string; variant?: Variant; className?: string; children: ReactNode; external?: boolean }) {
  const cls = cx("inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium", VARIANTS[variant], className);
  if (external) {
    // Opening an employer's page never changes application status.
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>
        {children}
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    );
  }
  return (
    <Link href={href} className={cls}>
      {children}
    </Link>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children(id)}
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}

const inputCls = "w-full rounded-md border border-rule-strong bg-panel px-3 py-2 text-sm placeholder:text-muted/70 focus:border-pine";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputCls, "leading-relaxed", props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputCls, "pr-8", props.className)} />;
}

export function Chip({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cx(
        "rounded-full border px-3 py-1 text-sm",
        selected ? "border-pine bg-pine-wash text-pine-deep" : "border-rule-strong bg-panel text-ink hover:border-ink",
      )}
    >
      {children}
    </button>
  );
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cx("inline-block animate-spin rounded-full border-2 border-current border-r-transparent", small ? "h-3.5 w-3.5" : "h-5 w-5")}
    />
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-muted">
      <Spinner /> <span>{label}…</span>
    </div>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "warn" | "error" | "ok"; title?: string; children?: ReactNode }) {
  const tones = {
    info: "border-rule-strong bg-panel",
    warn: "border-partial/40 bg-partial-wash",
    error: "border-danger/40 bg-danger-wash",
    ok: "border-pine/40 bg-pine-wash",
  };
  return (
    <div role={tone === "error" ? "alert" : undefined} className={cx("rounded-md border px-4 py-3 text-sm", tones[tone])}>
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={title ? "mt-1" : ""}>{children}</div>}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-rule-strong px-6 py-10">
      <p className="text-base font-semibold">{title}</p>
      {children && <div className="mt-2 max-w-prose text-sm text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-rule pb-4">
      <div className="max-w-2xl">
        <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.01em]">{title}</h1>
        {children && <div className="mt-1.5 text-sm text-muted">{children}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

// ---------------------------------------------------------------- evidence vocabulary

const STATUS_STYLE: Record<ReqStatus, string> = {
  supported: "bg-supported border-supported",
  partially_supported: "bg-partial border-partial",
  unclear: "hatch-unclear border-unclear",
  not_evidenced: "bg-panel border-absent",
};

export function StatusMark({ status, withLabel = true }: { status: ReqStatus; withLabel?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm">
      <span aria-hidden className={cx("inline-block h-3 w-3 rounded-[3px] border", STATUS_STYLE[status])} />
      {withLabel ? <span>{STATUS_LABEL[status]}</span> : <span className="sr-only">{STATUS_LABEL[status]}</span>}
    </span>
  );
}

/** One segment per checkable required qualification, coloured by evidence status. */
export function EvidenceStrip({ statuses, className }: { statuses: ReqStatus[]; className?: string }) {
  if (!statuses.length) {
    return <p className={cx("text-xs text-muted", className)}>No checkable requirements listed</p>;
  }
  const counts = statuses.reduce<Record<string, number>>((acc, s) => ((acc[s] = (acc[s] ?? 0) + 1), acc), {});
  const summary = (["supported", "partially_supported", "unclear", "not_evidenced"] as ReqStatus[])
    .filter((s) => counts[s])
    .map((s) => `${counts[s]} ${STATUS_LABEL[s].toLowerCase()}`)
    .join(", ");
  return (
    <div className={cx("flex items-center gap-2", className)}>
      <div role="img" aria-label={`Required qualifications: ${summary}`} className="flex h-2.5 w-36 shrink-0 gap-[2px]">
        {statuses.map((s, i) => (
          <span key={i} className={cx("h-full flex-1 rounded-[2px] border", STATUS_STYLE[s])} />
        ))}
      </div>
      <span className="text-xs text-muted">
        {counts.supported ?? 0} of {statuses.length} required shown
      </span>
    </div>
  );
}

export function ScoreFigure({ value, label, hint }: { value: number; label: string; hint?: string }) {
  return (
    <div>
      <p className="text-sm text-muted">{label}</p>
      <p className="text-2xl font-semibold leading-tight">
        {Math.round(value)}
        <span className="text-sm font-normal text-muted">/100</span>
      </p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}
