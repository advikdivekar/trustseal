import { AnimatePresence, motion } from "motion/react";
import { LiveScene, usePortrait } from "../components/Live";
import { ChainBadge, EventRow } from "../components/Ledger";
import { Wipe } from "../components/Wipe";
import { Brand } from "../components/Brand";
import { useLive } from "../lib/store";
import { useInbox } from "../lib/inbox";
import { ROLE_LABEL, STAGES } from "../lib/types";

/* The projector view: the audience watches the parcel travel the route while four phones act. */
export function Screen() {
  const { snap } = useLive();
  const { screen } = useInbox();
  const portrait = usePortrait();
  const cps = snap?.checkpoints ?? [];
  const photo = cps[0]?.evidence.find((e) => e.purpose === "product_front");
  const id = snap?.shipment.identity;
  return (
    <div className="ts-stage">
      <LiveScene snap={snap} fit={portrait ? "meet" : "cover"} />
      <div className="ts-big-top">
        <div>
          <Brand />
          <div className="ts-row" style={{ marginTop: "3vh", alignItems: "flex-start", gap: 20 }}>
            {photo && <motion.img initial={{ opacity: 0, scale: .9 }} animate={{ opacity: 1, scale: 1 }} src={photo.url} alt="" style={{ width: "9vw", height: "9vw", minWidth: 90, minHeight: 90, objectFit: "cover", border: "1px solid var(--ts-ink-deep)" }} />}
            <div>
              <div className="ts-tiny">{snap ? `${snap.shipment.order_ref}${id?.brand ? " · " + id.brand : ""}` : "TrustSeal"}</div>
              <h1 className="ts-h1" style={{ fontSize: "clamp(30px, 4vw, 60px)", marginTop: 4 }}>
                {snap ? <><Wipe text={snap.shipment.product_name} /><br /><em><Wipe text={stateLine(snap.shipment.state)} delay={0.3} /></em></> : <Wipe text="Waiting for an order" />}
              </h1>
              {snap && <div className="ts-row" style={{ flexWrap: "wrap", marginTop: 12 }}><ChainBadge snap={snap} />
                <span className="ts-chip">{({ PENDING: "pay at the door", RESERVED: "payment reserved", RELEASED: "paid", REFUNDED: "refunded", CANCELLED: "not charged", DISPUTE_HOLD: "payment on hold" } as Record<string, string>)[snap.payment.state]}</span>
                {snap.handover?.state === "OPEN" && <span className="ts-chip ok">delivery person at the door</span>}
                {snap.handover?.short_by ? <span className="ts-chip bad">wallet short by ₹{Math.round(snap.handover.short_by)}</span> : null}
                {snap.seal.registered && <span className="ts-chip">seal {snap.seal.hint}</span>}
                {snap.complaint?.status === "OPEN" && <span className="ts-chip bad">complaint {snap.complaint.ticket}</span>}
                {snap.case.open && <span className="ts-chip bad">waiting on {snap.case.waiting_on?.name}</span>}</div>}
            </div>
          </div>
        </div>
        {snap && (
          <div className="ts-ticker">
            <AnimatePresence initial={false}>
              {[...snap.events].slice(-3).reverse().map((e, i) => (
                <motion.div key={e.event_id} className="ts-tick" layout><EventRow e={e} fresh={i === 0} /></motion.div>))}
            </AnimatePresence>
          </div>
        )}
      </div>
      <div className="ts-feed">
        <AnimatePresence initial={false}>
          {screen.slice(0, 4).map((n) => (
            <motion.div key={n.id} layout initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className={"ts-feed-row " + n.severity}>
              <b>{n.to_name}</b> <span className="ts-tiny" style={{ color: "inherit", opacity: .7 }}>{n.channel === "SMS" ? "SMS" : "email"}</span> {n.subject}
            </motion.div>))}
        </AnimatePresence>
      </div>
      <div className="ts-big-cards">
        {STAGES.map((role, i) => {
          const c = cps[i];
          const st = c?.state ?? "PENDING";
          const cls = st === "LOCKED" ? "locked" : st === "FAILED" ? "failed" : "";
          return (
            <motion.div key={role} className={"ts-bigcard " + cls} layout animate={st === "FAILED" ? { x: [0, -6, 6, -4, 0] } : { x: 0 }}>
              <div className="ts-between"><span className="t">{i + 1}. {ROLE_LABEL[role]}</span>
                <span className="ts-tiny" style={{ color: "inherit", opacity: .8 }}>{st === "LOCKED" ? "locked" : st === "FAILED" ? "failed" : st === "IN_PROGRESS" ? "working" : "waiting"}</span></div>
              <div className="ts-small" style={{ opacity: .9, marginTop: 6 }}>
                {c?.actor_name && <b>{c.actor_name}{c.actor_org ? ` · ${c.actor_org}` : ""}<br /></b>}
                {st === "LOCKED" ? `${c.evidence.length} photo${c.evidence.length === 1 ? "" : "s"} locked${c.locked_at ? " · " + new Date(c.locked_at).toLocaleTimeString() : ""}`
                  : st === "FAILED" ? c.checks.find((k) => k.status === "FAIL")?.explanation ?? "Seal does not match"
                  : st === "IN_PROGRESS" ? `${c.evidence.length} of ${c.required.length + (i ? 1 : 0)} steps` : "Waiting for the previous stage"}
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

const stateLine = (s: string) => ({
  CREATED: "registered", IN_TRANSIT: "on its way", AWAITING_CONSUMER: "at your door", ACCEPTED: "delivered and paid",
  ON_HOLD: "on hold. A stage failed.", UNDER_REVIEW: "under investigation", DISPUTED: "complaint open", RESOLVED_REFUND: "refunded", RESOLVED_REJECTED: "dispute closed", RESOLVED_REPLACEMENT: "replacement requested", RETURNED: "returned to the seller",
}[s] ?? s);
