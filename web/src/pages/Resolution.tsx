import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Brand } from "../components/Brand";
import { Bell, InboxSheet, Toasts } from "../components/Inbox";
import { LiveScene } from "../components/Live";
import { EventRow } from "../components/Ledger";
import { Slide } from "../components/Slide";
import { Wipe } from "../components/Wipe";
import { api, loadSession, signOut } from "../lib/api";
import { useLive } from "../lib/store";
import { PURPOSE_LABEL, ROLE_LABEL, type Check, type Snapshot, type Stage } from "../lib/types";
import { CaseBanner, ComplaintCard, ProductCard, Thumb, money } from "./RoleApp";

type CaseRow = { id: string; order_ref: string; product_name: string; state: string; created_at: string; case: Snapshot["case"] };

const HOLD_OPTIONS = [
  { k: "resume", label: "Clear and continue", help: "False alarm. You checked the parcel and it is fine. The stage reopens and the parcel moves on." },
  { k: "recheck", label: "Ask for a recheck", help: "Send it back to the person at this stage with an instruction, and fresh tries.", needs: true },
  { k: "reseal", label: "Re-seal and continue", help: "The parcel was inspected and closed again. The old seal is voided; a new seal goes on and the person holding the parcel scans it to register it." },
  { k: "return", label: "Return to seller", help: "The parcel can't be trusted. It goes back and the buyer is refunded.", danger: true },
] as const;
const DISPUTE_OPTIONS = [
  { k: "refund", label: "Refund the buyer", help: "The buyer is right. If the seller was already paid, the money is taken back from them and returned to the buyer.", danger: true },
  { k: "replace", label: "Send a replacement", help: "The seller must send the right product. Funds stay held for it." },
  { k: "reject", label: "Reject the complaint", help: "The product was fine. The seller keeps the payment." },
  { k: "return", label: "Return to seller", help: "Collect the parcel and send it back. The buyer is refunded.", danger: true },
] as const;

