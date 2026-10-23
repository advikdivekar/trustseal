import { AnimatePresence, motion } from "motion/react";
import type { LedgerEvent, Snapshot } from "../lib/types";
import { Scramble } from "./Wipe";

const NAMES: Record<string, string> = {
  SHIPMENT_CREATED: "Order registered", PRODUCT_IDENTIFIED: "Product recognised", SEAL_BOUND: "Seal bound to parcel", EVIDENCE_CAPTURED: "Photo captured",
  AI_CHECK_COMPLETED: "Checks finished", STAGE_LOCKED: "Stage locked", HANDOVER_CONFIRMED: "Handover confirmed",
  SEAL_SCANNED: "Seal scanned", CONSUMER_SCANNED: "Parcel scanned by buyer", CHECK_FAILED: "Check failed",
  HOLD_PLACED: "Verification hold", EXCEPTION_REPORTED: "Exception reported", ACCEPTED: "Accepted by buyer",
  PAYMENT_STATE_CHANGED: "Payment", DOOR_ARRIVED: "Delivery person at the door", WALLET_SHORT: "Wallet too low", PAYMENT_RESERVED: "Payment reserved",
  CODE_WRONG: "Wrong handover code", DELIVERY_CONFIRMED: "Handover confirmed", PAYMENT_REVERSED: "Payment returned to wallet",
  HANDOVER_CANCELLED: "Handover cancelled", HANDOVER_EXPIRED: "Door visit timed out", HANDOVER_FAILED: "Delivery failed repeatedly", DISPUTE_OPENED: "Complaint opened", REVIEW_DECISION: "Decision", UNBOX_CHECKED: "Product scanned at home", UNBOX_VERIFIED: "Product verified by buyer", UNBOX_UNSURE: "Product check inconclusive",
};
export const eventName = (e: LedgerEvent) => NAMES[e.event_type] ?? e.event_type;
export const eventDetail = (e: LedgerEvent) => {
  const p = e.payload;
  if (e.event_type === "PRODUCT_IDENTIFIED") return String(p.identity?.product_name ?? "");
  if (e.event_type === "EVIDENCE_CAPTURED") return String(p.purpose).replace("_", " ");
  if (e.event_type === "SEAL_SCANNED" || e.event_type === "CONSUMER_SCANNED") return p.match ? `seal ${p.hint} matches` : `seal ${p.hint} does not match`;
  if (e.event_type === "SEAL_BOUND") return `seal ${p.hint} read from the parcel${p.how ? " (" + p.how + ")" : ""}`;
  if (e.event_type === "DISPUTE_OPENED") return `${p.ticket}${p.auto ? " · filed automatically" : ""}: ${p.reason}`;
  if (e.event_type === "UNBOX_CHECKED") return p.outcome === "MATCH" ? "barcode and product match" : p.outcome === "MISMATCH" ? "does not match what was registered" : "could not be sure";
  if (e.event_type === "CHECK_FAILED" || e.event_type === "HOLD_PLACED") return `${p.failed_stage?.toLowerCase()}: ${p.reason}`;
  if (e.event_type === "PAYMENT_STATE_CHANGED") return `${String(p.from).toLowerCase()} to ${String(p.to).toLowerCase()}`;
  if (e.event_type === "WALLET_SHORT") return `balance ₹${Math.round(p.balance)}, short by ₹${Math.round(p.short_by)}`;
  if (e.event_type === "PAYMENT_RESERVED") return `₹${Math.round(p.amount)} set aside from the wallet`;
  if (e.event_type === "PAYMENT_REVERSED") return p.why;
  if (e.event_type === "DOOR_ARRIVED") return String(p.agent);
  if (e.event_type === "CODE_WRONG") return `attempt ${p.attempt} of 3`;
  if (e.event_type === "DELIVERY_CONFIRMED") return `${p.agent} · ₹${Math.round(p.amount)} paid out`;
  if (e.event_type === "HANDOVER_CANCELLED" || e.event_type === "HANDOVER_EXPIRED") return p.why;
  if (e.event_type === "HANDOVER_CONFIRMED") return `${p.from.toLowerCase()} to ${p.to.toLowerCase()}`;
  if (e.event_type === "AI_CHECK_COMPLETED") return p.results.map((r: any) => `${r.check_type} ${r.status}`).join(", ");
  return p.reason || p.note || "";
};
const bad = (t: string) => ["CHECK_FAILED", "HOLD_PLACED", "DISPUTE_OPENED", "WALLET_SHORT", "CODE_WRONG", "PAYMENT_REVERSED", "HANDOVER_FAILED", "HANDOVER_CANCELLED", "UNBOX_UNSURE"].includes(t);

export function EventRow({ e, fresh }: { e: LedgerEvent; fresh?: boolean }) {
  return (
    <motion.div className="ts-ev" layout initial={fresh ? { opacity: 0, x: 24 } : false} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.5 }}>
      <div className="no">#{e.sequence_no}</div>
      <div>
        <div className="ts-between">
          <span style={bad(e.event_type) ? { color: "var(--ts-red)" } : undefined}>{eventName(e)}</span>
          <span className="ts-tiny">{e.actor_role.toLowerCase()}</span>
        </div>
        <div className="ts-why">{eventDetail(e)}</div>
        <div className="ts-hash"><Scramble text={e.event_hash.slice(0, 32)} run={!!fresh} /></div>
      </div>
    </motion.div>
  );
}

export function ChainBadge({ snap }: { snap: Snapshot | null }) {
  if (!snap) return null;
  const ok = snap.chain.ok;
  return (
    <span className={"ts-chip " + (ok ? "ok" : "bad")} title={ok ? `Head ${snap.chain.head?.slice(0, 12)}` : snap.chain.reason}>
      {ok ? `Chain verified · ${snap.chain.checked}` : `Broken at #${snap.chain.broken_at}`}
    </span>
  );
}

export function LedgerSheet({ snap, onClose }: { snap: Snapshot; onClose: () => void }) {
  return (
    <motion.div className="ts-sheet" initial="h" animate="s" exit="h">
      <motion.div className="back" variants={{ h: { opacity: 0 }, s: { opacity: 1 } }} onClick={onClose} />
      <motion.div className="panel" variants={{ h: { y: "100%" }, s: { y: 0 } }} transition={{ type: "spring", damping: 32, stiffness: 300 }}
                  drag="y" dragConstraints={{ top: 0, bottom: 0 }} dragElastic={{ top: 0, bottom: 0.6 }} onDragEnd={(_, i) => i.offset.y > 120 && onClose()}>
        <div className="ts-between" style={{ marginBottom: 6 }}>
          <h3 className="ts-h2">Shared <em>history</em></h3>
          <ChainBadge snap={snap} />
        </div>
        <p className="ts-why" style={{ marginTop: 0 }}>Every phone sees the same record. Each entry is hashed to the one before it, so nothing can be changed quietly.</p>
        <AnimatePresence initial={false}>
          {[...snap.events].reverse().map((e, i) => <EventRow key={e.event_id} e={e} fresh={i === 0} />)}
        </AnimatePresence>
      </motion.div>
    </motion.div>
  );
}
