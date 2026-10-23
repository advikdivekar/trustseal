export type Role = "SELLER" | "WAREHOUSE" | "DELIVERY" | "CONSUMER" | "REVIEWER" | "ADMIN";
export const STAGES = ["SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER"] as const;
export type Stage = (typeof STAGES)[number];

export type Session = { token: string; participant: { id: string; role: Role; name: string; org: string } };
export type WalletTxn = { id: number; account_id: string; kind: "TOPUP" | "RESERVE" | "REVERSAL" | "PAYOUT" | "DUE" | "REFUND" | "CLAWBACK"; amount: number; reference: string; note: string; created_at: string; balance_after?: number };
export type Handover = {
  state: "OPEN" | "PAID" | "CONFIRMED" | "CANCELLED" | "EXPIRED" | "FAILED"; agent_name: string; agent_phone: string; buyer_phone: string;
  opened_at: string; expires_at: string; reserve_expires_at: string; short_by: number; attempts: number; failed_sessions: number; max_failed: number;
};
export type Notice = { id: number; shipment_id: string; to_id: string; to_name: string; to_role: Role; channel: "SMS" | "EMAIL"; severity: "info" | "action" | "alert";
  subject: string; body: string; why: string; event_type: string; created_at: string; read_at: string | null };
export type Account = { id: string; role: Role; name: string; org: string; busy: boolean };
export type Config = { rehearsal: boolean; ai_mode: "live" | "mock"; model: string; accounts: Account[] };
export type Identity = {
  product_name: string; brand?: string; variant?: string; category?: string; net_quantity?: string;
  colours?: string[]; visible_text?: string[]; confidence?: number; model?: string;
  barcode?: { code: string; format: string; isbn: boolean; how?: string }; name_source?: string; barcode_sha256?: string;
  verification?: { status: "match" | "mismatch" | "unverified"; detail: string; catalog_title?: string; catalog_by?: string; source?: string };
};

export type Check = {
  id: string; evidence_id: string; purpose: string; check_type: string;
  status: "PASS" | "REVIEW" | "FAIL"; overridden?: number; score: number; threshold: number; explanation: string; model_name: string;
};
export type Evidence = { id: string; purpose: string; kind: string; sha256: string; url: string; captured_at: string };
export type Checkpoint = {
  id: string; sequence: number; role: Stage; state: "PENDING" | "IN_PROGRESS" | "LOCKED" | "FAILED";
  seal_scanned: number; locked_at: string | null; required: string[]; evidence: Evidence[]; checks: Check[];
  latest_checks: Record<string, Check[]>; actor_name?: string; actor_org?: string;
};
export type LedgerEvent = {
  event_id: string; sequence_no: number; actor_role: string; event_type: string; payload: Record<string, any>;
  timestamp_server: string; prev_hash: string; event_hash: string;
};
export type CaseInfo = {
  open: boolean; state?: string; kind?: "HOLD" | "DISPUTE"; stage?: Stage; reason?: string; last_verified?: string | null;
  handover?: boolean; attempts_left?: number; reported?: boolean; waiting_on?: { role: Role; name: string }; instruction?: string | null;
  opened_at?: string; steps?: { label: string; done: boolean }[];
  ticket?: string; auto?: boolean; source?: string; paid?: boolean; triage?: Triage | null;
};
export type Triage = { auto: boolean; cause: string; suggested: string; amount: number; limit: number; facts: { label: string; status: "PASS" | "REVIEW" | "FAIL"; text: string }[] };
export type Complaint = {
  ticket: string; auto: boolean; auto_resolved: boolean; reason: string; category: string; source: string; paid: boolean; triage: Triage | null;
  opened_at: string; status: "OPEN" | "CLOSED"; decision: { decision: string; reason: string; auto: boolean; at: string } | null;
};
export type Snapshot = {
  case: CaseInfo;
  shipment: { id: string; order_ref: string; product_name: string; declared_value: number; expected_colour: string;
    payment_mode: string; risk_tier: string; state: string; identity: Identity };
  checkpoints: Checkpoint[];
  payment: { state: "PENDING" | "RESERVED" | "RELEASED" | "REFUNDED" | "CANCELLED" | "DISPUTE_HOLD"; amount: number; mode: string; txns: WalletTxn[] };
  handover: Handover | null; now: string;
  seal: { registered: boolean; hint: string; how: string };
  complaint: Complaint | null;
  events: LedgerEvent[];
  chain: { ok: boolean; checked: number; head?: string; broken_at?: number; reason?: string; event_type?: string };
  settings: { scenario: "A" | "B" | "C"; ai_mode: "live" | "mock"; rehearsal: boolean };
};
export type ShipmentListItem = { id: string; order_ref: string; product_name: string; state: string };

export const ROLE_LABEL: Record<Role, string> = {
  SELLER: "Seller", WAREHOUSE: "Warehouse", DELIVERY: "Delivery", CONSUMER: "Buyer", REVIEWER: "Reviewer", ADMIN: "Presenter",
};
export const STEP_LABEL: Record<Stage, string> = { SELLER: "Packed", WAREHOUSE: "Warehouse", DELIVERY: "Delivery", CONSUMER: "Received" };
export const PURPOSE_LABEL: Record<string, string> = {
  product_front: "Product, front", product_side: "Product, side", seal_side: "Sealed side",
  parcel: "Parcel at the door", received_product: "Product you received", packing_video: "Packing video",
  unbox_front: "Product you took out", unbox_barcode: "Barcode you scanned",
};