export function ReviewerApp() {
  const session = loadSession()!;
  const nav = useNavigate();
  const { pulse, snap: newest } = useLive();
  const [rows, setRows] = useState<CaseRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [inbox, setInbox] = useState(false);
  const refresh = useCallback(() => api<CaseRow[]>("/cases").then(setRows).catch(() => {}), []);
  useEffect(() => { refresh(); }, [pulse, newest?.shipment.state, refresh]);
  return (
    <div className="ts-app"><div className="ts-app-col">
      <Toasts onOpen={() => setInbox(true)} />
      <div className="ts-app-bar"><Brand />
        <div className="ts-row"><Bell onOpen={() => setInbox(true)} />
          <button className="ts-chip" onClick={async () => { await signOut(); nav("/"); }}>{session.participant.name.split(" ")[0]} · sign out</button></div></div>
      <div className="ts-app-scene"><LiveScene snap={newest} still fit="strip" /></div>
      <div className="ts-app-main">
        {open ? <CaseView sid={open} back={() => { setOpen(null); refresh(); }} pulse={pulse} /> : (
          <>
            <div className="ts-tiny">{session.participant.org}</div>
            <h2 className="ts-h2"><Wipe text="Resolution" /> <em><Wipe text="center" delay={0.2} /></em></h2>
            <p className="ts-why">Every parcel that fails a check or gets a buyer complaint lands here. Read the evidence, then decide. Every decision needs a reason and goes on the shared record.</p>
            {!rows.length ? (
              <div className="ts-card" style={{ marginTop: 18 }}><b>No open cases.</b><div className="ts-why">When a seal does not match, a photo fails, or a buyer disputes a parcel, it appears here at once.</div></div>
            ) : (
              <ul className="ts-list" style={{ marginTop: 18 }}>
                {rows.map((r) => (
                  <li key={r.id}><button className="ts-case-row" onClick={() => setOpen(r.id)}>
                    <div className="ts-between"><b>{r.product_name}</b><span className={"ts-chip " + (r.case.state === "ON_HOLD" ? "warn" : "bad")}>{r.case.kind === "DISPUTE" ? "complaint" : r.case.state === "ON_HOLD" ? "with " + ROLE_LABEL[r.case.stage as Stage].toLowerCase() : "needs you"}</span></div>
                    <div className="ts-tiny">{r.order_ref} · {ROLE_LABEL[r.case.stage as Stage]} stage</div>
                    <div className="ts-why bad">{r.case.reason}</div>
                  </button></li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
      <AnimatePresence>{inbox && <InboxSheet onClose={() => setInbox(false)} />}</AnimatePresence>
    </div></div>
  );
}

function CaseView({ sid, back, pulse }: { sid: string; back: () => void; pulse: number }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [pick, setPick] = useState<string>("");
  const [reason, setReason] = useState("");
  const [instruction, setInstruction] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api<Snapshot>(`/shipments/${sid}`).then(setSnap).catch((e) => setErr(e.message)); }, [sid, pulse]);
  if (!snap) return <p className="ts-why">{err || "Loading the case…"}</p>;
  const c = snap.case;
  if (!c.open) return <div><button className="ts-chip" onClick={back}>← All cases</button><h2 className="ts-h2" style={{ marginTop: 14 }}>Case <em>closed</em></h2>
    <ProductCard snap={snap} /><p className="ts-why">This shipment is now {snap.shipment.state.toLowerCase().replace("_", " ")}. Payment: {snap.payment.state.toLowerCase().replace("_", " ")}.</p></div>;

  const cp = snap.checkpoints.find((x) => x.role === c.stage)!;
  const ref = snap.checkpoints[0].evidence.find((e) => e.purpose === "product_front");
  const options = c.kind === "DISPUTE" ? DISPUTE_OPTIONS : HOLD_OPTIONS;
  const chosen = options.find((o) => o.k === pick);
  const scans = snap.events.filter((e) => ["SEAL_SCANNED", "CONSUMER_SCANNED"].includes(e.event_type) && e.actor_role === c.stage);
  const bad = cp.checks.filter((x: Check) => x.status !== "PASS" && x.check_type !== "image_quality");
  const decide = async () => {
    try { setErr(""); setSnap(await api<Snapshot>(`/review/${sid}/decision`, { body: { decision: pick, reason, instruction } })); setPick(""); setReason(""); setInstruction(""); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <div>
      <button className="ts-chip" onClick={back}>← All cases</button>
      <h2 className="ts-h2" style={{ marginTop: 14 }}>Case at the <em>{ROLE_LABEL[c.stage!].toLowerCase()}</em> stage</h2>
      <ProductCard snap={snap} />
      <CaseBanner snap={snap} />
      <ComplaintCard snap={snap} />

      <div className="ts-tiny" style={{ margin: "18px 0 8px" }}>Evidence</div>
      <div className="ts-compare">
        <figure>{ref ? <img src={ref.url} alt="Seller reference" /> : <div className="ts-card" />}<figcaption>Seller registered</figcaption></figure>
        {cp.evidence.length ? (
          <figure>{(() => { const e = cp.evidence.at(-1)!; return e.kind === "video" ? <video src={e.url} controls muted playsInline style={{ width: "100%", aspectRatio: "4/3", objectFit: "cover", border: "1px solid var(--ts-ink-deep)" }} /> : <img src={e.url} alt="" />; })()}
            <figcaption>{ROLE_LABEL[c.stage!]}: {PURPOSE_LABEL[cp.evidence.at(-1)!.purpose]}</figcaption></figure>
        ) : <div className="ts-card"><span className="ts-why">{ROLE_LABEL[c.stage!]} has not captured a photo yet. See the scans below.</span></div>}
      </div>
      {cp.evidence.length > 1 && <div className="ts-row" style={{ flexWrap: "wrap", marginTop: 8 }}>{cp.evidence.slice(0, -1).slice(-5).map((e) => <Thumb key={e.id} e={e} />)}</div>}
      {c.kind === "DISPUTE" && (() => { const v = snap.checkpoints[0].evidence.find((e) => e.purpose === "packing_video"); return v ? (
        <div style={{ marginTop: 12 }}><div className="ts-tiny" style={{ marginBottom: 6 }}>Seller's packing video</div>
          <video src={v.url} controls muted playsInline preload="metadata" style={{ width: "100%", maxHeight: 220, background: "#120d33", border: "1px solid var(--ts-ink-deep)" }} /></div>) : null; })()}
      {scans.length > 0 && (
        <ul className="ts-list" style={{ marginTop: 12 }}>
          {scans.slice(-4).map((e) => <li key={e.event_id} className="ts-item"><span className={"ts-dot " + (e.payload.match ? "pass" : "fail")}>{e.payload.match ? "✓" : "✕"}</span>
            <div>Scanned a seal ending <b className="ts-hash" style={{ fontSize: 13 }}>{e.payload.hint}</b><div className={"ts-why" + (e.payload.match ? "" : " bad")}>{e.payload.match ? "matches this parcel" : "not this parcel's seal"}</div></div><span className="ts-tiny">{new Date(e.timestamp_server).toLocaleTimeString()}</span></li>)}
        </ul>
      )}
      {bad.map((x) => <div key={x.id} className="ts-card" style={{ marginTop: 8, borderColor: x.status === "FAIL" ? "var(--ts-red)" : "var(--ts-ochre)" }}>
        <span className={"ts-chip " + (x.status === "FAIL" ? "bad" : "warn")}>{x.check_type.replace("_", " ")} · {x.status.toLowerCase()}</span><div className="ts-why">{x.explanation}</div></div>)}
      <p className="ts-why">Responsibility is only a suggestion for you to weigh: the <b>{ROLE_LABEL[c.stage!].toLowerCase()}</b> stage is where it broke. Last verified: {c.last_verified ? ROLE_LABEL[c.last_verified as Stage].toLowerCase() : "nobody"}. Payment {money(snap.payment.amount)}: {snap.payment.state.replace("_", " ").toLowerCase()} (pay on delivery, nothing is charged before the buyer accepts).</p>

      <div className="ts-tiny" style={{ margin: "18px 0 8px" }}>Your decision</div>
      <div className="ts-col">
        {options.map((o) => (
          <button key={o.k} className={"ts-option" + (pick === o.k ? " on" : "")} onClick={() => setPick(o.k)}>
            <b>{o.label}</b><span className="ts-why" style={{ display: "block", color: "inherit", opacity: .75 }}>{o.help}</span></button>
        ))}
        <AnimatePresence>
          {chosen && (
            <motion.div className="ts-col" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              {"needs" in chosen && chosen.needs && <textarea className="ts-input" rows={2} placeholder={`What should ${c.waiting_on?.name ?? "they"} do? (e.g. rescan the label in good light)`} value={instruction} onChange={(e) => setInstruction(e.target.value)} />}
              <textarea className="ts-input" rows={2} placeholder="Your reason (required, goes on the record)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <Slide tone={"danger" in chosen && chosen.danger ? "red" : "ink"} label={`Slide to: ${chosen.label.toLowerCase()}`}
                     disabled={!reason.trim() || ("needs" in chosen && chosen.needs && !instruction.trim())} onConfirm={decide} />
            </motion.div>
          )}
        </AnimatePresence>
        {err && <div className="ts-err" role="alert">{err}</div>}
      </div>

      <div className="ts-tiny" style={{ margin: "22px 0 4px" }}>What happened</div>
      {[...snap.events].reverse().slice(0, 12).map((e) => <EventRow key={e.event_id} e={e} />)}
    </div>
  );
}
