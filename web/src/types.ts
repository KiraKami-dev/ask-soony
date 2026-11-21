export type Evidence =
  | string
  | { doc_path?: string; page?: number; quote?: string; language?: string; translation?: string | null };

export type Option =
  | string
  | { action: string; cost?: string; effect?: string; recommended?: boolean };

export interface Message {
  to_role: string;
  to_email: string;
  subject: string;
  body: string;
}

export type ActionKind = "allow_once" | "allow_always" | "edit" | "dismiss";

export interface Card {
  id: string;
  title: string;
  edition: string;
  at_risk: number;
  part: string;
  system_says: string;
  paper_says: string;
  evidence: Evidence[];
  options: Option[];
  decision: unknown;
  messages: Message[];
  at_risk_if_acted: number;
  act_by: string | null;
  runs_dry: string | null;
  blocked_only: boolean;
  stage?: "before_arrival" | "after_dock";
  human_decision?: { action: ActionKind; note?: string | null; ts: string };
}

export interface EditionRow {
  product: string;
  name: string;
  launch_date: string | null;
  day_one: number;
  packed: number;
  rate_per_hour: number;
  remaining: number;
  at_risk: number;
}

export interface State {
  source: string;
  mode: "ask" | "autonomous";
  now?: string;
  hours_left?: number;
  packing_ends?: string;
  editions: EditionRow[];
  parts: unknown[];
  cards: Card[];
}

export interface FeedEvent {
  ts: string;
  agent: string;
  text: string;
}
