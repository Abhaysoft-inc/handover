export type Urgency = "missed" | "critical" | "soon" | "upcoming" | "ok" | "none";
export type Action =
  | "renew"
  | "non_renew"
  | "terminate"
  | "amend"
  | "convert_to_permanent"
  | "confirm_probation"
  | "review"
  | "no_action";

export interface Status {
  mode: "live" | "simulate";
  mode_setting: string;
  has_credentials: boolean;
  provider: "anthropic" | "gemini" | "groq" | "openai_compat" | "none";
  provider_label: string;
  model: string;
  effort: string;
  company: string;
  counsel: { name: string; email: string };
  hr_email: string;
  today: string;
  drive: { source: string; label: string; requested: string; google_configured: boolean; google_connected: boolean };
  mail: { transport: "sandbox" | "smtp"; redirect: string | null };
  running: { run_id: string; kind: string; mode: string }[];
}

export interface Stage {
  key: string;
  title: string;
  status: "todo" | "active" | "done" | "running";
  detail: string;
}

export interface Overview {
  documents: { total: number; analyzed: number; skipped: number; scanned: number; employment: number; languages: string[] };
  contracts: { total: number; countries: string[]; urgency: Partial<Record<Urgency, number>> };
  risks: { high: number; medium: number; low: number };
  issues: number;
  decisions: Record<string, number>;
  drafts: Record<string, number>;
  emails: number;
  reminders: { scheduled: number; sent: number };
  filings: number;
  kb_records: number;
  stages: Stage[];
  briefing: { markdown: string; created_at: string; run_id: string } | null;
}

export interface Decision {
  id: number;
  action: Action;
  status: string;
  params: Record<string, string>;
}

export interface ContractSummary {
  id: string;
  employee_name: string;
  employee_email: string | null;
  job_title: string | null;
  department: string | null;
  employer_entity: string | null;
  country: string | null;
  work_location: string | null;
  governing_law: string | null;
  language: string | null;
  language_name: string | null;
  contract_type: string | null;
  start_date: string | null;
  end_date: string | null;
  action_deadline: string | null;
  action_deadline_reason: string | null;
  compensation_text: string | null;
  non_compete: string | null;
  recommended_action: Action | null;
  recommendation_rationale: string | null;
  status_note: string | null;
  summary: string | null;
  notice_terms: string | null;
  renewal_terms: string | null;
  probation_end_date: string | null;
  auto_renewal: boolean;
  signed: number | null;
  urgency: Urgency;
  days_left: number | null;
  risks: { high: number; medium: number; low: number };
  decision: Decision | null;
  drafts: { id: string; title: string; status: string }[];
  primary_document: { id: string; name: string; path: string; is_scanned: number; language_name: string } | null;
}

export interface Clause {
  heading: string;
  original_text: string;
  english_text: string;
  page: number;
}
export interface RiskFlag {
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
}
export interface DocRow {
  id: string;
  name: string;
  path: string;
  mime_type?: string;
  status: string;
  skip_reason?: string | null;
  kind: string | null;
  is_employment?: number | null;
  language?: string | null;
  language_name: string | null;
  is_scanned: number | null;
  title?: string | null;
  summary: string | null;
  pages: number | null;
  error?: string | null;
}
export interface Issue {
  id: number;
  contract_id: string | null;
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  employee_name?: string | null;
}
export interface Reminder {
  id: number;
  contract_id: string;
  draft_id: string | null;
  due_date: string;
  recipients: string[];
  subject: string;
  message: string;
  kind: string;
  status: string;
  created_by: string;
  employee_name?: string;
}
export interface ContractDetail extends ContractSummary {
  notice_period_days: number | null;
  termination_terms: string | null;
  confidence: string | null;
  key_dates: { date: string; label: string }[];
  key_clauses: Clause[];
  risk_flags: RiskFlag[];
  documents: DocRow[];
  issues: Issue[];
  activity: { ts: string; kind: string; text: string }[];
  reminders: Reminder[];
}

export interface Draft {
  id: string;
  contract_id: string;
  decision_id: number;
  doc_type: string;
  title: string;
  language: string;
  body_markdown: string;
  english_summary: string;
  status: string;
  version: number;
  docx_path: string | null;
  pdf_path: string | null;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
  compliance_notes: string[];
  delivery: {
    method: string;
    to: string[];
    cc: string[];
    subject: string;
    body: string;
    signature_required_from: string[];
    sign_by_date: string;
  };
  employee_name: string;
  country: string | null;
  contract_language: string | null;
}

export interface Email {
  id: number;
  kind: string;
  contract_id: string | null;
  draft_id: string | null;
  to: string[];
  cc: string[];
  subject: string;
  body: string;
  attachments: string[];
  status: string;
  transport: string;
  created_at: string;
  employee_name: string | null;
}

export interface Filing {
  id: number;
  draft_id: string;
  contract_id: string;
  folder_path: string;
  file_name: string;
  web_link: string | null;
  created_at: string;
  employee_name: string | null;
}

export interface RunEvent {
  run_id?: string;
  kind?: string;
  seq?: number;
  ts: string;
  type: string;
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface Run {
  id: string;
  kind: string;
  status: string;
  mode: string;
  title: string;
  started_at: string;
  finished_at: string | null;
  summary: string | null;
  error: string | null;
  usage: { cost_usd: number | null; requests: number; input_tokens: number; output_tokens: number } | null;
  events?: RunEvent[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(path, {
    method: init?.method ?? "GET",
    headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: "POST", body });
