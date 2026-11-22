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
  module?: string;
  source_file?: string;
  action_status?: {
    status: "sent" | "replied";
    at: string;
    reply?: string;
    reply_from?: string;
    reply_text?: string;
  };
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

export interface PartRow {
  part: string;
  description?: string;
  editions: string[];
  needed: number;
  usable: number;
  gap: number;
  runs_dry: string | null;
}

export interface State {
  source: string;
  mode: "ask" | "autonomous";
  now?: string;
  hours_left?: number;
  packing_ends?: string;
  editions: EditionRow[];
  parts: PartRow[];
  cards: Card[];
}

export interface FeedEvent {
  at: string;
  actor: string;
  text: string;
  ref?: string;
}

export interface NetNode {
  id: string;
  kind: "site" | "supplier" | "port";
  name: string;
  lat: number;
  lon: number;
  country?: string;
  mode?: string;
}

export interface NetRoute {
  id: string;
  origin: string;
  destination: string;
  mode: string;
  transit_days: number | null;
  via: string[];
  path: string[];
}

export interface NetShipment {
  id: string;
  supplier: string;
  mode: string;
  route: string;
  path: string[];
  etd: string | null;
  eta: string | null;
  transit_days: number | null;
  status: string;
  updated_at: string | null;
  parts: string[];
  po_numbers: string[];
  note: string;
}

export interface NetProduct {
  id: string;
  name: string;
  day_one: number | null;
  parts: { part: string; qty_per: number | null; description: string; category: string }[];
}

export interface Network {
  source: string;
  stale_hours: number;
  nodes: NetNode[];
  routes: NetRoute[];
  shipments: NetShipment[];
  part_suppliers: Record<string, { supplier: string; share: number | null }[]>;
  products: NetProduct[];
}
