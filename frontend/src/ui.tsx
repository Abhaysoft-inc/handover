import clsx from "clsx";
import { LoaderCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Urgency } from "./api";
import { navigate } from "./live";

export const cx = clsx;

// ── formatting ────────────────────────────────────────────────────────────

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function fmtDate(iso: string | null | undefined, withYear = true): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  return `${MONTHS[m - 1]} ${d}${withYear ? `, ${y}` : ""}`;
}

export function daysLabel(days: number | null | undefined): string {
  if (days === null || days === undefined) return "No deadline";
  if (days < 0) return `${-days}d overdue`;
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return `${days} days`;
}

export function money(n: number) {
  return n < 0.01 && n > 0 ? "<$0.01" : `$${n.toFixed(2)}`;
}

export function ago(iso: string, now = Date.now()) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function duration(from: string, to?: string, now = Date.now()) {
  const ms = (to ? new Date(to).getTime() : now) - new Date(from).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

export const ACTION_LABEL: Record<string, string> = {
  renew: "Renew",
  non_renew: "Non-renew",
  terminate: "Terminate",
  amend: "Amend",
  convert_to_permanent: "Make permanent",
  confirm_probation: "Confirm probation",
  review: "Needs review",
  no_action: "No action",
};

export const CONTRACT_TYPE: Record<string, string> = {
  fixed_term: "Fixed-term",
  indefinite: "Indefinite",
  at_will: "At-will",
  contractor: "Contractor",
  not_applicable: "—",
};

export const COUNTRY: Record<string, string> = {
  US: "United States",
  DE: "Germany",
  ES: "Spain",
  FR: "France",
  IN: "India",
  JP: "Japan",
  GB: "United Kingdom",
};

// ── primitives ────────────────────────────────────────────────────────────

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cx("rounded-xl border border-line bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)]", className)}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, aside, className }: { children: ReactNode; aside?: ReactNode; className?: string }) {
  return (
    <div className={cx("mb-3 flex items-center justify-between gap-3", className)}>
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">{children}</h2>
      {aside}
    </div>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "dark";

export function Button({
  variant = "secondary",
  busy,
  icon,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean; icon?: ReactNode }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition",
        "disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand",
        variant === "primary" && "bg-brand text-white shadow-sm hover:bg-brand-600",
        variant === "dark" && "bg-ink-950 text-white shadow-sm hover:bg-ink-800",
        variant === "secondary" && "border border-line bg-white text-ink-900 hover:bg-slate-50",
        variant === "ghost" && "text-ink-700 hover:bg-slate-100",
        variant === "danger" && "border border-red-200 bg-white text-red-700 hover:bg-red-50",
        className,
      )}
    >
      {busy ? <LoaderCircle className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function Badge({ tone = "slate", children, className }: { tone?: string; children: ReactNode; className?: string }) {
  const tones: Record<string, string> = {
    slate: "bg-slate-100 text-slate-700 ring-slate-200",
    blue: "bg-brand-50 text-brand-600 ring-blue-200",
    green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    amber: "bg-amber-50 text-amber-800 ring-amber-200",
    red: "bg-red-50 text-red-700 ring-red-200",
    rose: "bg-rose-600 text-white ring-rose-600",
    sky: "bg-sky-50 text-sky-700 ring-sky-200",
    violet: "bg-violet-50 text-violet-700 ring-violet-200",
    dark: "bg-ink-900 text-white ring-ink-900",
  };
  return (
    <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11.5px] font-medium ring-1 ring-inset", tones[tone] ?? tones.slate, className)}>
      {children}
    </span>
  );
}

export const URGENCY_TONE: Record<Urgency, string> = {
  missed: "rose",
  critical: "red",
  soon: "amber",
  upcoming: "sky",
  ok: "green",
  none: "slate",
};
export const URGENCY_COLOR: Record<Urgency, string> = {
  missed: "#e11d48",
  critical: "#dc2626",
  soon: "#d97706",
  upcoming: "#0284c7",
  ok: "#059669",
  none: "#94a3b8",
};
export const URGENCY_LABEL: Record<Urgency, string> = {
  missed: "Missed",
  critical: "Critical",
  soon: "Soon",
  upcoming: "Upcoming",
  ok: "On track",
  none: "No deadline",
};

export function UrgencyBadge({ urgency, days }: { urgency: Urgency; days: number | null }) {
  return (
    <Badge tone={URGENCY_TONE[urgency]} className="tabular">
      {urgency === "none" ? "No deadline" : urgency === "missed" ? `Missed · ${-(days ?? 0)}d ago` : daysLabel(days)}
    </Badge>
  );
}

export function CountryChip({ code }: { code: string | null }) {
  if (!code) return null;
  return (
    <span
      title={COUNTRY[code] ?? code}
      className="inline-flex h-[18px] min-w-[26px] items-center justify-center rounded-[4px] bg-ink-900 px-1 text-[10px] font-semibold tracking-wider text-white"
    >
      {code}
    </span>
  );
}

export function RiskDots({ risks }: { risks: { high: number; medium: number; low: number } }) {
  const total = risks.high + risks.medium + risks.low;
  if (!total) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5" title={`${risks.high} high · ${risks.medium} medium · ${risks.low} low`}>
      {risks.high > 0 && <Badge tone="red">{risks.high} high</Badge>}
      {risks.medium > 0 && <Badge tone="amber">{risks.medium} med</Badge>}
      {risks.low > 0 && !risks.high && !risks.medium && <Badge>{risks.low} low</Badge>}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={cx("size-4 animate-spin text-brand", className)} />;
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-3 rounded-xl bg-slate-100 p-3 text-slate-500">{icon}</div>
      <div className="font-medium text-ink-900">{title}</div>
      {children && <div className="mt-1 max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: string }) {
  return (
    <Card className="px-4 py-3.5">
      <div className="text-[12px] font-medium text-muted">{label}</div>
      <div className={cx("mt-1 text-[26px] font-semibold tracking-tight tabular", tone)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

// ── markdown with contract citations ──────────────────────────────────────

export function Markdown({ text, className = "prose-memo" }: { text: string; className?: string }) {
  const linked = text.replace(/\[(C-\d{3})\](?!\()/g, "[$1](#/portfolio/$1)");
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const m = href?.match(/^#\/portfolio\/(C-\d{3})$/);
            if (m)
              return (
                <button
                  type="button"
                  onClick={() => navigate(`portfolio/${m[1]}`)}
                  className="mx-0.5 inline-flex items-center rounded bg-brand-50 px-1.5 py-[1px] align-baseline font-sans text-[11.5px] font-semibold text-brand-600 ring-1 ring-blue-200 hover:bg-blue-100"
                >
                  {children}
                </button>
              );
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-brand underline underline-offset-2">
                {children}
              </a>
            );
          },
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
      {items.map((it) => (
        <button
          key={it.value}
          type="button"
          onClick={() => onChange(it.value)}
          className={cx(
            "rounded-md px-3 py-1.5 text-[13px] font-medium transition",
            value === it.value ? "bg-white text-ink-950 shadow-sm" : "text-muted hover:text-ink-900",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
